"""LangGraph 编排 + runner：离线核心链路、状态机、预算与 trace。"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest

from src.agent.runner import RunOptions, RunResult, run
from src.domain.script import parse_script
from src.llm.base import LLMError, LLMResponse, ToolCall
from src.tools.duration import estimate

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


# ---------------- 工具函数 ----------------


def _make_script(total: int = 28) -> dict[str, Any]:
    durations = {
        28: [7, 7, 2, 6, 6],
        38: [7, 7, 2, 7, 7, 8],
        58: [7, 7, 2, 7, 7, 7, 7, 7, 7],
    }[total]
    shots: list[dict[str, Any]] = []
    for index, duration in enumerate(durations):
        if index == 2:
            shots.append(
                {
                    "visual": "画面：字幕功能",
                    "narration": "支持字幕",
                    "caption": "支持字幕",
                    "citations": [
                        {"material_id": "P01", "fact_id": "P01-F1", "quote": "支持字幕"}
                    ],
                }
            )
        else:
            shots.append(
                {
                    "visual": "画面：生活记录",
                    "narration": "字" * (4 * duration),
                    "caption": "",
                    "citations": [],
                }
            )
    return {"title": "记录生活的小瞬间", "shots": shots}


def _estimate_args(script: dict[str, Any]) -> dict[str, Any]:
    return {
        "shots": [
            {"narration": shot["narration"], "caption": shot["caption"]}
            for shot in script["shots"]
        ]
    }


def _call(name: str, args: dict[str, Any], index: int) -> ToolCall:
    return ToolCall(
        id=f"fake-{index}",
        name=name,
        arguments=args,
        raw_arguments=json.dumps(args, ensure_ascii=False),
    )


def _response(content: str, *calls: tuple[str, dict[str, Any]]) -> LLMResponse:
    return LLMResponse(
        content=content,
        tool_calls=tuple(_call(name, args, index) for index, (name, args) in enumerate(calls)),
        finish_reason="tool_calls" if calls else "stop",
    )


def _normal_sequence(total: int = 28, content: str = "继续") -> list[LLMResponse]:
    script = _make_script(total)
    return [
        _response(content, ("submit_script", script)),
        _response(content, ("estimate_duration", _estimate_args(script)), ("check_rules", script)),
        _response(content, ("finish", {"outcome": "completed_intent", "message": "完成"})),
    ]


class FakeLLM:
    model_name = "fake"

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if not self._responses:
            raise AssertionError("没有更多预设响应")
        response = self._responses.pop(0)
        return response(messages) if callable(response) else response


def _run(
    raw: dict[str, Any],
    *,
    tmp_path: Path,
    llm_factory: Callable | None = None,
    offline: bool = True,
    max_steps: int = 8,
    data_dir: Path = DATA_DIR,
) -> tuple[RunResult, Path, Path]:
    run_dir = tmp_path / "run"
    out_path = run_dir / "output.json"
    trace_path = run_dir / "trace.jsonl"
    result = run(
        raw,
        options=RunOptions(
            offline=offline,
            data_dir=data_dir,
            max_steps=max_steps,
            out_path=out_path,
            trace_path=trace_path,
        ),
        llm_factory=llm_factory,
    )
    return result, out_path, trace_path


def _trace_events(trace_path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]


def _normal_input(**overrides) -> dict[str, Any]:
    raw = {
        "topic": "字幕功能上线",
        "audience": "剪辑新手",
        "platform": "douyin",
        "target_duration_s": 30,
        "style": "轻快教程",
    }
    raw.update(overrides)
    return raw


# ---------------- 核心链路 ----------------


def test_offline_normal_run_completes_with_trace(tmp_path: Path) -> None:
    result, out_path, trace_path = _run(_normal_input(), tmp_path=tmp_path)
    output = result.output or {}
    assert int(result.exit_code) == 0
    assert output["status"] == "completed"
    assert output["mode"] == "offline"
    assert out_path.is_file()
    assert output["trace_path"].endswith("trace.jsonl")
    script = output["script"]
    assert script["estimated_total_duration_s"] == 28
    assert 3 <= len(script["shots"]) <= 12
    assert script["citations"][0]["material_id"] == "P01"
    assert script["citations"][0]["shot_indexes"]

    events = _trace_events(trace_path)
    types = [event["type"] for event in events]
    assert types[0] == "run_start"
    assert types[-1] == "run_end"
    assert "input_validation" in types
    assert "finalize" in types
    model_events = [event for event in events if event["type"] == "model_response"]
    assert model_events
    assert all(event["decision_summary_source"] == "orchestrator" for event in model_events)
    assert all(event["decision_summary"] for event in model_events)
    raw_trace = trace_path.read_text(encoding="utf-8")
    assert "reasoning_content" not in raw_trace


def test_ac05_reference_example_38_to_28(tmp_path: Path) -> None:
    result, _, trace_path = _run(_normal_input(), tmp_path=tmp_path)
    output = result.output or {}
    assert output["status"] == "completed"
    assert output["script"]["estimated_total_duration_s"] == 28
    events = _trace_events(trace_path)
    estimates = [
        event
        for event in events
        if event["type"] == "tool_result" and event["tool"] == "estimate_duration"
    ]
    assert len(estimates) >= 2
    assert estimates[0]["observation"]["total_duration_s"] == 38
    assert estimates[0]["observation"]["verdict"] == "too_long"
    assert estimates[-1]["observation"]["total_duration_s"] == 28
    assert estimates[-1]["observation"]["within"] is True


def test_ac03a_no_hit_asks_user(tmp_path: Path) -> None:
    result, out_path, trace_path = _run(
        _normal_input(topic="火星旅拍攻略"), tmp_path=tmp_path
    )
    output = result.output or {}
    assert int(result.exit_code) == 3
    assert output["status"] == "needs_input"
    assert output["script"] is None
    assert output["needs_input"]["missing_fields"]
    assert "materials" in output["needs_input"]["missing_fields"]
    searches = [
        event
        for event in _trace_events(trace_path)
        if event["type"] == "tool_result" and event["tool"] == "search_materials"
    ]
    assert len(searches) >= 2
    assert all(event["observation"]["total_matched"] == 0 for event in searches)
    # 查询词在 model_response 的 tool_calls 里必须不同
    model_events = [
        event
        for event in _trace_events(trace_path)
        if event["type"] == "model_response"
    ]
    sent_queries = [
        call["arguments"]["query"]
        for event in model_events
        for call in event["tool_calls"]
        if call["name"] == "search_materials"
    ]
    assert len(sent_queries) >= 2
    assert len(set(sent_queries)) == len(sent_queries)


def test_ac03b_rewrite_hits_p01(tmp_path: Path) -> None:
    result, _, _ = _run(_normal_input(topic="怎么加字幕"), tmp_path=tmp_path)
    output = result.output or {}
    assert output["status"] == "completed"
    assert output["script"]["citations"]


def test_ac04a_recoverable_tool_failure(tmp_path: Path) -> None:
    raw = _normal_input(
        failure_injection=[{"tool": "search_materials", "error": "timeout", "call": 1}]
    )
    result, _, trace_path = _run(raw, tmp_path=tmp_path)
    output = result.output or {}
    assert output["status"] == "completed"
    assert output["tool_errors"]
    assert output["tool_errors"][0]["injected"] is True
    assert output["tool_errors"][0]["code"] == "timeout"
    results = [
        event
        for event in _trace_events(trace_path)
        if event["type"] == "tool_result" and event["tool"] == "search_materials"
    ]
    assert results[0]["ok"] is False
    assert any(event["ok"] is True for event in results)


def test_ac04b_persistent_tool_failure(tmp_path: Path) -> None:
    raw = _normal_input(
        failure_injection=[{"tool": "search_materials", "error": "unavailable"}]
    )
    result, _, _ = _run(raw, tmp_path=tmp_path)
    output = result.output or {}
    assert output["status"] == "failed"
    assert int(result.exit_code) == 5
    assert output["tool_errors"]
    assert output["failure"] is not None
    assert output["script"] is None


def test_ac06_unsatisfiable_duration_conflict(tmp_path: Path) -> None:
    result, _, trace_path = _run(
        _normal_input(platform="kuaishou", target_duration_s=30), tmp_path=tmp_path
    )
    output = result.output or {}
    assert output["status"] == "failed"
    assert int(result.exit_code) == 5
    assert output["failure"]["code"] == "cannot_satisfy"
    assert output["tool_errors"] == []  # 规则违规是业务结果，不是工具执行失败
    rule_ids = {
        violation["rule_id"]
        for violation in output["checks"]["rules"]["violations"]
    }
    assert "PLAT-kuaishou-DUR-001" in rule_ids
    estimates = [
        event
        for event in _trace_events(trace_path)
        if event["type"] == "tool_result" and event["tool"] == "estimate_duration"
    ]
    assert estimates[0]["observation"]["verdict"] == "too_long"


def test_ac07_step_limit_incomplete(tmp_path: Path) -> None:
    result, _, _ = _run(_normal_input(), tmp_path=tmp_path, max_steps=3)
    output = result.output or {}
    assert output["status"] == "incomplete"
    assert int(result.exit_code) == 4
    assert output["budget"]["steps_used"] == 3
    assert output["failure"]["code"] == "step_limit"


def test_ac08a_target_30_vs_60(tmp_path: Path) -> None:
    result_30, _, _ = _run(_normal_input(target_duration_s=30), tmp_path=tmp_path / "a")
    result_60, _, _ = _run(_normal_input(target_duration_s=60), tmp_path=tmp_path / "b")
    output_30 = result_30.output or {}
    output_60 = result_60.output or {}
    assert output_30["script"]["estimated_total_duration_s"] == 28
    assert output_60["script"]["estimated_total_duration_s"] == 58
    assert output_30["script"]["estimated_total_duration_s"] < output_60["script"]["estimated_total_duration_s"]


def test_ac08b_empty_material_dataset(tmp_path: Path) -> None:
    data_dir = tmp_path / "no_hit_data"
    shutil.copytree(DATA_DIR, data_dir)
    materials = json.loads((data_dir / "materials.json").read_text(encoding="utf-8"))
    materials["materials"] = []
    (data_dir / "materials.json").write_text(
        json.dumps(materials, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result, _, _ = _run(_normal_input(), tmp_path=tmp_path, data_dir=data_dir)
    output = result.output or {}
    assert output["status"] == "needs_input"
    assert int(result.exit_code) == 3
    assert output["data_digest"]


def test_needs_input_does_not_call_model(tmp_path: Path) -> None:
    def explode(dataset, requirements):
        raise AssertionError("missing input 不应调用模型")

    raw = _normal_input()
    raw.pop("audience")
    result, out_path, trace_path = _run(raw, tmp_path=tmp_path, llm_factory=explode)
    output = result.output or {}
    assert int(result.exit_code) == 3
    assert output["status"] == "needs_input"
    assert "audience" in output["needs_input"]["missing_fields"]
    types = [event["type"] for event in _trace_events(trace_path)]
    assert types == ["run_start", "input_validation", "run_end"]


# ---------------- finalize / 预算边界 ----------------


def test_final_step_finish_can_complete(tmp_path: Path) -> None:
    factory = lambda dataset, requirements: FakeLLM(_normal_sequence(28))  # noqa: E731
    result, _, _ = _run(_normal_input(), tmp_path=tmp_path, llm_factory=factory, max_steps=3)
    output = result.output or {}
    assert output["status"] == "completed"
    assert int(result.exit_code) == 0
    assert output["budget"]["steps_used"] == 3


def test_same_sequence_without_finish_is_incomplete(tmp_path: Path) -> None:
    sequence = _normal_sequence(28)[:2]
    factory = lambda dataset, requirements: FakeLLM(sequence)  # noqa: E731
    result, _, _ = _run(_normal_input(), tmp_path=tmp_path, llm_factory=factory, max_steps=2)
    output = result.output or {}
    assert output["status"] == "incomplete"
    assert output["failure"]["code"] == "step_limit"
    assert output["script"] is not None  # 保留证据稿


def test_cite_001_guard_blocks_completed(tmp_path: Path) -> None:
    bad_script = {
        "title": "测试标题",
        "shots": [
            {
                "visual": "画面",
                "narration": "效率提升 80%",
                "caption": "",
                "citations": [],
            }
        ],
    }
    sequence = [
        _response("提交", ("submit_script", bad_script)),
        _response(
            "检查",
            ("estimate_duration", _estimate_args(bad_script)),
            ("check_rules", bad_script),
        ),
        _response("完成", ("finish", {"outcome": "completed_intent"})),
    ]
    factory = lambda dataset, requirements: FakeLLM(sequence)  # noqa: E731
    result, _, _ = _run(_normal_input(), tmp_path=tmp_path, llm_factory=factory)
    output = result.output or {}
    assert output["status"] == "failed"
    assert output["checks"]["rules"]["ok"] is False
    rule_ids = {violation["rule_id"] for violation in output["checks"]["rules"]["violations"]}
    assert "CITE-001" in rule_ids
    assert "completed" != output["status"]


def test_tool_call_budget_overflow_batch_skipped(tmp_path: Path) -> None:
    def first_batch(messages):
        calls = tuple(("search_materials", {"query": "不存在"}) for _ in range(15))
        return _response("批量检索", *calls)

    def second_batch(messages):
        calls = tuple(("search_materials", {"query": "不存在"}) for _ in range(2))
        return _response("继续检索", *calls)

    factory = lambda dataset, requirements: FakeLLM([first_batch, second_batch])  # noqa: E731
    result, _, trace_path = _run(_normal_input(), tmp_path=tmp_path, llm_factory=factory)
    output = result.output or {}
    assert output["status"] == "incomplete"
    assert output["failure"]["code"] == "tool_call_limit"
    assert len(output["skipped_tool_calls"]) == 2
    assert output["budget"]["tool_calls_used"] == 15
    assert any(event["type"] == "skipped_tool_calls" for event in _trace_events(trace_path))


def test_tool_call_budget_exact_limit_ends_run(tmp_path: Path) -> None:
    def batch(messages):
        calls = tuple(("search_materials", {"query": "不存在"}) for _ in range(16))
        return _response("批量检索", *calls)

    factory = lambda dataset, requirements: FakeLLM([batch])  # noqa: E731
    result, _, _ = _run(_normal_input(), tmp_path=tmp_path, llm_factory=factory)
    output = result.output or {}
    assert output["status"] == "incomplete"
    assert output["failure"]["code"] == "tool_call_limit"
    assert output["budget"]["tool_calls_used"] == 16
    assert output["skipped_tool_calls"] == []


def test_decision_summary_when_model_content_missing(tmp_path: Path) -> None:
    sequence = _normal_sequence(28, content="")
    factory = lambda dataset, requirements: FakeLLM(sequence)  # noqa: E731
    result, _, trace_path = _run(_normal_input(), tmp_path=tmp_path, llm_factory=factory)
    assert (result.output or {})["status"] == "completed"
    model_events = [
        event for event in _trace_events(trace_path) if event["type"] == "model_response"
    ]
    assert model_events
    assert all(event["decision_note"] == "" for event in model_events)
    assert all(event["decision_note_source"] == "missing" for event in model_events)
    assert all(event["decision_summary"] for event in model_events)
    assert model_events[0]["decision_summary"].startswith("首步")
    assert any("上一步" in event["decision_summary"] for event in model_events)


def test_decision_summary_when_model_content_present(tmp_path: Path) -> None:
    sequence = _normal_sequence(28, content="提交脚本")
    factory = lambda dataset, requirements: FakeLLM(sequence)  # noqa: E731
    result, _, trace_path = _run(_normal_input(), tmp_path=tmp_path, llm_factory=factory)
    assert (result.output or {})["status"] == "completed"
    model_events = [
        event for event in _trace_events(trace_path) if event["type"] == "model_response"
    ]
    assert all(event["decision_note"] == "提交脚本" for event in model_events)
    assert all(event["decision_note_source"] == "model" for event in model_events)
    assert all(event["decision_summary"] for event in model_events)


def test_llm_error_is_explicit_failure(tmp_path: Path) -> None:
    class FailingLLM:
        model_name = "failing"

        def chat(self, messages, tools):
            raise LLMError("网络错误：测试")

    factory = lambda dataset, requirements: FailingLLM()  # noqa: E731
    result, _, _ = _run(_normal_input(), tmp_path=tmp_path, llm_factory=factory)
    output = result.output or {}
    assert int(result.exit_code) == 5
    assert output["status"] == "failed"
    assert output["failure"]["code"] == "llm_error"


# ---------------- 输出不变量 ----------------


def test_output_durations_and_citations_are_consistent(tmp_path: Path) -> None:
    result, _, _ = _run(_normal_input(), tmp_path=tmp_path)
    output = result.output or {}
    script_raw = output["script"]
    script = parse_script(script_raw)
    result_estimate = estimate(script.shots, 30)
    assert [shot["duration_s"] for shot in script_raw["shots"]] == [
        item.duration_s for item in result_estimate.per_shot
    ]
    assert script_raw["estimated_total_duration_s"] == result_estimate.total_duration_s
    assert sum(shot["duration_s"] for shot in script_raw["shots"]) == result_estimate.total_duration_s
    from src.dataset import load_dataset

    dataset = load_dataset(DATA_DIR)
    for citation in script_raw["citations"]:
        fact = dataset.get_fact(citation["material_id"], citation["fact_id"])
        assert fact is not None
        assert citation["quote"] in fact.text


def test_run_id_and_budget_reported(tmp_path: Path) -> None:
    result, _, _ = _run(_normal_input(), tmp_path=tmp_path, max_steps=5)
    output = result.output or {}
    assert output["run_id"]
    assert output["budget"]["max_steps"] == 5
    assert output["budget"]["max_tool_calls"] == 10
    assert output["budget"]["steps_used"] <= 5


def test_invalid_control_action_returns_to_loop(tmp_path: Path) -> None:
    """finish 参数非法 → 回填 invalid_arguments 并回循环，第二次合法 finish 可完成。"""
    script = _make_script(28)
    sequence = [
        _response(
            "提交并尝试完成",
            ("submit_script", script),
            ("finish", {"outcome": "bogus"}),
        ),
        _response(
            "估算检查",
            ("estimate_duration", _estimate_args(script)),
            ("check_rules", script),
        ),
        _response("完成", ("finish", {"outcome": "completed_intent"})),
    ]
    factory = lambda dataset, requirements: FakeLLM(sequence)  # noqa: E731
    result, _, trace_path = _run(_normal_input(), tmp_path=tmp_path, llm_factory=factory)
    output = result.output or {}
    assert output["status"] == "completed"
    model_events = [
        event for event in _trace_events(trace_path) if event["type"] == "model_response"
    ]
    assert len(model_events) == 3
    finish_events = [
        event
        for event in _trace_events(trace_path)
        if event["type"] == "tool_result" and event["tool"] == "finish"
    ]
    assert finish_events[0]["ok"] is False
    assert finish_events[0]["error"]["code"] == "invalid_arguments"
    assert finish_events[-1]["ok"] is True
    assert any(
        error["tool"] == "finish" and error["code"] == "invalid_arguments"
        for error in output["tool_errors"]
    )


class _FailingAfterResponses:
    model_name = "failing-after-two"

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)

    def chat(self, messages, tools):
        if self._responses:
            return self._responses.pop(0)
        raise LLMError("网络错误：测试")


def test_late_llm_error_reports_actual_steps(tmp_path: Path) -> None:
    script = _make_script(28)
    responses = [
        _response("提交", ("submit_script", script)),
        _response(
            "检查",
            ("estimate_duration", _estimate_args(script)),
            ("check_rules", script),
        ),
    ]
    factory = lambda dataset, requirements: _FailingAfterResponses(responses)  # noqa: E731
    result, _, trace_path = _run(_normal_input(), tmp_path=tmp_path, llm_factory=factory)
    output = result.output or {}
    assert output["status"] == "failed"
    assert output["failure"]["code"] == "llm_error"
    run_end = next(
        event for event in _trace_events(trace_path) if event["type"] == "run_end"
    )
    assert run_end["steps_used"] == 2
    assert run_end["tool_calls_used"] == 3
