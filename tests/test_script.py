"""脚本草稿解析与校验。"""

from __future__ import annotations

import pytest

from src.domain.script import (
    ScriptValidationError,
    parse_estimate_shots,
    parse_script,
    script_digest,
    script_to_dict,
)


def _valid_payload() -> dict:
    return {
        "title": "字幕功能上线",
        "shots": [
            {
                "visual": "手机竖屏画面：添加字幕",
                "narration": "支持字幕",
                "caption": "支持字幕",
                "citations": [{"material_id": "P01", "fact_id": "P01-F1", "quote": "支持字幕"}],
            }
        ],
    }


def test_parse_valid_script() -> None:
    script = parse_script(_valid_payload())
    assert script.title == "字幕功能上线"
    assert len(script.shots) == 1
    shot = script.shots[0]
    assert shot.narration == "支持字幕"
    assert shot.citations[0].fact_id == "P01-F1"
    assert script_to_dict(script) == _valid_payload()


def test_parse_script_allows_title_only_claims_but_requires_nonempty_shot() -> None:
    payload = _valid_payload()
    payload["shots"][0]["narration"] = ""
    payload["shots"][0]["caption"] = ""
    with pytest.raises(ScriptValidationError, match="旁白与字幕不能同时为空"):
        parse_script(payload)


def test_parse_estimate_shots_allows_empty_text() -> None:
    shots = parse_estimate_shots({"shots": [{"narration": "", "caption": ""}]})
    assert len(shots) == 1
    assert shots[0].narration == ""


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload.pop("title"), "title"),
        (lambda payload: payload.update(shots=[]), "shots 数量"),
        (lambda payload: payload["shots"][0].update(visual=""), "visual"),
        (lambda payload: payload["shots"][0].update(narration="字" * 121), "narration"),
        (lambda payload: payload["shots"][0].update(caption="字" * 61), "caption"),
        (lambda payload: payload["shots"][0].update(citations=[{}] * 5), "citations 最多"),
        (
            lambda payload: payload["shots"][0]["citations"][0].pop("quote"),
            "quote",
        ),
        (
            lambda payload: payload["shots"][0]["citations"][0].update(quote=" 支持字幕 "),
            "首尾空白",
        ),
    ],
)
def test_invalid_script_rejected(mutate, message: str) -> None:
    payload = _valid_payload()
    mutate(payload)
    with pytest.raises(ScriptValidationError, match=message):
        parse_script(payload)


def test_parse_script_rejects_non_object() -> None:
    with pytest.raises(ScriptValidationError, match="脚本必须是对象"):
        parse_script([])


def test_script_digest_stable_and_changes_with_content() -> None:
    first = parse_script(_valid_payload())
    second = parse_script(_valid_payload())
    assert script_digest(first) == script_digest(second)
    assert len(script_digest(first)) == 16

    changed_payload = _valid_payload()
    changed_payload["title"] = "字幕功能上线（修订）"
    changed = parse_script(changed_payload)
    assert script_digest(changed) != script_digest(first)
