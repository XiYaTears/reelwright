"""系统提示词与首条 user 消息。"""

from __future__ import annotations

import json

from src.agent.prompt import (
    DECISION_NOTE_LIMIT,
    build_initial_messages,
    build_system_prompt,
    build_user_message,
)
from src.agent.state import make_budgets
from src.domain.models import Requirements


def _requirements(**overrides) -> Requirements:
    base = {
        "topic": "字幕功能上线",
        "audience": "剪辑新手",
        "platform": "douyin",
        "target_duration_s": 30,
        "style": "轻快教程",
        "extra_notes": None,
    }
    base.update(overrides)
    return Requirements(**base)


def test_system_prompt_contains_key_constraints() -> None:
    prompt = build_system_prompt(_requirements(), make_budgets())
    assert "不得编造" in prompt
    assert "quote" in prompt and "逐字" in prompt
    assert "estimate_duration" in prompt and "check_rules" in prompt
    assert "ask_user" in prompt and "finish" in prompt
    assert "cannot_satisfy" in prompt
    assert "40 字" in prompt and "decision_note" in prompt
    assert "8 个模型步" in prompt and "16 次工具调用" in prompt


def test_user_message_is_parseable_json_with_requirements() -> None:
    requirements = _requirements()
    message = build_user_message(requirements)
    payload_text = message.split("\n", 1)[1]
    payload = json.loads(payload_text)
    assert payload == {
        "topic": "字幕功能上线",
        "audience": "剪辑新手",
        "platform": "douyin",
        "target_duration_s": 30,
        "style": "轻快教程",
        "extra_notes": None,
    }


def test_initial_messages_roles() -> None:
    messages = build_initial_messages(_requirements(), make_budgets())
    assert [message["role"] for message in messages] == ["system", "user"]
    assert all(isinstance(message["content"], str) and message["content"] for message in messages)
    assert DECISION_NOTE_LIMIT == 40
