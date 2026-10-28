"""data/ 合成数据集加载与校验。"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from src.dataset import (
    DATA_FILES,
    FIXED_PLATFORMS,
    DatasetError,
    load_dataset,
)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _copy_data(tmp_path: Path) -> Path:
    target = tmp_path / "data"
    shutil.copytree(DATA_DIR, target)
    return target


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_load_real_dataset_entities_and_indexes() -> None:
    dataset = load_dataset(DATA_DIR)

    assert re.fullmatch(r"[0-9a-f]{12}", dataset.data_digest)
    assert [material.material_id for material in dataset.materials] == [
        f"P{index:02d}" for index in range(1, 11)
    ]
    p01 = dataset.get_material("P01")
    assert p01 is not None
    assert p01.title == "字幕功能说明"
    assert p01.tags == ("字幕", "剪辑")
    assert len(p01.facts) == 1
    assert p01.facts[0].fact_id == "P01-F1"
    assert p01.facts[0].text == "支持字幕"
    assert dataset.get_fact("P01", "P01-F1") is not None
    assert dataset.get_fact("P01", "P01-F1").text == "支持字幕"
    assert dataset.get_fact("P01", "P01-F2") is None
    assert dataset.get_fact("P99", "P01-F1") is None
    assert dataset.get_material("P99") is None

    assert tuple(dataset.platforms) == FIXED_PLATFORMS
    assert dataset.platforms["kuaishou"].max_total_duration_s == 25
    assert dataset.platforms["kuaishou"].max_duration_rule_id == "PLAT-kuaishou-DUR-001"
    assert dataset.platforms["douyin"].max_total_duration_s == 180
    assert dataset.brand.brand_name == "小影"
    assert {rule.rule_id for rule in dataset.brand.banned_terms} >= {"BRAND-001", "BRAND-010"}


def test_default_data_p01_has_exactly_one_fact() -> None:
    """默认数据约定（由测试锁死，加载器不硬编码）：P01 只含"支持字幕"一条事实。"""
    dataset = load_dataset(DATA_DIR)
    p01 = dataset.get_material("P01")
    assert p01 is not None
    assert [(fact.fact_id, fact.text) for fact in p01.facts] == [("P01-F1", "支持字幕")]


def test_default_data_has_no_effect_data() -> None:
    """默认数据约定（由测试锁死）：素材不含效果数据/认证，保证 R2 反例可判定。"""
    dataset = load_dataset(DATA_DIR)
    facts_text = "".join(fact.text for fact in dataset.all_facts())
    assert "%" not in facts_text
    assert "效率提升" not in facts_text
    assert "认证" not in facts_text and "专利" not in facts_text


def test_fallback_query_misses_default_data() -> None:
    """mock 固定回退词在默认数据上必须 0 命中（用于 AC-03a / no_hit 确定性）。"""
    fallback = "星河旅拍指南"
    dataset = load_dataset(DATA_DIR)
    haystacks = [
        *[material.title for material in dataset.materials],
        *[tag for material in dataset.materials for tag in material.tags],
        *[fact.text for fact in dataset.all_facts()],
    ]
    assert all(fallback not in haystack for haystack in haystacks)


def test_digest_is_deterministic_and_changes_with_content(tmp_path: Path) -> None:
    first = load_dataset(DATA_DIR)
    second = load_dataset(DATA_DIR)
    assert first.data_digest == second.data_digest

    data_dir = _copy_data(tmp_path)
    materials = _load_json(data_dir / "materials.json")
    materials["materials"][0]["facts"][0]["text"] = "支持字幕与自动识别"
    _save_json(data_dir / "materials.json", materials)

    changed = load_dataset(data_dir)
    assert changed.data_digest != first.data_digest
    assert changed.get_material("P01").facts[0].text == "支持字幕与自动识别"


def test_missing_file_raises(tmp_path: Path) -> None:
    data_dir = _copy_data(tmp_path)
    (data_dir / "brand_rules.json").unlink()
    with pytest.raises(DatasetError, match="缺少文件"):
        load_dataset(data_dir)


def test_invalid_json_raises(tmp_path: Path) -> None:
    data_dir = _copy_data(tmp_path)
    (data_dir / "platform_rules.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(DatasetError, match="不是合法 JSON"):
        load_dataset(data_dir)


def test_duplicate_material_id_raises(tmp_path: Path) -> None:
    data_dir = _copy_data(tmp_path)
    materials = _load_json(data_dir / "materials.json")
    materials["materials"][1]["material_id"] = "P01"
    _save_json(data_dir / "materials.json", materials)
    with pytest.raises(DatasetError, match="重复的素材 ID"):
        load_dataset(data_dir)


def test_duplicate_fact_id_raises(tmp_path: Path) -> None:
    data_dir = _copy_data(tmp_path)
    materials = _load_json(data_dir / "materials.json")
    materials["materials"][0]["facts"].append(
        {"fact_id": "P01-F1", "text": "重复事实", "kind": "feature"}
    )
    _save_json(data_dir / "materials.json", materials)
    with pytest.raises(DatasetError, match="重复的事实 ID"):
        load_dataset(data_dir)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda materials: materials["materials"][0]["facts"][0].update(text="支持字幕 "), "首尾空白"),
        (lambda materials: materials["materials"][0]["facts"][0].update(kind="bogus"), "kind"),
        (lambda materials: materials["materials"][0].update(title=""), "不能为空"),
        (lambda materials: materials["materials"][0].update(tags=[]), "至少需要 1 项"),
        (lambda materials: materials["materials"][0].update(material_id="X01"), "格式不合法"),
    ],
)
def test_invalid_material_fields_raise(tmp_path: Path, mutate, message: str) -> None:
    data_dir = _copy_data(tmp_path)
    materials = _load_json(data_dir / "materials.json")
    mutate(materials)
    _save_json(data_dir / "materials.json", materials)
    with pytest.raises(DatasetError, match=message):
        load_dataset(data_dir)


def test_invalid_regex_raises(tmp_path: Path) -> None:
    data_dir = _copy_data(tmp_path)
    brand = _load_json(data_dir / "brand_rules.json")
    brand["exaggeration_patterns"][0]["pattern"] = "[unclosed"
    _save_json(data_dir / "brand_rules.json", brand)
    with pytest.raises(DatasetError, match="正则无法编译"):
        load_dataset(data_dir)


def test_invalid_rule_id_raises(tmp_path: Path) -> None:
    data_dir = _copy_data(tmp_path)
    brand = _load_json(data_dir / "brand_rules.json")
    brand["banned_terms"][0]["rule_id"] = "X-001"
    _save_json(data_dir / "brand_rules.json", brand)
    with pytest.raises(DatasetError, match="格式不合法"):
        load_dataset(data_dir)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda platforms: platforms["platforms"]["kuaishou"].update(max_total_duration_s=0), "5–600"),
        (lambda platforms: platforms["platforms"].pop("shipinhao"), "缺少固定平台"),
        (lambda platforms: platforms["platforms"].__setitem__("bilibili", {"display_name": "B"}), "未知平台"),
        (lambda platforms: platforms["platforms"]["kuaishou"].pop("max_duration_rule_id"), "max_duration_rule_id"),
        (
            lambda platforms: platforms["platforms"]["kuaishou"].update(
                max_duration_rule_id="PLAT-douyin-DUR-001"
            ),
            "格式不合法",
        ),
    ],
)
def test_invalid_platform_rules_raise(tmp_path: Path, mutate, message: str) -> None:
    data_dir = _copy_data(tmp_path)
    platform_rules = _load_json(data_dir / "platform_rules.json")
    mutate(platform_rules)
    _save_json(data_dir / "platform_rules.json", platform_rules)
    with pytest.raises(DatasetError, match=message):
        load_dataset(data_dir)


def test_empty_materials_allowed(tmp_path: Path) -> None:
    """AC-08b no_hit 夹具使用空素材数组；加载器不得拒绝。"""
    data_dir = _copy_data(tmp_path)
    materials = _load_json(data_dir / "materials.json")
    materials["materials"] = []
    _save_json(data_dir / "materials.json", materials)

    dataset = load_dataset(data_dir)
    assert dataset.materials == ()
    assert dataset.all_facts() == ()
    assert dataset.get_material("P01") is None
    # 平台/品牌规则仍完整
    assert dataset.platforms["kuaishou"].max_duration_rule_id == "PLAT-kuaishou-DUR-001"


def test_data_files_are_utf8_without_bom() -> None:
    for name in DATA_FILES:
        raw = (DATA_DIR / name).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{name} 不应含 BOM"
        raw.decode("utf-8")
