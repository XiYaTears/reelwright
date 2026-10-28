"""TraceWriter：JSONL、脱敏、禁止 reasoning_content。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.agent.trace import MASK, TraceWriter


def test_writes_jsonl_with_common_fields_and_sanitizes(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    with TraceWriter(path, run_id="run-1", secrets=["sk-secret"]) as trace:
        trace.event(
            "run_start",
            mode="offline",
            input={"topic": "字幕"},
            api_key="sk-secret",
            nested={"reasoning_content": "内部思考", "note": "key=sk-secret"},
        )
        trace.event("tool_result", step=1, tool="search_materials", ok=True, observation={"total": 0})
        assert len(trace.events) == 2

    raw = path.read_text(encoding="utf-8")
    assert "sk-secret" not in raw
    assert "reasoning_content" not in raw
    assert "内部思考" not in raw
    lines = raw.splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["seq"] == 1
    assert first["run_id"] == "run-1"
    assert first["type"] == "run_start"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}", first["ts"])
    assert "api_key" not in first
    assert first["nested"] == {"note": f"key={MASK}"}
    assert first["input"] == {"topic": "字幕"}

    second = json.loads(lines[1])
    assert second["step"] == 1
    assert second["tool"] == "search_materials"
    assert "step" not in first


def test_handles_nested_lists_and_forbidden_keys(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    trace = TraceWriter(path, run_id="run-2")
    trace.event(
        "model_response",
        step=1,
        tool_calls=[{"name": "x", "arguments": {"thinking": "secret", "ok": 1}}],
        usage={"prompt_tokens": 1},
    )
    trace.close()
    record = json.loads(path.read_text(encoding="utf-8").strip())
    assert "thinking" not in json.dumps(record)
    assert record["tool_calls"] == [{"name": "x", "arguments": {"ok": 1}}]


def test_new_writer_overwrites_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    path.write_text("old-content\n", encoding="utf-8")
    with TraceWriter(path, run_id="run-3") as trace:
        trace.event("run_start")
    assert path.read_text(encoding="utf-8").strip() != "old-content"


def test_file_is_utf8_without_bom(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    with TraceWriter(path, run_id="run-4") as trace:
        trace.event("run_start", note="中文")
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    raw.decode("utf-8")
