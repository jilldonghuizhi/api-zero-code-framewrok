# -*- coding: utf-8 -*-
"""项目路径集中管理：相对路径基于项目根，可由环境变量 PROJECT_ROOT 覆盖。

框架代码位于 <root>/common/paths.py，向上两级即项目根；以库方式导入时可用
PROJECT_ROOT 指向运行根（如 pytest 的 rootdir），避免所有相对路径硬绑定 CWD。
"""
import os
from pathlib import Path

_ROOT_ENV = os.environ.get("PROJECT_ROOT")


def _root() -> Path:
    if _ROOT_ENV:
        return Path(_ROOT_ENV)
    return Path(__file__).resolve().parent.parent


ROOT = _root()
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
ARTIFACTS_DIR = ROOT / "artifacts"
ALLURE_RESULTS_DIR = ROOT / "allure-results"
REPORTS_DIR = ROOT / "reports"
ALLURE_REPORT_DIR = REPORTS_DIR / "allure-report"
REPORT_JSON = REPORTS_DIR / "report.json"
