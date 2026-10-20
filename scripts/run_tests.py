# -*- coding: utf-8 -*-
"""CI/CD 统一入口：清理历史结果 → 按环境跑 pytest →（可选）生成报告/发邮件。

设计目标：本地与 CI 通用；所有行为通过参数或环境变量控制，便于流水线拼装。
复用既有 pytest 开关（ENABLE_ALLURE_REPORT / AUTO_OPEN_REPORT / ENABLE_EMAIL_REPORT）
与环境选择（--env / TEST_ENV / config.yaml 的 env），不重复造轮子。

CI 用法示例：
  python scripts/run_tests.py --env dev
  python scripts/run_tests.py --env prod --email on        # 需注入 MAIL_SMTP_PASSWORD
  python scripts/run_tests.py --env dev -k login -n auto    # 透传 pytest 参数（含并行）
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLURE_RESULTS = ROOT / "allure-results"
REPORT_DIR = ROOT / "reports" / "allure-report"
JUNIT = ROOT / "reports" / "junit.xml"


def _clean() -> None:
    """清空上一次的结果与报告，保证 CI 产物干净、可复现。"""
    for d in (ALLURE_RESULTS, REPORT_DIR):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    ALLURE_RESULTS.mkdir(parents=True, exist_ok=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="API 自动化 CI/CD 入口", allow_abbrev=False)
    p.add_argument("--env", default=None, help="目标环境(dev/prod)，对应 data/<env>.yaml")
    p.add_argument("--open", dest="open_report", action="store_true",
                   help="生成报告后自动打开（CI 默认不打开，无浏览器）")
    p.add_argument("--no-open", dest="open_report", action="store_false")
    p.add_argument("--report/--no-report", dest="report", default=True,
                   help="是否生成 Allure HTML 报告")
    p.add_argument("--email", choices=["on", "off", "auto"], default="auto",
                   help="邮件通知：on 强制开 / off 强制关 / auto 跟随 ENABLE_EMAIL_REPORT")
    p.add_argument("--junit/--no-junit", dest="junit", default=True,
                   help="是否输出 JUnit XML（reports/junit.xml），便于 CI 解析结果")
    p.add_argument("--keep-results", action="store_true",
                   help="不清空 allure-results（默认清空，保证产物干净）")
    p.add_argument("pytest_args", nargs="*",
                   help="透传给 pytest 的额外参数，如 -k login -n auto")
    args = p.parse_args(argv)

    if not args.keep_results:
        _clean()

    # CI 无浏览器：默认关闭自动打开；显式 --open 才打开
    os.environ["AUTO_OPEN_REPORT"] = "true" if args.open_report else "false"
    os.environ["ENABLE_ALLURE_REPORT"] = "true" if args.report else "false"
    if args.email == "on":
        os.environ["ENABLE_EMAIL_REPORT"] = "true"
    elif args.email == "off":
        os.environ["ENABLE_EMAIL_REPORT"] = "false"
    # 环境：优先用 --env；同时写 TEST_ENV 供直接跑 pytest 时复用
    if args.env:
        os.environ["TEST_ENV"] = args.env

    cmd = [sys.executable, "-m", "pytest", "-c", "pytest.ini"]
    if args.junit:
        cmd += [f"--junitxml={JUNIT}"]
    if args.env:
        cmd += ["--env", args.env]
    cmd += args.pytest_args

    rc = subprocess.call(cmd, cwd=str(ROOT))
    print(f"\n[ci] exit={rc}  allure-results={ALLURE_RESULTS}  report={REPORT_DIR}"
          + (f"  junit={JUNIT}" if args.junit else ""))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
