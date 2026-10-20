# -*- coding: utf-8 -*-
"""pytest 启动器：透传参数给 pytest（并行/重试/报告等开关统一在 pytest.ini 配置）。
用法：python scripts/runner.py [pytest 参数]   例：python scripts/runner.py -k login
如需并行，可在 pytest.ini 的 addopts 加 -n auto，或运行时加 -n auto。
"""
import sys

import pytest


def main():
    return pytest.main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
