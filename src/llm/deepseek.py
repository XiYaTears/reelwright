"""DeepSeek 真实客户端：httpx 直连 OpenAI 风格 /chat/completions。

- 原生 tool calling：解析全部 tool_calls，arguments 是 JSON 字符串；
- `reasoning_content` 不进入返回结构，不落任何日志；
- 超时/429/5xx 有限重试；finish_reason=length 视为截断错误，不静默接受；
- 密钥只用于 Authorization 头，出现在错误消息里的只有状态码/异常类名。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import httpx

from src.llm.base import LLMError, LLMResponse, ToolCall, Usage

DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_RETRIES = 2
RETRY_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def normalize_base_url(base_url: str) -> str:
    """统一口径：OpenAI 兼容根地址，缺 /v1 时自动补。"""
    base = base_url.strip().rstrip("/")
    if not base:
        raise ValueError("LLM_BASE_URL 不能为空")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return base


class DeepSeekClient:
    """统一 LLM 接口的真实实现。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_s: float = 60.0,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise ValueError("LLM_API_KEY 不能为空")
        if not model:
            raise ValueError("LLM_MODEL 不能为空")
        self.model_name = model
        self._api_key = api_key
        self._endpoint = f"{normalize_base_url(base_url)}/chat/completions"
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._max_retries = max(0, max_retries)
        self._sleep = sleep
        self._client = httpx.Client(timeout=timeout_s, transport=transport)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> DeepSeekClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model_name,
            "messages": list(messages),
            "tools": list(tools),
            "tool_choice": "auto",
            "stream": False,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        attempts = self._max_retries + 1
        last_error = ""
        for attempt in range(attempts):
            try:
                response = self._client.post(self._endpoint, headers=headers, json=body)
            except httpx.TransportError as exc:
                last_error = f"网络错误：{exc.__class__.__name__}"
                if attempt + 1 < attempts:
                    self._sleep(1.0 * (attempt + 1))
                    continue
                raise LLMError(last_error, transient=True) from None
            if response.status_code in RETRY_STATUS_CODES:
                last_error = f"HTTP {response.status_code}"
                if attempt + 1 < attempts:
                    self._sleep(1.0 * (attempt + 1))
                    continue
                raise LLMError(last_error, transient=True)
            if response.status_code >= 400:
                raise LLMError(
                    f"HTTP {response.status_code}：{response.text[:200]}",
                    transient=False,
                )
            return self._parse_response(response)
        raise LLMError(last_error or "模型调用失败", transient=True)

    def _parse_response(self, response: httpx.Response) -> LLMResponse:
        try:
            payload = response.json()
        except ValueError:
            raise LLMError("模型响应不是合法 JSON", transient=False) from None
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMError("模型响应缺少 choices", transient=False)
        first = choices[0]
        if not isinstance(first, dict):
            raise LLMError("模型响应 choices[0] 不是对象", transient=False)
        finish_reason = first.get("finish_reason")
        if finish_reason == "length":
            raise LLMError("模型响应被截断（finish_reason=length），max_tokens 可能不足", transient=False)

        message = first.get("message") or {}
        if not isinstance(message, dict):
            raise LLMError("模型响应 message 不是对象", transient=False)
        content = message.get("content") or ""
        if not isinstance(content, str):
            content = ""

        # 注意：message["reasoning_content"] 被刻意忽略，不进入返回结构。
        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise LLMError("模型响应 tool_calls 不是数组", transient=False)
        tool_calls: list[ToolCall] = []
        for index, raw in enumerate(raw_calls, start=1):
            if not isinstance(raw, dict):
                raise LLMError(f"tool_calls[{index}] 不是对象", transient=False)
            call_id = raw.get("id") or f"call-{index}"
            function = raw.get("function") or {}
            if not isinstance(function, dict):
                function = {}
            name = function.get("name")
            if not isinstance(name, str) or not name:
                tool_calls.append(
                    ToolCall(
                        id=str(call_id),
                        name="",
                        arguments={},
                        arguments_error="tool_call 缺少 function.name",
                    )
                )
                continue
            raw_arguments = function.get("arguments")
            arguments: dict[str, Any] = {}
            arguments_error: str | None = None
            if isinstance(raw_arguments, str):
                try:
                    parsed = json.loads(raw_arguments) if raw_arguments.strip() else {}
                except json.JSONDecodeError as exc:
                    arguments_error = f"arguments 不是合法 JSON：{exc.msg}"
                else:
                    if isinstance(parsed, dict):
                        arguments = parsed
                    else:
                        arguments_error = "arguments 不是 JSON 对象"
            elif raw_arguments is None:
                arguments_error = "tool_call 缺少 arguments"
            else:
                arguments_error = "arguments 不是字符串"
            tool_calls.append(
                ToolCall(
                    id=str(call_id),
                    name=name,
                    arguments=arguments,
                    arguments_error=arguments_error,
                )
            )

        usage_raw = payload.get("usage") or {}
        if not isinstance(usage_raw, dict):
            usage_raw = {}
        usage = Usage(
            prompt_tokens=_as_int(usage_raw.get("prompt_tokens")),
            completion_tokens=_as_int(usage_raw.get("completion_tokens")),
            total_tokens=_as_int(usage_raw.get("total_tokens")),
        )
        model = payload.get("model")
        return LLMResponse(
            content=content,
            tool_calls=tuple(tool_calls),
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
            usage=usage,
            model=model if isinstance(model, str) else self.model_name,
        )


def _as_int(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0
