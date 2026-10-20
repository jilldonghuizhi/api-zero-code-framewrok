# -*- coding: utf-8 -*-
"""执行引擎：build_step_spec / _run_single_api / _flatten_steps / run_flow。

一个 business flow = 一个 case，逐步记日志、包 allure.step、失败留证并硬停止后续步骤。
失败现场（接口/请求/响应/错误）由 `_format_failure` 组装，同时用于终端日志与 Allure 附件。
"""
import logging
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import allure
except ImportError:  # pragma: no cover
    allure = None  # type: ignore

from common.assert_utils import assert_spec
from common.data_loader import load_case
from common.paths import ARTIFACTS_DIR, ROOT
from common.request_client import ApiClient, ApiExecError
from engine.reporting.report import attach_failure_artifact, attach_failure_text
from utils.dicts import deep_merge
from utils.extract import jsonpath_extract, parse_extract
from utils.redact import redact
from utils.templating import get_constants, resolve

log = logging.getLogger("engine.flow")

_METHODS = {
    "get": "GET", "post": "POST", "put": "PUT", "delete": "DELETE",
    "patch": "PATCH", "head": "HEAD", "options": "OPTIONS",
}


def normalize_method(method: Any) -> str:
    """method 经 ${...} 解析后规范化：'post' -> 'POST'。"""
    if not method:
        return "GET"
    m = str(method).strip().lower()
    return _METHODS.get(m, m.upper())


def _fmt_req(client, method: str, target: str, headers, params, body) -> str:
    """请求段（完整 URL + 脱敏头/参数/体）。"""
    full = f"{client.base_url}{target}" if client else target
    lines = [f"  请求: {method} {full}"]
    if headers:
        lines.append(f"    头: {redact(headers)}")
    if params:
        lines.append(f"    参数: {params}")
    if body is not None:
        lines.append(f"    体: {redact(body)}")
    return "\n".join(lines)


def _fmt_resp(resp) -> str:
    """响应段（状态码/耗时 + 脱敏头/体）。"""
    if resp is None:
        return "  响应: (无，请求未成功)"
    lines = [f"  响应: HTTP {resp.status_code} ({resp.elapsed_ms}ms)"]
    if getattr(resp, "headers", None):
        lines.append(f"    头: {redact(dict(resp.headers))}")
    body = resp.json if resp.json is not None else (resp.text or "")
    if body:
        lines.append(f"    体: {redact(body)}")
    return "\n".join(lines)


def _format_failure(api_name: str, client, method: str, target: str,
                    headers, params, body, resp, error: str) -> str:
    """组装可读失败现场（接口 + 请求 + 响应 + 错误），供日志与 Allure 共用。"""
    return (f"接口[{api_name}]\n"
            f"{_fmt_req(client, method, target, headers, params, body)}\n"
            f"{_fmt_resp(resp)}\n"
            f"  错误: {error}")


def _resolve_api_path(include: str, base_dir, apis_dir) -> Path:
    """解析 include 路径：业务目录 → 全局 apis/flows → 按文件名兜底。"""
    base = Path(base_dir)
    apis = Path(apis_dir)
    p = Path(str(include).strip().replace("\\", "/"))
    if p.is_absolute():
        return p
    parts = p.parts
    # 去掉 include 前缀 apis/ 或 flows/，便于落到统一目录
    rel = Path(*parts[1:]) if (parts and parts[0] in ("apis", "flows") and len(parts) > 1) else p
    candidates = [
        base / include,                       # 业务文件所在目录下的 apis/flows
        apis / rel,                           # 全局 testcases/apis
        base / rel,
        ROOT / "testcases" / include,         # 项目根相对（CWD 无关）
        ROOT / "testcases" / "flows" / rel,   # 嵌套业务
        apis / p.name,                        # 仅按文件名在 apis 下查找
        base.parent / include,
    ]
    for c in candidates:
        try:
            if c.is_file():
                return c.resolve()
        except OSError:
            continue
    raise FileNotFoundError(
        f"无法解析 include 路径: {include}（已尝试业务目录与 testcases/apis、testcases/flows）")


def build_step_spec(step: Dict[str, Any], base_dir, apis_dir) -> Dict[str, Any]:
    """构建单个 API step 的 spec：加载被引用文件 + 应用 with 覆盖。

    `with` 顶层键（json/form/params/headers/url/method...）直接并进 request，
    `validate`/`extract`/`extract_global` 单独覆盖。
    """
    if isinstance(step, str):
        step = {"include": step}
    path = _resolve_api_path(step["include"], base_dir, apis_dir)
    api = load_case(path)
    spec = dict(api)
    spec["__file__"] = str(path)
    if "name" in step:
        spec["name"] = step["name"]
    w = step.get("with") or {}
    if w:
        # 兼容两种写法：① `with: {url:..., form:..., headers:..., method:..., validate:...}` 顶层直平铺（参考实现风格）；
        # ② `with: {request: {url:...}, validate:...}` 把 request 子字典拆出平铺。
        req_override = dict(w)
        if isinstance(req_override.get("request"), dict):
            req_override.update(req_override.pop("request"))
        spec["request"] = deep_merge(api.get("request", {}) or {}, req_override)
        for k in ("validate", "extract", "extract_global"):
            if w.get(k) is not None:
                spec[k] = w[k]
    return spec


def _save_evidence(api_name: str, *, request=None, response=None, error=None,
                   detail: Optional[str] = None) -> Optional[Path]:
    """失败留证：快照写 artifacts/，并挂载 Allure（文件 + 内联可读文本）。"""
    import json as _json

    art_dir = Path(ARTIFACTS_DIR)
    art_dir.mkdir(parents=True, exist_ok=True)
    snap = {"api": api_name, "request": request, "response": response, "error": error}
    safe = "".join(c if c not in '\\/:*?"<>|' else "_" for c in str(api_name))[:60]
    path = art_dir / f"{safe}__{time.strftime('%H%M%S', time.localtime())}.json"
    try:
        path.write_text(_json.dumps(snap, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
        attach_failure_artifact(f"失败证据-{api_name}", str(path))
    except Exception:
        return None
    if detail:
        attach_failure_text(f"失败详情-{api_name}", detail)
    return path


def _step(name: str, record: bool = True):
    """Allure 步骤包裹；record=False 时不写步骤（避免重试产生重复步骤）。"""
    if not allure:
        return nullcontext()
    return allure.step(name) if record else nullcontext()


def _build_headers(req: Dict[str, Any], ctx) -> Dict[str, Any]:
    """默认头单一来源：default_headers < feature.headers < request.headers。"""
    consts = get_constants() or {}
    merged = deep_merge(consts.get("default_headers", {}) or {}, consts.get("headers", {}) or {})
    return resolve(deep_merge(merged, req.get("headers", {}) or {}), ctx)


def _body_kwargs(headers: Dict[str, Any], json_body, form_body, files_body) -> Dict[str, Any]:
    """按最终 Content-Type 决定编码方式：json -> json=，x-www-form-urlencoded -> data=。"""
    ct = str(headers.get("Content-Type", "")).lower()
    kwargs: Dict[str, Any] = {}
    if "json" in ct:
        payload = json_body if json_body is not None else form_body
        if payload is not None:
            kwargs["json"] = payload
    elif "x-www-form-urlencoded" in ct or "form" in ct:
        payload = form_body if form_body is not None else json_body
        if payload is not None:
            kwargs["data"] = payload
    else:
        if json_body is not None:
            kwargs["json"] = json_body
        elif form_body is not None:
            kwargs["data"] = form_body
    if files_body is not None:
        kwargs["files"] = files_body
    return kwargs


def _run_single_api(client: ApiClient, api_spec: Dict[str, Any], ctx,
                    record: bool = True) -> None:
    req = api_spec.get("request", {}) or {}
    method = normalize_method(resolve(req.get("method"), ctx))
    target = resolve(req.get("url") or req.get("path") or "", ctx)
    headers = _build_headers(req, ctx)
    params = resolve(req.get("params") or req.get("query") or {}, ctx)
    json_body = resolve(req.get("json"), ctx) or None                        # application/json
    form_body = resolve(req.get("form") or req.get("data"), ctx) or None     # x-www-form-urlencoded
    files_body = resolve(req.get("files"), ctx) or None                      # multipart/form-data
    body_payload = json_body if json_body is not None else form_body
    api_name = api_spec.get("name", target)
    body_kwargs = _body_kwargs(headers, json_body, form_body, files_body)

    with _step(f"{method} {target} [{api_name}]", record):
        try:
            resp = client.request(method, target, params=params, headers=headers, **body_kwargs)
        except Exception as e:
            req_snap = {"method": method, "url": f"{client.base_url}{target}",
                        "headers": redact(headers), "params": params, "body": redact(body_payload)}
            detail = _format_failure(api_name, client, method, target, headers, params,
                                     body_payload, None, f"{type(e).__name__}: {e}")
            err = ApiExecError(f"请求失败：{method} {target} - {type(e).__name__}: {e}")
            setattr(err, "details", detail)     # 供 run_flow 直接输出完整现场
            _save_evidence(api_name, request=req_snap,
                           error=f"{type(e).__name__}: {e}", detail=detail)
            raise err from e

        # extract（仅本业务可见）
        for key, val in (api_spec.get("extract") or {}).items():
            mode, jp, default = parse_extract(val)
            ctx.set(key, jsonpath_extract(resp.json, jp, default) if mode == "json"
                    else resp.headers.get(jp, default))
        # extract_global（跨业务全局共享）
        for key, val in (api_spec.get("extract_global") or {}).items():
            mode, jp, default = parse_extract(val)
            v = jsonpath_extract(resp.json, jp, default) if mode == "json" \
                else resp.headers.get(jp, default)
            ctx.set_global(key, v)
            ctx.set(key, v)

        try:
            assert_spec(resp, api_spec.get("validate") or [], ctx,
                        api_name=api_name, biz_title=ctx.biz_title)
        except AssertionError as e:
            resp_snap = {"status": resp.status_code, "elapsed_ms": resp.elapsed_ms,
                         "headers": redact(dict(resp.headers)),
                         "json": redact(resp.json), "text": (resp.text or "")[:2000]}
            req_snap = {"method": method, "url": f"{client.base_url}{target}",
                        "headers": redact(headers), "params": params, "body": redact(body_payload)}
            detail = _format_failure(api_name, client, method, target, headers, params,
                                     body_payload, resp, str(e))
            setattr(e, "details", detail)
            _save_evidence(api_name, request=req_snap, response=resp_snap,
                           error=str(e), detail=detail)
            raise


def _flatten_steps(case: Dict[str, Any], base_dir, apis_dir) -> List[Dict[str, Any]]:
    """把 steps（含嵌套 flow include）展开为有序单 API spec 列表——一个 flow 即一个 case 的全部步骤。"""
    out: List[Dict[str, Any]] = []
    for step in (case.get("steps") or []):
        if isinstance(step, str):
            step = {"include": step}
        path = _resolve_api_path(step["include"], base_dir, apis_dir)
        included = load_case(path)
        if included.get("steps"):                     # 嵌套业务：递归展开并入本 case
            out.extend(_flatten_steps(included, path.parent, apis_dir))
        else:
            out.append(build_step_spec(step, base_dir, apis_dir))
    return out


def run_flow(client: ApiClient, case: Dict[str, Any], ctx, log=None,
             record: bool = True) -> None:
    """顺序执行该 flow 展开后的全部步骤；任一步骤失败 → 本 case 失败、后续步骤不再执行。"""
    logger = log or globals()["log"]
    steps = _flatten_steps(case, ctx.base_dir, ctx.apis_dir)
    if not steps:
        raise ValueError(f"业务 {case.get('title') or case.get('name')} 缺少 steps")
    total = len(steps)
    for i, spec in enumerate(steps, 1):
        req = spec.get("request", {}) or {}
        method = normalize_method(resolve(req.get("method"), ctx))
        target = resolve(req.get("url") or req.get("path") or "", ctx)
        api_name = spec.get("name", target)
        if logger:
            logger.info("步骤 %d/%d · %s %s [%s]", i, total, method, target, api_name)
        try:
            _run_single_api(client, spec, ctx, record=record)
        except (AssertionError, ApiExecError) as e:
            if logger:
                logger.error("步骤 %d/%d 失败，硬停止后续步骤 [%s]\n%s",
                             i, total, api_name, getattr(e, "details", None) or str(e))
                for nxt in steps[i:]:
                    logger.warning("⏭️  API「%s」已跳过：上游 API『%s』执行失败，已停止执行本业务后续 API",
                                   nxt.get("name", "?"), api_name)
            raise
        if logger:
            logger.info("步骤 %d/%d 通过 [%s]", i, total, api_name)
