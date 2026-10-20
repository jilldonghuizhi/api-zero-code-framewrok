# -*- coding: utf-8 -*-
"""pytest 收集器（插件）：仅 YAML；一个 business flow = 一个 case（单 ApiItem）。

- 只收集 testcases/ 下的 .yaml/.yml；testcases/apis/（单 API 组件库）与被标记为
  component/reusable 的业务文件不直接收集（仅供 include 复用）。
- 每个业务文件收集为一个 ApiItem（Item 名 = 业务 title），其内部 steps 由 run_flow 顺序执行。
"""
import logging
import os
from pathlib import Path
from typing import Any, Dict

import pytest

from common.config_reader import get_config
from common.context import ApiContext
from common.data_loader import load_case
from common.request_client import ApiClient
from engine.executor import run_flow
from utils.templating import get_constants

log = logging.getLogger("engine.collector")


def _find_apis_dir(path) -> Path:
    """定位 testcases/apis（单 API 组件库）目录；找不到则回退到业务文件所在目录。"""
    p = Path(path).resolve()
    for parent in p.parents:
        if parent.name == "testcases":
            d = parent / "apis"
            return d if d.is_dir() else parent
    return p.parent


def _resolve_env(spec: Dict[str, Any]) -> str:
    """从 config.yaml 读 base_url；缺失即抛配置错误（不 skip、禁止无服务空跑）。"""
    cfg = get_config("config/config.yaml")
    base_url = cfg.get("base_url") or os.environ.get("BASE_URL") or ""
    if not str(base_url).strip():
        raise RuntimeError(
            "base_url 未配置：请在 config/config.yaml 填写被测服务地址 base_url"
            "（或用环境变量 BASE_URL 覆盖）；未配置时不允许空跑")
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
        title = case.get("title") or case.get("name") or self.path.stem
        spec = dict(case)
        spec["__title__"] = title
        spec["__file__"] = str(self.path)
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
        spec = self.spec
        title = spec.get("__title__", self.name)
        self._apply_allure_meta()
        base_url = _resolve_env(spec)
        cfg = get_config("config/config.yaml")
        client = ApiClient(
            base_url,
            timeout=int(cfg.get("timeout", 30) or 30),
            retries=int(cfg.get("retries", 1) or 0),
            headers=(get_constants() or {}).get("default_headers") or {},
            verify=bool(cfg.get("verify", True)),
            token=cfg.get("token") or None,
        )
        ctx = ApiContext(biz_title=title,
                         base_dir=self.path.parent,
                         apis_dir=_find_apis_dir(self.path))
        run_flow(client, spec, ctx, log=log)
