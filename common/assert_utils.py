# -*- coding: utf-8 -*-
"""多类型断言引擎 + 软断言。

一个 API 内所有断言均执行，全部跑完后若有失败则抛出「汇总」AssertionError，
逐条列出类型、名称、期望值、实际值与取值路径（见 reference.md 第三节）。
"""
import re
from typing import Any, Dict, List, Optional, Tuple

from utils.extract import jsonpath_extract
from utils.templating import resolve

# 断言类型 -> 展示用操作符
_OPS = {
    "equals": "==", "eq": "==",
    "not_equals": "!=", "ne": "!=",
    "greater_than": ">", "gt": ">",
    "less_than": "<", "lt": "<",
    "greater_or_equal": ">=", "gte": ">=",
    "less_or_equal": "<=", "lte": "<=",
}

_STATUS_ALIASES = ("status_code", "status")

_TYPE_MAP = {
    "str": str, "string": str,
    "int": int, "integer": int,
    "float": float, "number": (int, float),
    "bool": bool, "boolean": bool,
    "list": list, "array": list,
    "dict": dict, "object": dict,
    "null": type(None), "none": type(None),
}


def _normalize_validate(validate) -> List[Tuple[str, Any]]:
    """把 validate 归一为 [(类型, 参数), ...]；支持列表、单 dict、{类型: 参数} 三种写法。"""
    if not validate:
        return []
    out: List[Tuple[str, Any]] = []
    if isinstance(validate, dict):
        for k, v in validate.items():
            out.append((str(k), v))
        return out
    if isinstance(validate, list):
        for item in validate:
            if isinstance(item, dict):
                for k, v in item.items():
                    out.append((str(k), v))
    return out


def _num(value: Any) -> Optional[float]:
    """数值宽容转换：bool 不算数值；"200" -> 200.0；非数值返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _loose_eq(a: Any, b: Any) -> bool:
    """宽松相等：数值与数值字符串互认，其余用 ==。"""
    if a == b:
        return True
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        return na == nb
    return False


def _in_list(actual: Any, expected: Any) -> bool:
    if isinstance(expected, (list, tuple, set)):
        return any(_loose_eq(actual, e) for e in expected)
    return _loose_eq(actual, expected)


def _is_empty(actual: Any) -> bool:
    return actual is None or actual == "" or actual == [] or actual == {}


def _size(actual: Any) -> Optional[int]:
    try:
        return len(actual)
    except TypeError:
        return None


def _resolve_actual(expr: Any, resp, ctx) -> Tuple[Any, str]:
    """解析 actual 表达式，返回 (值, 取值说明)。"""
    if not isinstance(expr, str):
        return expr, "字面量"
    e = expr.strip()
    if e in _STATUS_ALIASES:
        return resp.status_code, "响应状态码"
    if e.startswith("$") and not e.startswith("${"):
        if resp is not None and getattr(resp, "json", None) is None:
            return None, (f"响应非 JSON（HTTP {getattr(resp, 'status_code', '?')}，"
                          f"可能路径或方法不符），无法按 JSONPath 取值 {e}")
        return jsonpath_extract(resp.json, e, None), f"响应 {e}"
    if "${" in e:
        return resolve(e, ctx), f"变量 {e}"
    return e, "字面量"


def _fail(msg: str) -> None:
    raise AssertionError(msg)


def _check(atype: str, actual: Any, spec: Dict[str, Any], resp=None, ctx=None) -> None:
    """按类型执行单条断言，失败抛 AssertionError（消息含期望/实际）。"""
    at = str(atype).strip().lower()
    expected = spec.get("expected")
    pattern = spec.get("pattern")
    # status_code / status：actual 隐含为响应状态码，按相等比较
    if at in _STATUS_ALIASES:
        at = "equals"
        expected = spec.get("expected")

    def _pair(op: str) -> str:
        return f"期望 {op} {expected!r} | 实际 {actual!r}"

    if at in ("equals", "eq"):
        if not _loose_eq(actual, expected):
            _fail(_pair("=="))
    elif at in ("not_equals", "ne"):
        if _loose_eq(actual, expected):
            _fail(_pair("!="))
    elif at in ("greater_than", "gt", "less_than", "lt",
                "greater_or_equal", "gte", "less_or_equal", "lte"):
        na, nb = _num(actual), _num(expected)
        if na is None or nb is None:
            _fail(f"比较需数值 | 实际 {actual!r} | 期望 {expected!r}")
        op = _OPS[at]
        ok = (na > nb) if op == ">" else (na < nb) if op == "<" else \
             (na >= nb) if op == ">=" else (na <= nb)
        if not ok:
            _fail(f"期望 {op} {expected!r} | 实际 {actual!r}")
    elif at == "contains":
        try:
            ok = expected in actual
        except TypeError:
            ok = False
        if not ok:
            _fail(f"期望包含 {expected!r} | 实际 {actual!r}")
    elif at == "not_contains":
        try:
            ok = expected not in actual
        except TypeError:
            ok = True
        if not ok:
            _fail(f"期望不包含 {expected!r} | 实际 {actual!r}")
    elif at == "in":
        if not _in_list(actual, expected):
            _fail(f"期望属于 {expected!r} | 实际 {actual!r}")
    elif at == "not_in":
        if _in_list(actual, expected):
            _fail(f"期望不属于 {expected!r} | 实际 {actual!r}")
    elif at in ("length", "min_length", "max_length"):
        size = _size(actual)
        if size is None:
            _fail(f"长度类断言需可计长对象 | 实际 {actual!r}")
        nb = _num(expected)
        if nb is None:
            _fail(f"长度期望需数值 | 期望 {expected!r}")
        if at == "length" and size != nb:
            _fail(f"期望长度 == {expected!r} | 实际长度 {size}")
        if at == "min_length" and size < nb:
            _fail(f"期望长度 >= {expected!r} | 实际长度 {size}")
        if at == "max_length" and size > nb:
            _fail(f"期望长度 <= {expected!r} | 实际长度 {size}")
    elif at == "type":
        t = _TYPE_MAP.get(str(expected).strip().lower())
        if t is None:
            _fail(f"未知类型名 {expected!r}（可选 str/int/float/bool/list/dict/null）")
        if t is type(None):
            ok = actual is None
        elif t is bool:
            ok = isinstance(actual, bool)
        elif t is int:
            ok = isinstance(actual, int) and not isinstance(actual, bool)
        elif t is (int, float):
            ok = isinstance(actual, (int, float)) and not isinstance(actual, bool)
        else:
            ok = isinstance(actual, t)
        if not ok:
            _fail(f"期望类型 {expected!r} | 实际 {type(actual).__name__}（值 {actual!r}）")
    elif at in ("regex", "matches"):
        if pattern is None:
            _fail("regex 断言缺少 pattern")
        if not re.search(str(pattern), str(actual)):
            _fail(f"期望匹配 {pattern!r} | 实际 {actual!r}")
    elif at == "exists":
        if _is_empty(actual):
            _fail(f"期望存在且非空 | 实际 {actual!r}")
    elif at == "not_exists":
        if not _is_empty(actual):
            _fail(f"期望不存在或为空 | 实际 {actual!r}")
    elif at == "schema":
        try:
            import jsonschema
        except ImportError:  # pragma: no cover
            _fail("jsonschema 未安装，请 pip install -r requirements.txt")
            return
        try:
            jsonschema.validate(instance=actual, schema=expected)
        except Exception as e:
            _fail(f"JSON Schema 校验未通过：{e}")
    else:
        _fail(f"未知断言类型 {atype!r}")


def _format_summary(biz_title: str, api_name: str, failures: List[Tuple[str, str, str, str]]) -> str:
    """软断言汇总信息：共 N 项未通过 + 逐条类型/名称/期望/实际/取值路径。"""
    head = f"❌ 业务「{biz_title}」· API「{api_name}」断言失败：共 {len(failures)} 项未通过"
    lines = [head]
    for i, (atype, name, msg, src) in enumerate(failures, 1):
        lines.append(f"  {i}. [{atype} · {name}] {msg}  （取值: {src}）")
    return "\n".join(lines)


def assert_spec(resp, validate, ctx, *, api_name: str = "", biz_title: str = "") -> None:
    """执行一个 API 的全部断言（软断言：全部执行后汇总抛出）。"""
    specs = _normalize_validate(validate)
    failures: List[Tuple[str, str, str, str]] = []
    for atype, raw in specs:
        spec = dict(raw) if isinstance(raw, dict) else {}
        for key in ("expected", "pattern"):   # expected/pattern 支持 ${...} 常量
            if key in spec:
                spec[key] = resolve(spec[key], ctx)
        if str(atype).strip().lower() in _STATUS_ALIASES and "actual" not in spec:
            actual, src = resp.status_code, "响应状态码"
        else:
            actual, src = _resolve_actual(spec.get("actual"), resp, ctx)
        try:
            _check(atype, actual, spec, resp, ctx)
        except AssertionError as e:
            failures.append((atype, spec.get("name", atype), str(e), src))
    if failures:
        raise AssertionError(_format_summary(biz_title, api_name, failures))
