# -*- coding: utf-8 -*-
"""${var} / ${env.X} / ${context.X} / ${read_yaml(X)} 模板解析。
变量查找顺序：运行时变量(ctx) → 常量命名空间(data/ 环境合并后) → 未命中保留原样。
"""
import os
import re
from typing import Any, Dict, Optional

_VAR = re.compile(r"\$\{([^}]+)\}")

_CONSTANTS: Dict[str, Any] = {}


def set_constants(ns: Optional[Dict[str, Any]]) -> None:
    """注入当前环境常量命名空间（由 conftest 在会话开始时调用）。"""
    global _CONSTANTS
    _CONSTANTS = ns or {}


def get_constants() -> Dict[str, Any]:
    return _CONSTANTS


def _as_runtime(ctx) -> Dict[str, Any]:
    """把 ctx（dict 或 ApiContext）归一为运行时变量字典。"""
    if isinstance(ctx, dict):
        return ctx
    runtime = getattr(ctx, "runtime", None)
    if callable(runtime):
        return runtime()
    data = getattr(ctx, "data", None)
    return data if isinstance(data, dict) else {}


def _lookup(name: str, runtime: Dict[str, Any]) -> Any:
    name = name.strip()
    # ${env.X} / ${env(X)}
    if name.startswith("env."):
        return os.environ.get(name[4:])
    if name.startswith("env("):
        return os.environ.get(name[4:].rstrip(")"))
    if name.startswith("context."):
        return runtime.get(name[8:])
    # ${read_yaml(X)} 等价于 ${X}（运行时变量）
    if name.startswith("read_yaml("):
        name = name[len("read_yaml("):].rstrip(")")

    # 优先运行时变量（含 extract_global）
    if name in runtime:
        return runtime[name]
    # 嵌套路径逐段查找运行时变量
    cur = runtime
    ok = True
    for part in name.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            ok = False
            break
    if ok:
        return cur
    # 回退到常量命名空间
    cur = _CONSTANTS
    ok = True
    for part in name.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            ok = False
            break
    if ok:
        return cur
    # 未命中：保留原样（便于排障）
    return "${" + name + "}"


def _resolve_str(s: str, runtime: Dict[str, Any]) -> Any:
    s = str(s)
    m = _VAR.fullmatch(s.strip())
    if m:
        return _lookup(m.group(1), runtime)        # 整值替换：返回对象（dict/list/标量）
    return _VAR.sub(lambda mm: str(_lookup(mm.group(1), runtime)), s)


def resolve(value: Any, ctx: Any) -> Any:
    """递归渲染 ${...}。dict/list 递归；字符串按整值或内嵌替换。"""
    if isinstance(value, str):
        return _resolve_str(value, _as_runtime(ctx))
    if isinstance(value, dict):
        return {k: resolve(v, ctx) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(resolve(v, ctx) for v in value)
    return value
