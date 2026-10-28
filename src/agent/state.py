"""LangGraph 运行状态与预算。

state 只放决策相关数据；trace/LLM/数据集/配置通过节点闭包注入，不进 state。
"""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

DEFAULT_MAX_STEPS = 8
MIN_MAX_STEPS = 1
MAX_MAX_STEPS = 20
DEFAULT_MAX_TOOL_CALLS = 2 * DEFAULT_MAX_STEPS


@dataclass(frozen=True, slots=True)
class Budgets:
    max_steps: int
    max_tool_calls: int

    @property
    def max_tool_calls_expected(self) -> int:
        return self.max_tool_calls


def make_budgets(max_steps: int = DEFAULT_MAX_STEPS) -> Budgets:
    if not isinstance(max_steps, int) or isinstance(max_steps, bool):
        raise ValueError("max_steps 必须是整数")
    if not MIN_MAX_STEPS <= max_steps <= MAX_MAX_STEPS:
        raise ValueError(f"max_steps 必须在 {MIN_MAX_STEPS}–{MAX_MAX_STEPS} 之间")
    return Budgets(max_steps=max_steps, max_tool_calls=2 * max_steps)


class RunState(TypedDict, total=False):
    """图状态。messages/tool_errors 用 operator.add 累积，其余字段按最后写入覆盖。"""

    messages: Annotated[list[dict[str, Any]], operator.add]
    step: int
    tool_calls_used: int
    draft: dict[str, Any] | None
    draft_revision: int
    draft_digest: str | None
    last_estimate: dict[str, Any] | None
    last_rule_report: dict[str, Any] | None
    estimate_digest: str | None
    rules_digest: str | None
    terminal: str | None
    finish_outcome: str | None
    pending_question: dict[str, Any] | None
    status: str | None
    failure: dict[str, Any] | None
    final_checks: dict[str, Any] | None
    tool_errors: Annotated[list[dict[str, Any]], operator.add]
    skipped_tool_calls: list[dict[str, Any]] | None


def initial_state() -> RunState:
    return RunState(
        messages=[],
        step=0,
        tool_calls_used=0,
        draft=None,
        draft_revision=0,
        draft_digest=None,
        last_estimate=None,
        last_rule_report=None,
        estimate_digest=None,
        rules_digest=None,
        terminal=None,
        finish_outcome=None,
        pending_question=None,
        status=None,
        failure=None,
        final_checks=None,
        tool_errors=[],
        skipped_tool_calls=None,
    )
