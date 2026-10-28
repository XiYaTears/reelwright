"""真实模型冒烟（marker: real，默认不跑；需要 .env.local 与网络）。

只做小样本硬约束断言与正式决策证据场景；真实 trace 另存 examples/。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.agent.runner import RunOptions, run
from src.config import ConfigError, load_llm_settings
from src.dataset import load_dataset
from src.tools.registry import parse_injection_specs

pytestmark = pytest.mark.real

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
BASE_INPUT = {
    "topic": "字幕功能上线",
    "audience": "剪辑新手",
    "platform": "douyin",
    "target_duration_s": 30,
    "style": "轻快教程",
}


def _require_real_config() -> None:
    try:
        load_llm_settings()
    except ConfigError:
        pytest.skip("缺少 .env.local 真实配置，跳过真实冒烟")


def _run_real(tmp_path: Path, raw: dict, *, injections=()):
    result = run(
        raw,
        options=RunOptions(
            offline=False,
            data_dir=DATA_DIR,
            out_path=tmp_path / "output.real.json",
            trace_path=tmp_path / "trace.real.jsonl",
            injections=tuple(injections),
        ),
    )
    trace = [
        json.loads(line)
        for line in (tmp_path / "trace.real.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    return result, trace


def test_real_ac01_normal_hard_constraints(tmp_path: Path) -> None:
    _require_real_config()
    result, trace = _run_real(tmp_path, BASE_INPUT)
    output = result.output or {}
    assert int(result.exit_code) == 0, output.get("failure")
    assert output["status"] == "completed"
    script = output["script"]
    assert script is not None
    window = script["window"]
    assert window["min_s"] <= script["estimated_total_duration_s"] <= window["max_s"]
    assert output["checks"]["rules"]["ok"] is True
    dataset = load_dataset(DATA_DIR)
    for citation in script["citations"]:
        fact = dataset.get_fact(citation["material_id"], citation["fact_id"])
        assert fact is not None and citation["quote"] in fact.text
    assert any(event["type"] == "model_response" for event in trace)
    model_events = [event for event in trace if event["type"] == "model_response"]
    assert all(event.get("response_id") for event in model_events)


def test_real_no_hit_asks_user(tmp_path: Path) -> None:
    _require_real_config()
    data_dir = tmp_path / "no_hit_data"
    shutil.copytree(DATA_DIR, data_dir)
    materials = json.loads((data_dir / "materials.json").read_text(encoding="utf-8"))
    materials["materials"] = []
    (data_dir / "materials.json").write_text(
        json.dumps(materials, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result = run(
        BASE_INPUT,
        options=RunOptions(
            offline=False,
            data_dir=data_dir,
            out_path=tmp_path / "output.real.json",
            trace_path=tmp_path / "trace.real.jsonl",
        ),
    )
    trace = [
        json.loads(line)
        for line in (tmp_path / "trace.real.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    output = result.output or {}
    assert output["status"] == "needs_input"
    assert int(result.exit_code) == 3
    assert output["script"] is None
    searches = [
        event
        for event in trace
        if event["type"] == "tool_result" and event["tool"] == "search_materials"
    ]
    assert len(searches) >= 2
    assert all(event["observation"]["total_matched"] == 0 for event in searches)


def test_real_injected_timeout_is_explicit_and_recovered(tmp_path: Path) -> None:
    _require_real_config()
    injections = parse_injection_specs(
        [{"tool": "search_materials", "error": "timeout", "call": 1}]
    )
    result, trace = _run_real(tmp_path, BASE_INPUT, injections=injections)
    output = result.output or {}
    assert output["tool_errors"]
    assert any(error["injected"] for error in output["tool_errors"])
    assert output["tool_errors"][0]["code"] == "timeout"
    assert output["status"] in ("completed", "failed")
    if output["status"] == "failed":
        assert output["failure"] is not None  # 明确失败，不假装成功
