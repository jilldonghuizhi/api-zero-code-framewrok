# 零代码 API 自动化框架

以「**单个 API 一个 YAML 文件**」为可复用单元，业务文件用 `steps` 引用多个单 API 文件组合成链路。
**新增一个 YAML 文件即新增一条用例，无需编写任何 Python 代码。**

## 一、能力一览

| 能力 | 说明 |
| --- | --- |
| 零代码用例 | `testcases/apis/`（单 API 组件）+ `testcases/flows/`（业务编排）放 `.yaml` 即被自动收集 |
| 复用三层 | ① 一份 `apis/` 文件被多个 `flows/` 引用；② `flows/` 可嵌套引用另一份 `flows/`；③ `with` 覆盖本次调用的 `request`/`validate`/`extract` |
| 多类型软断言 | `equals`/比较/包含/集合/长度/类型/正则/存在性/schema/状态码；一个 API 内全部执行后汇总 |
| 链路硬停止 | 业务内任一 API 失败 → 本 case 失败，后续 API 不再执行 |
| 失败留证 | 请求/响应快照写入 `artifacts/` 并挂载到 Allure（含内联「失败详情」） |
| 失败重试 | 交由 `pytest-rerunfailures`（`pytest.ini` 的 `--reruns 1 --reruns-delay 1`），Allure 生成 Retries 视图 |
| Allure 报告 | 会话结束自动 `allure generate`（复用 `history` 带趋势）并可 detached 打开 |
| 邮件通知 | 按开关发送 HTML 摘要邮件（含失败现场），默认打包报告 zip；缺配置仅告警不阻断 |
| 多环境 | `--env dev/prod` 或 `TEST_ENV` 选择 `data/<env>.yaml`，零改动切换 |
| CI/CD | `scripts/run_tests.py` 统一入口 + GitHub Actions / GitLab CI 示例 |

## 二、目录结构

```
./
├── engine/                     零代码核心（通用，不随业务变化）
│   ├── executor.py             build_step_spec / _run_single_api / _flatten_steps / run_flow
│   ├── collector.py            pytest 收集插件：一个 business flow = 一个 case
│   └── reporting/              report.py（汇总/留证）/ mailer.py（邮件）/ report_generator.py（Allure）
├── common/                     核心层
│   ├── config_reader.py        ConfigReader / get_config（含环境变量覆盖、进程内缓存）
│   ├── request_client.py       ApiClient / ApiResponse / ApiExecError（唯一发请求处，鉴权单点）
│   ├── assert_utils.py         多类型断言 + 软断言汇总
│   ├── data_loader.py          YAML 用例加载 + data/ 常量（share ⊕ env 深合并）
│   ├── context.py              ApiContext（extract 本业务 / extract_global 跨业务）
│   └── paths.py                项目路径集中管理
├── utils/                      纯函数：dicts / extract(JSONPath) / templating(${}) / redact(脱敏)
├── testcases/
│   ├── apis/                   单 API 组件（不直接收集，被 flows 引用）
│   └── flows/                  业务编排（被收集为用例）
├── config/config.yaml          base_url / env / 请求 / 报告 / 邮件
├── data/                       share.yaml（公共常量）+ dev.yaml / prod.yaml（环境专属）
├── scripts/                    runner.py（透传 pytest）/ run_tests.py（CI 入口）
├── conftest.py                 会话钩子：常量注入、报告收尾、--env 选项
└── pytest.ini                  日志与功能开关
```

## 三、快速开始

```bash
pip install -r requirements.txt

# 1) 配置被测服务（必填，否则运行直接报错，不允许空跑）
#    编辑 config/config.yaml 的 base_url，或用环境变量：BASE_URL=https://api.xxx.com

# 2) 运行
pytest                                  # 串行
python scripts/runner.py -n auto        # 并行（同一业务步骤落在同一 worker）
python scripts/run_tests.py --env dev   # CI 入口（清理结果 → 跑测 → 报告）

# 3) 收集检查（不执行）
pytest --collect-only
```

Allure 报告需要 **Java(JRE/JDK 8+) + allure-commandline**：

```bash
npm install -g allure-commandline        # 或 scoop/choco/brew/apt 安装，或设 ALLURE_BIN
```

生成结束后自动打开 http://127.0.0.1:4040/ ；报告 HTML 在 `reports/allure-report`，机器可读摘要在 `reports/report.json`。

## 四、如何添加测试用例（零代码）

### 步骤 1：定义单 API（`testcases/apis/your_api.yaml`）

```yaml
name: 示例接口
request:
  method: "${methods.POST}"        # 引用 data/share.yaml 的常量，勿硬编码
  url: /demo/path                  # 相对路径，自动拼接 config.yaml 的 base_url
  json: { key: value }             # 收 JSON 用 json:；收表单用 form:
extract_global:                    # 跨业务共享，下游用 ${shared_value} 取
  shared_value: [json, $.data.shared_value, ""]
validate:                          # 多类型软断言
  - status_code: { name: HTTP200, expected: "${expect.http_ok}" }
  - equals:
      name: 业务码为预期值
      actual: $.code
      expected: "${expect.success_code}"
```

### 步骤 2：编排业务（`testcases/flows/your_flow.yaml`）——一个文件即一条用例

```yaml
feature: 示例模块
story: 示例业务
title: 示例业务链路
steps:
  - include: apis/your_api.yaml
  - include: apis/your_api.yaml          # 复用同一份 API
    name: 示例接口-无效ID
    with:
      json: { id: 1 }                    # 覆盖请求体
      validate:                          # 覆盖断言，衍生正向/反向用例
        - not_equals: { name: 拒绝无效ID, actual: $.code, expected: 0 }
  # - include: flows/子业务.yaml          # 嵌套业务，递归展开
```

要点：

- `include` 可指向 `apis/` 单 API，也可指向另一份 `flows/` 业务（嵌套展开，被复用文件加 `component: true` 即不独立收集）。
- `with` 深度合并：可覆盖 `request`（`json`/`params`/`headers`）以及 `validate`/`extract`/`extract_global`。
- 同业务内上游 `extract`/`extract_global` 对下游直接可见；跨业务用 `extract_global` + `${read_yaml(name)}`。
- 数据与结构分离：能用常量替代的值都放 `data/`，用例用 `${...}` 引用；`${...}` 出现在行内 `{}` 映射里**必须加引号**。
- 敏感值（账号密码/token）只放 `data/<env>.yaml`，不要放 `share.yaml`。

## 五、断言类型

`validate` 为列表，每项唯一键为断言类型：

```yaml
validate:
  - equals:       { name: 业务码为0, actual: $.code, expected: 0 }      # 数值宽松："200" == 200
  - status_code:  { name: HTTP200, expected: 200 }                     # actual 隐含为状态码
  - greater_than: { name: 余额为正, actual: $.data.balance, expected: 0 }
  - not_equals / less_than / greater_or_equal / less_or_equal
  - contains:     { name: 含前缀, actual: $.data.order_id, expected: "ORD" }
  - not_contains / in / not_in
  - length:       { name: 列表长度, actual: $.data.items, expected: 3 }
  - min_length / max_length
  - type:         { name: 是字符串, actual: $.data.order_id, expected: str }
  - regex:        { name: 手机号, actual: $.data.phone, pattern: "^1[3-9]\\d{9}$" }
  - exists:       { name: 返回用户ID, actual: $.data.user_id }
  - not_exists:   { name: 无错误码, actual: $.data.err_code }
  - schema:       { name: 结构合法, actual: $, schema: { type: object, required: [code, data] } }
```

`actual` 取值：`status_code` / `$.x`（响应 JSONPath）/ `${read_yaml(name)}`、`${name}`（提取变量）/ `${env.X}`、`${context.X}` / 字面量。

失败汇总示例：

```
❌ 业务「示例业务链路」· API「示例接口」断言失败：共 2 项未通过
  1. [equals · 业务码为0] 期望 == 0 | 实际 1  （取值: 响应 $.code）
  2. [greater_than · 余额为正] 期望 > 0 | 实际 -5  （取值: 响应 $.data.balance）
```

## 六、配置、环境与开关

`config/config.yaml`：

```yaml
base_url: ""      # 必填：被测服务地址（可用环境变量 BASE_URL 覆盖）
env: dev          # 对应 data/<env>.yaml
timeout: 30
retries: 1        # 连接错误/超时重试；HTTP 4xx/5xx 不重试
token: ""         # 全局 Token（已有 Authorization 头时不覆盖）
report: { allure_dir: allure-results, report_dir: reports/allure-report, json_path: reports/report.json }
mail:   { smtp_host: "", receivers: [], ... }
```

功能开关（`pytest.ini`，环境变量可覆盖）：

| 开关 | 默认 | 环境变量 |
| --- | --- | --- |
| `enable_allure_report` | true | `ENABLE_ALLURE_REPORT` |
| `auto_open_report` | true | `AUTO_OPEN_REPORT` |
| `enable_email_report` | false | `ENABLE_EMAIL_REPORT` |

环境优先级：`--env` > `TEST_ENV` > `config.yaml` 的 `env` > `dev`。
邮件密码用环境变量 `MAIL_SMTP_PASSWORD` 注入，勿提交明文。

## 七、CI/CD

```bash
python scripts/run_tests.py --env dev                     # 默认生成报告、不自动打开、输出 junit
python scripts/run_tests.py --env prod --email on         # 强制发邮件（需 MAIL_SMTP_PASSWORD）
python scripts/run_tests.py --env dev -k login -n auto    # 透传 pytest 参数
```

产物：`allure-results/`、`reports/allure-report/`、`reports/junit.xml`（均已被 `.gitignore` 排除）。
流水线示例见 `.github/workflows/api-tests.yml` 与 `.gitlab-ci.yml`。

## 八、常见坑

1. **请求体类型**：`json:` → JSON；`form:`/`data:` → 表单；`files:` → multipart。用错常导致「HTTP 200 + 业务码 500 未知异常」。
2. **业务码常是字符串**：`equals` 已做数值宽容；预期码统一走 `${expect.success_code}`。
3. **404/非 JSON**：路径或方法写错时响应为 HTML，框架会提示「响应非 JSON（HTTP xxx）」，便于定位。
4. **Token 形态**：`Bearer <token>` / 裸 token / 放在参数或 body，以能跑通的那次请求为准，1:1 复刻到 `headers`/`params`/`form`。
5. **并行**：`--dist loadscope` 已固化，保证同一业务的步骤在同一 worker；`extract_global` 为进程级，跨 worker 不互通。
