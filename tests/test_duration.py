"""时长估算公式（duration-v1）。"""

from __future__ import annotations

import pytest

from src.domain.models import Shot
from src.tools.duration import (
    CAPTION_DIVISOR,
    FORMULA_VERSION,
    MIN_SHOT_S,
    NARRATION_DIVISOR,
    count_chars,
    duration_window,
    estimate,
    handle,
    total_duration,
)
from src.tools.errors import ToolArgumentError


def _shot(narration: str = "", caption: str = "") -> Shot:
    return Shot(visual="画面", narration=narration, caption=caption)


def _shot_with_narration_chars(chars: int) -> Shot:
    return _shot(narration="字" * chars)


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (5, (5, 5)),
        (10, (9, 11)),
        (30, (27, 33)),
        (60, (54, 66)),
        (90, (81, 99)),
        (300, (270, 330)),
    ],
)
def test_duration_window(target: int, expected: tuple[int, int]) -> None:
    window = duration_window(target)
    assert (window.min_s, window.max_s) == expected


@pytest.mark.parametrize("target", [0, 4, 301, True, "30"])
def test_invalid_target_rejected(target) -> None:
    with pytest.raises(ValueError):
        duration_window(target)


def test_count_chars_ignores_whitespace_but_counts_punctuation() -> None:
    assert count_chars("支持 字幕\n") == 4
    assert count_chars("你好，世界！") == 6
    assert NARRATION_DIVISOR == 4 and CAPTION_DIVISOR == 5 and MIN_SHOT_S == 2


def test_ceil_division_and_min_shot() -> None:
    result = estimate([_shot_with_narration_chars(25)], 30)
    assert result.per_shot[0].narration_s == 7
    assert result.per_shot[0].duration_s == 7
    # 1 字 → 1 秒，但单镜下限 2 秒
    assert estimate([_shot(narration="字")], 30).per_shot[0].duration_s == 2
    # 字幕更长时取字幕时长
    caption_shot = _shot(caption="字" * 12)
    assert estimate([caption_shot], 30).per_shot[0].caption_s == 3
    assert estimate([caption_shot], 30).per_shot[0].duration_s == 3


def test_reference_example_38_to_28() -> None:
    """AC-05 核心数值：6 镜 38 秒 too_long → 5 镜 28 秒 within。"""
    initial = [_shot_with_narration_chars(chars) for chars in (25, 26, 27, 22, 23, 18)]
    first = estimate(initial, 30)
    assert first.total_duration_s == 38
    assert [item.duration_s for item in first.per_shot] == [7, 7, 7, 6, 6, 5]
    assert first.verdict == "too_long"
    assert first.over_by_s == 5
    assert first.within is False

    compressed = [_shot_with_narration_chars(chars) for chars in (22, 23, 24, 18, 19)]
    second = estimate(compressed, 30)
    assert second.total_duration_s == 28
    assert second.verdict == "within"
    assert second.within is True
    assert (second.window.min_s, second.window.max_s) == (27, 33)


def test_too_short_verdict() -> None:
    result = estimate([_shot_with_narration_chars(8)], 30)
    assert result.total_duration_s == 2
    assert result.verdict == "too_short"
    assert result.under_by_s == 25


def test_target_60_with_same_38s_draft_is_too_short() -> None:
    shots = [_shot_with_narration_chars(chars) for chars in (25, 26, 27, 22, 23, 18)]
    result = estimate(shots, 60)
    assert result.total_duration_s == 38
    assert result.verdict == "too_short"
    assert (result.window.min_s, result.window.max_s) == (54, 66)


def test_result_is_self_consistent_and_integer() -> None:
    shots = [_shot_with_narration_chars(chars) for chars in (25, 26, 27, 22, 23, 18)]
    result = estimate(shots, 30)
    assert result.formula_version == FORMULA_VERSION
    assert result.total_duration_s == sum(item.duration_s for item in result.per_shot)
    for item, shot in zip(result.per_shot, shots):
        assert isinstance(item.duration_s, int)
        assert item.duration_s == max(
            MIN_SHOT_S,
            (count_chars(shot.narration) + NARRATION_DIVISOR - 1) // NARRATION_DIVISOR,
            (count_chars(shot.caption) + CAPTION_DIVISOR - 1) // CAPTION_DIVISOR,
        )
    assert total_duration(shots) == 38


def test_tool_handle_returns_json_observation() -> None:
    observation = handle(
        {"shots": [{"narration": "字" * 25, "caption": ""}, {"narration": "字" * 18, "caption": ""}]},
        target_duration_s=30,
    )
    assert observation["ok"] is True
    assert observation["total_duration_s"] == 12
    assert observation["verdict"] == "too_short"
    assert len(observation["per_shot"]) == 2


def test_tool_handle_rejects_bad_arguments() -> None:
    with pytest.raises(ToolArgumentError, match="shots"):
        handle({"shots": []}, target_duration_s=30)
    with pytest.raises(ToolArgumentError, match="narration"):
        handle({"shots": [{"narration": 1, "caption": ""}]}, target_duration_s=30)
