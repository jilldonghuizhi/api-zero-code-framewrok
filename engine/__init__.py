# -*- coding: utf-8 -*-
"""零代码执行核心：通用引擎（不随业务变化）。

- executor.py：build_step_spec / _run_single_api / _flatten_steps / run_flow
- collector.py：pytest 自动收集（一个 business flow = 一个 case）
- reporting/：报告与通知编排
"""
