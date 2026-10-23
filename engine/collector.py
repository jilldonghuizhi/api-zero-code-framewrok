# -*- coding: utf-8 -*-
"""pytest 自动收集：一个 business flow = 一个 case（单 ApiItem）；仅 YAML；排除 apis/ 库。"""
import logging
import os
from pathlib import Path

import pytest

from common.config_reader import get_config
from common.context import ApiContext
from common.data_loader import load_case
from common.request_client import ApiClient
from engine.executor import run_flow
from utils.templating import get_constants

log = logging.getLogger("engine.collector")


def _find_apis_dir(path) -> Path:
    """定位 testcases/apis 目录（单 API 组件库）。"""
    p = Path(path)
    cur = p.parent
    while True:
        cand = cur / "apis"
        if cand.is_dir():
            return cand
        tc = cur / "testcases" / "apis"
        if tc.is_dir():
            return tc
        if str(cur) in (str(cur.root), cur.root):
            break
        cur = cur.parent
    return Path("testcases/apis")


def _resolve_env(spec) -> str:
    """从 config.yaml 读取 base_url；缺失即抛配置错误（不收集/不 skip，禁止无服务空跑）。"""
    cfg = get_config("config/config.yaml")
    base_url = cfg.get("base_url") or os.environ.get("BASE_URL") or ""
    if not base_url:
        raise RuntimeError(
            "config.yaml 未配置 base_url，无法执行测试。"
            "请在 config/config.yaml 中配置被测服务地址 base_url（如 http://127.0.0.1:8080）。"
            f" [诊断] 配置文件={getattr(cfg, '_path', 'config/config.yaml')}；"
            f"BASE_URL 环境变量={os.environ.get('BASE_URL', '<未设置>')!r}")
    return str(base_url).strip().rstrip("/")


def pytest_collect_file(parent, file_path):
    p = Path(file_path)
    if p.suffix.lower() not in (".yaml", ".yml"):
        return None                                   # 仅 YAML
    if "testcases" not in p.parts:
        return None
    if p.parent.name == "apis":
        return None                                   # 库目录不直接收集
    try:
        _case = load_case(p)
    except Exception:
        _case = {}
    if _case.get("component") or _case.get("reusable"):
        return None                                   # 仅被 include 的业务，不独立收集
    return YamlFile.from_parent(parent, path=p)


class YamlFile(pytest.File):
    """一个业务 YAML 文件 = 一个 case 容器。"""

    def collect(self):
        case = load_case(self.path)
        title = case.get("title") or case.get("name") or Path(self.path).stem
        spec = dict(case)
        spec["__title__"] = title
        spec["__file__"] = str(self.path)
        # 一个 business flow = 一个 case：单个 ApiItem，steps 在 runtest 内顺序执行
        yield ApiItem.from_parent(self, name=title, spec=spec)


class ApiItem(pytest.Item):
    """一个 business flow = 一个 case；runtest 内部顺序执行全部步骤。"""

    def __init__(self, name, parent, **kwargs):
        self.spec = kwargs.pop("spec")
        super().__init__(name=name, parent=parent, **kwargs)

    def reportinfo(self):
        return self.path, 0, f"api: {self.name}"

    def _apply_allure_meta(self) -> None:
        try:
            import allure
            if self.spec.get("feature"):
                allure.dynamic.feature(str(self.spec["feature"]))
            if self.spec.get("story"):
                allure.dynamic.story(str(self.spec["story"]))
            if self.spec.get("severity"):
                allure.dynamic.severity(str(self.spec["severity"]))
            if self.spec.get("description"):
                allure.dynamic.description(str(self.spec["description"]))
        except Exception:
            pass

    def runtest(self):
        self._apply_allure_meta()
        base_url = _resolve_env(self.spec)
        cfg = get_config("config/config.yaml")
        client = ApiClient(
            base_url,
            timeout=int(cfg.get("timeout", 30) or 30),
            retries=int(cfg.get("retries", 1) or 0),
            headers=(get_constants() or {}).get("default_headers") or {},
            verify=bool(cfg.get("verify", True)),
            token=cfg.get("token") or None,
        )
        ctx = ApiContext(biz_title=self.spec.get("__title__"),
                         base_dir=Path(self.path).parent,
                         apis_dir=_find_apis_dir(self.path))
        # 失败重试交由 pytest-rerunfailures（--reruns/--reruns-delay，见 pytest.ini）；
        # 每次重跑是独立 attempt，allure-pytest 按 historyId 自动生成 Retries 视图。
        run_flow(client, self.spec, ctx, log=log, record=True)

    def repr_failure(self, excinfo, style=None):
        """失败只展示业务可读信息（断言汇总/请求失败详情），不打印框架堆栈。"""
        return str(excinfo.value)
