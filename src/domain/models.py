"""领域数据模型。

第一阶段只包含数据集实体（素材/事实/规则）；脚本、估算与运行输出模型在后续模块加入。
所有对象只读（frozen），由 dataset.py 统一校验与构建。
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
    banned_terms: tuple[RuleTerm, ...]


@dataclass(frozen=True, slots=True)
class PlatformRules:
    """平台规则集合。"""

    platforms: Mapping[str, PlatformRule]
