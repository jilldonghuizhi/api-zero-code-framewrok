# -*- coding: utf-8 -*-
"""邮件通知：测试完成后把 Allure 报告摘要（或报告 zip）通过 SMTP 发出。

配置从 config/config.yaml 的 `mail:` 节读取，环境变量 MAIL_* 可覆盖任意子键：
  smtp_host / smtp_port / smtp_user / smtp_password / sender / receivers
  use_ssl / use_tls / subject_prefix / attach_report
开关由 pytest.ini 的 enable_email_report 控制（环境变量 ENABLE_EMAIL_REPORT 可覆盖）。
"""
import html
import io
import logging
import smtplib
import ssl
import zipfile
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional

from common.config_reader import get_config

log = logging.getLogger("engine.reporting.mailer")

_TD = "border:1px solid #d0d7de;padding:6px 10px;font-size:13px;"
_LBL = _TD + "background:#f6f8fa;color:#57606a;"
_STATUS_COLOR = {"passed": "#1a7f37", "failed": "#cf222e",
                 "broken": "#bf8700", "skipped": "#6e7781"}


def _td(text: Any, style: str = _TD) -> str:
    return f"<td style='{style}'>{html.escape(str(text))}</td>"


def _td_raw(html_content: str, style: str = _TD) -> str:
    return f"<td style='{style}'>{html_content}</td>"


def _status_cell(status: str) -> str:
    color = _STATUS_COLOR.get(status, "#57606a")
    return (f"<td style='{_TD}color:{color};font-weight:bold;'>"
            f"{html.escape(status.upper())}</td>")


def _load_mail_config() -> Dict[str, Any]:
    return get_config("config/config.yaml").get_section("mail") or {}


def _build_html(summary: Dict[str, Any], report_url: str) -> str:
    env_info = summary.get("env", {}) or {}
    env = str(env_info.get("env", ""))
    base_url = str(env_info.get("base_url", ""))
    total = int(summary.get("total", 0) or 0)
    passed = int(summary.get("passed", 0) or 0)
    failed = int(summary.get("failed", 0) or 0)
    broken = int(summary.get("broken", 0) or 0)
    skipped = int(summary.get("skipped", 0) or 0)
    duration = float(summary.get("duration", 0) or 0)
    rate = f"{passed / total * 100:.1f}%" if total else "-"
    cases: List[Dict[str, Any]] = summary.get("cases", []) or []

    # 信息区：测试时长 / 被测服务 / 环境 / Allure 报告链接
    dur_str = f"{duration:.2f}s"
    link = f"<a href='{html.escape(report_url, quote=True)}'>{html.escape(report_url)}</a>"
    info = (
        "<table style='border-collapse:collapse;'>"
        f"<tr>{_td('测试时长', _LBL)}{_td(dur_str)}"
        f"{_td('被测服务', _LBL)}{_td(base_url)}</tr>"
        f"<tr>{_td('环境', _LBL)}{_td(env)}"
        f"{_td('Allure报告', _LBL)}<td style='{_TD}'>{link}</td></tr>"
        "</table>"
    )

    # 统计区：总数 / 通过 / 失败 / 异常 / 跳过 / 通过率
    stat_items = (
        ("用例总数", str(total), ""),
        ("通过", str(passed), "color:#1a7f37;font-weight:bold;"),
        ("失败", str(failed), "color:#cf222e;font-weight:bold;"),
        ("异常", str(broken), "color:#bf8700;font-weight:bold;"),
        ("跳过", str(skipped), ""),
        ("通过率", rate, "font-weight:bold;"),
    )
    stats = "<table style='border-collapse:collapse;'><tr>" + "".join(
        f"{_td(name, _LBL)}<td style='{_TD}{style}'>{val}</td>"
        for name, val, style in stat_items
    ) + "</tr></table>"

    # 用例明细：全部用例（名称/状态/耗时/错误信息，无截图列）
    def _row(c: Dict[str, Any]) -> str:
        dur = f"{float(c.get('duration', 0) or 0):.2f}s"
        err = html.escape(str(c.get("error_detail") or c.get("error") or "")).replace("\n", "<br>")
        return (f"<tr>{_td(c.get('name', ''))}"
                f"{_status_cell(str(c.get('status', '')))}"
                f"{_td(dur)}{_td_raw(err)}</tr>")

    rows = "".join(_row(c) for c in cases)
    detail = (
        "<h3 style='margin:16px 0 8px;'>用例明细</h3>"
        "<table style='border-collapse:collapse;'>"
        f"<tr>{_td('用例名称', _LBL)}{_td('状态', _LBL)}"
        f"{_td('耗时', _LBL)}{_td('错误信息', _LBL)}</tr>"
        f"{rows}</table>"
    )

    return (
        "<html><body style=\"font-family:'Microsoft YaHei',Arial,sans-serif;color:#24292f;\">"
        f"<h2>API 自动化测试报告</h2>{info}"
        f"<div style='height:12px;'></div>{stats}{detail}"
        "</body></html>"
    )


def _zip_report(report_dir: str) -> Optional[bytes]:
    """把报告目录打成 zip（内存中），便于作为邮件附件。"""
    rd = Path(report_dir)
    if not rd.is_dir():
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in rd.rglob("*"):
            if p.is_file():
                zf.write(p, str(p.relative_to(rd)))
    return buf.getvalue()


def send_report_email(summary: Optional[Dict[str, Any]] = None,
                      report_url: str = "",
                      report_dir: str = "reports/allure-report",
                      extra: Optional[Dict[str, Any]] = None) -> bool:
    """发送测试报告邮件；缺 SMTP 配置或失败均返回 False 且不抛异常（不阻断主流程）。"""
    cfg = _load_mail_config()
    smtp_host = str(cfg.get("smtp_host") or "").strip()
    receivers = cfg.get("receivers") or []
    if isinstance(receivers, str):
        receivers = [r.strip() for r in receivers.split(",") if r.strip()]
    sender = str(cfg.get("sender") or "").strip()
    if not smtp_host or not receivers:
        log.warning("邮件未发送：缺少 smtp_host 或 receivers 配置（config.yaml 的 mail: 节）")
        return False
    if not sender:
        sender = receivers[0]

    smtp_port = int(cfg.get("smtp_port") or 465)
    smtp_user = str(cfg.get("smtp_user") or "").strip()
    smtp_password = str(cfg.get("smtp_password") or "").strip()
    use_ssl = bool(cfg.get("use_ssl", True))
    use_tls = bool(cfg.get("use_tls", False))
    subject_prefix = str(cfg.get("subject_prefix") or "[API 自动化]").strip()
    attach_report = bool(cfg.get("attach_report", True))

    summary = summary or {}
    env_info = summary.get("env", {}) or {}
    env = env_info.get("env", "")
    failed = summary.get("failed", 0)
    broken = summary.get("broken", 0)
    passed = summary.get("passed", 0)
    total = summary.get("total", 0)
    subject = f"{subject_prefix} 环境 {env}：通过{passed}/共{total}，失败{failed} 异常{broken}"

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = ", ".join(receivers)
    msg["Subject"] = subject
    msg.attach(MIMEText(_build_html(summary, report_url), "html", "utf-8"))

    if attach_report:
        data = _zip_report(report_dir)
        if data:
            part = MIMEApplication(data, Name="allure-report.zip")
            part["Content-Disposition"] = 'attachment; filename="allure-report.zip"'
            msg.attach(part)

    mode = "SSL" if use_ssl else ("STARTTLS" if use_tls else "plain")
    log.info("正在连接 SMTP %s:%s（模式=%s）发送邮件...", smtp_host, smtp_port, mode)
    try:
        ctx = ssl.create_default_context()
        if use_ssl:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30, context=ctx) as s:
                if smtp_user:
                    s.login(smtp_user, smtp_password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as s:
                if use_tls:
                    s.starttls(context=ctx)
                if smtp_user:
                    s.login(smtp_user, smtp_password)
                s.send_message(msg)
        log.info("测试报告邮件已发送至 %s", ", ".join(receivers))
        return True
    except Exception as e:
        log.warning("邮件发送失败（%s:%s 模式=%s）：%s", smtp_host, smtp_port, mode, e)
        return False
