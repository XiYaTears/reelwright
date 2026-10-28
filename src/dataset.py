"""合成数据集加载与校验。

职责：读取并校验 data/ 下三个 JSON，构建只读 Dataset；计算 data_digest。
不做任何业务判定（时长/规则判定在 tools/ 中实现）。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from src.domain.models import (
    BrandRules,
    ClaimPattern,
    Fact,
    Material,
    PatternRule,
    PlatformRule,
    PlatformRules,
    RuleTerm,
)

DATA_FILES: tuple[str, ...] = ("materials.json", "brand_rules.json", "platform_rules.json")
FIXED_PLATFORMS: tuple[str, ...] = ("douyin", "kuaishou", "xiaohongshu", "shipinhao")
FACT_KINDS: frozenset[str] = frozenset({"feature", "parameter", "scene", "audience", "certification"})
SEVERITIES: frozenset[str] = frozenset({"error", "warning"})

MATERIAL_ID_RE = re.compile(r"^P\d{2}$")
FACT_ID_RE = re.compile(r"^P\d{2}-F\d+$")
RULE_ID_PATTERNS: dict[str, re.Pattern[str]] = {
    "brand": re.compile(r"^BRAND-\d{3}$"),
    "exaggeration": re.compile(r"^EXAG-\d{3}$"),
    "claim": re.compile(r"^CITE-\d{3}$"),
}


class DatasetError(ValueError):
    """数据集缺失、JSON 非法或字段校验失败。"""


@dataclass(frozen=True)
class Dataset:
    """只读数据集视图，附带素材/事实索引与 digest。"""

    materials: tuple[Material, ...]
    brand: BrandRules
    platforms: Mapping[str, PlatformRule]
    data_digest: str
    _material_index: Mapping[str, Material] = field(init=False, repr=False)
    _fact_index: Mapping[tuple[str, str], Fact] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_material_index",
            MappingProxyType({material.material_id: material for material in self.materials}),
        )
        object.__setattr__(
            self,
            "_fact_index",
            MappingProxyType(
                {
                    (material.material_id, fact.fact_id): fact
                    for material in self.materials
                    for fact in material.facts
                }
            ),
        )

    def get_material(self, material_id: str) -> Material | None:
        return self._material_index.get(material_id)

    def get_fact(self, material_id: str, fact_id: str) -> Fact | None:
        return self._fact_index.get((material_id, fact_id))

    def get_platform(self, platform: str) -> PlatformRule | None:
        return self.platforms.get(platform)

    def all_facts(self) -> tuple[Fact, ...]:
        return tuple(fact for material in self.materials for fact in material.facts)


def load_dataset(data_dir: str | Path) -> Dataset:
    """加载并校验数据集；任何结构问题都抛 DatasetError，不静默降级。"""
    root = Path(data_dir)
    raw_files = _read_files(root)
    digest = _compute_digest(raw_files)
    payloads = {name: _parse_json(name, data) for name, data in raw_files.items()}
    materials = _load_materials(payloads["materials.json"])
    brand = _load_brand_rules(payloads["brand_rules.json"])
    platforms = _load_platform_rules(payloads["platform_rules.json"])
    return Dataset(
        materials=materials,
        brand=brand,
        platforms=platforms,
        data_digest=digest,
    )


def _read_files(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for name in DATA_FILES:
        path = root / name
        try:
            files[name] = path.read_bytes()
        except FileNotFoundError:
            raise DatasetError(f"数据集缺少文件：{path}") from None
        except OSError as exc:
            raise DatasetError(f"无法读取数据文件 {path}：{exc}") from exc
    return files


def _compute_digest(raw_files: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in DATA_FILES:
        digest.update(name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(raw_files[name])
    return digest.hexdigest()[:12]


def _parse_json(name: str, raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise DatasetError(f"{name} 不是 UTF-8 编码：{exc}") from None
    except json.JSONDecodeError as exc:
        raise DatasetError(f"{name} 不是合法 JSON：{exc}") from None
    if not isinstance(payload, dict):
        raise DatasetError(f"{name} 顶层必须是对象")
    return payload


def _fail(where: str, message: str) -> None:
    raise DatasetError(f"{where}: {message}")


def _require_str(
    obj: Mapping[str, Any],
    key: str,
    where: str,
    *,
    max_len: int,
    pattern: re.Pattern[str] | None = None,
) -> str:
    value = obj.get(key)
    if not isinstance(value, str):
        _fail(where, f"字段 {key} 必须是字符串")
    if not value:
        _fail(where, f"字段 {key} 不能为空")
    if value != value.strip():
        _fail(where, f"字段 {key} 不能有首尾空白")
    if len(value) > max_len:
        _fail(where, f"字段 {key} 超过 {max_len} 字")
    if pattern is not None and not pattern.fullmatch(value):
        _fail(where, f"字段 {key} 格式不合法：{value}")
    return value


def _require_dict(obj: Mapping[str, Any], key: str, where: str) -> Mapping[str, Any]:
    value = obj.get(key)
    if not isinstance(value, dict):
        _fail(where, f"字段 {key} 必须是对象")
    return value


def _require_list(obj: Mapping[str, Any], key: str, where: str, *, min_len: int = 0) -> list[Any]:
    value = obj.get(key)
    if not isinstance(value, list):
        _fail(where, f"字段 {key} 必须是数组")
    if len(value) < min_len:
        _fail(where, f"字段 {key} 至少需要 {min_len} 项")
    return value


def _require_version(payload: Mapping[str, Any], expected: str, where: str) -> None:
    version = _require_str(payload, "version", where, max_len=40)
    if version != expected:
        _fail(where, f"version 必须是 {expected}，实际为 {version}")


def _load_materials(payload: Mapping[str, Any]) -> tuple[Material, ...]:
    _require_version(payload, "materials-v1", "materials.json")
    items = _require_list(payload, "materials", "materials.json", min_len=1)
    materials: list[Material] = []
    seen_materials: set[str] = set()
    seen_facts: set[str] = set()
    for index, item in enumerate(items):
        where = f"materials.json.materials[{index}]"
        if not isinstance(item, dict):
            _fail(where, "必须是对象")
        material_id = _require_str(item, "material_id", where, max_len=10, pattern=MATERIAL_ID_RE)
        if material_id in seen_materials:
            _fail(where, f"重复的素材 ID：{material_id}")
        seen_materials.add(material_id)
        product = _require_str(item, "product", where, max_len=40)
        title = _require_str(item, "title", where, max_len=60)
        source = _require_str(item, "source", where, max_len=100)
        tags_raw = _require_list(item, "tags", where, min_len=1)
        if len(tags_raw) > 6:
            _fail(where, "tags 最多 6 个")
        tags: list[str] = []
        for tag_index, tag in enumerate(tags_raw):
            if not isinstance(tag, str) or not tag or tag != tag.strip() or len(tag) > 20:
                _fail(where, f"tags[{tag_index}] 必须是 1–20 字的非空字符串")
            if tag in tags:
                _fail(where, f"tags 重复：{tag}")
            tags.append(tag)
        facts_raw = _require_list(item, "facts", where, min_len=1)
        if len(facts_raw) > 10:
            _fail(where, "facts 最多 10 条")
        facts: list[Fact] = []
        for fact_index, fact_raw in enumerate(facts_raw):
            fact_where = f"{where}.facts[{fact_index}]"
            if not isinstance(fact_raw, dict):
                _fail(fact_where, "必须是对象")
            fact_id = _require_str(fact_raw, "fact_id", fact_where, max_len=20, pattern=FACT_ID_RE)
            if fact_id in seen_facts:
                _fail(fact_where, f"重复的事实 ID：{fact_id}")
            seen_facts.add(fact_id)
            if not fact_id.startswith(f"{material_id}-"):
                _fail(fact_where, f"事实 ID 必须以 {material_id}- 开头：{fact_id}")
            text = _require_str(fact_raw, "text", fact_where, max_len=120)
            kind = _require_str(fact_raw, "kind", fact_where, max_len=20)
            if kind not in FACT_KINDS:
                _fail(fact_where, f"kind 必须是 {sorted(FACT_KINDS)} 之一，实际为 {kind}")
            facts.append(Fact(fact_id=fact_id, text=text, kind=kind))
        materials.append(
            Material(
                material_id=material_id,
                product=product,
                title=title,
                tags=tuple(tags),
                facts=tuple(facts),
                source=source,
            )
        )
    return tuple(materials)


def _load_brand_rules(payload: Mapping[str, Any]) -> BrandRules:
    _require_version(payload, "brand-rules-v1", "brand_rules.json")
    brand_name = _require_str(payload, "brand_name", "brand_rules.json", max_len=40)
    banned_terms = _load_banned_terms(
        payload, "brand_rules.json", rule_id_pattern=RULE_ID_PATTERNS["brand"]
    )
    exaggeration_patterns = _load_pattern_rules(
        payload, "brand_rules.json", "exaggeration_patterns", RULE_ID_PATTERNS["exaggeration"]
    )
    claim_patterns = _load_claim_patterns(payload, "brand_rules.json")
    return BrandRules(
        brand_name=brand_name,
        banned_terms=banned_terms,
        exaggeration_patterns=exaggeration_patterns,
        claim_patterns=claim_patterns,
    )


def _load_banned_terms(
    payload: Mapping[str, Any],
    where_file: str,
    *,
    rule_id_pattern: re.Pattern[str],
) -> tuple[RuleTerm, ...]:
    items = _require_list(payload, "banned_terms", where_file)
    terms: list[RuleTerm] = []
    seen_rule_ids: set[str] = set()
    for index, item in enumerate(items):
        where = f"{where_file}.banned_terms[{index}]"
        if not isinstance(item, dict):
            _fail(where, "必须是对象")
        rule_id = _require_str(item, "rule_id", where, max_len=30, pattern=rule_id_pattern)
        if rule_id in seen_rule_ids:
            _fail(where, f"重复的规则 ID：{rule_id}")
        seen_rule_ids.add(rule_id)
        term = _require_str(item, "term", where, max_len=30)
        severity = _require_str(item, "severity", where, max_len=10)
        if severity not in SEVERITIES:
            _fail(where, f"severity 必须是 {sorted(SEVERITIES)} 之一")
        reason = _require_str(item, "reason", where, max_len=100)
        suggestion = _require_str(item, "suggestion", where, max_len=100)
        terms.append(
            RuleTerm(rule_id=rule_id, term=term, severity=severity, reason=reason, suggestion=suggestion)
        )
    return tuple(terms)


def _compile_pattern(where: str, pattern_text: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern_text)
    except re.error as exc:
        raise DatasetError(f"{where}: 正则无法编译（{pattern_text}）：{exc}") from None


def _load_pattern_rules(
    payload: Mapping[str, Any],
    where_file: str,
    key: str,
    rule_id_pattern: re.Pattern[str],
) -> tuple[PatternRule, ...]:
    items = _require_list(payload, key, where_file)
    rules: list[PatternRule] = []
    seen_rule_ids: set[str] = set()
    for index, item in enumerate(items):
        where = f"{where_file}.{key}[{index}]"
        if not isinstance(item, dict):
            _fail(where, "必须是对象")
        rule_id = _require_str(item, "rule_id", where, max_len=30, pattern=rule_id_pattern)
        if rule_id in seen_rule_ids:
            _fail(where, f"重复的规则 ID：{rule_id}")
        seen_rule_ids.add(rule_id)
        pattern_text = _require_str(item, "pattern", where, max_len=200)
        severity = _require_str(item, "severity", where, max_len=10)
        if severity not in SEVERITIES:
            _fail(where, f"severity 必须是 {sorted(SEVERITIES)} 之一")
        reason = _require_str(item, "reason", where, max_len=100)
        suggestion = _require_str(item, "suggestion", where, max_len=100)
        rules.append(
            PatternRule(
                rule_id=rule_id,
                pattern=_compile_pattern(where, pattern_text),
                severity=severity,
                reason=reason,
                suggestion=suggestion,
            )
        )
    return tuple(rules)


def _load_claim_patterns(payload: Mapping[str, Any], where_file: str) -> tuple[ClaimPattern, ...]:
    items = _require_list(payload, "claim_patterns", where_file)
    rules: list[ClaimPattern] = []
    seen_rule_ids: set[str] = set()
    for index, item in enumerate(items):
        where = f"{where_file}.claim_patterns[{index}]"
        if not isinstance(item, dict):
            _fail(where, "必须是对象")
        rule_id = _require_str(item, "rule_id", where, max_len=30, pattern=RULE_ID_PATTERNS["claim"])
        if rule_id in seen_rule_ids:
            _fail(where, f"重复的规则 ID：{rule_id}")
        seen_rule_ids.add(rule_id)
        pattern_text = _require_str(item, "pattern", where, max_len=200)
        label = _require_str(item, "label", where, max_len=40)
        rules.append(
            ClaimPattern(
                rule_id=rule_id,
                pattern=_compile_pattern(where, pattern_text),
                label=label,
            )
        )
    return tuple(rules)


def _load_platform_rules(payload: Mapping[str, Any]) -> Mapping[str, PlatformRule]:
    _require_version(payload, "platform-rules-v1", "platform_rules.json")
    platforms_raw = _require_dict(payload, "platforms", "platform_rules.json")
    missing = [name for name in FIXED_PLATFORMS if name not in platforms_raw]
    if missing:
        _fail("platform_rules.json", f"缺少固定平台：{', '.join(missing)}")
    platforms: dict[str, PlatformRule] = {}
    seen_rule_ids: set[str] = set()
    for platform in FIXED_PLATFORMS:
        item = platforms_raw[platform]
        where = f"platform_rules.json.platforms.{platform}"
        if not isinstance(item, dict):
            _fail(where, "必须是对象")
        display_name = _require_str(item, "display_name", where, max_len=20)
        max_total = item.get("max_total_duration_s")
        if not isinstance(max_total, int) or isinstance(max_total, bool):
            _fail(where, "max_total_duration_s 必须是整数")
        if not 5 <= max_total <= 600:
            _fail(where, f"max_total_duration_s 必须在 5–600 之间，实际为 {max_total}")
        rule_id_pattern = re.compile(rf"^PLAT-{platform}-\d{{3}}$")
        banned_terms = _load_banned_terms(item, where, rule_id_pattern=rule_id_pattern)
        for term in banned_terms:
            if term.rule_id in seen_rule_ids:
                _fail(where, f"重复的平台规则 ID：{term.rule_id}")
            seen_rule_ids.add(term.rule_id)
        platforms[platform] = PlatformRule(
            platform=platform,
            display_name=display_name,
            max_total_duration_s=max_total,
            banned_terms=banned_terms,
        )
    extra = [name for name in platforms_raw if name not in FIXED_PLATFORMS]
    if extra:
        _fail("platform_rules.json", f"未知平台：{', '.join(extra)}")
    return MappingProxyType(platforms)
