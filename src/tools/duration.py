"""时长估算工具（确定性，版本 duration-v1）。

公式（写死、整数运算）：
    chars(s)    = 删除空白字符后的字数（标点计入）
    narration_s = ceil(chars(narration) / 4)
    caption_s   = ceil(chars(caption) / 5)
    shot_s      = max(2, narration_s, caption_s)
    total_s     = Σ shot_s
    window      = [ceil(0.9 × target), floor(1.1 × target)]
模型只能通过改文本/分镜数影响时长，不能自行填写时长。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from src.domain.models import EstimateResult, Shot, ShotDuration, Window
from src.domain.script import ScriptValidationError, parse_estimate_shots
from src.tools.errors import ToolArgumentError

NARRATION_DIVISOR = 4
CAPTION_DIVISOR = 5
MIN_SHOT_S = 2
MAX_SHOTS = 12
MIN_TARGET_S = 5
MAX_TARGET_S = 300
FORMULA_VERSION = "duration-v1"

_WHITESPACE_RE = re.compile(r"\s+")

TOOL_SPEC: dict[str, Any] = {
    "name": "estimate_duration",
    "description": "对分镜脚本做确定性时长估算，返回每镜时长、总时长、目标允许区间与是否达标。",
    "parameters": {
        "type": "object",
        "properties": {
            "shots": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_SHOTS,
                "items": {
                    "type": "object",
                    "properties": {
                        "narration": {"type": "string", "maxLength": 120},
                        "caption": {"type": "string", "maxLength": 60},
                    },
                    "required": ["narration", "caption"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["shots"],
        "additionalProperties": False,
    },
}


def count_chars(text: str) -> int:
    """删除空白字符后的字数（标点计入）。"""
    return len(_WHITESPACE_RE.sub("", text))


def _ceil_div(value: int, divisor: int) -> int:
    return (value + divisor - 1) // divisor


def duration_window(target_duration_s: int) -> Window:
    if not isinstance(target_duration_s, int) or isinstance(target_duration_s, bool):
        raise ValueError("target_duration_s 必须是整数")
    if not MIN_TARGET_S <= target_duration_s <= MAX_TARGET_S:
        raise ValueError(f"target_duration_s 必须在 {MIN_TARGET_S}–{MAX_TARGET_S} 之间")
    min_s = _ceil_div(9 * target_duration_s, 10)
    max_s = (11 * target_duration_s) // 10
    return Window(min_s=min_s, max_s=max_s)


def _shot_seconds(shot: Shot) -> tuple[int, int, int, int, int]:
    narration_chars = count_chars(shot.narration)
    caption_chars = count_chars(shot.caption)
    narration_s = _ceil_div(narration_chars, NARRATION_DIVISOR)
    caption_s = _ceil_div(caption_chars, CAPTION_DIVISOR)
    duration_s = max(MIN_SHOT_S, narration_s, caption_s)
    return narration_chars, caption_chars, narration_s, caption_s, duration_s


def estimate(shots: Sequence[Shot], target_duration_s: int) -> EstimateResult:
    """核心纯函数：逐镜与总时长、允许区间、verdict。"""
    if not 1 <= len(shots) <= MAX_SHOTS:
        raise ValueError(f"shots 数量必须在 1–{MAX_SHOTS} 之间")
    window = duration_window(target_duration_s)
    per_shot: list[ShotDuration] = []
    for index, shot in enumerate(shots, start=1):
        narration_chars, caption_chars, narration_s, caption_s, duration_s = _shot_seconds(shot)
        per_shot.append(
            ShotDuration(
                index=index,
                narration_chars=narration_chars,
                caption_chars=caption_chars,
                narration_s=narration_s,
                caption_s=caption_s,
                duration_s=duration_s,
            )
        )
    total_duration_s = sum(item.duration_s for item in per_shot)
    if total_duration_s > window.max_s:
        verdict = "too_long"
        over_by_s = total_duration_s - window.max_s
        under_by_s = 0
    elif total_duration_s < window.min_s:
        verdict = "too_short"
        over_by_s = 0
        under_by_s = window.min_s - total_duration_s
    else:
        verdict = "within"
        over_by_s = 0
        under_by_s = 0
    return EstimateResult(
        per_shot=tuple(per_shot),
        total_duration_s=total_duration_s,
        target_duration_s=target_duration_s,
        window=window,
        within=verdict == "within",
        verdict=verdict,
        over_by_s=over_by_s,
        under_by_s=under_by_s,
        formula_version=FORMULA_VERSION,
    )


def total_duration(shots: Sequence[Shot]) -> int:
    """只算总时长（规则检查的平台上限需要它）。"""
    return sum(_shot_seconds(shot)[4] for shot in shots)


def handle(args: Mapping[str, Any], *, target_duration_s: int) -> dict[str, Any]:
    """工具入口：解析模型参数 → 纯函数 → 返回 JSON 观察。"""
    try:
        shots = parse_estimate_shots(args, source="estimate_duration")
    except ScriptValidationError as exc:
        raise ToolArgumentError(str(exc)) from None
    return estimate(shots, target_duration_s).to_dict()
