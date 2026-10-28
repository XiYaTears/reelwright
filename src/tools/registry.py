"""工具注册表：业务工具调度、控制动作 schema、失败注入与调用计数。

只做调度与错误归一化，业务计算留在 tools/materials|duration|rules 的纯函数里。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.dataset import Dataset
from src.tools import duration, materials, rules
from src.tools.errors import ToolArgumentError

BUSINESS_TOOLS: tuple[str, ...] = ("search_materials", "estimate_duration", "check_rules")
CONTROL_TOOLS: tuple[str, ...] = ("submit_script", "ask_user", "finish")
INJECTION_ERRORS: dict[str, bool] = {
    "timeout": True,
    "unavailable": True,
    "invalid_response": False,
}

SUBMIT_SCRIPT_SPEC: dict[str, Any] = {
    "name": "submit_script",
    "description": "提交或更新脚本草稿；同一次运行可多次提交，仅最新稿参与最终判定。",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 60},
            "shots": {
                "type": "array",
                "minItems": 1,
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "properties": {
                        "visual": {"type": "string", "minLength": 1, "maxLength": 200},
                        "narration": {"type": "string", "maxLength": 120},
                        "caption": {"type": "string", "maxLength": 60},
                        "citations": {
                            "type": "array",
                            "maxItems": 4,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "material_id": {"type": "string"},
                                    "fact_id": {"type": "string"},
                                    "quote": {"type": "string"},
                                },
                                "required": ["material_id", "fact_id", "quote"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["visual", "narration", "caption", "citations"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["title", "shots"],
        "additionalProperties": False,
    },
}

ASK_USER_SPEC: dict[str, Any] = {
    "name": "ask_user",
    "description": "当关键信息不足或检索持续无结果时，向用户请求补充信息；整批执行后结束为 needs_input。",
    "parameters": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "minLength": 1, "maxLength": 300},
            "missing_fields": {
                "type": "array",
                "maxItems": 10,
                "items": {"type": "string"},
            },
            "reason": {"type": "string", "maxLength": 200},
        },
        "required": ["question", "missing_fields"],
        "additionalProperties": False,
    },
}

FINISH_SPEC: dict[str, Any] = {
    "name": "finish",
    "description": "声明收尾意图；最终状态由编排层对最新提交稿独立复算决定。",
    "parameters": {
        "type": "object",
        "properties": {
            "outcome": {"type": "string", "enum": ["completed_intent", "cannot_satisfy"]},
            "message": {"type": "string", "maxLength": 200},
        },
        "required": ["outcome"],
        "additionalProperties": False,
    },
}

TOOL_SPECS: tuple[dict[str, Any], ...] = (
    materials.TOOL_SPEC,
    duration.TOOL_SPEC,
    rules.TOOL_SPEC,
    SUBMIT_SCRIPT_SPEC,
    ASK_USER_SPEC,
    FINISH_SPEC,
)


class ToolExecutionError(RuntimeError):
    """业务工具执行失败（模型可见；injected 标记是否来自失败注入）。"""

    def __init__(self, code: str, message: str, *, retryable: bool, injected: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.injected = injected

    def to_observation(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
            },
        }


@dataclass(frozen=True, slots=True)
class InjectionRule:
    """一条失败注入规则；call 为 1 起的调用序号，None 表示每次调用都失败。"""

    tool: str
    error: str
    call: int | None = None


def parse_injection_specs(raw_specs: Sequence[Mapping[str, Any]]) -> tuple[InjectionRule, ...]:
    """解析并校验注入配置（JSON 输入/CLI 共用）；非法配置抛 ValueError（CLI 用法错误）。"""
    parsed: list[InjectionRule] = []
    seen_tools: set[str] = set()
    for index, raw in enumerate(raw_specs):
        where = f"failure_injection[{index}]"
        if not isinstance(raw, Mapping):
            raise ValueError(f"{where} 必须是对象")
        tool = raw.get("tool")
        if tool not in BUSINESS_TOOLS:
            raise ValueError(f"{where}.tool 必须是业务工具之一：{', '.join(BUSINESS_TOOLS)}")
        if tool in seen_tools:
            raise ValueError(f"{where}.tool 重复：{tool}")
        seen_tools.add(tool)
        error = raw.get("error")
        if error not in INJECTION_ERRORS:
            raise ValueError(f"{where}.error 必须是 {', '.join(sorted(INJECTION_ERRORS))} 之一")
        call = raw.get("call")
        if call is not None and (
            not isinstance(call, int) or isinstance(call, bool) or call < 1
        ):
            raise ValueError(f"{where}.call 必须是 ≥1 的整数或省略")
        parsed.append(InjectionRule(tool=tool, error=error, call=call))
    return tuple(parsed)


class ToolRegistry:
    """每次运行一个实例：绑定数据集/目标时长/平台，维护注入与调用计数。"""

    def __init__(
        self,
        dataset: Dataset,
        *,
        target_duration_s: int,
        platform: str,
        injections: Sequence[InjectionRule] = (),
    ) -> None:
        self.dataset = dataset
        self.target_duration_s = target_duration_s
        self.platform = platform
        self._injections: dict[str, InjectionRule] = {}
        for rule in injections:
            if rule.tool in self._injections:
                raise ValueError(f"工具注入规则重复：{rule.tool}")
            self._injections[rule.tool] = rule
        self._call_counts: dict[str, int] = {name: 0 for name in BUSINESS_TOOLS}

    @property
    def call_counts(self) -> dict[str, int]:
        return dict(self._call_counts)

    def execute(self, name: str, args: Mapping[str, Any]) -> dict[str, Any]:
        if name not in BUSINESS_TOOLS:
            raise ToolExecutionError(
                "unknown_tool", f"未知工具：{name}", retryable=False
            )
        call_index = self._call_counts[name] + 1
        self._call_counts[name] = call_index
        rule = self._injections.get(name)
        if rule is not None and (rule.call is None or rule.call == call_index):
            raise ToolExecutionError(
                rule.error,
                f"注入故障：{name} 第 {call_index} 次调用失败（{rule.error}）",
                retryable=INJECTION_ERRORS[rule.error],
                injected=True,
            )
        try:
            if name == "search_materials":
                return materials.handle(args, dataset=self.dataset)
            if name == "estimate_duration":
                return duration.handle(args, target_duration_s=self.target_duration_s)
            return rules.handle(args, dataset=self.dataset, platform=self.platform)
        except ToolArgumentError as exc:
            raise ToolExecutionError(
                "invalid_arguments", str(exc), retryable=False
            ) from None
        except ToolExecutionError:
            raise
        except Exception as exc:  # 工具内部异常归一化为模型可见错误，不吞掉
            raise ToolExecutionError(
                "internal", f"工具内部错误：{exc}", retryable=False
            ) from exc
