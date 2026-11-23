"""Single-request adapter for a compatible Chat Completions API."""
from __future__ import annotations

import json
import logging
import os
from http.client import HTTPException
from time import monotonic
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..contracts.models import ErrorInfo
from ..contracts.services import ServiceError

logger = logging.getLogger(__name__)
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def generation_error(
    code: str, message: str, *, retryable: bool = False, **details: object,
) -> ServiceError:
    return ServiceError(ErrorInfo(
        code=code, message=message, retryable=retryable, details=details,
    ))


class ChapterWriter(Protocol):
    def complete(self, instructions: str, context: str) -> str: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ChatCompletionsClient:
    """A caller owns budget reservations, retry decisions and persistence."""

    def __init__(self, base_url: str, model: str, api_key: str, timeout: float = 60):
        try:
            url = urlsplit(base_url.strip())
            valid_url = (
                url.scheme in ("http", "https")
                and url.hostname
                and url.username is None
                and url.password is None
                and not url.query
                and not url.fragment
            )
            url.port  # Force validation of an explicitly configured port.
        except ValueError:
            valid_url = False
        if not valid_url or not model.strip() or not api_key.strip():
            raise generation_error("MODEL_CONFIGURATION_ERROR", "模型服务配置不完整或无效。")

        path = url.path.rstrip("/")
        if not path.endswith("/chat/completions"):
            path += "/chat/completions"
        self._endpoint = urlunsplit(url._replace(path=path))
        self.model = model.strip()
        self._api_key = api_key.strip()
        self._timeout = timeout
        self._http = build_opener(_NoRedirect())

    @classmethod
    def from_env(cls) -> ChatCompletionsClient:
        return cls(
            os.getenv("MODEL_BASE_URL", ""),
            os.getenv("MODEL_NAME", ""),
            os.getenv("MODEL_API_KEY", ""),
        )

    def complete(self, instructions: str, context: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": context},
            ],
            "response_format": {"type": "json_object"},
        }
        request = Request(
            self._endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        started = monotonic()
        try:
            with self._http.open(request, timeout=self._timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            status = exc.code
            exc.close()
            if status == 429:
                raise generation_error(
                    "MODEL_RATE_LIMIT", "模型服务暂时限流。", retryable=True, status=status,
                ) from None
            if status in (408, 504):
                raise generation_error(
                    "MODEL_TIMEOUT", "模型服务请求超时。", retryable=True, status=status,
                ) from None
            raise generation_error(
                "MODEL_HTTP_ERROR", "模型服务请求失败。",
                retryable=500 <= status < 600, status=status,
            ) from None
        except TimeoutError:
            raise generation_error("MODEL_TIMEOUT", "模型服务请求超时。", retryable=True) from None
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise generation_error("MODEL_TIMEOUT", "模型服务请求超时。", retryable=True) from None
            raise generation_error("MODEL_UNAVAILABLE", "无法连接模型服务。", retryable=True) from None
        except (OSError, HTTPException):
            raise generation_error("MODEL_UNAVAILABLE", "无法读取模型服务响应。", retryable=True) from None

        if len(body) > MAX_RESPONSE_BYTES:
            raise generation_error(
                "INVALID_MODEL_OUTPUT", "模型响应超出章节大小限制。", retryable=True,
            )
        try:
            result = json.loads(body)
            if not isinstance(result, dict):
                raise ValueError
        except ValueError:
            raise generation_error(
                "INVALID_MODEL_OUTPUT", "模型响应不是 JSON 对象。", retryable=True,
            ) from None
        self._log_usage(result.get("usage"), started)

        try:
            choice = result["choices"][0]
            message = choice["message"]
            content = message["content"]
            if choice.get("finish_reason") not in (None, "stop") or message.get("refusal"):
                raise ValueError
            if not isinstance(content, str) or not content.strip():
                raise ValueError
        except (ValueError, TypeError, KeyError, IndexError):
            raise generation_error(
                "INVALID_MODEL_OUTPUT", "模型响应未包含完整章节 JSON。", retryable=True,
            ) from None
        return content

    def _log_usage(self, usage: object, started: float) -> None:
        if not isinstance(usage, dict):
            usage = {}
        counts = [
            usage.get(field) if type(usage.get(field)) is int else None
            for field in ("prompt_tokens", "completion_tokens")
        ]
        logger.info(
            "模型调用 model=%s elapsed_ms=%d prompt_tokens=%s completion_tokens=%s",
            self.model, round((monotonic() - started) * 1000), *counts,
        )
