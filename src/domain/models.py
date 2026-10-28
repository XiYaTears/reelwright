"""领域数据模型。

包含数据集实体（素材/事实/规则）、脚本/引用/估算/规则结果与运行输入；全部只读（frozen），
由 dataset.py（实体）与 domain/script.py（脚本）统一校验与构建。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class Fact:
    """素材中的一条最小可引用事实。"""

    fact_id: str
    text: str
    kind: str


@dataclass(frozen=True, slots=True)
class Material:
    """一条合成产品素材。"""

    material_id: str
    product: str
    title: str
    tags: tuple[str, ...]
    facts: tuple[Fact, ...]
    source: str


@dataclass(frozen=True, slots=True)
class RuleTerm:
    """禁用词规则项。"""

    rule_id: str
    term: str
    severity: str
    reason: str
    suggestion: str


@dataclass(frozen=True, slots=True)
class PatternRule:
    """已编译正则规则项。"""

    rule_id: str
    pattern: re.Pattern[str]
    severity: str
    reason: str
    suggestion: str


@dataclass(frozen=True, slots=True)
class ClaimPattern:
    """需要事实支撑的表述模式（百分比/参数/资质等）。"""

    rule_id: str
    pattern: re.Pattern[str]
    label: str


@dataclass(frozen=True, slots=True)
class BrandRules:
    """品牌规则集合。"""

    brand_name: str
    banned_terms: tuple[RuleTerm, ...]
    exaggeration_patterns: tuple[PatternRule, ...]
    claim_patterns: tuple[ClaimPattern, ...]


@dataclass(frozen=True, slots=True)
class PlatformRule:
    """单个平台的合成规范。"""

    platform: str
    display_name: str
    max_total_duration_s: int
    max_duration_rule_id: str
    banned_terms: tuple[RuleTerm, ...]


@dataclass(frozen=True, slots=True)
class PlatformRules:
    """平台规则集合。"""

    platforms: Mapping[str, PlatformRule]


@dataclass(frozen=True, slots=True)
class Requirements:
    """一次脚本生成请求的输入。"""

    topic: str
    audience: str
    platform: str
    target_duration_s: int
    style: str
    extra_notes: str | None = None


@dataclass(frozen=True, slots=True)
class Citation:
    """素材引用：素材 ID + 事实 ID + 原文片段。"""

    material_id: str
    fact_id: str
    quote: str

    def to_dict(self) -> dict[str, str]:
        return {"material_id": self.material_id, "fact_id": self.fact_id, "quote": self.quote}


@dataclass(frozen=True, slots=True)
class Shot:
    """一个分镜。时长由估算工具计算，不在提交稿中出现。"""

    visual: str
    narration: str
    caption: str
    citations: tuple[Citation, ...] = ()


@dataclass(frozen=True, slots=True)
class Script:
    """模型提交的脚本草稿。"""

    title: str
    shots: tuple[Shot, ...]


@dataclass(frozen=True, slots=True)
class Window:
    """目标时长的允许区间（整数秒）。"""

    min_s: int
    max_s: int

    def to_dict(self) -> dict[str, int]:
        return {"min_s": self.min_s, "max_s": self.max_s}


@dataclass(frozen=True, slots=True)
class ShotDuration:
    """单镜确定性时长分解。"""

    index: int
    narration_chars: int
    caption_chars: int
    narration_s: int
    caption_s: int
    duration_s: int

    def to_dict(self) -> dict[str, int]:
        return {
            "index": self.index,
            "narration_chars": self.narration_chars,
            "caption_chars": self.caption_chars,
            "narration_s": self.narration_s,
            "caption_s": self.caption_s,
            "duration_s": self.duration_s,
        }


@dataclass(frozen=True, slots=True)
class EstimateResult:
    """时长估算结果。"""

    per_shot: tuple[ShotDuration, ...]
    total_duration_s: int
    target_duration_s: int
    window: Window
    within: bool
    verdict: str
    over_by_s: int
    under_by_s: int
    formula_version: str

    def to_dict(self) -> dict:
        return {
            "ok": True,
            "per_shot": [item.to_dict() for item in self.per_shot],
            "total_duration_s": self.total_duration_s,
            "target_duration_s": self.target_duration_s,
            "window": self.window.to_dict(),
            "within": self.within,
            "verdict": self.verdict,
            "over_by_s": self.over_by_s,
            "under_by_s": self.under_by_s,
            "formula_version": self.formula_version,
        }


@dataclass(frozen=True, slots=True)
class RuleViolation:
    """规则/事实支撑检查命中项。"""

    rule_id: str
    category: str
    severity: str
    shot_index: int | None
    field: str | None
    matched_text: str
    message: str
    suggestion: str

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "category": self.category,
            "severity": self.severity,
            "shot_index": self.shot_index,
            "field": self.field,
            "matched_text": self.matched_text,
            "message": self.message,
            "suggestion": self.suggestion,
        }


@dataclass(frozen=True, slots=True)
class RuleStats:
    """规则检查统计。"""

    shots_checked: int
    rules_checked: int
    errors: int
    warnings: int
    truncated: bool

    def to_dict(self) -> dict:
        return {
            "shots_checked": self.shots_checked,
            "rules_checked": self.rules_checked,
            "errors": self.errors,
            "warnings": self.warnings,
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True)
class RuleReport:
    """规则检查结果。"""

    ok: bool
    platform: str
    violations: tuple[RuleViolation, ...]
    stats: RuleStats

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "platform": self.platform,
            "violations": [violation.to_dict() for violation in self.violations],
            "stats": self.stats.to_dict(),
        }
