# -*- coding: utf-8 -*-
"""请求会话：封装 requests.Session，负责 URL 拼接、默认头合并、Token 注入、重试与异常脱敏。

鉴权单点：全框架仅此处发请求、仅此处注入 Authorization。
"""
import time
from typing import Any, Dict, Optional

try:
    import requests
    from requests.exceptions import RequestException
except ImportError:  # pragma: no cover
    requests = None  # type: ignore
    RequestException = Exception  # type: ignore

import logging
from utils.redact import redact

log = logging.getLogger("api.client")


class ApiExecError(Exception):
    """请求级失败，触发业务链路硬停止。"""


class ApiResponse:
    """对单次 HTTP 响应的轻封装。"""

    def __init__(self, status_code: int, json_body: Any, text: str,
                 elapsed_ms: float, headers: Dict[str, str], request_path: str) -> None:
        self.status_code = status_code
        self.json = json_body
        self.text = text
        self.elapsed_ms = elapsed_ms
        self.headers = headers
        self.request_path = request_path


class ApiClient:
    """持有 requests.Session；对外仅此一处发请求（鉴权单点在此）。"""

    def __init__(self, base_url: str, timeout: int = 30, retries: int = 1,
                 headers: Optional[Dict[str, str]] = None, verify: bool = True,
                 token: Optional[str] = None, auth_prefix: str = "Bearer") -> None:
        if requests is None:  # pragma: no cover
            raise RuntimeError("requests 未安装，请执行 pip install -r requirements.txt")
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self.retries = max(0, int(retries))
        self.default_headers = dict(headers or {})
        self.verify = verify
        self.token = token
        self.auth_prefix = auth_prefix
        self._session = requests.Session()

    def set_token(self, token: str) -> None:
        """设置后续请求的 Token（已有 Authorization 头时不覆盖）。"""
        self.token = token

    def request(self, method: str, path: str, **kwargs) -> ApiResponse:
        url = path if str(path).startswith(("http://", "https://")) else f"{self.base_url}{path}"
        # 合并默认头 + 本次请求头（本次同名覆盖默认头）
        headers = dict(self.default_headers)
        headers.update({k: str(v) for k, v in (kwargs.pop("headers", None) or {}).items()})
        # 鉴权单点：仅在尚未设置 Authorization 时注入（裸 token 或 Bearer，按 auth_prefix）
        if self.token and not any(k.lower() == "authorization" for k in headers):
            raw = str(self.token)
            prefix = (self.auth_prefix or "").strip()
            headers["Authorization"] = raw if (not prefix or raw.lower().startswith(prefix.lower())) \
                else f"{prefix} {raw}"
        attempt = 0
        last_err: Optional[Exception] = None
        while attempt <= self.retries:
            try:
                resp = self._session.request(
                    method, url, headers=headers, timeout=self.timeout,
                    verify=self.verify, **kwargs)
                try:
                    body = resp.json()
                except ValueError:
                    body = None
                result = ApiResponse(
                    status_code=resp.status_code, json_body=body, text=resp.text,
                    elapsed_ms=round(resp.elapsed.total_seconds() * 1000, 2),
                    headers=dict(resp.headers), request_path=path)
                log.info("→ %s %s | HTTP %s | %sms", method, path, resp.status_code, result.elapsed_ms)
                return result
            except RequestException as e:
                last_err = e
                # 连接错误/超时重试；HTTP 4xx/5xx 不重试（已进入上面的分支）
                if attempt >= self.retries:
                    break
                attempt += 1
                time.sleep(0.5)
        # 脱敏后抛 ApiExecError（不泄露敏感头）
        safe_headers = redact(dict(headers))
        raise ApiExecError(
            f"{method} {path} 请求失败 ({type(last_err).__name__}: {last_err}) | headers={safe_headers}")

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "ApiClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
