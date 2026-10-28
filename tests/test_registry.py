"""工具注册表：调度、失败注入与错误归一化。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.dataset import load_dataset
from src.tools import registry as registry_module
from src.tools.registry import (
    BUSINESS_TOOLS,
    CONTROL_TOOLS,
    TOOL_SPECS,
    InjectionRule,
    ToolExecutionError,
    ToolRegistry,
    parse_injection_specs,
)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="module")
def dataset():
    return load_dataset(DATA_DIR)


def _registry(dataset, injections: tuple[InjectionRule, ...] = ()) -> ToolRegistry:
    return ToolRegistry(
        dataset,
        target_duration_s=30,
        platform="douyin",
        injections=injections,
    )


def test_tool_specs_cover_business_and_control_actions() -> None:
    names = [spec["name"] for spec in TOOL_SPECS]
    assert names == [*BUSINESS_TOOLS, *CONTROL_TOOLS]
    assert all(spec["parameters"]["type"] == "object" for spec in TOOL_SPECS)
    json.dumps(TOOL_SPECS)  # schema 必须可 JSON 序列化给模型


def test_execute_business_tools(dataset) -> None:
    registry = _registry(dataset)

    search = registry.execute("search_materials", {"query": "字幕"})
    assert search["ok"] is True
    assert search["materials"][0]["material_id"] == "P01"

    estimate = registry.execute(
        "estimate_duration",
        {"shots": [{"narration": "字" * chars, "caption": ""} for chars in (25, 26, 27, 22, 23, 18)]},
    )
    assert estimate["total_duration_s"] == 38
    assert estimate["verdict"] == "too_long"

    check = registry.execute(
        "check_rules",
        {
            "title": "字幕功能上线",
            "shots": [
                {
                    "visual": "画面",
                    "narration": "支持字幕",
                    "caption": "支持字幕",
                    "citations": [
                        {"material_id": "P01", "fact_id": "P01-F1", "quote": "支持字幕"}
                    ],
                }
            ],
        },
    )
    assert check["ok"] is True
    assert registry.call_counts == {
        "search_materials": 1,
        "estimate_duration": 1,
        "check_rules": 1,
    }


def test_injection_first_call_then_success(dataset) -> None:
    registry = _registry(dataset, (InjectionRule("search_materials", "timeout", 1),))

    with pytest.raises(ToolExecutionError) as error_info:
        registry.execute("search_materials", {"query": "字幕"})
    error = error_info.value
    assert error.code == "timeout"
    assert error.retryable is True
    assert error.injected is True
    assert error.to_observation()["ok"] is False

    second = registry.execute("search_materials", {"query": "字幕"})
    assert second["ok"] is True
    assert registry.call_counts["search_materials"] == 2


def test_injection_every_call(dataset) -> None:
    registry = _registry(dataset, (InjectionRule("estimate_duration", "unavailable"),))
    for _ in range(2):
        with pytest.raises(ToolExecutionError) as error_info:
            registry.execute(
                "estimate_duration", {"shots": [{"narration": "字", "caption": ""}]}
            )
        assert error_info.value.code == "unavailable"
        assert error_info.value.injected is True


def test_injection_on_specific_later_call(dataset) -> None:
    registry = _registry(dataset, (InjectionRule("search_materials", "invalid_response", 2),))
    assert registry.execute("search_materials", {"query": "字幕"})["ok"] is True
    with pytest.raises(ToolExecutionError) as error_info:
        registry.execute("search_materials", {"query": "字幕"})
    assert error_info.value.code == "invalid_response"
    assert error_info.value.retryable is False


def test_invalid_arguments_normalized(dataset) -> None:
    registry = _registry(dataset)
    with pytest.raises(ToolExecutionError) as error_info:
        registry.execute("estimate_duration", {"shots": []})
    error = error_info.value
    assert error.code == "invalid_arguments"
    assert error.injected is False
    assert error.retryable is False
    assert registry.call_counts["estimate_duration"] == 1


def test_control_action_is_not_a_business_tool(dataset) -> None:
    registry = _registry(dataset)
    with pytest.raises(ToolExecutionError) as error_info:
        registry.execute("submit_script", {"title": "x", "shots": []})
    assert error_info.value.code == "unknown_tool"
    assert error_info.value.injected is False


def test_internal_error_normalized(dataset, monkeypatch) -> None:
    def _boom(args, *, dataset):
        raise RuntimeError("模拟内部异常")

    monkeypatch.setattr(registry_module.materials, "handle", _boom)
    registry = _registry(dataset)
    with pytest.raises(ToolExecutionError) as error_info:
        registry.execute("search_materials", {"query": "字幕"})
    assert error_info.value.code == "internal"
    assert error_info.value.retryable is False


def test_duplicate_injection_rule_rejected(dataset) -> None:
    with pytest.raises(ValueError, match="重复"):
        _registry(
            dataset,
            (
                InjectionRule("search_materials", "timeout", 1),
                InjectionRule("search_materials", "unavailable", 2),
            ),
        )


def test_parse_injection_specs_valid() -> None:
    parsed = parse_injection_specs(
        [
            {"tool": "search_materials", "error": "timeout", "call": 1},
            {"tool": "estimate_duration", "error": "unavailable"},
        ]
    )
    assert parsed == (
        InjectionRule("search_materials", "timeout", 1),
        InjectionRule("estimate_duration", "unavailable", None),
    )


@pytest.mark.parametrize(
    "raw",
    [
        [{"tool": "submit_script", "error": "timeout"}],
        [{"tool": "unknown", "error": "timeout"}],
        [{"tool": "search_materials", "error": "explode"}],
        [{"tool": "search_materials", "error": "timeout", "call": 0}],
        [{"tool": "search_materials", "error": "timeout", "call": True}],
        [
            {"tool": "search_materials", "error": "timeout", "call": 1},
            {"tool": "search_materials", "error": "unavailable", "call": 2},
        ],
        ["not-a-dict"],
    ],
)
def test_parse_injection_specs_invalid(raw) -> None:
    with pytest.raises(ValueError):
        parse_injection_specs(raw)
