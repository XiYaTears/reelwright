"""统一 CLI 入口：python -m src.cli run ...（不做交互式输入）。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

from src.agent.runner import RunOptions, RunResult, run
from src.tools.registry import parse_injection_specs

INJECT_PATTERN = re.compile(r"^(?P<tool>[a-z_]+)=(?P<error>[a-z_]+)(?:@(?P<call>\d+))?$")


class CliUsageError(ValueError):
    """CLI 用法错误（退出码 2）。"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="短视频脚本 Agent（非交互式；参数或输入 JSON）",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="生成一份脚本")
    run_parser.add_argument("--input", type=Path, help="输入 JSON 文件")
    run_parser.add_argument("--topic", help="主题")
    run_parser.add_argument("--audience", help="目标受众")
    run_parser.add_argument("--platform", help="平台：douyin/kuaishou/xiaohongshu/shipinhao")
    run_parser.add_argument("--target-duration", type=int, help="目标时长（秒）")
    run_parser.add_argument("--style", help="风格")
    run_parser.add_argument("--notes", help="可选补充信息（输入 JSON 字段 extra_notes）")
    run_parser.add_argument("--offline", action="store_true", help="离线 mock 模式")
    run_parser.add_argument("--out", type=Path, help="输出 JSON 路径")
    run_parser.add_argument("--trace", type=Path, help="trace JSONL 路径")
    run_parser.add_argument("--max-steps", type=int, default=8, help="模型步数上限（1–20）")
    run_parser.add_argument(
        "--inject-failure",
        action="append",
        default=[],
        metavar="TOOL=CODE[@N]",
        help="注入工具失败，可重复；CODE ∈ timeout|unavailable|invalid_response",
    )
    run_parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="数据集目录覆盖（受控实验/测试用）",
    )
    run_parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env.local"),
        help="真实模式环境变量文件（默认项目根 .env.local）",
    )
    return parser


def _read_input_file(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CliUsageError(f"无法读取输入文件 {path}：{exc}") from None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CliUsageError(f"输入 JSON 解析失败：{exc.msg}") from None
    if not isinstance(payload, dict):
        raise CliUsageError("输入 JSON 顶层必须是对象")
    return payload


def _parse_inject_failures(specs: Sequence[str]):
    raw_specs: list[dict[str, Any]] = []
    for spec in specs:
        match = INJECT_PATTERN.fullmatch(spec.strip())
        if match is None:
            raise CliUsageError(f"--inject-failure 格式应为 TOOL=CODE[@N]，实际为：{spec}")
        item: dict[str, Any] = {"tool": match.group("tool"), "error": match.group("error")}
        if match.group("call") is not None:
            item["call"] = int(match.group("call"))
        raw_specs.append(item)
    try:
        return parse_injection_specs(raw_specs)
    except ValueError as exc:
        raise CliUsageError(str(exc)) from None


def _merge_cli_input(args: argparse.Namespace) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    if args.input is not None:
        raw.update(_read_input_file(args.input))
    overrides = {
        "topic": args.topic,
        "audience": args.audience,
        "platform": args.platform,
        "target_duration_s": args.target_duration,
        "style": args.style,
        "extra_notes": args.notes,
    }
    for key, value in overrides.items():
        if value is not None:
            raw[key] = value
    return raw


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "run":  # pragma: no cover - argparse 限制
        parser.error("未知子命令")
    try:
        raw = _merge_cli_input(args)
        injections = _parse_inject_failures(args.inject_failure)
    except CliUsageError as exc:
        print(f"用法错误：{exc}", file=sys.stderr)
        return 2

    options = RunOptions(
        offline=bool(args.offline),
        data_dir=args.data_dir,
        max_steps=args.max_steps,
        out_path=args.out,
        trace_path=args.trace,
        injections=injections,
        env_file=args.env_file,
    )
    result: RunResult = run(raw, options=options)
    if result.output is not None:
        print(json.dumps(result.output, ensure_ascii=False, indent=2))
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return int(result.exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
