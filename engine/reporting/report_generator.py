# -*- coding: utf-8 -*-
"""Allure 报告生成：定位 allure CLI → 写入环境信息 → 生成 HTML → 可选 detached 打开。"""
import datetime
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Optional

from common.paths import ALLURE_RESULTS_DIR, ALLURE_REPORT_DIR


def _candidate_bins() -> list:
    cands = []
    if os.environ.get("ALLURE_BIN"):
        cands.append(os.environ["ALLURE_BIN"])
    # Windows 上 subprocess 无法直接解析 "allure" -> allure.bat，用 shutil.which
    # 借助 PATHEXT 拿到含扩展名的真实路径（如 ...\allure.bat），否则误判“未检测到”。
    resolved = shutil.which("allure")
    if resolved:
        cands.append(resolved)
    return cands


def _allure_bin() -> Optional[str]:
    """返回可用的 allure 路径；缺失（或 Java 不可运行）返回 None。"""
    for c in _candidate_bins():
        try:
            subprocess.run([c, "--version"], capture_output=True, text=True, timeout=30)
            return c
        except Exception:
            continue
    return None


def _allure_version(bin_path: str) -> str:
    try:
        out = subprocess.run([bin_path, "--version"], capture_output=True,
                             text=True, timeout=30)
        return (out.stdout or out.stderr).strip()
    except Exception:
        return ""


def write_allure_environment(allure_dir: str = str(ALLURE_RESULTS_DIR),
                             extra: Optional[dict] = None) -> None:
    """写 allure-results/environment.properties，Allure 报告 Overview 的 Environment 控件展示测试环境。"""
    from common.config_reader import get_config
    ad = Path(allure_dir)
    ad.mkdir(parents=True, exist_ok=True)
    cfg = get_config("config/config.yaml")
    av = _allure_version(_allure_bin() or "allure")
    # 键名不能含空格：environment.properties 按 Java Properties 解析，空格会被当作键值分隔符
    info = {
        "Environment": str(cfg.get("env", "dev")),
        "BaseURL": str(cfg.get("base_url", "")),
        "Python": platform.python_version(),
        "OS": f"{platform.system()} {platform.release()}",
        "Allure": av,
        "GeneratedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if extra:
        info.update({k: str(v) for k, v in extra.items()})
    lines = [f"{k}={v}" for k, v in info.items() if v not in ("", None)]
    (ad / "environment.properties").write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate_report(allure_dir: str = str(ALLURE_RESULTS_DIR),
                    report_dir: str = str(ALLURE_REPORT_DIR),
                    open_report: bool = False) -> int:
    """生成 Allure HTML 报告；返回 0 成功、非 0 失败（前置缺失/生成失败如实返回）。"""
    bin_path = _allure_bin()
    if not bin_path:
        try:
            sys.stderr.write(
                "未生成 Allure 报告：未检测到 allure-commandline（需 Java JRE/JDK 8+ 且 allure 可执行）。\n"
                "  - Node:    npm install -g allure-commandline\n"
                "  - Windows: scoop install allure / choco install allure-commandline / winget install qameta.allure\n"
                "  - macOS:   brew install allure\n"
                "  - Linux:   sudo apt install allure\n"
                "  - 或设置环境变量 ALLURE_BIN 指向 allure 可执行文件\n")
        except UnicodeEncodeError:
            sys.stderr.write(
                "Allure report NOT generated: allure-commandline missing (needs Java + allure CLI).\n")
        return 1
    history_src = Path(report_dir) / "history"
    if history_src.is_dir():  # 复用历史 -> 报告带趋势(trend)/分类/重试视图
        shutil.copytree(history_src, Path(allure_dir) / "history", dirs_exist_ok=True)
    try:  # 写入环境信息（Allure 报告 Environment 控件）
        write_allure_environment(allure_dir)
    except Exception:
        pass
    rc = subprocess.call([bin_path, "generate", allure_dir, "-o", report_dir, "--clean"])
    if rc != 0:
        sys.stderr.write(f"allure generate 失败 rc={rc}\n")
        return rc
    if open_report:
        # 已打开则跳过重新打开，刷新浏览器即可看到最新结果（避免重复起服务/端口冲突）
        if _allure_report_already_open():
            try:
                sys.stdout.write(
                    "Allure 报告已在打开（端口 4040 已占用）：跳过重新打开，刷新浏览器即可查看最新结果。\n")
            except Exception:
                pass
        else:
            # 禁止 file:// 直开；优先 allure open（自带 HTTP 服务）；脱离进程树启动，避免随 pytest 退出被回收
            _open_report_detached(bin_path, report_dir)
    return 0


_ALLURE_PORT = 4040  # 报告服务固定端口：打开与探测都用它，避免自增变化


def _allure_report_already_open(host: str = "127.0.0.1",
                                port: int = _ALLURE_PORT) -> bool:
    """判断 allure 报告服务是否已在本机固定端口监听，避免重复打开。

    仅做 TCP 连通性探测：能连上即视为已有服务在跑，直接复用、刷新即可。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def _open_report_detached(bin_path: str, report_dir: str,
                          port: int = _ALLURE_PORT) -> None:
    """以脱离父进程树的方式启动 allure open（固定端口），使本地报告服务在 pytest 退出后仍存活。"""
    kwargs = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([bin_path, "open", "--port", str(port), report_dir], **kwargs)
    except Exception:
        pass
