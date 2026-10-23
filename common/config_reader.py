# -*- coding: utf-8 -*-
"""YAML 配置读取 + 环境变量覆盖（顶层 ENV_KEY 或 {节名}_{子键}）。"""
import os
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


class ConfigReader:
    """加载 YAML 配置并提供 get / get_section / to_dict，支持环境变量覆盖。"""

    def __init__(self, config_path: Optional[str] = None) -> None:
        self._path = Path(config_path) if config_path else None
        self._data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if self._path and self._path.exists() and yaml is not None:
            return yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
        return {}

    def get(self, key: str, default: Any = None) -> Any:
        """顶层字段；环境变量 KEY(大写) 优先；环境变量为空字符串时不覆盖配置文件。"""
        env = os.environ.get(key.upper())
        if env is not None and env.strip() != "":
            return _coerce(env)
        return self._data.get(key, default)

    def get_section(self, section: str) -> Dict[str, Any]:
        """整段读取；节内子键支持 {SECTION}_{SUBKEY}(大写) 环境变量覆盖（空值不覆盖）。"""
        base = dict(self._data.get(section, {}) or {})
        prefix = f"{section.upper()}_"
        for k, v in os.environ.items():
            if k.startswith(prefix) and v.strip() != "":
                base[k[len(prefix):].lower()] = _coerce(v)
        return base

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._data)


def _coerce(value: str) -> Any:
    v = value.strip()
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [x.strip() for x in inner.split(",") if x.strip()] or []
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("none", "null", ""):
        return None
    try:
        return int(v)
    except ValueError:
        return v


_CONFIG_CACHE: Dict[str, "ConfigReader"] = {}


def get_config(config_path: Optional[str] = None) -> ConfigReader:
    """便利工厂：优先 config_path，其次 API_FRAMEWORK_CONFIG，再回落 config/config.yaml。
    同一路径进程内缓存，避免重复 yaml.safe_load。"""
    path = config_path or os.environ.get("API_FRAMEWORK_CONFIG", "config/config.yaml")
    key = str(Path(path).resolve())
    cached = _CONFIG_CACHE.get(key)
    if cached is not None:
        return cached
    inst = ConfigReader(path)
    _CONFIG_CACHE[key] = inst
    return inst
