"""规则检查工具：品牌禁用词、平台规范、夸大表述、事实支撑与引用校验。

规则来自 data/brand_rules.json 与 data/platform_rules.json（合成数据）；
本模块只做确定性扫描，不调用模型、不写文件。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.dataset import Dataset
from src.domain.models import RuleReport, RuleStats, RuleViolation, Script, Shot
from src.domain.script import ScriptValidationError, parse_script
from src.tools.duration import total_duration
from src.tools.errors import ToolArgumentError

MAX_VIOLATIONS = 50

CITE_MATERIAL_NOT_FOUND = "CITE-101"
CITE_FACT_NOT_FOUND = "CITE-102"
CITE_QUOTE_MISMATCH = "CITE-103"
STRUCT_EMPTY_SHOT = "STRUCT-001"
STRUCT_NO_CITATION = "STRUCT-002"

FIXED_CHECKS = 5  # 平台时长、素材存在、事实存在、quote 一致、结构检查

TOOL_SPEC: dict[str, Any] = {
    "name": "check_rules",
    "description": "检查脚本的品牌禁用词、平台规范、夸大表述与事实引用，返回可定位的违规项。",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "shots": {
                "type": "array",
                "minItems": 1,
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "properties": {
                        "visual": {"type": "string"},
                        "narration": {"type": "string"},
                        "caption": {"type": "string"},
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


def _supported(matched_text: str, fact_texts: list[str]) -> bool:
    return any(matched_text in fact_text for fact_text in fact_texts)


def _sort_key(violation: RuleViolation) -> tuple:
    has_shot = violation.shot_index is not None
    return (
        0 if not has_shot else 1,
        violation.shot_index or 0,
        violation.rule_id,
        violation.field or "",
        violation.matched_text,
    )


def check(
    script: Script,
    dataset: Dataset,
    platform: str,
    total_duration_s: int,
) -> RuleReport:
    """核心纯函数：返回可定位的违规项与统计。"""
    platform_rule = dataset.get_platform(platform)
    if platform_rule is None:
        raise ValueError(f"未知平台：{platform}")

    violations: list[RuleViolation] = []

    def add(
        rule_id: str,
        category: str,
        severity: str,
        shot_index: int | None,
        field: str | None,
        matched_text: str,
        message: str,
        suggestion: str,
    ) -> None:
        violations.append(
            RuleViolation(
                rule_id=rule_id,
                category=category,
                severity=severity,
                shot_index=shot_index,
                field=field,
                matched_text=matched_text,
                message=message,
                suggestion=suggestion,
            )
        )

    # 待扫描文本（title 用 None 表示整稿级位置；visual 不参与扫描）
    text_fields: list[tuple[int | None, str, str]] = [(None, "title", script.title)]
    for index, shot in enumerate(script.shots, start=1):
        text_fields.append((index, "narration", shot.narration))
        text_fields.append((index, "caption", shot.caption))
        if not shot.narration.strip() and not shot.caption.strip():
            add(
                STRUCT_EMPTY_SHOT,
                "structure",
                "warning",
                index,
                None,
                "",
                "分镜旁白与字幕均为空，按 2 秒最短时长计",
                "补充旁白或字幕",
            )

    # 品牌禁用词
    for term_rule in dataset.brand.banned_terms:
        for shot_index, field, text in text_fields:
            if term_rule.term in text:
                add(
                    term_rule.rule_id,
                    "brand",
                    term_rule.severity,
                    shot_index,
                    field,
                    term_rule.term,
                    f"品牌禁用词「{term_rule.term}」：{term_rule.reason}",
                    term_rule.suggestion,
                )

    # 平台禁用词
    for term_rule in platform_rule.banned_terms:
        for shot_index, field, text in text_fields:
            if term_rule.term in text:
                add(
                    term_rule.rule_id,
                    "platform",
                    term_rule.severity,
                    shot_index,
                    field,
                    term_rule.term,
                    f"平台禁用词「{term_rule.term}」：{term_rule.reason}",
                    term_rule.suggestion,
                )

    # 夸大表述
    for pattern_rule in dataset.brand.exaggeration_patterns:
        for shot_index, field, text in text_fields:
            for match in pattern_rule.pattern.finditer(text):
                matched_text = match.group(0)
                add(
                    pattern_rule.rule_id,
                    "exaggeration",
                    pattern_rule.severity,
                    shot_index,
                    field,
                    matched_text,
                    f"夸大表述「{matched_text}」：{pattern_rule.reason}",
                    pattern_rule.suggestion,
                )

    # 平台总时长上限（合成规则；rule_id 来自数据文件 max_duration_rule_id）
    if total_duration_s > platform_rule.max_total_duration_s:
        add(
            platform_rule.max_duration_rule_id,
            "platform",
            "error",
            None,
            None,
            str(total_duration_s),
            f"总时长 {total_duration_s}s 超过平台上限 {platform_rule.max_total_duration_s}s",
            "压缩旁白/字幕或减少分镜后重新估算",
        )

    # 引用三段校验 + 事实支撑素材池
    valid_fact_texts_by_shot: dict[int, list[str]] = {index: [] for index in range(1, len(script.shots) + 1)}
    all_valid_fact_texts: list[str] = []
    total_citations = 0
    for index, shot in enumerate(script.shots, start=1):
        for citation in shot.citations:
            total_citations += 1
            if dataset.get_material(citation.material_id) is None:
                add(
                    CITE_MATERIAL_NOT_FOUND,
                    "citation",
                    "error",
                    index,
                    None,
                    citation.material_id,
                    f"素材 {citation.material_id} 不存在",
                    "只能引用素材库中存在的素材 ID",
                )
                continue
            fact = dataset.get_fact(citation.material_id, citation.fact_id)
            if fact is None:
                add(
                    CITE_FACT_NOT_FOUND,
                    "citation",
                    "error",
                    index,
                    None,
                    citation.fact_id,
                    f"事实 {citation.fact_id} 不存在",
                    "只能引用素材中存在的 fact_id",
                )
                continue
            if citation.quote not in fact.text:
                add(
                    CITE_QUOTE_MISMATCH,
                    "citation",
                    "error",
                    index,
                    None,
                    citation.quote,
                    f"引用原文「{citation.quote}」不是素材事实原文的连续片段（原文：{fact.text}）",
                    "quote 必须与素材事实原文逐字一致",
                )
                continue
            valid_fact_texts_by_shot[index].append(fact.text)
            all_valid_fact_texts.append(fact.text)

    # 旁白/字幕中的数字、参数、资质类表述必须有引用事实支撑
    for index, shot in enumerate(script.shots, start=1):
        supported_texts = valid_fact_texts_by_shot[index]
        for field, text in (("narration", shot.narration), ("caption", shot.caption)):
            for claim_pattern in dataset.brand.claim_patterns:
                for match in claim_pattern.pattern.finditer(text):
                    matched_text = match.group(0).strip()
                    if _supported(matched_text, supported_texts):
                        continue
                    add(
                        claim_pattern.rule_id,
                        "citation",
                        "error",
                        index,
                        field,
                        matched_text,
                        f"{claim_pattern.label}「{matched_text}」没有素材事实支撑",
                        "删除该表述，或从素材库找到原文并添加引用",
                    )

    # 标题中的数字/资质类表述需要全稿任一有效引用支撑
    for claim_pattern in dataset.brand.claim_patterns:
        for match in claim_pattern.pattern.finditer(script.title):
            matched_text = match.group(0).strip()
            if _supported(matched_text, all_valid_fact_texts):
                continue
            add(
                claim_pattern.rule_id,
                "citation",
                "error",
                None,
                "title",
                matched_text,
                f"标题中的{claim_pattern.label}「{matched_text}」没有素材事实支撑",
                "删除该表述，或改用素材事实原文",
            )

    if total_citations == 0:
        add(
            STRUCT_NO_CITATION,
            "structure",
            "warning",
            None,
            None,
            "",
            "全稿没有任何素材引用，请确认内容未涉及产品事实",
            "如涉及产品事实请补充素材引用",
        )

    # 去重（同一规则/位置/命中只保留一次）
    deduped: dict[tuple, RuleViolation] = {}
    for violation in violations:
        key = (violation.rule_id, violation.shot_index, violation.field, violation.matched_text)
        deduped.setdefault(key, violation)
    ordered = sorted(deduped.values(), key=_sort_key)
    truncated = len(ordered) > MAX_VIOLATIONS
    kept = ordered[:MAX_VIOLATIONS]

    errors = sum(1 for violation in kept if violation.severity == "error")
    warnings = sum(1 for violation in kept if violation.severity == "warning")
    rules_checked = (
        len(dataset.brand.banned_terms)
        + len(platform_rule.banned_terms)
        + len(dataset.brand.exaggeration_patterns)
        + len(dataset.brand.claim_patterns)
        + FIXED_CHECKS
    )
    return RuleReport(
        ok=errors == 0,
        platform=platform,
        violations=tuple(kept),
        stats=RuleStats(
            shots_checked=len(script.shots),
            rules_checked=rules_checked,
            errors=errors,
            warnings=warnings,
            truncated=truncated,
        ),
    )


def handle(args: Mapping[str, Any], *, dataset: Dataset, platform: str) -> dict[str, Any]:
    """工具入口：解析模型参数 → 纯函数 → 返回 JSON 观察。"""
    try:
        script = parse_script(args, source="check_rules")
    except ScriptValidationError as exc:
        raise ToolArgumentError(str(exc)) from None
    total_duration_s = total_duration(script.shots)
    return check(script, dataset, platform, total_duration_s).to_dict()
