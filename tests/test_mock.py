"""离线 mock 策略：确定性查询序列、38→28/58、工具错误重试。"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from src.dataset import load_dataset
from src.domain.models import Requirements, Shot
from src.llm.mock import (
    COMPRESSED_DRAFT_SECONDS,
    EXPANDED_DRAFT_SECONDS,
    INITIAL_DRAFT_SECONDS,
    MockClient,
    build_query_candidates,
)
from src.tools.duration import total_duration
from src.tools.registry import (
    TOOL_SPECS,
    ToolExecutionError,
    ToolRegistry,
    parse_injection_specs,
)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@dataclass
class MockRun:
    events: list[dict[str, Any]]
    queries: tuple[str, ...]
    terminal: str | None
    steps: int


@pytest.fixture(scope="module")
def dataset():
    return load_dataset(DATA_DIR)


def _requirements(
    topic: str = "字幕功能上线",
    target_duration_s: int = 30,
    platform: str = "douyin",
) -> Requirements:
    return Requirements(
        topic=topic,
        audience="剪辑新手",
        platform=platform,
        target_duration_s=target_duration_s,
        style="轻快教程",
    )


def _run_mock(
    dataset,
    requirements: Requirements,
    injections=(),
    max_steps: int = 14,
) -> MockRun:
    registry = ToolRegistry(
        dataset,
        target_duration_s=requirements.target_duration_s,
        platform=requirements.platform,
        injections=injections,
    )
    client = MockClient(dataset, requirements)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "requirements"},
    ]
    events: list[dict[str, Any]] = []
    terminal: str | None = None
    steps = 0
    while steps < max_steps and terminal is None:
        steps += 1
        response = client.chat(messages, TOOL_SPECS)
        assert response.content
        assert len(response.content) <= 40
        assistant = {
            "role": "assistant",
            "content": response.content,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in response.tool_calls
            ],
        }
        messages.append(assistant)
        batch_results: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        for call in response.tool_calls:
            if call.name == "submit_script":
                result: dict[str, Any] = {"ok": True, "revision": steps}
            elif call.name in ("ask_user", "finish"):
                result = {"ok": True}
                terminal = call.name
            else:
                try:
                    result = registry.execute(call.name, call.arguments)
                except ToolExecutionError as exc:
                    result = exc.to_observation()
            batch_results.append((call.name, call.arguments, result))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
        events.append(
            {
                "step": steps,
                "note": response.content,
                "calls": batch_results,
            }
        )
    return MockRun(
        events=events,
        queries=client.queries_sent,
        terminal=terminal,
        steps=steps,
    )


def _submitted_scripts(run: MockRun) -> list[dict[str, Any]]:
    scripts: list[dict[str, Any]] = []
    for event in run.events:
        for name, args, _ in event["calls"]:
            if name == "submit_script":
                scripts.append(args)
    return scripts


def _results(run: MockRun, tool: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for event in run.events:
        for name, _, result in event["calls"]:
            if name == tool:
                results.append(result)
    return results


def _terminal_call(run: MockRun) -> tuple[str, dict[str, Any]]:
    for event in reversed(run.events):
        for name, args, _ in event["calls"]:
            if name in ("finish", "ask_user"):
                return name, args
    raise AssertionError("没有终止调用")


def _script_total(script: dict[str, Any], target_duration_s: int = 30) -> int:
    shots = [
        Shot(visual=shot["visual"], narration=shot["narration"], caption=shot["caption"])
        for shot in script["shots"]
    ]
    return total_duration(shots)


def _citations(script: dict[str, Any]) -> list[dict[str, str]]:
    return [
        citation
        for shot in script["shots"]
        for citation in shot.get("citations", [])
    ]


def test_build_query_candidates_sequence() -> None:
    assert build_query_candidates("火星旅拍攻略") == (
        "火星旅拍攻略",
        "火星",
        "星旅",
        "旅拍",
        "星河旅拍指南",
    )
    assert build_query_candidates("怎么加字幕") == (
        "怎么加字幕",
        "加字",
        "字幕",
        "星河旅拍指南",
    )
    assert build_query_candidates("字幕功能上线") == (
        "字幕功能上线",
        "字幕",
        "幕功",
        "功能",
        "星河旅拍指南",
    )


def test_reference_example_38_to_28_and_citation(dataset) -> None:
    run = _run_mock(dataset, _requirements())
    assert run.terminal == "finish"
    assert run.steps <= 8
    assert run.queries == ("字幕功能上线", "字幕")

    scripts = _submitted_scripts(run)
    assert [_script_total(script) for script in scripts] == [INITIAL_DRAFT_SECONDS, COMPRESSED_DRAFT_SECONDS]

    estimates = _results(run, "estimate_duration")
    assert estimates[0]["total_duration_s"] == 38
    assert estimates[0]["verdict"] == "too_long"
    assert estimates[-1]["total_duration_s"] == 28
    assert estimates[-1]["within"] is True

    citations = _citations(scripts[-1])
    assert citations == [{"material_id": "P01", "fact_id": "P01-F1", "quote": "支持字幕"}]
    name, args = _terminal_call(run)
    assert name == "finish"
    assert args["outcome"] == "completed_intent"


def test_target_60_expands_same_initial_draft(dataset) -> None:
    run = _run_mock(dataset, _requirements(target_duration_s=60))
    assert run.terminal == "finish"
    scripts = _submitted_scripts(run)
    assert [_script_total(script, 60) for script in scripts] == [
        INITIAL_DRAFT_SECONDS,
        EXPANDED_DRAFT_SECONDS,
    ]
    estimates = _results(run, "estimate_duration")
    assert estimates[0]["total_duration_s"] == 38
    assert estimates[0]["verdict"] == "too_short"
    assert estimates[-1]["within"] is True
    assert estimates[-1]["window"] == {"min_s": 54, "max_s": 66}


def test_no_hit_topic_asks_user_after_three_plus_queries(dataset) -> None:
    run = _run_mock(dataset, _requirements(topic="火星旅拍攻略"))
    assert run.terminal == "ask_user"
    assert run.queries == build_query_candidates("火星旅拍攻略")
    assert len(run.queries) >= 2
    assert len(set(run.queries)) == len(run.queries)

    search_results = _results(run, "search_materials")
    assert len(search_results) == len(run.queries)
    assert all(result["total_matched"] == 0 for result in search_results)
    assert _submitted_scripts(run) == []


def test_empty_material_dataset_no_hit(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    shutil.copytree(DATA_DIR, data_dir)
    materials = json.loads((data_dir / "materials.json").read_text(encoding="utf-8"))
    materials["materials"] = []
    (data_dir / "materials.json").write_text(
        json.dumps(materials, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    empty_dataset = load_dataset(data_dir)

    run = _run_mock(empty_dataset, _requirements())
    assert run.terminal == "ask_user"
    assert run.queries == ("字幕功能上线", "字幕", "幕功", "功能", "星河旅拍指南")
    assert all(result["total_matched"] == 0 for result in _results(run, "search_materials"))


def test_platform_duration_conflict_finishes_cannot_satisfy(dataset) -> None:
    run = _run_mock(dataset, _requirements(platform="kuaishou"))
    assert run.terminal == "finish"
    name, args = _terminal_call(run)
    assert name == "finish"
    assert args["outcome"] == "cannot_satisfy"
    assert len(_submitted_scripts(run)) == 1  # 只提交过 38 秒初稿，未强行通过

    rule_results = _results(run, "check_rules")
    rule_ids = {violation["rule_id"] for violation in rule_results[0]["violations"]}
    assert "PLAT-kuaishou-DUR-001" in rule_ids


def test_recoverable_search_error_is_retried(dataset) -> None:
    injections = parse_injection_specs(
        [{"tool": "search_materials", "error": "timeout", "call": 1}]
    )
    run = _run_mock(dataset, _requirements(), injections=injections)
    assert run.terminal == "finish"
    search_results = _results(run, "search_materials")
    assert search_results[0]["ok"] is False
    assert search_results[0]["error"]["code"] == "timeout"
    assert search_results[1]["ok"] is True
    assert run.queries[:3] == ("字幕功能上线", "字幕功能上线", "字幕")
    _, args = _terminal_call(run)
    assert args["outcome"] == "completed_intent"


def test_persistent_search_failure_finishes_cannot_satisfy(dataset) -> None:
    injections = parse_injection_specs(
        [{"tool": "search_materials", "error": "unavailable"}]
    )
    run = _run_mock(dataset, _requirements(), injections=injections)
    assert run.terminal == "finish"
    _, args = _terminal_call(run)
    assert args["outcome"] == "cannot_satisfy"
    assert run.steps == 3
    assert _submitted_scripts(run) == []


def test_mock_rejects_call_after_terminal(dataset) -> None:
    run = _run_mock(dataset, _requirements())
    assert run.terminal == "finish"
    client = MockClient(dataset, _requirements())
    client._terminal_emitted = True  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="终止"):
        client.chat([], TOOL_SPECS)
