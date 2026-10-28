"""examples/ 证据结构与一致性（marker: evidence；第三轮后默认执行）。"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.evidence

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"

PREFIX_RE = re.compile(r"^\$env:PYTHONUTF8=1;\s*")
LAUNCHER_RE = re.compile(r"^\.\\\.venv\\Scripts\\python\.exe\s+-m\s+src\.cli\s+")
STATUS_EXIT_CODES = {"completed": 0, "needs_input": 3, "incomplete": 4, "failed": 5}


def _case_dirs() -> list[Path]:
    return sorted(path.parent for path in EXAMPLES.rglob("input.json"))


def _offline_commands() -> list[Path]:
    return sorted(EXAMPLES.rglob("command.offline.txt"))


def _parse_command(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8").strip()
    text = PREFIX_RE.sub("", text).strip()
    match = LAUNCHER_RE.match(text)
    assert match is not None, f"{path} 启动前缀不符合约定"
    return shlex.split(text[match.end() :].strip())


def _run_command_with_temp_paths(command_path: Path, tmp_path: Path) -> tuple[int, Path, Path]:
    args = _parse_command(command_path)
    output_path = tmp_path / "output.json"
    trace_path = tmp_path / "trace.jsonl"
    rewritten: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg == "--out":
            rewritten.extend(["--out", str(output_path)])
            skip_next = True
        elif arg == "--trace":
            rewritten.extend(["--trace", str(trace_path)])
            skip_next = True
        else:
            rewritten.append(arg)
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    process = subprocess.run(
        [sys.executable, "-m", "src.cli", *rewritten],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    return process.returncode, output_path, trace_path


def test_examples_root_has_readme_and_test_results() -> None:
    assert (EXAMPLES / "README.md").is_file()
    assert (EXAMPLES / "pytest-offline.txt").is_file()
    assert (EXAMPLES / "pytest-real.txt").is_file()
    assert _case_dirs(), "examples 下没有任何证据用例"


@pytest.mark.parametrize("case_dir", _case_dirs(), ids=lambda path: str(path.relative_to(EXAMPLES)))
def test_case_evidence_consistency(case_dir: Path) -> None:
    assert (case_dir / "README.md").is_file(), f"{case_dir} 缺少 README.md"
    outputs = sorted(case_dir.glob("output.*.json"))
    assert outputs, f"{case_dir} 缺少 output.<mode>.json"
    for output_path in outputs:
        mode = output_path.name.split(".")[1]
        trace_path = case_dir / f"trace.{mode}.jsonl"
        command_path = case_dir / f"command.{mode}.txt"
        assert trace_path.is_file(), f"{case_dir} 缺少 {trace_path.name}"
        assert command_path.is_file(), f"{case_dir} 缺少 {command_path.name}"

        output = json.loads(output_path.read_text(encoding="utf-8"))
        events = [
            json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert events, f"{trace_path} 为空"
        assert events[0]["type"] == "run_start"
        assert events[-1]["type"] == "run_end"
        assert output["mode"] == mode == events[0]["mode"]
        assert output["run_id"] == events[0]["run_id"]
        assert output["status"] == events[-1]["status"]
        if mode == "real":
            assert events[0].get("model") and events[0]["model"] != "mock"
        if mode == "offline":
            assert "只证明流程" in (case_dir / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "command_path", _offline_commands(), ids=lambda path: str(path.relative_to(EXAMPLES))
)
def test_offline_command_reproduces_archived_evidence(command_path: Path, tmp_path: Path) -> None:
    """全部离线 command 在临时目录复跑，逐条对比归档 output（offline 确定性）。"""
    archived_output_path = command_path.parent / "output.offline.json"
    assert archived_output_path.is_file()
    archived = json.loads(archived_output_path.read_text(encoding="utf-8"))

    exit_code, fresh_output_path, fresh_trace_path = _run_command_with_temp_paths(
        command_path, tmp_path
    )
    assert exit_code == STATUS_EXIT_CODES[archived["status"]], command_path
    fresh = json.loads(fresh_output_path.read_text(encoding="utf-8"))

    assert fresh["status"] == archived["status"]
    assert fresh["budget"]["steps_used"] == archived["budget"]["steps_used"]
    assert fresh["budget"]["tool_calls_used"] == archived["budget"]["tool_calls_used"]
    assert len(fresh["skipped_tool_calls"]) == len(archived["skipped_tool_calls"])
    if archived["script"] is not None:
        assert (
            fresh["script"]["estimated_total_duration_s"]
            == archived["script"]["estimated_total_duration_s"]
        )
    else:
        assert fresh["script"] is None
    assert [(e["tool"], e["code"], e["injected"]) for e in fresh["tool_errors"]] == [
        (e["tool"], e["code"], e["injected"]) for e in archived["tool_errors"]
    ]
    for key in ("missing_fields", "invalid_fields"):
        assert (fresh.get("needs_input") or {}).get(key) == (
            archived.get("needs_input") or {}
        ).get(key)

    fresh_events = [
        json.loads(line)
        for line in fresh_trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    model_events = [event for event in fresh_events if event["type"] == "model_response"]
    if model_events:
        assert all(event.get("response_id") for event in model_events)
