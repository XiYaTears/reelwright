"""运行状态与预算。"""

from __future__ import annotations

import pytest

from src.agent.state import (
    DEFAULT_MAX_STEPS,
    DEFAULT_MAX_TOOL_CALLS,
    Budgets,
    initial_state,
    make_budgets,
)


def test_default_budgets() -> None:
    budgets = make_budgets()
    assert budgets.max_steps == DEFAULT_MAX_STEPS == 8
    assert budgets.max_tool_calls == DEFAULT_MAX_TOOL_CALLS == 16
    assert budgets == Budgets(max_steps=8, max_tool_calls=16)


def test_custom_budgets_scale_tool_calls() -> None:
    budgets = make_budgets(3)
    assert budgets.max_steps == 3
    assert budgets.max_tool_calls == 6


@pytest.mark.parametrize("value", [0, -1, 21, True, 1.5, "8"])
def test_invalid_budgets_rejected(value) -> None:
    with pytest.raises(ValueError):
        make_budgets(value)


def test_initial_state_is_explicit_and_empty() -> None:
    state = initial_state()
    assert state["messages"] == []
    assert state["step"] == 0
    assert state["tool_calls_used"] == 0
    assert state["draft"] is None
    assert state["status"] is None
    assert state["tool_errors"] == []
    assert state["skipped_tool_calls"] is None
