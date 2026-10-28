"""统一 LLM 接口：真实 DeepSeek 与离线 mock 共用。

messages 使用 OpenAI 风格 dict；工具调用保留原生 tool_calls 语义。
`reasoning_content` 不属于本接口的返回结构，客户端解析时直接丢弃。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True, slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ToolCall:
    """一次工具调用；arguments 解析失败时 arguments_error 非空，调用方不得执行。"""

    id: str
    name: str
    arguments: dict[str, Any]
    arguments_error: str | None = None


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str
    tool_calls: tuple[ToolCall, ...]
    finish_reason: str | None = None
    usage: Usage = field(default_factory=Usage)
    model: str | None = None


class LLMError(RuntimeError):
    """模型调用失败（网络/协议/配置）；transient 表示是否值得上层重试。"""

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.transient = transient


class LLMClient(Protocol):
    """统一模型接口。"""

    model_name: str

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> LLMResponse:
        ...
