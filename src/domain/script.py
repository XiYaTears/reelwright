"""脚本草稿解析、校验与规范化。

模型提交/工具入参都经过这里校验；错误信息会回填给模型（invalid_arguments）或作为用法错误。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from src.domain.models import Citation, Script, Shot

MAX_TITLE_LEN = 60
MAX_SHOTS = 12
MAX_VISUAL_LEN = 200
MAX_NARRATION_LEN = 120
MAX_CAPTION_LEN = 60
MAX_CITATIONS = 4
MAX_MATERIAL_ID_LEN = 10
MAX_FACT_ID_LEN = 20
MAX_QUOTE_LEN = 120


class ScriptValidationError(ValueError):
    """脚本/参数结构不合法。"""


def _fail(source: str, message: str) -> None:
    raise ScriptValidationError(f"{source}: {message}")


def _require_text(
    value: Any,
    name: str,
    *,
    source: str,
    max_len: int,
    min_len: int = 0,
) -> str:
    if not isinstance(value, str):
        _fail(source, f"{name} 必须是字符串")
    if len(value) < min_len:
        _fail(source, f"{name} 长度至少 {min_len}")
    if len(value) > max_len:
        _fail(source, f"{name} 长度不能超过 {max_len}")
    return value


def parse_script(payload: Any, *, source: str = "script") -> Script:
    """解析完整脚本（提交稿 / 规则检查入参）。"""
    if not isinstance(payload, Mapping):
        _fail(source, "脚本必须是对象")
    title = _require_text(payload.get("title"), "title", source=source, max_len=MAX_TITLE_LEN, min_len=1)
    shots_raw = payload.get("shots")
    if not isinstance(shots_raw, list):
        _fail(source, "shots 必须是数组")
    if not 1 <= len(shots_raw) <= MAX_SHOTS:
        _fail(source, f"shots 数量必须在 1–{MAX_SHOTS} 之间")
    shots: list[Shot] = []
    for index, shot_raw in enumerate(shots_raw, start=1):
        where = f"{source}.shots[{index}]"
        if not isinstance(shot_raw, Mapping):
            _fail(where, "分镜必须是对象")
        visual = _require_text(
            shot_raw.get("visual"), "visual", source=where, max_len=MAX_VISUAL_LEN, min_len=1
        )
        narration = _require_text(
            shot_raw.get("narration"), "narration", source=where, max_len=MAX_NARRATION_LEN
        )
        caption = _require_text(
            shot_raw.get("caption"), "caption", source=where, max_len=MAX_CAPTION_LEN
        )
        if not narration.strip() and not caption.strip():
            _fail(where, "旁白与字幕不能同时为空")
        citations_raw = shot_raw.get("citations", [])
        if not isinstance(citations_raw, list):
            _fail(where, "citations 必须是数组")
        if len(citations_raw) > MAX_CITATIONS:
            _fail(where, f"citations 最多 {MAX_CITATIONS} 条")
        citations: list[Citation] = []
        for citation_index, citation_raw in enumerate(citations_raw):
            citation_where = f"{where}.citations[{citation_index}]"
            if not isinstance(citation_raw, Mapping):
                _fail(citation_where, "引用必须是对象")
            material_id = _require_text(
                citation_raw.get("material_id"),
                "material_id",
                source=citation_where,
                max_len=MAX_MATERIAL_ID_LEN,
                min_len=1,
            )
            fact_id = _require_text(
                citation_raw.get("fact_id"),
                "fact_id",
                source=citation_where,
                max_len=MAX_FACT_ID_LEN,
                min_len=1,
            )
            quote = _require_text(
                citation_raw.get("quote"), "quote", source=citation_where, max_len=MAX_QUOTE_LEN, min_len=1
            )
            if quote != quote.strip():
                _fail(citation_where, "quote 不能有首尾空白")
            citations.append(Citation(material_id=material_id, fact_id=fact_id, quote=quote))
        shots.append(
            Shot(visual=visual, narration=narration, caption=caption, citations=tuple(citations))
        )
    return Script(title=title, shots=tuple(shots))


def parse_estimate_shots(payload: Any, *, source: str = "estimate_duration") -> tuple[Shot, ...]:
    """解析时长估算入参：只需要 narration/caption，允许空文本（按最短 2 秒计）。"""
    if not isinstance(payload, Mapping):
        _fail(source, "参数必须是对象")
    shots_raw = payload.get("shots")
    if not isinstance(shots_raw, list):
        _fail(source, "shots 必须是数组")
    if not 1 <= len(shots_raw) <= MAX_SHOTS:
        _fail(source, f"shots 数量必须在 1–{MAX_SHOTS} 之间")
    shots: list[Shot] = []
    for index, shot_raw in enumerate(shots_raw, start=1):
        where = f"{source}.shots[{index}]"
        if not isinstance(shot_raw, Mapping):
            _fail(where, "分镜必须是对象")
        narration = _require_text(
            shot_raw.get("narration"), "narration", source=where, max_len=MAX_NARRATION_LEN
        )
        caption = _require_text(
            shot_raw.get("caption"), "caption", source=where, max_len=MAX_CAPTION_LEN
        )
        shots.append(Shot(visual="", narration=narration, caption=caption))
    return tuple(shots)


def script_to_dict(script: Script) -> dict[str, Any]:
    """规范化脚本 JSON（用于提交/输出/摘要）。"""
    return {
        "title": script.title,
        "shots": [
            {
                "visual": shot.visual,
                "narration": shot.narration,
                "caption": shot.caption,
                "citations": [citation.to_dict() for citation in shot.citations],
            }
            for shot in script.shots
        ],
    }


def script_digest(script: Script) -> str:
    """脚本规范化内容的 sha256 前 16 位，用于核对"检查的是不是提交稿"。"""
    canonical = json.dumps(script_to_dict(script), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
