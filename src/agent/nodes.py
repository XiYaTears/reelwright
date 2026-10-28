"""LangGraph 节点：agent / tools / finalize。

所有副作用（trace、工具调用、草稿状态）都限制在节点内；finalize 不信任模型提交的工具结果，
对最新草稿用纯函数独立复算。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from typing import Any

from src.agent.state import Budgets, RunState
from src.agent.trace import TraceWriter
from src.dataset import Dataset
from src.domain.models import Requirements, Script
from src.domain.script import (
    ScriptValidationError,
    parse_script,
    script_digest,
    script_to_dict,
)
from src.domain.status import FailureCode, Status
from src.llm.base import LLMClient, LLMResponse, ToolCall
from src.tools.duration import estimate
from src.tools.registry import TOOL_SPECS, ToolExecutionError, ToolRegistry
from src.tools.rules import check

TERMINAL_FINISH = "finish"
TERMINAL_ASK_USER = "ask_user"
TERMINAL_NO_ACTION = "no_action"
TERMINAL_INVALID = "invalid"
FINISH_OUTCOMES = frozenset({"completed_intent", "cannot_satisfy"})
MAX_DECISION_NOTE_CHARS = 40


def _decision_note(content: str) -> tuple[str, bool]:
    text = (content or "").strip()
    note = text[:MAX_DECISION_NOTE_CHARS]
    return note, not note


def _terminal_from_calls(calls: tuple[ToolCall, ...]) -> str | None:
    names = {call.name for call in calls}
    if not calls:
        return TERMINAL_NO_ACTION
    has_finish = TERMINAL_FINISH in names
    has_ask = TERMINAL_ASK_USER in names
    if has_finish and has_ask:
        return TERMINAL_INVALID
    if has_finish:
        return TERMINAL_FINISH
    if has_ask:
        return TERMINAL_ASK_USER
    return None


def _assistant_message(response: LLMResponse) -> dict[str, Any]:
    assistant: dict[str, Any] = {"role": "assistant", "content": response.content or ""}
    if response.tool_calls:
        assistant["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": call.raw_arguments
                    or json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in response.tool_calls
        ]
    return assistant


def _parse_arguments(raw: str) -> tuple[dict[str, Any], str | None]:
    if not isinstance(raw, str) or not raw.strip():
        return {}, "tool_call.arguments 为空或不是字符串"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"tool_call.arguments 不是合法 JSON：{exc.msg}"
    if not isinstance(parsed, dict):
        return {}, "tool_call.arguments 不是 JSON 对象"
    return parsed, None


def _summarize_observation(tool: str, observation: dict[str, Any]) -> dict[str, Any]:
    if isinstance(observation.get("error"), dict):
        error = observation.get("error") or {}
        return {"error": {"code": error.get("code"), "retryable": error.get("retryable")}}
    if tool == "search_materials":
        material_ids = [
            material.get("material_id")
            for material in observation.get("materials", [])
            if isinstance(material, dict)
        ]
        return {"total_matched": observation.get("total_matched"), "material_ids": material_ids}
    if tool == "estimate_duration":
        return {
            "total_duration_s": observation.get("total_duration_s"),
            "verdict": observation.get("verdict"),
            "within": observation.get("within"),
            "window": observation.get("window"),
        }
    if tool == "check_rules":
        violations = observation.get("violations") or []
        return {
            "ok": observation.get("ok"),
            "errors": (observation.get("stats") or {}).get("errors"),
            "rule_ids": sorted({item.get("rule_id") for item in violations if isinstance(item, dict)}),
            "shot_indexes": sorted(
                {
                    item.get("shot_index")
                    for item in violations
                    if isinstance(item, dict) and item.get("shot_index") is not None
                }
            ),
        }
    if tool == "submit_script":
        return {"revision": observation.get("revision"), "shots": observation.get("shots")}
    if tool in ("ask_user", "finish"):
        return {"ok": True, "outcome": observation.get("outcome")}
    return {}


def _decision_hint(tool: str, observation: dict[str, Any]) -> str:
    if isinstance(observation.get("error"), dict):
        return "tool_error:retry_or_escalate"
    if tool == "search_materials":
        return "no_hits:rewrite_or_ask_user" if observation.get("total_matched") == 0 else "materials_found:write_script"
    if tool == "estimate_duration":
        verdict = observation.get("verdict")
        if verdict == "too_long":
            return "too_long:compress_and_reestimate"
        if verdict == "too_short":
            return "too_short:expand_and_reestimate"
        return "within:run_rules_check"
    if tool == "check_rules":
        return "rule_errors:fix_script" if observation.get("ok") is False else "rules_ok:maybe_finish"
    return "state_action"


def _shots_text_digest(shots: list[dict[str, Any]]) -> str:
    payload = [
        {"narration": shot.get("narration", ""), "caption": shot.get("caption", "")}
        for shot in shots
    ]
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _describe_previous_observation(observation: dict[str, Any]) -> str:
    """只拼接上一步实际观察摘要，不推断模型动机。"""
    tool = observation.get("tool") or "unknown_tool"
    summary = observation.get("summary") or {}
    if observation.get("ok") is False:
        error = summary.get("error") or {}
        code = error.get("code") or "error"
        return f"{tool} 返回错误({code})"
    if tool == "search_materials":
        total = summary.get("total_matched")
        return f"{tool} 命中 {total} 条素材" if total else f"{tool} 命中 0 条素材"
    if tool == "estimate_duration":
        total = summary.get("total_duration_s")
        verdict = summary.get("verdict")
        window = summary.get("window") or {}
        return f"{tool} 返回 {verdict}({total}s, 区间 {window.get('min_s')}-{window.get('max_s')})"
    if tool == "check_rules":
        return f"{tool} 返回 ok={summary.get('ok')}(errors={summary.get('errors')})"
    if tool == "submit_script":
        return f"{tool} 已提交第 {summary.get('revision')} 稿({summary.get('shots')} 镜)"
    if tool in ("ask_user", "finish"):
        return f"{tool} 已执行"
    return f"{tool} 已执行"


def _describe_current_call(call: ToolCall) -> str:
    """只拼接本步实际工具名与关键参数。"""
    arguments = call.arguments
    if call.name == "search_materials":
        query = arguments.get("query")
        return f'search_materials(query="{query}")'
    if call.name == "submit_script":
        shots = arguments.get("shots")
        count = len(shots) if isinstance(shots, list) else "?"
        return f"submit_script(shots={count})"
    if call.name in ("estimate_duration", "check_rules"):
        shots = arguments.get("shots")
        count = len(shots) if isinstance(shots, list) else "?"
        return f"{call.name}(shots={count})"
    if call.name == "ask_user":
        fields = arguments.get("missing_fields")
        return f"ask_user(missing_fields={fields})"
    if call.name == "finish":
        return f"finish(outcome={arguments.get('outcome')})"
    return call.name


def _build_decision_summary(
    previous_observations: list[dict[str, Any]] | None,
    calls: tuple[ToolCall, ...],
) -> str:
    action = "；".join(_describe_current_call(call) for call in calls) or "无工具调用"
    if not previous_observations:
        if any(call.name == "search_materials" for call in calls):
            return "首步：检索主题相关素材"
        return f"首步：{action}"
    previous = "；".join(
        _describe_previous_observation(observation) for observation in previous_observations
    )
    summary = f"上一步 {previous} → 本步 {action}"
    return summary[:160]


def make_agent_node(
    *,
    llm: LLMClient,
    trace: TraceWriter,
) -> Callable[[RunState], dict[str, Any]]:
    def agent_node(state: RunState) -> dict[str, Any]:
        messages = list(state.get("messages", []))
        step = int(state.get("step", 0)) + 1
        response = llm.chat(messages, TOOL_SPECS)
        note, note_missing = _decision_note(response.content)
        decision_summary = _build_decision_summary(state.get("last_observations"), response.tool_calls)
        tool_calls = [
            {
                "name": call.name,
                "arguments": call.arguments if call.arguments_error is None else call.raw_arguments,
            }
            for call in response.tool_calls
        ]
        trace.event(
            "model_response",
            step=step,
            response_id=response.response_id,
            decision_note=note,
            decision_note_source="missing" if note_missing else "model",
            decision_summary=decision_summary,
            decision_summary_source="orchestrator",
            content_excerpt=response.content[:200],
            tool_calls=tool_calls,
            finish_reason=response.finish_reason,
            usage={
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
            },
        )
        terminal = _terminal_from_calls(response.tool_calls)
        return {
            "messages": [_assistant_message(response)],
            "step": step,
            "terminal": terminal,
        }

    return agent_node


def make_tool_node(
    *,
    registry: ToolRegistry,
    trace: TraceWriter,
    budgets: Budgets,
) -> Callable[[RunState], dict[str, Any]]:
    def tool_node(state: RunState) -> dict[str, Any]:
        messages = list(state.get("messages", []))
        assistant = messages[-1] if messages else {}
        raw_calls = assistant.get("tool_calls") or [] if isinstance(assistant, dict) else []
        step = int(state.get("step", 0))
        used = int(state.get("tool_calls_used", 0))

        # 批执行前预检：整批会超工具调用上限时，不执行本批，记录未执行调用后收尾。
        if used + len(raw_calls) > budgets.max_tool_calls:
            skipped = [
                {
                    "id": (raw.get("id") if isinstance(raw, dict) else None),
                    "name": (
                        (raw.get("function") or {}).get("name")
                        if isinstance(raw, dict) and isinstance(raw.get("function"), dict)
                        else None
                    ),
                }
                for raw in raw_calls
            ]
            trace.event(
                "skipped_tool_calls",
                step=step,
                used=used,
                max_tool_calls=budgets.max_tool_calls,
                skipped=skipped,
            )
            # 未执行的批不能保留 agent 写入的 finish/ask_user 语义：强制走预算收尾。
            return {
                "skipped_tool_calls": skipped,
                "tool_calls_used": used,
                "terminal": None,
            }

        new_messages: list[dict[str, Any]] = []
        tool_errors: list[dict[str, Any]] = []
        observations: list[dict[str, Any]] = []
        draft = state.get("draft")
        draft_revision = int(state.get("draft_revision", 0))
        draft_digest = state.get("draft_digest")
        last_estimate = state.get("last_estimate")
        last_rule_report = state.get("last_rule_report")
        estimate_digest = state.get("estimate_digest")
        rules_digest = state.get("rules_digest")
        pending_question = state.get("pending_question")
        finish_outcome = state.get("finish_outcome")
        control_action_results: dict[str, bool] = {}

        for raw in raw_calls:
            if not isinstance(raw, dict):
                continue
            call_id = raw.get("id") or f"call-{step}-{len(new_messages) + 1}"
            function = raw.get("function") or {}
            name = function.get("name") if isinstance(function, dict) else None
            raw_arguments = function.get("arguments") if isinstance(function, dict) else None
            started = time.perf_counter()

            def append_observation(observation: dict[str, Any], *, tool_name: str | None) -> None:
                tool_failed = isinstance(observation.get("error"), dict)
                new_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(observation, ensure_ascii=False),
                    }
                )
                trace.event(
                    "tool_result",
                    step=step,
                    tool=tool_name,
                    call_id=call_id,
                    ok=not tool_failed,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                    injected=bool(observation.get("_injected")),
                    error=observation.get("error"),
                    observation=_summarize_observation(tool_name or "", observation),
                    decision_hint=_decision_hint(tool_name or "", observation),
                )
                if tool_failed:
                    error = observation.get("error") or {}
                    tool_errors.append(
                        {
                            "tool": tool_name or "",
                            "code": error.get("code"),
                            "message": error.get("message"),
                            "call_index": (
                                registry.call_counts.get(tool_name, None)
                                if tool_name in registry.call_counts
                                else None
                            ),
                            "injected": bool(observation.get("_injected")),
                            "step": step,
                            "retryable": bool(error.get("retryable")),
                        }
                    )
                observations.append(
                    {
                        "tool": tool_name or "unknown_tool",
                        "ok": not tool_failed,
                        "summary": _summarize_observation(tool_name or "", observation),
                    }
                )

            if not isinstance(name, str) or not name:
                append_observation(
                    {"ok": False, "error": {"code": "invalid_arguments", "message": "tool_call 缺少工具名", "retryable": False}},
                    tool_name=None,
                )
                continue

            if name in ("submit_script", "ask_user", "finish"):
                observation, effects = _handle_control_action(
                    name,
                    raw_arguments,
                    draft=draft,
                    draft_revision=draft_revision,
                    pending_question=pending_question,
                    finish_outcome=finish_outcome,
                )
                if effects:
                    draft = effects.get("draft", draft)
                    draft_revision = effects.get("draft_revision", draft_revision)
                    draft_digest = effects.get("draft_digest", draft_digest)
                    pending_question = effects.get("pending_question", pending_question)
                    finish_outcome = effects.get("finish_outcome", finish_outcome)
                    if "draft" in effects:
                        last_estimate = None
                        last_rule_report = None
                        estimate_digest = None
                        rules_digest = None
                append_observation(observation, tool_name=name)
                control_action_results[name] = bool(observation.get("ok"))
                continue

            if name not in ("search_materials", "estimate_duration", "check_rules"):
                append_observation(
                    {"ok": False, "error": {"code": "unknown_tool", "message": f"未知工具：{name}", "retryable": False}},
                    tool_name=name,
                )
                continue

            args, parsing_error = _parse_arguments(raw_arguments)
            if parsing_error is not None:
                append_observation(
                    {"ok": False, "error": {"code": "invalid_arguments", "message": parsing_error, "retryable": False}},
                    tool_name=name,
                )
                continue

            try:
                result = registry.execute(name, args)
            except ToolExecutionError as exc:
                observation = exc.to_observation()
                observation["_injected"] = exc.injected
                append_observation(observation, tool_name=name)
                continue

            append_observation(result, tool_name=name)
            if name == "estimate_duration":
                last_estimate = result
                shots = args.get("shots") if isinstance(args.get("shots"), list) else []
                estimate_digest = _shots_text_digest([shot for shot in shots if isinstance(shot, dict)])
            elif name == "check_rules":
                last_rule_report = result
                try:
                    rules_digest = script_digest(parse_script(args, source="check_rules"))
                except ScriptValidationError:
                    rules_digest = None

        # 控制动作校验失败不能提前收尾：清空 terminal，回到模型循环继续纠正。
        terminal = state.get("terminal")
        terminal_update = terminal
        if terminal in (TERMINAL_FINISH, TERMINAL_ASK_USER):
            if control_action_results.get(terminal) is False:
                terminal_update = None

        return {
            "messages": new_messages,
            "tool_calls_used": used + len(raw_calls),
            "tool_errors": tool_errors,
            "draft": draft,
            "draft_revision": draft_revision,
            "draft_digest": draft_digest,
            "last_estimate": last_estimate,
            "last_rule_report": last_rule_report,
            "estimate_digest": estimate_digest,
            "rules_digest": rules_digest,
            "pending_question": pending_question,
            "finish_outcome": finish_outcome,
            "last_observations": observations,
            "terminal": terminal_update,
        }

    return tool_node


def _handle_control_action(
    name: str,
    raw_arguments: Any,
    *,
    draft: dict[str, Any] | None,
    draft_revision: int,
    pending_question: dict[str, Any] | None,
    finish_outcome: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    args, parsing_error = _parse_arguments(raw_arguments if isinstance(raw_arguments, str) else "")
    if parsing_error is not None:
        return (
            {"ok": False, "error": {"code": "invalid_arguments", "message": parsing_error, "retryable": False}},
            {},
        )
    if name == "submit_script":
        try:
            script = parse_script(args, source="submit_script")
        except ScriptValidationError as exc:
            return (
                {"ok": False, "error": {"code": "invalid_arguments", "message": str(exc), "retryable": False}},
                {},
            )
        canonical = script_to_dict(script)
        revision = draft_revision + 1
        return (
            {"ok": True, "revision": revision, "shots": len(script.shots)},
            {
                "draft": canonical,
                "draft_revision": revision,
                "draft_digest": script_digest(script),
            },
        )
    if name == "ask_user":
        question = args.get("question")
        missing_fields = args.get("missing_fields", [])
        reason = args.get("reason")
        valid = (
            isinstance(question, str)
            and 1 <= len(question) <= 300
            and isinstance(missing_fields, list)
            and len(missing_fields) <= 10
            and all(isinstance(item, str) for item in missing_fields)
            and (reason is None or (isinstance(reason, str) and len(reason) <= 200))
        )
        if not valid:
            return (
                {"ok": False, "error": {"code": "invalid_arguments", "message": "ask_user 参数不合法", "retryable": False}},
                {},
            )
        return (
            {"ok": True},
            {
                "pending_question": {
                    "question": question,
                    "missing_fields": list(missing_fields),
                    "reason": reason,
                }
            },
        )
    # finish
    outcome = args.get("outcome")
    message = args.get("message")
    valid = outcome in FINISH_OUTCOMES and (
        message is None or (isinstance(message, str) and len(message) <= 200)
    )
    if not valid:
        return (
            {"ok": False, "error": {"code": "invalid_arguments", "message": "finish 参数不合法", "retryable": False}},
            {},
        )
    return ({"ok": True, "outcome": outcome}, {"finish_outcome": outcome})


def make_finalize_node(
    *,
    dataset: Dataset,
    requirements: Requirements,
    trace: TraceWriter,
    budgets: Budgets,
) -> Callable[[RunState], dict[str, Any]]:
    def finalize_node(state: RunState) -> dict[str, Any]:
        step = int(state.get("step", 0))
        terminal = state.get("terminal")
        pending_question = state.get("pending_question")
        finish_outcome = state.get("finish_outcome")
        draft = state.get("draft")
        tool_errors = list(state.get("tool_errors") or [])
        skipped = state.get("skipped_tool_calls")

        script: Script | None = None
        estimate_result = None
        rule_report = None
        checks: dict[str, Any] | None = None
        if isinstance(draft, dict):
            try:
                script = parse_script(draft, source="finalize")
            except ScriptValidationError:
                script = None
        if script is not None:
            estimate_result = estimate(script.shots, requirements.target_duration_s)
            rule_report = check(
                script,
                dataset,
                requirements.platform,
                estimate_result.total_duration_s,
            )
            estimate_mismatch = (
                state.get("estimate_digest") is not None
                and state.get("estimate_digest") != _shots_text_digest(script_to_dict(script)["shots"])
            )
            rules_mismatch = (
                state.get("rules_digest") is not None
                and state.get("rules_digest") != script_digest(script)
            )
            checks = {
                "estimate": estimate_result.to_dict(),
                "rules": rule_report.to_dict(),
                "engine": "finalize",
                "mismatch": {"estimate": bool(estimate_mismatch), "rules": bool(rules_mismatch)},
            }

        status: str
        failure: dict[str, Any] | None = None

        def fail(code: str, message: str, evidence: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
            payload = {"code": code, "message": message}
            if evidence:
                payload["evidence"] = evidence
            return Status.FAILED.value, payload

        if pending_question is not None:
            status = Status.NEEDS_INPUT.value
        elif terminal == TERMINAL_INVALID:
            status, failure = fail(FailureCode.INVALID_ACTION_COMBINATION.value, "finish 与 ask_user 不能同批出现")
        elif terminal == TERMINAL_ASK_USER:
            status, failure = fail(FailureCode.INVALID_ACTION_COMBINATION.value, "ask_user 参数无效，未形成补充请求")
        elif terminal == TERMINAL_FINISH:
            if script is None or estimate_result is None or rule_report is None:
                code = (
                    FailureCode.TOOL_FAILURE_UNRECOVERED.value
                    if tool_errors
                    else FailureCode.NO_SCRIPT_SUBMITTED.value
                )
                status, failure = fail(code, "没有可复算的提交稿", {"tool_error_count": len(tool_errors)})
            elif estimate_result.within and rule_report.ok:
                status = Status.COMPLETED.value
            else:
                code = (
                    FailureCode.CANNOT_SATISFY.value
                    if finish_outcome == "cannot_satisfy"
                    else FailureCode.CHECKS_FAILED_AFTER_FINISH.value
                )
                status, failure = fail(
                    code,
                    "finish 时最终复算未通过",
                    {
                        "finish_outcome": finish_outcome,
                        "estimate": {
                            "total_duration_s": estimate_result.total_duration_s,
                            "window": estimate_result.window.to_dict(),
                            "verdict": estimate_result.verdict,
                            "within": estimate_result.within,
                        },
                        "rule_errors": [
                            violation.to_dict()
                            for violation in rule_report.violations
                            if violation.severity == "error"
                        ],
                        "platform_max_total_duration_s": dataset.platforms[
                            requirements.platform
                        ].max_total_duration_s,
                        "tool_error_count": len(tool_errors),
                    },
                )
        elif terminal == TERMINAL_NO_ACTION:
            if script is None or estimate_result is None or rule_report is None:
                status, failure = fail(
                    FailureCode.NO_SCRIPT_SUBMITTED.value, "模型未调用工具且没有可复算的提交稿"
                )
            elif estimate_result.within and rule_report.ok:
                status = Status.COMPLETED.value
            else:
                status, failure = fail(
                    FailureCode.MODEL_TERMINATED_WITHOUT_FINISH.value,
                    "模型未调用 finish 即终止，且最终复算未通过",
                    {
                        "estimate": estimate_result.to_dict(),
                        "rule_errors": [
                            violation.to_dict()
                            for violation in rule_report.violations
                            if violation.severity == "error"
                        ],
                    },
                )
        else:
            # 预算触顶或异常：未 finish 不得判定完成
            budget_code = FailureCode.STEP_LIMIT.value
            if skipped:
                budget_code = FailureCode.TOOL_CALL_LIMIT.value
            elif int(state.get("tool_calls_used", 0)) >= budgets.max_tool_calls:
                budget_code = FailureCode.TOOL_CALL_LIMIT.value
            status = Status.INCOMPLETE.value
            failure = {
                "code": budget_code,
                "message": "预算耗尽且模型未 finish",
                "evidence": {
                    "steps_used": step,
                    "max_steps": budgets.max_steps,
                    "tool_calls_used": int(state.get("tool_calls_used", 0)),
                    "max_tool_calls": budgets.max_tool_calls,
                    "skipped_tool_calls": skipped or [],
                    "estimate": estimate_result.to_dict() if estimate_result is not None else None,
                    "rule_errors": [
                        violation.to_dict()
                        for violation in (rule_report.violations if rule_report is not None else [])
                        if violation.severity == "error"
                    ],
                },
            }

        trace.event(
            "finalize",
            step=step,
            status=status,
            failure_code=(failure or {}).get("code"),
            estimate=(
                {
                    "total_duration_s": estimate_result.total_duration_s,
                    "within": estimate_result.within,
                    "verdict": estimate_result.verdict,
                }
                if estimate_result is not None
                else None
            ),
            rules=(
                {
                    "ok": rule_report.ok,
                    "errors": rule_report.stats.errors,
                    "warnings": rule_report.stats.warnings,
                    "rule_ids": sorted(
                        {violation.rule_id for violation in rule_report.violations}
                    ),
                }
                if rule_report is not None
                else None
            ),
            mismatch=(checks or {}).get("mismatch"),
            budget={
                "steps_used": step,
                "max_steps": budgets.max_steps,
                "tool_calls_used": int(state.get("tool_calls_used", 0)),
                "max_tool_calls": budgets.max_tool_calls,
            },
        )
        return {"status": status, "failure": failure, "final_checks": checks}

    return finalize_node
