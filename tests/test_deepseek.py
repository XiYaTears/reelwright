"""DeepSeek 客户端：HTTP 协议、tool_calls 解析、截断与重试。"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from src.llm.base import LLMError, LLMResponse
from src.llm.deepseek import DeepSeekClient, normalize_base_url, normalize_tools

MODEL = "deepseek-flash"


def _ok_payload() -> dict[str, Any]:
    return {
        "id": "resp-123",
        "model": MODEL,
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": "先检索素材",
                    "reasoning_content": "内部思考，不应被记录",
                    "tool_calls": [
                        {
                            "id": "call_a",
                            "type": "function",
                            "function": {
                                "name": "search_materials",
                                "arguments": json.dumps({"query": "字幕"}, ensure_ascii=False),
                            },
                        },
                        {
                            "id": "call_b",
                            "type": "function",
                            "function": {
                                "name": "estimate_duration",
                                "arguments": json.dumps({"shots": []}),
                            },
                        },
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _make_client(handler, **kwargs) -> DeepSeekClient:
    return DeepSeekClient(
        api_key="test-key",
        base_url="https://example.test/v1",
        model=MODEL,
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
        **kwargs,
    )


def test_normalize_base_url() -> None:
    assert normalize_base_url("https://api.deepseek.com/v1") == "https://api.deepseek.com/v1"
    assert normalize_base_url("https://api.deepseek.com/v1/") == "https://api.deepseek.com/v1"
    assert normalize_base_url("https://api.deepseek.com") == "https://api.deepseek.com/v1"
    assert normalize_base_url(" http://proxy:8080/ ") == "http://proxy:8080/v1"
    with pytest.raises(ValueError):
        normalize_base_url("   ")


def test_chat_parses_multiple_tool_calls_and_drops_reasoning() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json=_ok_payload())

    tools = [{"type": "function", "function": {"name": "search_materials"}}]
    client = _make_client(handler)
    response = client.chat(
        [{"role": "system", "content": "system"}, {"role": "user", "content": "主题"}],
        tools,
    )
    client.close()

    assert captured["url"].endswith("/v1/chat/completions")
    assert captured["headers"]["authorization"] == "Bearer test-key"
    body = captured["body"]
    assert body["model"] == MODEL
    assert body["tool_choice"] == "auto"
    assert body["stream"] is False
    assert body["temperature"] == 0.0
    assert body["max_tokens"] == 4096
    assert body["tools"] == tools

    assert isinstance(response, LLMResponse)
    assert response.content == "先检索素材"
    assert response.finish_reason == "tool_calls"
    assert [call.name for call in response.tool_calls] == ["search_materials", "estimate_duration"]
    assert [call.id for call in response.tool_calls] == ["call_a", "call_b"]
    assert response.tool_calls[0].arguments == {"query": "字幕"}
    assert response.usage.total_tokens == 15
    assert response.response_id == "resp-123"
    assert "内部思考，不应被记录" not in repr(response)
    assert not hasattr(response, "reasoning_content")


def test_base_url_without_v1_is_normalized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/v1/chat/completions")
        return httpx.Response(200, json=_ok_payload())

    client = DeepSeekClient(
        api_key="test-key",
        base_url="https://example.test",
        model=MODEL,
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )
    client.chat([{"role": "user", "content": "x"}], [])
    client.close()


def test_malformed_arguments_are_marked_not_executed() -> None:
    payload = _ok_payload()
    payload["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{bad json"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = _make_client(handler)
    response = client.chat([{"role": "user", "content": "x"}], [])
    client.close()
    call = response.tool_calls[0]
    assert call.name == "search_materials"
    assert call.arguments == {}
    assert call.arguments_error is not None
    assert "JSON" in call.arguments_error


def test_length_finish_reason_raises() -> None:
    payload = _ok_payload()
    payload["choices"][0]["finish_reason"] = "length"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = _make_client(handler)
    with pytest.raises(LLMError, match="截断"):
        client.chat([{"role": "user", "content": "x"}], [])
    client.close()


def test_retries_server_error_then_succeeds() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json=_ok_payload())

    client = _make_client(handler)
    response = client.chat([{"role": "user", "content": "x"}], [])
    client.close()
    assert attempts["count"] == 2
    assert response.tool_calls[0].name == "search_materials"


def test_client_error_is_not_retried_and_does_not_leak_key() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(401, text="unauthorized")

    client = _make_client(handler)
    with pytest.raises(LLMError) as error_info:
        client.chat([{"role": "user", "content": "x"}], [])
    client.close()
    assert attempts["count"] == 1
    assert "401" in str(error_info.value)
    assert "test-key" not in str(error_info.value)


def test_network_error_retries_then_raises_transient() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        raise httpx.ConnectError("connect failed", request=request)

    client = _make_client(handler, max_retries=1)
    with pytest.raises(LLMError) as error_info:
        client.chat([{"role": "user", "content": "x"}], [])
    client.close()
    assert attempts["count"] == 2
    assert error_info.value.transient is True
    assert "test-key" not in str(error_info.value)


def test_missing_function_name_marked() -> None:
    payload = _ok_payload()
    payload["choices"][0]["message"]["tool_calls"] = [
        {"id": "call_x", "type": "function", "function": {"arguments": "{}"}}
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = _make_client(handler)
    response = client.chat([{"role": "user", "content": "x"}], [])
    client.close()
    assert response.tool_calls[0].arguments_error is not None


def test_broken_no_proxy_env_does_not_break_client(monkeypatch) -> None:
    """回归：本机 NO_PROXY 含 [::1] 时，httpx 默认 trust_env 会因非法 URL 崩溃。"""
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,::1,[::1]")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_payload())

    client = _make_client(handler)
    response = client.chat([{"role": "user", "content": "x"}], [])
    client.close()
    assert response.tool_calls[0].name == "search_materials"


def test_normalize_tools_wraps_bare_specs_and_keeps_wrapped() -> None:
    bare = {"name": "search_materials", "description": "d", "parameters": {"type": "object"}}
    wrapped = {"type": "function", "function": {"name": "estimate_duration"}}
    assert normalize_tools([bare, wrapped]) == [
        {"type": "function", "function": bare},
        wrapped,
    ]


def test_chat_wraps_project_tool_specs_in_request_body() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["tools"] = json.loads(request.content.decode("utf-8"))["tools"]
        return httpx.Response(200, json=_ok_payload())

    spec = {"name": "search_materials", "description": "d", "parameters": {"type": "object"}}
    client = _make_client(handler)
    client.chat([{"role": "user", "content": "x"}], [spec])
    client.close()
    assert captured["tools"] == [{"type": "function", "function": spec}]


def test_explicit_proxy_option_constructs() -> None:
    client = DeepSeekClient(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model=MODEL,
        proxy="http://127.0.0.1:7897",
    )
    client.close()
