# -*- coding: utf-8 -*-
"""测试上下文：本业务可见变量（extract）+ 跨业务全局变量（extract_global，进程级）。"""


class ApiContext:
    """一次业务运行的上下文；extract 仅本业务可见，extract_global 写入模块级 _GLOBAL（同进程跨 case 共享）。"""

    _GLOBAL: dict = {}

    def __init__(self, biz_title: str = "", base_dir=None, apis_dir=None) -> None:
        self.biz_title = biz_title
        self.base_dir = base_dir
        self.apis_dir = apis_dir
        self.data: dict = {}

    def set(self, key: str, value) -> None:
        self.data[key] = value

    def set_global(self, key: str, value) -> None:
        type(self)._GLOBAL[key] = value

    def get(self, key: str, default=None):
        if key in self.data:
            return self.data[key]
        return type(self)._GLOBAL.get(key, default)

    def runtime(self) -> dict:
        """运行时变量：全局在前、实例在后（实例覆盖全局）。供模板解析使用。"""
        merged = dict(type(self)._GLOBAL)
        merged.update(self.data)
        return merged

    @classmethod
    def reset_global(cls) -> None:
        cls._GLOBAL = {}
