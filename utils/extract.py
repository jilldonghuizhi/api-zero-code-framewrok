# -*- coding: utf-8 -*-
"""JSONPath 提取（带默认值）。"""
from typing import Any, Tuple

try:
    from jsonpath_ng import parse as _jp_parse
except ImportError:  # pragma: no cover
    _jp_parse = None  # type: ignore


def parse_extract(val) -> Tuple[str, Any, Any]:
    """解析 extract/extract_global 规则。
    - 列表形式: [mode, path, default]，如 [json, $.data.id, ""]
    - 字符串形式: 视为 [json, path, None]
    返回 (mode, path, default)。
    """
    if isinstance(val, list):
        mode = str(val[0]).lower() if len(val) > 0 else "json"
        path = val[1] if len(val) > 1 else None
        default = val[2] if len(val) > 2 else None
        return mode, path, default
    return "json", val, None


def jsonpath_extract(data: Any, expr: str, default: Any = None) -> Any:
    """对 data 用 JSONPath 取值；无匹配或解析失败返回 default。"""
    if _jp_parse is None:  # pragma: no cover
        raise RuntimeError("jsonpath-ng 未安装，请 pip install -r requirements.txt")
    if data is None:
        return default
    try:
        matches = [m.value for m in _jp_parse(expr).find(data)]
    except Exception:
        return default
    if not matches:
        return default
    return matches[0] if len(matches) == 1 else matches
