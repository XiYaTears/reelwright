"""规则检查：品牌/平台/夸大表述/引用与事实支撑。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.dataset import load_dataset
from src.domain.models import Citation, RuleReport, Script, Shot
from src.tools.duration import total_duration
from src.tools.errors import ToolArgumentError
from src.tools.rules import (
    CITE_FACT_NOT_FOUND,
    CITE_MATERIAL_NOT_FOUND,
    CITE_QUOTE_MISMATCH,
    STRUCT_EMPTY_SHOT,
    STRUCT_NO_CITATION,
    check,
    handle,
)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="module")
def dataset():
    return load_dataset(DATA_DIR)


def _shot(narration: str = "", caption: str = "", citations: tuple[Citation, ...] = ()) -> Shot:
    return Shot(visual="画面", narration=narration, caption=caption, citations=citations)


def _cite(material_id: str = "P01", fact_id: str = "P01-F1", quote: str = "支持字幕") -> Citation:
    return Citation(material_id=material_id, fact_id=fact_id, quote=quote)


def _script(*shots: Shot, title: str = "测试标题") -> Script:
    return Script(title=title, shots=tuple(shots))


def _check(script: Script, dataset, platform: str = "douyin") -> RuleReport:
    return check(script, dataset, platform, total_duration(script.shots))


def _rule_ids(report: RuleReport) -> list[str]:
    return [violation.rule_id for violation in report.violations]


def test_clean_script_with_valid_citation_passes(dataset) -> None:
    report = _check(_script(_shot("支持字幕", "支持字幕", (_cite(),))), dataset)
    assert report.ok is True
    assert report.stats.errors == 0
    assert report.stats.shots_checked == 1
    assert report.stats.rules_checked > 0


def test_brand_banned_term_in_narration(dataset) -> None:
    report = _check(_script(_shot("全网最强剪辑工具", "")), dataset)
    assert report.ok is False
    violation = next(item for item in report.violations if item.rule_id == "BRAND-001")
    assert violation.category == "brand"
    assert violation.shot_index == 1
    assert violation.field == "narration"
    assert violation.matched_text == "最强"


def test_banned_term_in_title_is_whole_script_location(dataset) -> None:
    report = _check(_script(_shot("支持字幕", "支持字幕", (_cite(),)), title="最强字幕工具"), dataset)
    violation = next(item for item in report.violations if item.rule_id == "BRAND-001")
    assert violation.shot_index is None
    assert violation.field == "title"


def test_platform_banned_term_only_applies_to_its_platform(dataset) -> None:
    script = _script(_shot("加微信领模板", ""))
    douyin = _check(script, dataset, platform="douyin")
    assert any(item.rule_id == "PLAT-douyin-001" for item in douyin.violations)
    assert douyin.ok is False

    shipinhao = _check(script, dataset, platform="shipinhao")
    assert not any(item.rule_id.startswith("PLAT-shipinhao-") for item in shipinhao.violations)
    assert shipinhao.ok is True  # 仅有"无引用"warning


def test_exaggeration_pattern(dataset) -> None:
    report = _check(_script(_shot("保证有效果", "")), dataset)
    violation = next(item for item in report.violations if item.rule_id == "EXAG-001")
    assert violation.category == "exaggeration"
    assert "保证有效果" in violation.matched_text


@pytest.mark.parametrize(
    ("citation", "rule_id"),
    [
        (_cite("P99", "P99-F1", "支持字幕"), CITE_MATERIAL_NOT_FOUND),
        (_cite("P01", "P01-F9", "支持字幕"), CITE_FACT_NOT_FOUND),
        (_cite("P01", "P01-F1", "支持字幕功能"), CITE_QUOTE_MISMATCH),
    ],
)
def test_invalid_citation_variants(dataset, citation: Citation, rule_id: str) -> None:
    report = _check(_script(_shot("支持字幕", "支持字幕", (citation,))), dataset)
    assert report.ok is False
    assert rule_id in _rule_ids(report)


def test_unsupported_effect_data_is_cite_error(dataset) -> None:
    """Q2 独立规则：旁白"效率提升 80%"且无引用 → CITE-001 error。"""
    report = _check(_script(_shot("效率提升 80%", "")), dataset)
    assert report.ok is False
    violation = next(item for item in report.violations if item.rule_id == "CITE-001")
    assert violation.category == "citation"
    assert violation.severity == "error"
    assert violation.field == "narration"
    assert violation.matched_text == "80%"
    assert STRUCT_NO_CITATION in _rule_ids(report)


def test_supported_parameter_claim_passes(dataset) -> None:
    citation = _cite("P08", "P08-F1", "支持导出 1080P 视频")
    report = _check(_script(_shot("支持导出 1080P 视频", "", (citation,))), dataset)
    assert report.ok is True
    assert not any(item.rule_id == "CITE-002" for item in report.violations)


def test_parameter_claim_with_wrong_citation_is_error(dataset) -> None:
    report = _check(_script(_shot("支持导出 1080P 视频", "", (_cite(),))), dataset)
    violation = next(item for item in report.violations if item.rule_id == "CITE-002")
    assert violation.matched_text == "1080P"


def test_title_claim_needs_script_wide_support(dataset) -> None:
    report = _check(_script(_shot("支持字幕", "支持字幕", (_cite(),)), title="效率提升 80%"), dataset)
    violation = next(item for item in report.violations if item.rule_id == "CITE-001")
    assert violation.field == "title"
    assert violation.shot_index is None

    supported = _check(
        _script(
            _shot(
                "支持字幕",
                "支持字幕",
                (_cite(), _cite("P08", "P08-F1", "支持导出 1080P 视频")),
            ),
            title="支持导出 1080P 视频",
        ),
        dataset,
    )
    assert not any(item.field == "title" for item in supported.violations)
    assert supported.ok is True


def test_platform_duration_cap_uses_rule_id(dataset) -> None:
    shots = [_shot("字" * 60, ""), _shot("字" * 60, "")]
    report = _check(_script(*shots), dataset, platform="kuaishou")
    violation = next(item for item in report.violations if item.rule_id == "PLAT-kuaishou-DUR-001")
    assert violation.category == "platform"
    assert violation.matched_text == "30"
    assert report.ok is False

    douyin = _check(_script(*shots), dataset, platform="douyin")
    assert not any(item.rule_id == "PLAT-douyin-DUR-001" for item in douyin.violations)


def test_empty_shot_and_no_citation_warnings(dataset) -> None:
    report = _check(_script(_shot("", "")), dataset)
    assert report.ok is True
    assert report.stats.warnings >= 2
    assert STRUCT_EMPTY_SHOT in _rule_ids(report)
    assert STRUCT_NO_CITATION in _rule_ids(report)


def test_violations_deduplicated(dataset) -> None:
    report = _check(_script(_shot("全网最强最强", "")), dataset)
    brand_violations = [item for item in report.violations if item.rule_id == "BRAND-001"]
    assert len(brand_violations) == 1


def test_violations_are_deterministically_sorted(dataset) -> None:
    report = _check(_script(_shot("最强 保证有效果 微信", "")), dataset)
    keys = [
        (0 if item.shot_index is None else 1, item.shot_index or 0, item.rule_id, item.field or "", item.matched_text)
        for item in report.violations
    ]
    assert keys == sorted(keys)


def test_unknown_platform_raises(dataset) -> None:
    with pytest.raises(ValueError, match="未知平台"):
        _check(_script(_shot("支持字幕", "", (_cite(),))), dataset, platform="bilibili")


def test_tool_handle_valid_and_invalid(dataset) -> None:
    payload = {
        "title": "字幕功能上线",
        "shots": [
            {
                "visual": "画面",
                "narration": "支持字幕",
                "caption": "支持字幕",
                "citations": [{"material_id": "P01", "fact_id": "P01-F1", "quote": "支持字幕"}],
            }
        ],
    }
    observation = handle(payload, dataset=dataset, platform="douyin")
    assert observation["ok"] is True
    assert observation["stats"]["shots_checked"] == 1

    with pytest.raises(ToolArgumentError):
        handle({"title": "x", "shots": []}, dataset=dataset, platform="douyin")
