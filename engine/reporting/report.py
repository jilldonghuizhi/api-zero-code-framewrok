# -*- coding: utf-8 -*-
"""汇总 Allure 结果 -> report.json；并行安全收尾与清理；失败工件挂载。"""
import json
import logging
import os
import platform
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from common.config_reader import get_config
from common.paths import ALLURE_RESULTS_DIR, REPORTS_DIR, ALLURE_REPORT_DIR, REPORT_JSON


def _attach_type_for(ext: str) -> str:
    ext = ext.lower().lstrip(".")
    if ext == "webp":  # allure-pytest<=2.13 无 WEBP，降级为 PNG
        try:
            import allure  # noqa: F401
            from allure_commons.types import AttachmentType
            getattr(AttachmentType, "WEBP")
            return "webp"
        except Exception:
            return "png"
    table = {
        "png": "png", "jpg": "jpg", "jpeg": "jpg", "gif": "gif", "bmp": "bmp",
        "txt": "txt", "log": "txt", "html": "html", "json": "json",
        "xml": "xml", "zip": "zip",
    }
    return table.get(ext, "txt")


def attach_failure_artifact(name: str, artifact_path: str,
                            attach_type: Optional[str] = None) -> bool:
    """驱动器无关地把失败工件挂到 Allure（仅包装 allure.attach.file）。文件缺失返回 False 不阻断。"""
    p = Path(artifact_path)
    if not p.is_file():
        return False
    try:
        import allure
        allure.attach.file(str(p), name=name,
                           attachment_type=attach_type or _attach_type_for(p.suffix))
        return True
    except Exception:
        return False


def attach_failure_text(name: str, text: str) -> bool:
    """把可读的失败详情直接内联挂到 Allure（TEXT 类型，便于在报告里直接查看）。"""
    try:
        import allure
        from allure_commons.types import AttachmentType
        allure.attach(str(text), name=name, attachment_type=AttachmentType.TEXT)
        return True
    except Exception:
        return False


def _first_image(allure_dir: Path, attachments: Any) -> Optional[str]:
    """取该 case 第一张图片附件（失败截图）在 allure-results 下的路径。"""
    for a in (attachments or []):
        if isinstance(a, dict) and str(a.get("type", "")).startswith("image"):
            src = a.get("source")
            if src:
                p = allure_dir / src
                return str(p) if p.is_file() else None
    return None


def _collect_attachments(node: Any) -> List[Dict[str, Any]]:
    """递归收集 case（含嵌套 step）下的所有附件条目。"""
    out: List[Dict[str, Any]] = []
    if isinstance(node, dict):
        for a in (node.get("attachments") or []):
            if isinstance(a, dict):
                out.append(a)
        for st in (node.get("steps") or []):
            if isinstance(st, dict):
                out.extend(_collect_attachments(st))
    return out


def _first_text(allure_dir: Path, case_data: Any) -> Optional[str]:
    """取该 case 的「失败详情」文本附件（接口/请求/响应/错误全貌，干净无日志）。

    allure.attach 在 step 内调用时附件挂在 step.attachments 下，故递归 steps 收集；
    仅按名称命中「失败详情」（只有失败用例才会挂载）。绝不回退到 pytest 的 log 附件，
    否则会混入 engine.collector 的 INFO/ERROR 日志及被截断的现场，污染邮件错误列。
    """
    atts = _collect_attachments(case_data) if isinstance(case_data, dict) else []
    for a in atts:
        if isinstance(a, dict) and "失败详情" in str(a.get("name", "")):
            src = a.get("source")
            if src:
                p = allure_dir / src
                if p.is_file():
                    return p.read_text(encoding="utf-8", errors="ignore")
    return None


def build_summary(allure_dir: str = str(ALLURE_RESULTS_DIR)) -> Dict[str, Any]:
    """解析 allure-results，按 historyId 聚合（一个 flow = 一个 case）。

    pytest-rerunfailures 每次重跑会写一个独立的 *-result.json，但共享同一
    historyId；此处按 historyId 分组并取最终一次（stop 最大）的状态作为该
    case 的最终结果，保证「重试同一 case 仍为一条」，不会因重试次数增加而让
    用例计数变多（Allure HTML 也按 historyId 合并为一条，仅带重试记录）。
    """
    ad = Path(allure_dir)
    latest: Dict[str, Any] = {}   # historyId -> (stop, data)
    order: List[str] = []
    passed = failed = skipped = broken = 0
    duration = 0.0
    if ad.is_dir():
        for f in ad.glob("*-result.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            hid = data.get("historyId") or data.get("testCaseId") or data.get("name") or f.stem
            stop = data.get("stop", 0) or 0
            if hid not in latest or stop >= latest[hid][0]:
                latest[hid] = (stop, data)
                if hid not in order:
                    order.append(hid)
    cases: List[Dict[str, Any]] = []
    for hid in order:
        data = latest[hid][1]
        status = data.get("status", "unknown")
        name = data.get("name", hid)
        start = data.get("start", 0) or 0
        stop = data.get("stop", 0) or 0
        dur = round(max(0.0, (stop - start) / 1000.0), 3)
        duration += dur
        status_details = data.get("statusDetails") or {}
        err_msg = data.get("statusMessage")
        if not err_msg and isinstance(status_details, dict):
            err_msg = status_details.get("message")
        cases.append({
            "name": name, "status": status, "duration": dur,
            "error": err_msg,
            "error_detail": _first_text(ad, data),
            "screenshot": _first_image(ad, data.get("attachments")),
        })
        if status == "passed":
            passed += 1
        elif status == "failed":
            failed += 1
        elif status == "broken":
            broken += 1
        elif status == "skipped":
            skipped += 1
    return {
        "total": len(cases), "passed": passed, "failed": failed,
        "broken": broken, "skipped": skipped, "duration": round(duration, 3),
        "cases": cases, "env": {},
    }


def prune_reruns(allure_dir: str = str(ALLURE_RESULTS_DIR)) -> int:
    """兜底去重：按 historyId 仅保留最终一次结果文件（默认不调用，保留 Allure Retries 视图）。"""
    ad = Path(allure_dir)
    if not ad.is_dir():
        return 0
    latest: Dict[str, Any] = {}
    for f in ad.glob("*-result.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        hid = data.get("historyId") or f.stem
        stop = data.get("stop", 0) or 0
        if hid not in latest or stop >= latest[hid][0]:
            latest[hid] = (stop, f)
    keep = {v[1].name for v in latest.values()}
    removed = 0
    for f in ad.glob("*-result.json"):
        if f.name not in keep:
            f.unlink(missing_ok=True)
            removed += 1
    return removed


def write_json_report(summary: Dict[str, Any], out_path: str = str(REPORT_JSON)) -> Path:
    """写机器可读 report.json。"""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _is_worker(session: Any) -> bool:
    cfg = getattr(session, "config", None)
    return bool(cfg and getattr(cfg, "workerinput", None))


def reset_allure_dir(session: Any = None, allure_dir: str = str(ALLURE_RESULTS_DIR)) -> None:
    """并行安全清理：仅控制进程、非 collect-only 时清空结果目录。"""
    if session is not None:
        if _is_worker(session):
            return
        option = getattr(session.config, "option", None)
        if option and getattr(option, "collectonly", False):
            return
    ad = Path(allure_dir)
    if ad.is_dir():
        shutil.rmtree(ad, ignore_errors=True)
    ad.mkdir(parents=True, exist_ok=True)


def finalize_report_session(session: Any, allure_dir: str = str(ALLURE_RESULTS_DIR),
                            report_out: str = str(REPORT_JSON),
                            auto_open: Optional[bool] = None,
                            report_enabled: Optional[bool] = None,
                            enable_email: Optional[bool] = None,
                            env: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """并行安全收尾：仅控制进程执行一次 汇总->report.json->(可选)Allure->(可选)邮件。
    auto_open/report_enabled/enable_email 通常由 conftest.pytest_sessionfinish 从 pytest.ini + 环境变量注入。"""
    if _is_worker(session):
        return None
    option = getattr(session.config, "option", None)
    if option and getattr(option, "collectonly", False):
        return None
    if auto_open is None:
        auto_open = os.environ.get("AUTO_OPEN_REPORT", "true").lower() == "true"
    if report_enabled is None:
        report_enabled = os.environ.get("ENABLE_ALLURE_REPORT", "true").lower() == "true"
    if enable_email is None:
        enable_email = os.environ.get("ENABLE_EMAIL_REPORT", "false").lower() == "true"
    cfg = get_config("config/config.yaml")
    allure_dir_resolved = str(cfg.get_section("report").get("allure_dir", allure_dir))
    report_dir = str(cfg.get_section("report").get("report_dir", "reports/allure-report"))
    summary = build_summary(allure_dir_resolved)
    merged_env = {
        "env": str(cfg.get("env", "dev")),
        "base_url": str(cfg.get("base_url", "")),
        "app": str(cfg.get("app", "")),
        "device": str(cfg.get("device", "")),
        "os": f"{platform.system()} {platform.release()}",
        "report_dir": report_dir,
        "report_url": f"{report_dir} (本地，已自动打开)" if auto_open else report_dir,
    }
    if env:
        merged_env.update(env)
    summary["env"] = merged_env
    write_json_report(summary, str(cfg.get_section("report").get("json_path", report_out)))
    # 生成报告与自动打开是两个独立开关：enable_allure_report / auto_open_report
    if report_enabled:
        try:
            from .report_generator import generate_report
            generate_report(open_report=bool(auto_open))
        except Exception:
            pass
    if enable_email:
        try:
            from .mailer import send_report_email
            report_url = "http://127.0.0.1:4040/" if auto_open else report_dir
            send_report_email(summary=summary, report_url=report_url, report_dir=report_dir)
        except Exception as e:
            logging.getLogger("engine.reporting.report").warning("邮件发送失败：%s", e)
    return summary
