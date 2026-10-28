"""examples/ 证据结构与一致性（marker: evidence；第三轮后默认执行）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.evidence

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def _case_dirs() -> list[Path]:
    return sorted(path.parent for path in EXAMPLES.rglob("input.json"))


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
