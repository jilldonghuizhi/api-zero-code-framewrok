# -*- coding: utf-8 -*-
"""字典深合并（executor 与 data_loader 共用）。"""


def deep_merge(base: dict, override: dict) -> dict:
    """返回 base 与 override 的深合并结果（不修改入参）；override 中同键覆盖 base，dict 递归合并，其余直接覆盖。"""
    result = dict(base)
    for k, v in (override or {}).items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result
