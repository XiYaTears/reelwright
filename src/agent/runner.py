"""运行入口：输入校验、模式装配、图调用、输出组装与退出码。

顺序（评审要求）：创建 TraceWriter → run_start → 输入校验（needs_input 也落
run_start/input_validation/run_end）→ 图调用 → 输出 → run_end。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from langgraph.errors import GraphRecursionError

from src.agent.graph import build_graph, graph_recursion_limit
from src.agent.prompt import build_initial_messages
from src.agent.state import Budgets, initial_state, make_budgets
from src.agent.trace import TraceWriter
from src.config import ConfigError, LLMSettings, load_llm_settings
from src.dataset import Dataset, DatasetError, FIXED_PLATFORMS, load_dataset
from src.domain.models import Requirements
from src.domain.script import parse_script
from src.domain.status import EXIT_CODE_BY_STATUS, ExitCode, Status
from src.llm.base import LLMClient, LLMError
from src.llm.deepseek import DeepSeekClient
from src.llm.mock import MockClient
from src.tools.duration import FORMULA_VERSION
from src.tools.registry import InjectionRule, ToolRegistry, parse_injection_specs

INPUT_KEYS: tuple[str, ...] = (
    "topic",
    "audience",
    "platform",
    "target_duration_s",
    "style",
    "extra_notes",
)
MAX_TEXT_LENGTHS = {"topic": 100, "audience": 100, "style": 50}
MIN_TARGET_S = 5
MAX_TARGET_S = 300
MAX_EXTRA_NOTES = 500

LLMFactory = Callable[[Dataset, Requirements], LLMClient]


@dataclass(frozen=True)
class RunOptions:
    offline: bool = False
    data_dir: Path = Path("data")
    max_steps: int = 8
    out_path: Path | None = None
    trace_path: Path | None = None
    injections: tuple[InjectionRule, ...] = ()
    env_file: Path = Path(".env.local")


@dataclass(frozen=True)
class RunResult:
    output: dict[str, Any] | None
    exit_code: int
    stderr: str | None = None


def run(
    raw_input: Mapping[str, Any] | None,
    *,
    options: RunOptions = RunOptions(),
    llm_factory: LLMFactory | None = None,
) -> RunResult:
    raw = dict(raw_input or {})
    try:
        injections = _resolve_injections(raw, options)
        budgets = make_budgets(options.max_steps)
    except ValueError as exc:
        return RunResult(None, ExitCode.USAGE_ERROR, str(exc))

    settings: LLMSettings | None = None
    secrets: tuple[str, ...] = ()
    if not options.offline and llm_factory is None:
        try:
            settings = load_llm_settings(options.env_file)
        except ConfigError as exc:
            return RunResult(None, ExitCode.INTERNAL_ERROR, str(exc))
        secrets = (settings.api_key,)

    try:
        dataset = load_dataset(options.data_dir)
    except DatasetError as exc:
        return RunResult(None, ExitCode.INTERNAL_ERROR, f"数据集错误：{exc}")

    run_id = make_run_id(raw)
    default_dir = Path("runs") / run_id
    out_path = Path(options.out_path) if options.out_path is not None else default_dir / "output.json"
    trace_path = (
        Path(options.trace_path) if options.trace_path is not None else default_dir / "trace.jsonl"
    )
    mode = "offline" if options.offline else "real"
    started = time.perf_counter()
    trace = TraceWriter(trace_path, run_id=run_id, secrets=secrets)
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    budget_info = {
        "max_steps": budgets.max_steps,
        "max_tool_calls": budgets.max_tool_calls,
    }
    trace.event(
        "run_start",
        mode=mode,
        input=_input_echo(raw),
        budget=budget_info,
        data_digest=dataset.data_digest,
        formula_version=FORMULA_VERSION,
        model=(settings.model if settings is not None else ("mock" if options.offline else "injected")),
        injection=[
            {"tool": rule.tool, "error": rule.error, "call": rule.call} for rule in injections
        ],
    )

    requirements, validation = validate_input(raw)
    if requirements is None:
        trace.event(
            "input_validation",
            ok=False,
            missing_fields=validation["missing_fields"],
            invalid_fields=validation["invalid_fields"],
            status=Status.NEEDS_INPUT.value,
        )
        output = _needs_input_output(
            run_id=run_id,
            mode=mode,
            raw_input=raw,
            validation=validation,
            budget=budget_info,
            data_digest=dataset.data_digest,
            trace_path=trace_path,
            generated_at=generated_at,
        )
        _write_json(out_path, output)
        exit_code = int(ExitCode.NEEDS_INPUT)
        trace.event(
            "run_end",
            status=Status.NEEDS_INPUT.value,
            steps_used=0,
            tool_calls_used=0,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            exit_code=exit_code,
        )
        trace.close()
        return RunResult(output, exit_code)

    trace.event("input_validation", ok=True)

    try:
        if llm_factory is not None:
            llm = llm_factory(dataset, requirements)
        elif options.offline:
            llm = MockClient(dataset, requirements)
        else:
            assert settings is not None
            llm = DeepSeekClient(
                api_key=settings.api_key,
                base_url=settings.base_url,
                model=settings.model,
                proxy=settings.proxy,
            )
        registry = ToolRegistry(
            dataset,
            target_duration_s=requirements.target_duration_s,
            platform=requirements.platform,
            injections=injections,
        )
        compiled = build_graph(
            llm=llm,
            registry=registry,
            dataset=dataset,
            requirements=requirements,
            trace=trace,
            budgets=budgets,
        )
        initial = initial_state()
        initial["messages"] = build_initial_messages(requirements, budgets)
        final_state = compiled.invoke(
            initial,
            config={"recursion_limit": graph_recursion_limit(budgets)},
        )
    except LLMError as exc:
        status = Status.FAILED.value
        output = _error_output(
            run_id=run_id,
            mode=mode,
            requirements=requirements,
            budget=budget_info,
            data_digest=dataset.data_digest,
            trace_path=trace_path,
            generated_at=generated_at,
            code="llm_error",
            message=str(exc),
        )
        trace.event("finalize", status=status, failure_code="llm_error")
        _write_json(out_path, output)
        exit_code = int(ExitCode.FAILED)
        trace.event(
            "run_end",
            status=status,
            steps_used=0,
            tool_calls_used=0,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            exit_code=exit_code,
        )
        trace.close()
        return RunResult(output, exit_code)
    except GraphRecursionError:
        status = Status.FAILED.value
        output = _error_output(
            run_id=run_id,
            mode=mode,
            requirements=requirements,
            budget=budget_info,
            data_digest=dataset.data_digest,
            trace_path=trace_path,
            generated_at=generated_at,
            code="orchestration_error",
            message="LangGraph recursion_limit 触发，编排层兜底失败",
        )
        trace.event("finalize", status=status, failure_code="orchestration_error")
        _write_json(out_path, output)
        exit_code = int(ExitCode.FAILED)
        trace.event(
            "run_end",
            status=status,
            steps_used=0,
            tool_calls_used=0,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            exit_code=exit_code,
        )
        trace.close()
        return RunResult(output, exit_code)
    except Exception as exc:  # 未预期内部错误：不伪装成业务状态
        trace.event("run_end", status="internal_error", error=f"{type(exc).__name__}: {exc}")
        trace.close()
        return RunResult(None, ExitCode.INTERNAL_ERROR, f"内部错误：{type(exc).__name__}: {exc}")

    status = str(final_state.get("status") or Status.FAILED.value)
    output = _assemble_output(
        run_id=run_id,
        mode=mode,
        requirements=requirements,
        final_state=final_state,
        budget=budget_info,
        data_digest=dataset.data_digest,
        trace_path=trace_path,
        generated_at=generated_at,
        trace=trace,
    )
    _write_json(out_path, output)
    exit_code = int(EXIT_CODE_BY_STATUS[Status(status)])
    trace.event(
        "run_end",
        status=status,
        steps_used=int(final_state.get("step", 0)),
        tool_calls_used=int(final_state.get("tool_calls_used", 0)),
        duration_ms=round((time.perf_counter() - started) * 1000, 1),
        exit_code=exit_code,
    )
    trace.close()
    return RunResult(output, exit_code)


# ---------------- 输入校验 ----------------


def validate_input(raw: Mapping[str, Any]) -> tuple[Requirements | None, dict[str, Any]]:
    missing: list[str] = []
    invalid: list[str] = []
    values: dict[str, Any] = {}

    for key, max_length in MAX_TEXT_LENGTHS.items():
        value = raw.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(key)
            continue
        if not isinstance(value, str):
            invalid.append(key)
            continue
        trimmed = value.strip()
        if len(trimmed) > max_length:
            invalid.append(key)
            continue
        values[key] = trimmed

    platform = raw.get("platform")
    if platform is None or platform == "":
        missing.append("platform")
    elif platform not in FIXED_PLATFORMS:
        invalid.append("platform")
    else:
        values["platform"] = platform

    target = raw.get("target_duration_s")
    if target is None:
        missing.append("target_duration_s")
    elif (
        not isinstance(target, int)
        or isinstance(target, bool)
        or not MIN_TARGET_S <= target <= MAX_TARGET_S
    ):
        invalid.append("target_duration_s")
    else:
        values["target_duration_s"] = target

    extra_notes = raw.get("extra_notes")
    if extra_notes is not None:
        if not isinstance(extra_notes, str) or len(extra_notes) > MAX_EXTRA_NOTES:
            invalid.append("extra_notes")
        else:
            values["extra_notes"] = extra_notes

    if missing or invalid:
        question_parts: list[str] = []
        if missing:
            question_parts.append(f"请补充缺失字段：{', '.join(missing)}")
        if invalid:
            question_parts.append(f"请修正非法字段：{', '.join(invalid)}")
        return None, {
            "missing_fields": missing,
            "invalid_fields": invalid,
            "question": "；".join(question_parts) + "。",
        }

    return (
        Requirements(
            topic=values["topic"],
            audience=values["audience"],
            platform=values["platform"],
            target_duration_s=values["target_duration_s"],
            style=values["style"],
            extra_notes=values.get("extra_notes"),
        ),
        {"missing_fields": [], "invalid_fields": []},
    )


def _resolve_injections(
    raw: Mapping[str, Any], options: RunOptions
) -> tuple[InjectionRule, ...]:
    from_raw: tuple[InjectionRule, ...] = ()
    if "failure_injection" in raw:
        raw_specs = raw.get("failure_injection")
        if raw_specs is None:
            raw_specs = []
        if not isinstance(raw_specs, list):
            raise ValueError("failure_injection 必须是数组")
        from_raw = parse_injection_specs(raw_specs)
    combined = list(options.injections) + list(from_raw)
    seen: set[str] = set()
    for rule in combined:
        if rule.tool in seen:
            raise ValueError(f"工具注入规则重复：{rule.tool}")
        seen.add(rule.tool)
    return tuple(combined)


# ---------------- 输出组装 ----------------


def _input_echo(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {key: raw[key] for key in INPUT_KEYS if key in raw}


def _requirements_dict(requirements: Requirements) -> dict[str, Any]:
    return {
        "topic": requirements.topic,
        "audience": requirements.audience,
        "platform": requirements.platform,
        "target_duration_s": requirements.target_duration_s,
        "style": requirements.style,
        "extra_notes": requirements.extra_notes,
    }


def _base_output(
    *,
    run_id: str,
    mode: str,
    status: str,
    input_echo: dict[str, Any],
    budget: dict[str, int],
    data_digest: str,
    trace_path: Path,
    generated_at: str,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "mode": mode,
        "status": status,
        "input": input_echo,
        "script": None,
        "checks": None,
        "needs_input": None,
        "failure": None,
        "tool_errors": [],
        "skipped_tool_calls": [],
        "risks": [],
        "budget": {
            "max_steps": budget["max_steps"],
            "steps_used": 0,
            "max_tool_calls": budget["max_tool_calls"],
            "tool_calls_used": 0,
        },
        "data_digest": data_digest,
        "trace_path": str(trace_path),
        "generated_at": generated_at,
    }


def _needs_input_output(
    *,
    run_id: str,
    mode: str,
    raw_input: Mapping[str, Any],
    validation: dict[str, Any],
    budget: dict[str, int],
    data_digest: str,
    trace_path: Path,
    generated_at: str,
) -> dict[str, Any]:
    output = _base_output(
        run_id=run_id,
        mode=mode,
        status=Status.NEEDS_INPUT.value,
        input_echo=_input_echo(raw_input),
        budget=budget,
        data_digest=data_digest,
        trace_path=trace_path,
        generated_at=generated_at,
    )
    output["needs_input"] = {
        "missing_fields": validation["missing_fields"],
        "invalid_fields": validation["invalid_fields"],
        "question": validation["question"],
    }
    return output


def _error_output(
    *,
    run_id: str,
    mode: str,
    requirements: Requirements,
    budget: dict[str, int],
    data_digest: str,
    trace_path: Path,
    generated_at: str,
    code: str,
    message: str,
) -> dict[str, Any]:
    output = _base_output(
        run_id=run_id,
        mode=mode,
        status=Status.FAILED.value,
        input_echo=_requirements_dict(requirements),
        budget=budget,
        data_digest=data_digest,
        trace_path=trace_path,
        generated_at=generated_at,
    )
    output["failure"] = {"code": code, "message": message}
    return output


def _assemble_output(
    *,
    run_id: str,
    mode: str,
    requirements: Requirements,
    final_state: Mapping[str, Any],
    budget: dict[str, int],
    data_digest: str,
    trace_path: Path,
    generated_at: str,
    trace: TraceWriter,
) -> dict[str, Any]:
    status = str(final_state.get("status") or Status.FAILED.value)
    tool_errors = list(final_state.get("tool_errors") or [])
    skipped = list(final_state.get("skipped_tool_calls") or [])
    checks = final_state.get("final_checks")
    draft = final_state.get("draft")
    finish_outcome = final_state.get("finish_outcome")
    risks = _build_risks(
        checks=checks,
        tool_errors=tool_errors,
        finish_outcome=finish_outcome,
        status=status,
        trace_events=trace.events,
    )

    script_payload: dict[str, Any] | None = None
    if isinstance(draft, dict) and isinstance(checks, dict):
        script = parse_script(draft, source="output")
        estimate_payload = checks["estimate"]
        per_shot = {item["index"]: item for item in estimate_payload["per_shot"]}
        shots_payload: list[dict[str, Any]] = []
        citations: dict[tuple[str, str, str], dict[str, Any]] = {}
        for index, shot in enumerate(script.shots, start=1):
            duration = per_shot[index]
            shots_payload.append(
                {
                    "index": index,
                    "visual": shot.visual,
                    "narration": shot.narration,
                    "caption": shot.caption,
                    "duration_s": duration["duration_s"],
                    "duration_breakdown": {
                        "narration_chars": duration["narration_chars"],
                        "caption_chars": duration["caption_chars"],
                        "narration_s": duration["narration_s"],
                        "caption_s": duration["caption_s"],
                    },
                    "citations": [citation.to_dict() for citation in shot.citations],
                }
            )
            for citation in shot.citations:
                key = (citation.material_id, citation.fact_id, citation.quote)
                entry = citations.setdefault(
                    key,
                    {
                        "material_id": citation.material_id,
                        "fact_id": citation.fact_id,
                        "quote": citation.quote,
                        "shot_indexes": [],
                    },
                )
                if index not in entry["shot_indexes"]:
                    entry["shot_indexes"].append(index)
        script_payload = {
            "title": script.title,
            "platform": requirements.platform,
            "style": requirements.style,
            "target_duration_s": requirements.target_duration_s,
            "window": estimate_payload["window"],
            "shots": shots_payload,
            "estimated_total_duration_s": estimate_payload["total_duration_s"],
            "citations": list(citations.values()),
            "risks": risks,
        }

    output = _base_output(
        run_id=run_id,
        mode=mode,
        status=status,
        input_echo=_requirements_dict(requirements),
        budget=budget,
        data_digest=data_digest,
        trace_path=trace_path,
        generated_at=generated_at,
    )
    output.update(
        {
            "script": script_payload,
            "checks": checks,
            "needs_input": final_state.get("pending_question"),
            "failure": final_state.get("failure"),
            "tool_errors": tool_errors,
            "skipped_tool_calls": skipped,
            "risks": risks,
            "budget": {
                "max_steps": budget["max_steps"],
                "steps_used": int(final_state.get("step", 0)),
                "max_tool_calls": budget["max_tool_calls"],
                "tool_calls_used": int(final_state.get("tool_calls_used", 0)),
            },
        }
    )
    return output


def _build_risks(
    *,
    checks: Mapping[str, Any] | None,
    tool_errors: list[dict[str, Any]],
    finish_outcome: str | None,
    status: str,
    trace_events: tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    if isinstance(checks, Mapping):
        rules = checks.get("rules") or {}
        for violation in rules.get("violations") or []:
            if violation.get("severity") == "warning":
                risks.append(
                    {
                        "category": violation.get("category"),
                        "severity": "warning",
                        "rule_id": violation.get("rule_id"),
                        "shot_index": violation.get("shot_index"),
                        "message": violation.get("message"),
                    }
                )
        mismatch = checks.get("mismatch") or {}
        if mismatch.get("estimate") or mismatch.get("rules"):
            risks.append(
                {
                    "category": "trace",
                    "severity": "warning",
                    "message": "模型检查的脚本与最终提交稿不一致，最终判定已按提交稿独立复算",
                }
            )
    if tool_errors:
        counts: dict[str, int] = {}
        for error in tool_errors:
            key = f"{error.get('tool')}:{error.get('code')}"
            counts[key] = counts.get(key, 0) + 1
        detail = "；".join(f"{key} ×{count}" for key, count in sorted(counts.items()))
        risks.append({"category": "tool", "severity": "warning", "message": f"工具失败历史：{detail}"})
    if finish_outcome == "cannot_satisfy" and status == Status.COMPLETED.value:
        risks.append(
            {
                "category": "model",
                "severity": "warning",
                "message": "模型声明无法满足，但编排层复算通过，按复算结果判 completed",
            }
        )
    missing_notes = sum(
        1
        for event in trace_events
        if event.get("type") == "model_response" and event.get("decision_note_source") == "missing"
    )
    if missing_notes:
        risks.append(
            {
                "category": "trace",
                "severity": "warning",
                "message": (
                    f"{missing_notes} 步模型未提供可见 decision_note；"
                    "trace 中的 decision_summary 为编排层事实摘要（来源 orchestrator），不是模型输出"
                ),
            }
        )
    return risks


# ---------------- 其他 ----------------


def make_run_id(raw: Mapping[str, Any], now: datetime | None = None) -> str:
    timestamp = (now or datetime.now().astimezone()).strftime("%Y%m%dT%H%M%S%z")
    canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:6]
    return f"{timestamp}-{digest}"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
