# -*- coding: utf-8 -*-
"""YAML 用例加载 + 测试数据常量加载（share ⊕ env 深合并）。"""
import os
from pathlib import Path
from typing import Any, Dict

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

from utils.dicts import deep_merge
from common.paths import DATA_DIR


def _load_yaml_file(path) -> Dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"用例文件不存在: {p}")
    if yaml is None:  # pragma: no cover
        raise RuntimeError("pyyaml 未安装，请执行 pip install -r requirements.txt")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def load_case(path) -> Dict[str, Any]:
    """加载单 API / 业务 YAML 文件。"""
    return _load_yaml_file(path)


def load_constants(env: str = "dev") -> Dict[str, Any]:
    """加载 data/share.yaml 并按 env 深合并 data/<env>.yaml，得到当前环境常量命名空间。"""
    base = _load_yaml_file(DATA_DIR / "share.yaml")
    env_file = DATA_DIR / f"{env}.yaml"
    if env_file.is_file():
        over = _load_yaml_file(env_file)
        base = deep_merge(base, over)
    return base
