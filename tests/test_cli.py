"""CLI 端到端：退出码、stdout JSON、参数/JSON 合并与失败注入。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def _run_cli(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "src.cli", "run", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )


def _write_input(tmp_path: Path, **overrides) -> Path:
    payload = {
        "topic": "字幕功能上线",
        "audience": "剪辑新手",
        "platform": "douyin",
        "target_duration_s": 30,
        "style": "轻快教程",
    }
    payload.update(overrides)
    path = tmp_path / "input.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "output.json", tmp_path / "trace.jsonl"


def test_offline_normal_run_exit_zero_and_json(tmp_path: Path) -> None:
    input_path = _write_input(tmp_path)
    out_path, trace_path = _paths(tmp_path)
    result = _run_cli(
        [
            "--input",
            str(input_path),
            "--offline",
            "--out",
            str(out_path),
            "--trace",
            str(trace_path),
        ]
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["status"] == "completed"
    assert out_path.is_file()
    assert trace_path.is_file()
    assert output["trace_path"] == str(trace_path)


def test_missing_audience_exit_three(tmp_path: Path) -> None:
    input_path = _write_input(tmp_path)
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    payload.pop("audience")
    input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    out_path, trace_path = _paths(tmp_path)
    result = _run_cli(
        ["--input", str(input_path), "--offline", "--out", str(out_path), "--trace", str(trace_path)]
    )
    assert result.returncode == 3
    output = json.loads(result.stdout)
    assert output["status"] == "needs_input"
    assert "audience" in output["needs_input"]["missing_fields"]


def test_unknown_platform_exit_three(tmp_path: Path) -> None:
    input_path = _write_input(tmp_path, platform="bilibili")
    out_path, trace_path = _paths(tmp_path)
    result = _run_cli(
        ["--input", str(input_path), "--offline", "--out", str(out_path), "--trace", str(trace_path)]
    )
    assert result.returncode == 3
    assert json.loads(result.stdout)["status"] == "needs_input"


def test_invalid_input_json_exit_two(tmp_path: Path) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_text("{not json", encoding="utf-8")
    out_path, trace_path = _paths(tmp_path)
    result = _run_cli(
        ["--input", str(input_path), "--offline", "--out", str(out_path), "--trace", str(trace_path)]
    )
    assert result.returncode == 2
    assert "JSON" in result.stderr


def test_invalid_inject_failure_format_exit_two(tmp_path: Path) -> None:
    input_path = _write_input(tmp_path)
    out_path, trace_path = _paths(tmp_path)
    result = _run_cli(
        [
            "--input",
            str(input_path),
            "--offline",
            "--inject-failure",
            "search_materials:timeout",
            "--out",
            str(out_path),
            "--trace",
            str(trace_path),
        ]
    )
    assert result.returncode == 2
    assert "用法错误" in result.stderr


def test_cli_overrides_input_json(tmp_path: Path) -> None:
    input_path = _write_input(tmp_path, audience="输入文件受众")
    out_path, trace_path = _paths(tmp_path)
    result = _run_cli(
        [
            "--input",
            str(input_path),
            "--audience",
            "CLI 受众",
            "--offline",
            "--out",
            str(out_path),
            "--trace",
            str(trace_path),
        ]
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["input"]["audience"] == "CLI 受众"


def test_inject_failure_recovered(tmp_path: Path) -> None:
    input_path = _write_input(tmp_path)
    out_path, trace_path = _paths(tmp_path)
    result = _run_cli(
        [
            "--input",
            str(input_path),
            "--offline",
            "--inject-failure",
            "search_materials=timeout@1",
            "--out",
            str(out_path),
            "--trace",
            str(trace_path),
        ]
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["tool_errors"]
    assert output["tool_errors"][0]["injected"] is True


def test_step_limit_exit_four(tmp_path: Path) -> None:
    input_path = _write_input(tmp_path)
    out_path, trace_path = _paths(tmp_path)
    result = _run_cli(
        [
            "--input",
            str(input_path),
            "--offline",
            "--max-steps",
            "3",
            "--out",
            str(out_path),
            "--trace",
            str(trace_path),
        ]
    )
    assert result.returncode == 4
    assert json.loads(result.stdout)["status"] == "incomplete"


def test_target_60_via_cli(tmp_path: Path) -> None:
    input_path = _write_input(tmp_path, target_duration_s=60)
    out_path, trace_path = _paths(tmp_path)
    result = _run_cli(
        ["--input", str(input_path), "--offline", "--out", str(out_path), "--trace", str(trace_path)]
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["script"]["estimated_total_duration_s"] == 58


def test_data_dir_empty_material_variant(tmp_path: Path) -> None:
    data_dir = tmp_path / "no_hit_data"
    shutil.copytree(DATA_DIR, data_dir)
    materials = json.loads((data_dir / "materials.json").read_text(encoding="utf-8"))
    materials["materials"] = []
    (data_dir / "materials.json").write_text(
        json.dumps(materials, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    input_path = _write_input(tmp_path)
    out_path, trace_path = _paths(tmp_path)
    result = _run_cli(
        [
            "--input",
            str(input_path),
            "--offline",
            "--data-dir",
            str(data_dir),
            "--out",
            str(out_path),
            "--trace",
            str(trace_path),
        ]
    )
    assert result.returncode == 3
    assert json.loads(result.stdout)["status"] == "needs_input"
