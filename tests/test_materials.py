"""素材检索工具。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.dataset import load_dataset
from src.tools.errors import ToolArgumentError
from src.tools.materials import handle, search_materials, tokenize_query

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="module")
def dataset():
    return load_dataset(DATA_DIR)


def test_tokenize_splits_and_dedupes() -> None:
    assert tokenize_query("字幕 剪辑") == ("字幕", "剪辑")
    assert tokenize_query("字幕，剪辑！字幕") == ("字幕", "剪辑")
    assert tokenize_query("  ") == ()


def test_query_subtitles_hits_p01_with_expected_score(dataset) -> None:
    result = search_materials("字幕", dataset)
    assert result["ok"] is True
    assert result["total_matched"] == 1
    material = result["materials"][0]
    assert material["material_id"] == "P01"
    assert material["score"] == 6  # title 3 + tags 2 + fact 1
    assert material["matched_terms"] == ["字幕"]
    assert material["matched_in"] == ["title", "tags", "fact"]
    assert material["facts"] == [{"fact_id": "P01-F1", "text": "支持字幕", "kind": "feature"}]


def test_score_ordering_and_top_k(dataset) -> None:
    result = search_materials("剪辑", dataset)
    assert [item["material_id"] for item in result["materials"]] == ["P06", "P01", "P02"]
    assert [item["score"] for item in result["materials"]] == [6, 2, 2]
    assert result["total_matched"] == 3

    truncated = search_materials("剪辑", dataset, top_k=1)
    assert [item["material_id"] for item in truncated["materials"]] == ["P06"]
    assert truncated["total_matched"] == 3


def test_case_insensitive_latin_match(dataset) -> None:
    result = search_materials("1080p", dataset)
    assert result["total_matched"] == 1
    assert result["materials"][0]["material_id"] == "P08"
    assert result["materials"][0]["matched_in"] == ["fact"]


def test_no_result_returns_empty_ok_and_hint(dataset) -> None:
    result = search_materials("星河旅拍指南", dataset)
    assert result["ok"] is True
    assert result["total_matched"] == 0
    assert result["materials"] == []
    assert "hint" in result


def test_search_empty_dataset_returns_empty(tmp_path: Path) -> None:
    target = tmp_path / "data"
    shutil.copytree(DATA_DIR, target)
    import json

    materials = json.loads((target / "materials.json").read_text(encoding="utf-8"))
    materials["materials"] = []
    (target / "materials.json").write_text(
        json.dumps(materials, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    empty_dataset = load_dataset(target)
    result = search_materials("字幕", empty_dataset)
    assert result["ok"] is True
    assert result["total_matched"] == 0


def test_handle_valid_arguments(dataset) -> None:
    observation = handle({"query": "字幕", "top_k": 3}, dataset=dataset)
    assert observation["ok"] is True
    assert observation["materials"][0]["material_id"] == "P01"


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"query": ""},
        {"query": "   "},
        {"query": 1},
        {"query": "字" * 121},
        {"query": "字幕", "top_k": 0},
        {"query": "字幕", "top_k": 11},
        {"query": "字幕", "top_k": True},
        {"query": "字幕", "top_k": "3"},
    ],
)
def test_handle_rejects_bad_arguments(dataset, args: dict) -> None:
    with pytest.raises(ToolArgumentError):
        handle(args, dataset=dataset)
