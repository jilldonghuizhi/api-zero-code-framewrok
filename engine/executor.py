# -*- coding: utf-8 -*-
"""执行引擎：一个 business flow = 一个 case，逐步顺序执行、软断言、失败留证、硬停止。

日志格式：「步骤 i/n · METHOD url [name] → 通过/失败」。
"""
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import allure
except ImportError:  # pragma: no cover
    allure = None  # type: ignore

from common.assert_utils import assert_spec
from common.data_loader import load_case as _load_case
from common.paths import ARTIFACTS_DIR
from common.request_client import ApiExecError
from utils.dicts import deep_merge
from utils.extract import jsonpath_extract, parse_extract
from utils.redact import redact
from utils.templating import get_constants, resolve

log = logging.getLogger("engine.flow")


def normalize_method(method: Any) -> str:
    """方法规范化：常量解析后统一转大写（GET/POST/PUT/DELETE/PATCH）。"""
    return str(method or "GET").strip().upper()


def _resolve_api_path(include: str, base_dir, apis_dir) -> Path:
    """解析 include 路径。

    优先级：业务文件目录下的 apis//flows/ → 全局 testcases/apis//flows/ → 业务文件相对路径。
    include 形如 "apis/your_api.yaml" 或 "flows/sub_flow.yaml"，两种前缀都会被尝试。
    """
    from common.paths import ROOT

    rel = str(include).strip().replace("\\", "/").strip("/")
    base = Path(base_dir) if base_dir else Path.cwd()
    parts = Path(rel).parts
    tail = Path(*parts[1:]) if len(parts) > 1 else Path(rel)

    candidates = []
    for d in (base / "apis", base / "flows",
              ROOT / "testcases" / "apis", ROOT / "testcases" / "flows"):
        candidates.append(d / rel)
        if len(parts) > 1:
            candidates.append(d / tail)
    candidates.append(base / rel)
    candidates.append(Path(rel))
    for c in candidates:
        if c.is_file():
            return c.resolve()
    raise FileNotFoundError(
        f"无法解析 include 路径: {include}（已尝试业务目录与 testcases/apis、testcases/flows）")


def build_step_spec(step, base_dir, apis_dir) -> Dict[str, Any]:
    """把 step（字符串或 {include, name?, with?}）构建成本次调用的单 API spec。"""
    if isinstance(step, str):
        step = {"include": step}
    path = _resolve_api_path(step["include"], base_dir, apis_dir)
    api = _load_case(path)
    spec = dict(api)
    spec["__file__"] = str(path)
    if "name" in step:
        spec["name"] = step["name"]
    if "with" in step:
        w = step["with"] or {}
        # 覆盖 request（json/params/headers 等；method/url 通常不覆盖）
        spec["request"] = deep_merge(api.get("request", {}) or {}, w.get("request", {}) or {})
        # 兼容：with 顶层直接写 json/params/headers
        for k in ("json", "params", "query", "headers", "form", "data", "files"):
            if k in w:
                spec["request"][k] = w[k]
        for k in ("validate", "extract", "extract_global"):   # 覆盖断言/提取
            if k in w:
                spec[k] = w[k]
    return spec


def _flatten_steps(case: Dict[str, Any], base_dir, apis_dir) -> List[Dict[str, Any]]:
    """把 steps（含嵌套 flow include）展开为有序单 API spec 列表——一个 flow 即一个 case 的全部步骤。"""
    out: List[Dict[str, Any]] = []
    for step in (case.get("steps") or []):
        if isinstance(step, str):
            step = {"include": step}
        path = _resolve_api_path(step["include"], base_dir, apis_dir)
        included = _load_case(path)
        if included.get("steps"):                     # 嵌套业务：递归展开并入本 case
            out.extend(_flatten_steps(included, path.parent, apis_dir))
        else:
            out.append(build_step_spec(step, base_dir, apis_dir))
    return out


def _save_evidence(api_name: str, *, request=None, response=None, error=None) -> Optional[Path]:
    """失败现场留证：把请求/响应快照写入 artifacts/ 并挂载 Allure。"""
    import json as _json
    import time as _t

    from engine.reporting.report import attach_failure_artifact, attach_failure_text

    art_dir = Path(ARTIFACTS_DIR)
    art_dir.mkdir(parents=True, exist_ok=True)
    snap = {
        "api": api_name,
        "request": redact(request),
        "response": redact(response),
        "error": error,
    }
    safe_name = "".join(c if c not in '\\/:*?"<>|' else "_" for c in str(api_name))[:60]
    path = art_dir / f"{safe_name}__{_t.strftime('%H%M%S', _t.localtime())}.json"
    payload = _json.dumps(snap, ensure_ascii=False, indent=2, default=str)
    path.write_text(payload, encoding="utf-8")
    attach_failure_artifact(f"失败证据-{api_name}", str(path))   # 挂载到 Allure，可回溯
    attach_failure_text(f"失败详情-{api_name}", payload)         # 内联可读详情（报告/邮件直接可见）
    return path


def _build_headers(req: Dict[str, Any], ctx) -> Dict[str, Any]:
    """默认头单一来源：default_headers < feature.headers < request.headers（后者覆盖同名）。"""
    consts = get_constants() or {}
    merged = deep_merge(consts.get("default_headers", {}) or {}, consts.get("headers", {}) or {})
    merged = deep_merge(merged, req.get("headers", {}) or {})
    return resolve(merged, ctx)


def _run_single_api(client, api_spec: Dict[str, Any], ctx) -> None:
    req = api_spec.get("request", {}) or {}
    method = normalize_method(resolve(req.get("method"), ctx))
    target = resolve(req.get("url") or req.get("path") or "", ctx)
    headers = _build_headers(req, ctx)
    params = resolve(req.get("params") or req.get("query") or {}, ctx)
    json_body = resolve(req.get("json"), ctx) or None                        # application/json
    form_body = resolve(req.get("form") or req.get("data"), ctx) or None     # x-www-form-urlencoded
    files_body = resolve(req.get("files"), ctx) or None                      # multipart/form-data
    api_name = api_spec.get("name", target)

    step_ctx = allure.step(f"{method} {target} [{api_name}]") if allure else None
    if step_ctx:
        step_ctx.__enter__()
    try:
        try:
            resp = client.request(method, target, json=json_body, data=form_body,
                                  files=files_body, params=params, headers=headers)
        except ApiExecError as e:
            _save_evidence(api_name, request={"method": method, "url": target,
                                              "headers": headers, "json": json_body},
                           error=str(e))
            raise ApiExecError(f"请求失败：{method} {target} - {e}") from e

        for key, val in (api_spec.get("extract") or {}).items():
            _mode, jp, default = parse_extract(val)
            ctx.set(key, jsonpath_extract(resp.json, jp, default))
        for key, val in (api_spec.get("extract_global") or {}).items():
            _mode, jp, default = parse_extract(val)
            ctx.set_global(key, jsonpath_extract(resp.json, jp, default))
            ctx.set(key, ctx.get(key))

        try:
            assert_spec(resp, api_spec.get("validate") or [], ctx,
                        api_name=api_name, biz_title=ctx.biz_title)
        except AssertionError:
            _save_evidence(api_name,
                           request={"method": method, "url": target, "headers": headers},
                           response={"status": resp.status_code,
                                     "json": resp.json, "text": (resp.text or "")[:2000]})
            raise
    finally:
        if step_ctx:
            step_ctx.__exit__(None, None, None)


def run_flow(client, case: Dict[str, Any], ctx, log=None) -> None:
    """顺序执行一个业务（flow）展开后的全部步骤；任一失败即硬停止后续步骤。"""
    logger = log or globals()["log"]
    steps = _flatten_steps(case, ctx.base_dir, ctx.apis_dir)
    if not steps:
        raise ValueError(f"业务 {case.get('title') or case.get('name')} 缺少 steps")
    total = len(steps)
    for i, spec in enumerate(steps, 1):
        req = spec.get("request", {}) or {}
        method = normalize_method(resolve(req.get("method"), ctx))
        target = resolve(req.get("url") or req.get("path") or "", ctx)
        api_name = spec.get("name", target)
        if logger:
            logger.info("步骤 %d/%d · %s %s [%s]", i, total, method, target, api_name)
        try:
            _run_single_api(client, spec, ctx)
        except (AssertionError, ApiExecError) as e:
            if logger:
                logger.error("步骤 %d/%d 失败，硬停止后续步骤 [%s]", i, total, api_name)
                for nxt in steps[i:]:
                    logger.warning("⏭️  API「%s」已跳过：上游 API『%s』执行失败，已停止执行本业务后续 API",
                                   nxt.get("name", "?"), api_name)
            raise
        if logger:
            logger.info("步骤 %d/%d 通过 [%s]", i, total, api_name)
