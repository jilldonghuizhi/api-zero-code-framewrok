# -*- coding: utf-8 -*-
"""多类型断言引擎 + 软断言（API 内全部执行后汇总）。

期望/实际均带类型展示（如 `期望 == '200' (str) | 实际 '1004' (str)`），
一眼分辨「字符串 200 与数字 200」这类差异。
"""
import logging
import re
from typing import Any, Dict, List, Tuple

from utils.extract import jsonpath_extract
from utils.templating import resolve

try:
    from jsonschema import validate as _jsonschema_validate
    _HAS_JSONSCHEMA = True
except ImportError:  # pragma: no cover
    _HAS_JSONSCHEMA = False

log = logging.getLogger("api.assert")

# 断言类型别名 -> 规范类型
_ALIAS = {
    "eq": "equals", "ne": "not_equals",
    "gt": "greater_than", "lt": "less_than",
    "gte": "greater_or_equal", "lte": "less_or_equal",
    "matches": "regex",
}

_TYPE_MAP = {
    "str": str, "string": str, "int": int, "integer": int,
    "float": float, "number": (int, float), "bool": bool, "boolean": bool,
    "list": list, "array": list, "dict": dict, "object": dict, "null": type(None),
}


def _normalize_validate(validate) -> List[Tuple[str, dict]]:
    """把 validate 统一为 [(类型, 参数dict)]。"""
    if validate is None:
        return []
    if isinstance(validate, dict):
        return [(k, v if isinstance(v, dict) else {}) for k, v in validate.items()]
    if isinstance(validate, list):
        out = []
        for item in validate:
            if isinstance(item, dict):
                for k, v in item.items():
                    out.append((k, v if isinstance(v, dict) else {}))
            elif isinstance(item, str):
                out.append((item, {}))
        return out
    return []


def _resolve_actual(actual, resp, ctx) -> Tuple[Any, str]:
    """返回 (取值, 取值说明)。actual 支持 status_code / $.x / ${var} / 字面量。"""
    if actual is None:
        return None, "无 actual"
    if isinstance(actual, str) and actual.strip() in ("status_code", "status"):
        return resp.status_code, "响应状态码"
    s = str(actual).strip()
    if s.startswith("${"):
        return resolve(actual, ctx), f"变量 {actual}"
    if s.startswith("$."):
        if resp.json is None:
            raise AssertionError(
                f"响应非 JSON（HTTP {resp.status_code}，可能路径或方法不符），无法按 JSONPath 取值")
        return jsonpath_extract(resp.json, s, None), f"响应 {actual}"
    if s.startswith("$"):                                  # $ 根/其它 JSONPath
        if resp.json is None:
            raise AssertionError(
                f"响应非 JSON（HTTP {resp.status_code}，可能路径或方法不符），无法按 JSONPath 取值")
        return jsonpath_extract(resp.json, s, None), f"响应 {actual}"
    return actual, f"字面量 {actual}"


def _as_number(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        return int(v)
    except (ValueError, TypeError):
        try:
            return float(v)
        except (ValueError, TypeError):
            return None


def _loose_eq(a, b) -> bool:
    """equals 数值宽松：字符串与数字比较时按数值判等。"""
    if type(a) == type(b):
        return a == b
    na, nb = _as_number(a), _as_number(b)
    if na is not None and nb is not None:
        return na == nb
    return str(a) == str(b)


def _fmt(v: Any) -> str:
    """带类型的取值展示：`'200' (str)`。"""
    return f"{v!r} ({type(v).__name__})"


def _check(atype: str, actual, spec: dict, resp, ctx) -> None:
    """按类型比较；失败抛 AssertionError（含「期望 X | 实际 Y」与类型语义）。"""
    at = _ALIAS.get(str(atype).strip().lower(), str(atype).strip().lower())
    expected = spec.get("expected")
    pattern = spec.get("pattern")

    if at in ("status_code", "status"):                    # actual 隐含为响应状态码
        if not _loose_eq(actual, expected):
            raise AssertionError(f"期望 HTTP 状态码 == {_fmt(expected)} | 实际 {_fmt(actual)}")
        return

    if at == "equals":
        if not _loose_eq(actual, expected):
            raise AssertionError(f"期望 == {_fmt(expected)} | 实际 {_fmt(actual)}")
        return

    if at == "not_equals":
        if _loose_eq(actual, expected):
            raise AssertionError(f"期望 != {_fmt(expected)} | 实际 {_fmt(actual)}")
        return

    if at in ("greater_than", "less_than", "greater_or_equal", "less_or_equal"):
        op = {"greater_than": ">", "less_than": "<",
              "greater_or_equal": ">=", "less_or_equal": "<="}[at]
        ok = (actual > expected) if op == ">" else (actual < expected) if op == "<" else \
             (actual >= expected) if op == ">=" else (actual <= expected)
        if not ok:
            raise AssertionError(f"期望 {op} {_fmt(expected)} | 实际 {_fmt(actual)}")
        return

    if at == "contains":
        if isinstance(actual, str):
            if expected not in actual:
                raise AssertionError(f"期望包含子串 {_fmt(expected)} | 实际 {_fmt(actual)}")
        elif isinstance(actual, (list, tuple)):
            if expected not in actual:
                raise AssertionError(f"期望列表包含 {_fmt(expected)} | 实际 {_fmt(actual)}")
        elif isinstance(actual, dict):
            if expected not in actual:
                raise AssertionError(f"期望 dict 含键 {_fmt(expected)} | 实际键 {list(actual.keys())}")
        else:
            raise AssertionError(f"contains 不支持的类型 {type(actual).__name__}")
        return

    if at == "not_contains":
        if isinstance(actual, str):
            if expected in actual:
                raise AssertionError(f"期望不包含子串 {_fmt(expected)} | 实际 {_fmt(actual)}")
        elif isinstance(actual, (list, tuple)):
            if expected in actual:
                raise AssertionError(f"期望列表不包含 {_fmt(expected)} | 实际 {_fmt(actual)}")
        elif isinstance(actual, dict):
            if expected in actual:
                raise AssertionError(f"期望 dict 不含键 {_fmt(expected)} | 实际键 {list(actual.keys())}")
        else:
            raise AssertionError(f"not_contains 不支持的类型 {type(actual).__name__}")
        return

    if at in ("in", "not_in"):
        if not isinstance(expected, (list, tuple, set, dict)):
            raise AssertionError(
                f"{at} 断言的 expected 必须是集合(list/tuple/set)，当前为 {type(expected).__name__}: {expected!r}")
        hit = actual in expected
        if at == "in" and not hit:
            raise AssertionError(f"期望属于集合 {expected!r} | 实际 {_fmt(actual)}")
        if at == "not_in" and hit:
            raise AssertionError(f"期望不属于集合 {expected!r} | 实际 {_fmt(actual)}")
        return

    if at in ("length", "min_length", "max_length"):
        try:
            size = len(actual)
        except TypeError:
            raise AssertionError(f"长度类断言需可计长对象 | 实际 {_fmt(actual)}")
        if at == "length" and size != expected:
            raise AssertionError(f"期望长度 == {expected} | 实际长度 {size}")
        if at == "min_length" and size < expected:
            raise AssertionError(f"期望长度 >= {expected} | 实际长度 {size}")
        if at == "max_length" and size > expected:
            raise AssertionError(f"期望长度 <= {expected} | 实际长度 {size}")
        return

    if at == "type":
        name = str(expected).lower()
        target = _TYPE_MAP.get(name)
        if target is None:
            raise AssertionError(f"未知的 type 期望值 {expected!r}")
        if not isinstance(actual, target) or (target is int and isinstance(actual, bool)):
            raise AssertionError(f"期望类型 {name} | 实际类型 {type(actual).__name__}")
        return

    if at == "regex":
        if pattern is None:
            raise AssertionError("regex 断言缺少 pattern")
        if re.search(str(pattern), str(actual)) is None:
            raise AssertionError(f"期望匹配正则 {pattern!r} | 实际 {_fmt(actual)}")
        return

    if at == "exists":
        if actual is None or actual == "" or actual == [] or actual == {}:
            raise AssertionError(f"期望存在且非空 | 实际 {_fmt(actual)}")
        return

    if at == "not_exists":
        if not (actual is None or actual == "" or actual == [] or actual == {}):
            raise AssertionError(f"期望不存在或为空 | 实际 {_fmt(actual)}")
        return

    if at == "schema":
        if not _HAS_JSONSCHEMA:
            raise AssertionError("jsonschema 未安装，无法执行 schema 断言（请 pip install jsonschema）")
        schema = spec.get("schema") or expected
        try:
            _jsonschema_validate(instance=actual, schema=schema)
        except Exception as e:
            raise AssertionError(f"schema 校验失败: {e}")
        return

    raise AssertionError(f"未知断言类型: {atype}")


def _format_summary(biz_title: str, api_name: str, failures: List[Tuple[str, str, str, str]]) -> str:
    """软断言汇总：共 N 项未通过 + 逐条类型/名称/期望/实际/取值路径。"""
    lines = [f"❌ 业务「{biz_title}」· API「{api_name}」断言失败：共 {len(failures)} 项未通过"]
    for i, (atype, name, err, src) in enumerate(failures, 1):
        lines.append(f"  {i}. [{atype} · {name}] {err}  （取值: {src}）")
    return "\n".join(lines)


def assert_spec(resp, validate, ctx, *, api_name: str = "", biz_title: str = "") -> None:
    """对单个 API 响应执行断言列表（软断言：全部跑完再汇总）。"""
    specs = _normalize_validate(validate)
    failures: List[Tuple[str, str, str, str]] = []
    for atype, raw in specs:
        spec = dict(raw) if isinstance(raw, dict) else {}
        for key in ("expected", "pattern"):      # expected/pattern 支持 ${...} 常量
            if key in spec:
                spec[key] = resolve(spec[key], ctx)
        # status_code/status 无需声明 actual，默认取响应状态码（避免取到 None）
        if str(atype).strip().lower() in ("status_code", "status") and spec.get("actual") is None:
            spec["actual"] = "status_code"
        actual, src = _resolve_actual(spec.get("actual"), resp, ctx)
        try:
            _check(atype, actual, spec, resp, ctx)
        except AssertionError as e:
            failures.append((atype, spec.get("name", atype), str(e), src))
    if failures:
        raise AssertionError(_format_summary(biz_title, api_name, failures))
