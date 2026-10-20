# -*- coding: utf-8 -*-
"""会话级配置：常量注入、Allure 报告生成与收尾。失败重试交由 pytest-rerunfailures 负责。"""
import os
import sys

# 确保项目根目录在 sys.path 中（common/engine/utils 顶层可导入）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

pytest_plugins = ["engine.collector"]


def pytest_addoption(parser):
    # 功能开关（与 pytest.ini 对应，环境变量可覆盖，便于 CI 动态开关）
    parser.addini("enable_allure_report", "是否生成 Allure HTML 报告", type="bool", default=True)
    parser.addini("auto_open_report", "生成后是否自动打开", type="bool", default=True)
    parser.addini("enable_email_report", "测试完成后是否发送邮件通知", type="bool", default=False)
    # 环境选择：--env 覆盖 config.yaml 的 env（对应 data/<env>.yaml）
    parser.addoption("--env", action="store", default=None,
                     help="目标环境(dev/prod)，对应 data/<env>.yaml，覆盖 config.yaml 的 env")


# 开关：pytest.ini 值 < 同名环境变量
_ENV_OVERRIDE = {
    "enable_allure_report": "ENABLE_ALLURE_REPORT",
    "auto_open_report": "AUTO_OPEN_REPORT",
    "enable_email_report": "ENABLE_EMAIL_REPORT",
}


def _switch(config, name, cast=str):
    env_key = _ENV_OVERRIDE.get(name)
    if env_key and os.environ.get(env_key) is not None:
        return cast(os.environ[env_key])
    return cast(config.getini(name))


def _bool(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def pytest_configure(config):
    # 注入当前环境常量到模板解析（data/share.yaml ⊕ data/<env>.yaml）
    from common.config_reader import get_config
    from common.data_loader import load_constants
    from utils.templating import set_constants
    # 环境名优先级：--env > 环境变量 TEST_ENV > config.yaml 的 env > "dev"
    env_name = config.getoption("--env", default=None)
    if not env_name:
        env_name = os.environ.get("TEST_ENV")
    if not env_name:
        env_name = get_config("config/config.yaml").get("env", "dev")
    env_name = env_name or "dev"
    set_constants(load_constants(env_name))


def pytest_sessionstart(session):
    from common.context import ApiContext
    from engine.reporting.report import reset_allure_dir
    ApiContext.reset_global()      # 每个会话开始清空跨 case 全局变量，避免进程级污染
    reset_allure_dir(session)


def pytest_sessionfinish(session, exitstatus):
    from engine.reporting.report import finalize_report_session

    auto_open = _switch(session.config, "auto_open_report", _bool)
    report_enabled = _switch(session.config, "enable_allure_report", _bool)
    enable_email = _switch(session.config, "enable_email_report", _bool)
    finalize_report_session(
        session,
        auto_open=auto_open,
        report_enabled=report_enabled,
        enable_email=enable_email,
    )
