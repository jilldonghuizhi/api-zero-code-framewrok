# -*- coding: utf-8 -*-
"""日志/证据/邮件中的敏感字段脱敏（统一实现，供 executor 与 request_client 复用）。"""
from typing import Any

# 忽略大小写与 _/- 后的敏感 key；命中则其值替换为 ***（安全侧偏严：含敏感子串即命中）
_SENSITIVE = {
    "authorization", "cookie", "token", "password", "passwd",
    "secret", "accesstoken", "refreshtoken",
}


def redact(obj: Any) -> Any:
    """递归脱敏：敏感 key 的值替换为 ***（dict/list 递归；标量原样返回）。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key = str(k).lower().replace("_", "").replace("-", "")
            out[k] = "***" if any(s in key for s in _SENSITIVE) else redact(v)
        return out
    if isinstance(obj, (list, tuple)):
        return type(obj)(redact(v) for v in obj)
    return obj
