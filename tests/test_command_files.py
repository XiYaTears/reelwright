"""examples/**/command.*.txt 必须可直接复跑：语法、模式与输出路径一致性。

把命令文件里的启动前缀剥掉，交给 CLI 的真实 argparse 解析；能提前拦住
`python -m src.cli run run ...` 这类低级错误。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from src.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
EXPECTED_COMMAND_COUNT = 19

PREFIX_RE = re.compile(r"^\$env:PYTHONUTF8=1;\s*")
LAUNCHER_RE = re.compile(r"^\.\\\.venv\\Scripts\\python\.exe\s+-m\s+src\.cli\s+")
MODE_RE = re.compile(r"^command\.(?P<mode>[a-z]+)\.txt$")


def _command_files() -> list[Path]:
    return sorted(EXAMPLES.rglob("command.*.txt"))


def _parse_command(path: Path) -> tuple[str, list[str]]:
    text = path.read_text(encoding="utf-8").strip()
    text = PREFIX_RE.sub("", text).strip()
    match = LAUNCHER_RE.match(text)
    assert match is not None, f"{path} 启动前缀不符合约定：{text[:80]}"
    rest = text[match.end() :].strip()
    args = shlex.split(rest)
    return rest, args


def _expected_input(command_path: Path, case_dir: Path) -> Path:
    direct = case_dir / "input.json"
    if direct.is_file():
        return direct
    if case_dir.relative_to(EXAMPLES).as_posix() == "05_over_duration/real":
        return EXAMPLES / "05_over_duration/offline/input.json"
    candidates = sorted(case_dir.parent.rglob("input.json"))
    assert candidates, f"{case_dir} 找不到输入文件"
    return candidates[0]


def test_command_files_exist_and_are_parseable() -> None:
    files = _command_files()
    assert len(files) == EXPECTED_COMMAND_COUNT
    for path in files:
        mode = MODE_RE.match(path.name).group("mode")
        rest, args = _parse_command(path)
        assert args, f"{path} 参数为空"
        assert args[0] == "run", f"{path} 子命令必须是 run"
        assert args.count("run") == 1, f"{path} 出现重复 run：{rest}"
        try:
            namespace = build_parser().parse_args(args)
        except SystemExit as exc:  # argparse 用法错误
            raise AssertionError(f"{path} 无法解析（SystemExit {exc.code}）：{rest}") from None
        assert namespace.command == "run"
        assert namespace.offline is (mode == "offline"), (
            f"{path} 的 --offline 与文件名模式 {mode} 不一致"
        )
        case_dir = path.parent
        expected_out = (case_dir / f"output.{mode}.json").relative_to(ROOT).as_posix()
        expected_trace = (case_dir / f"trace.{mode}.jsonl").relative_to(ROOT).as_posix()
        expected_input = _expected_input(path, case_dir).relative_to(ROOT).as_posix()
        assert Path(namespace.input).as_posix() == expected_input
        assert Path(namespace.out).as_posix() == expected_out
        assert Path(namespace.trace).as_posix() == expected_trace
        command_text = path.read_text(encoding="utf-8")
        rel = path.relative_to(EXAMPLES).as_posix()
        if "04_tool_failure/a_recoverable/" in rel:
            assert "--inject-failure search_materials=timeout@1" in command_text
        elif "04_tool_failure/b_persistent/" in rel:
            assert "--inject-failure search_materials=unavailable" in command_text
        elif "07_step_limit/" in rel:
            assert "--max-steps 3" in command_text
        elif "09_controlled_retrieval/no_hit/" in rel:
            assert "--data-dir examples/09_controlled_retrieval/no_hit_data" in command_text
        else:
            assert "--inject-failure" not in args and "--data-dir" not in args


@pytest.mark.parametrize(
    "case_rel",
    [
        "01_normal/command.offline.txt",
        "04_tool_failure/a_recoverable/command.offline.txt",
    ],
)
def test_command_runs_end_to_end_with_temp_outputs(tmp_path: Path, case_rel: str) -> None:
    command_path = EXAMPLES / case_rel
    _, args = _parse_command(command_path)
    mode = MODE_RE.match(command_path.name).group("mode")
    output_path = tmp_path / f"output.{mode}.json"
    trace_path = tmp_path / f"trace.{mode}.jsonl"
    rewritten: list[str] = []
    skip_next = False
    for index, arg in enumerate(args):
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
    result = subprocess.run(
        [sys.executable, "-m", "src.cli", *rewritten],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert result.returncode == 0, f"{command_path} 复跑失败：{result.stderr[-500:]}"
    output = json.loads(output_path.read_text(encoding="utf-8"))
    assert output["status"] == "completed"
    first_line = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[0])
    assert first_line["type"] == "run_start"
    if "a_recoverable" in case_rel:
        assert any(error["injected"] for error in output["tool_errors"])
