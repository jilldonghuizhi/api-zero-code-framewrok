# -*- coding: utf-8 -*-
"""报告与通知编排：由 conftest 会话钩子驱动（sessionstart / sessionfinish）。

- report.py：汇总 allure-results → report.json；失败工件挂载；并行安全清理
- mailer.py：HTML 摘要邮件（含失败现场，SSL/STARTTLS），缺配置仅告警
- report_generator.py：allure CLI 生成 HTML 报告（含 history 趋势），缺失给安装指引
"""
