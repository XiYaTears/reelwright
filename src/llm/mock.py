"""离线 mock LLM：固定策略，只用于验证编排/工具/校验链路。

边界（必须写进 README 与 docs/03）：mock 按固定规则读取工具结果并决定下一步，
其查询改写、初稿字数、压缩/扩张都是脚本化行为，不能作为模型决策质量证据。

检索序列（与数据集无关地写死）：
    1. topic 原文；
    2. 去固定停用词后，按字符顺序取前 3 个 2-gram（去重）；
    3. 固定回退词「星河旅拍指南」（测试锁死其在默认数据与空素材夹具上 0 命中）；
    任一步命中即进入写稿；全部 0 结果 → ask_user。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from src.dataset import Dataset
from src.domain.models import Requirements, Shot
from src.llm.base import LLMResponse, ToolCall, Usage
from src.tools.duration import duration_window, estimate, total_duration

STOPWORDS: tuple[str, ...] = (
    "怎么",
    "如何",
    "攻略",
    "教程",
    "技巧",
    "新手",
    "入门",
    "方法",
    "我的",
)
FALLBACK_QUERY = "星河旅拍指南"
MAX_TWO_GRAM_CANDIDATES = 3
INITIAL_DRAFT_SECONDS = 38
COMPRESSED_DRAFT_SECONDS = 28
EXPANDED_DRAFT_SECONDS = 58
FILLER = "记录生活里的小瞬间"
DEFAULT_TITLE = "记录生活的小瞬间"
CITATION_SHOT_INDEX = 2


def _strip_stopwords(topic: str) -> str:
    cleaned = topic
    for word in STOPWORDS:
        cleaned = cleaned.replace(word, "")
    return cleaned.strip()


def build_query_candidates(topic: str) -> tuple[str, ...]:
    """确定性查询序列：topic → 去停用词后的前 3 个 2-gram → 固定回退词。"""
    candidates: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            candidates.append(value)

    add(topic)
    cleaned = _strip_stopwords(topic)
    grams: list[str] = []
    for index in range(max(0, len(cleaned) - 1)):
        gram = cleaned[index : index + 2]
        if gram not in grams:
            grams.append(gram)
        if len(grams) >= MAX_TWO_GRAM_CANDIDATES:
            break
    for gram in grams:
        add(gram)
    add(FALLBACK_QUERY)
    return tuple(candidates)


def _durations_for_total(total_seconds: int) -> list[int]:
    fixed = {
        INITIAL_DRAFT_SECONDS: [7, 7, 2, 7, 7, 8],
        COMPRESSED_DRAFT_SECONDS: [7, 7, 2, 6, 6],
        EXPANDED_DRAFT_SECONDS: [7, 7, 2, 7, 7, 7, 7, 7, 7],
    }
    if total_seconds in fixed:
        return list(fixed[total_seconds])
    durations = [2]
    remaining = total_seconds - 2
    while remaining > 0:
        chunk = min(7, remaining)
        if remaining - chunk == 1 and chunk > 3:
            chunk -= 1
        durations.append(chunk)
        remaining -= chunk
    if remaining < 0:
        durations[-1] += remaining
    return durations


def _filler(chars: int) -> str:
    return (FILLER * (chars // len(FILLER) + 1))[:chars]


def _short_note(text: str) -> str:
    text = text.strip()
    return text[:40]


class MockClient:
    """固定策略 LLM，接口与 DeepSeekClient 相同。"""

    model_name = "mock"

    def __init__(self, dataset: Dataset, requirements: Requirements) -> None:
        self.dataset = dataset
        self.requirements = requirements
        platform_rule = dataset.get_platform(requirements.platform)
        if platform_rule is None:
            raise ValueError(f"未知平台：{requirements.platform}")
        self._platform = platform_rule
        self._window = duration_window(requirements.target_duration_s)
        self._candidates = build_query_candidates(requirements.topic)
        self._query_index = 0
        self._queries_sent: list[str] = []
        self._found_facts: list[tuple[str, str, str]] = []
        self._draft: dict[str, Any] | None = None
        self._draft_revision = 0
        self._last_estimate: dict[str, Any] | None = None
        self._last_rules: dict[str, Any] | None = None
        self._pending_retries: list[tuple[str, dict[str, Any]]] = []
        self._search_retry_used = False
        self._search_error_streak = 0
        self._search_failed = False
        self._tool_retry_counts: dict[str, int] = {"estimate_duration": 0, "check_rules": 0}
        self._tool_error_exhausted: set[str] = set()
        self._args_by_tool: dict[str, dict[str, Any]] = {}
        self._response_seq = 0
        self._terminal_emitted = False

    # ---- 测试/证据可读状态 ----
    @property
    def queries_sent(self) -> tuple[str, ...]:
        return tuple(self._queries_sent)

    @property
    def found_facts(self) -> tuple[tuple[str, str, str], ...]:
        return tuple(self._found_facts)

    @property
    def draft_revision(self) -> int:
        return self._draft_revision

    # ---- LLM 接口 ----

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> LLMResponse:
        if self._terminal_emitted:
            raise RuntimeError("mock 在已终止的会话上被再次调用")
        batch = self._last_batch(messages)
        if batch is not None:
            self._apply_batch(batch)
        return self._decide()

    # ---- 结果应用 ----

    def _last_batch(
        self, messages: Sequence[Mapping[str, Any]]
    ) -> list[tuple[str, dict[str, Any], Mapping[str, Any]]] | None:
        assistant_index = -1
        last_calls: list[Mapping[str, Any]] = []
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if not isinstance(message, Mapping):
                continue
            if message.get("role") == "assistant" and message.get("tool_calls"):
                assistant_index = index
                raw_calls = message.get("tool_calls") or []
                last_calls = [call for call in raw_calls if isinstance(call, Mapping)]
                break
        if assistant_index < 0:
            return None
        results: dict[str, Mapping[str, Any]] = {}
        for message in messages[assistant_index + 1 :]:
            if not isinstance(message, Mapping) or message.get("role") != "tool":
                continue
            call_id = message.get("tool_call_id")
            content = message.get("content")
            parsed: Any = None
            if isinstance(content, str):
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError:
                    parsed = {"ok": False, "error": {"code": "invalid_response"}}
            if isinstance(call_id, str) and isinstance(parsed, Mapping):
                results[call_id] = parsed
        batch: list[tuple[str, dict[str, Any], Mapping[str, Any]]] = []
        for raw in last_calls:
            function = raw.get("function") or {}
            name = function.get("name") if isinstance(function, Mapping) else None
            raw_args = function.get("arguments") if isinstance(function, Mapping) else None
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) and raw_args else {}
            except json.JSONDecodeError:
                args = {}
            call_id = raw.get("id")
            result = results.get(call_id, {}) if isinstance(call_id, str) else {}
            if isinstance(name, str):
                batch.append((name, args if isinstance(args, dict) else {}, result))
        return batch

    def _apply_batch(
        self, batch: list[tuple[str, dict[str, Any], Mapping[str, Any]]]
    ) -> None:
        for name, args, result in batch:
            ok = isinstance(result, Mapping) and result.get("ok") is not False
            self._args_by_tool[name] = args
            if name == "search_materials":
                self._apply_search(ok, args, result)
            elif name == "estimate_duration":
                self._apply_tool_result(name, ok, args, result, target="estimate")
            elif name == "check_rules":
                self._apply_tool_result(name, ok, args, result, target="rules")
            elif name == "submit_script":
                if ok:
                    self._draft = dict(args)
                    self._draft_revision += 1
                    self._last_estimate = None
                    self._last_rules = None
            elif name in ("finish", "ask_user"):
                self._terminal_emitted = True

    def _apply_search(
        self, ok: bool, args: dict[str, Any], result: Mapping[str, Any]
    ) -> None:
        if not ok:
            self._search_error_streak += 1
            if self._search_error_streak == 1 and not self._search_retry_used:
                self._search_retry_used = True
                self._pending_retries.append(("search_materials", dict(args)))
            else:
                self._search_failed = True
            return
        self._search_retry_used = False
        self._search_error_streak = 0
        materials = result.get("materials") or []
        facts: list[tuple[str, str, str]] = []
        if isinstance(materials, list):
            for material in materials:
                if not isinstance(material, Mapping):
                    continue
                material_id = material.get("material_id")
                raw_facts = material.get("facts") or []
                if not isinstance(material_id, str) or not isinstance(raw_facts, list):
                    continue
                for fact in raw_facts:
                    if not isinstance(fact, Mapping):
                        continue
                    fact_id = fact.get("fact_id")
                    text = fact.get("text")
                    if isinstance(fact_id, str) and isinstance(text, str):
                        facts.append((material_id, fact_id, text))
        if facts:
            self._found_facts = facts
        else:
            self._query_index += 1

    def _apply_tool_result(
        self,
        name: str,
        ok: bool,
        args: dict[str, Any],
        result: Mapping[str, Any],
        *,
        target: str,
    ) -> None:
        if not ok:
            self._tool_retry_counts[name] = self._tool_retry_counts.get(name, 0) + 1
            if self._tool_retry_counts[name] == 1:
                self._pending_retries.append((name, dict(args)))
            else:
                self._tool_error_exhausted.add(name)
            return
        self._tool_retry_counts[name] = 0
        if target == "estimate":
            self._last_estimate = dict(result)
        else:
            self._last_rules = dict(result)

    # ---- 决策 ----

    def _decide(self) -> LLMResponse:
        if self._pending_retries:
            calls = self._pending_retries
            self._pending_retries = []
            for name, args in calls:
                if name == "search_materials":
                    self._queries_sent.append(str(args.get("query", "")))
            return self._respond("重试失败的工具调用", calls)

        if self._search_failed:
            return self._finish("cannot_satisfy", "检索工具持续失败，无法获得素材依据")
        if "estimate_duration" in self._tool_error_exhausted:
            return self._finish("cannot_satisfy", "时长估算工具持续失败，无法验证时长")
        if "check_rules" in self._tool_error_exhausted:
            return self._finish("cannot_satisfy", "规则检查工具持续失败，无法验证合规")

        if self._found_facts:
            return self._decide_with_materials()

        if self._query_index < len(self._candidates):
            query = self._candidates[self._query_index]
            self._queries_sent.append(query)
            return self._respond(f"检索素材：{query}", [("search_materials", {"query": query})])
        return self._ask_user()

    def _decide_with_materials(self) -> LLMResponse:
        if self._draft is None:
            self._draft = self._build_draft(INITIAL_DRAFT_SECONDS)
            return self._submit()
        if self._last_estimate is None or self._last_rules is None:
            estimate_args = {
                "shots": [
                    {"narration": shot["narration"], "caption": shot["caption"]}
                    for shot in self._draft["shots"]
                ]
            }
            check_args = dict(self._draft)
            return self._respond(
                "估算并检查当前脚本",
                [("estimate_duration", estimate_args), ("check_rules", check_args)],
            )

        estimate_result = self._last_estimate
        rules_result = self._last_rules
        if estimate_result.get("within") and rules_result.get("ok"):
            return self._finish("completed_intent", "估算与规则检查通过")

        if self._window.min_s > self._platform.max_total_duration_s:
            return self._finish(
                "cannot_satisfy",
                f"目标区间下限 {self._window.min_s} 秒超过平台上限 {self._platform.max_total_duration_s} 秒，无法同时满足",
            )

        if not estimate_result.get("within"):
            verdict = estimate_result.get("verdict")
            desired = (
                COMPRESSED_DRAFT_SECONDS
                if verdict == "too_long"
                else EXPANDED_DRAFT_SECONDS
            )
            if not self._window.min_s <= desired <= self._window.max_s:
                return self._finish(
                    "cannot_satisfy",
                    f"调整到 {desired} 秒仍不在目标区间 [{self._window.min_s}, {self._window.max_s}]",
                )
            self._draft = self._build_draft(desired)
            return self._submit()

        return self._finish("cannot_satisfy", "规则检查未通过，且无法在不编造事实的前提下修正")

    # ---- 草稿构造 ----

    def _pick_citation_fact(self) -> tuple[str, str, str] | None:
        if not self._found_facts:
            return None
        return min(self._found_facts, key=lambda item: len(item[2]))

    def _build_draft(self, total_seconds: int) -> dict[str, Any]:
        durations = _durations_for_total(total_seconds)
        citation = self._pick_citation_fact()
        shots: list[dict[str, Any]] = []
        for index, duration in enumerate(durations):
            is_citation_shot = index == CITATION_SHOT_INDEX and citation is not None
            if is_citation_shot:
                assert citation is not None
                material_id, fact_id, text = citation
                # 引用镜头使用事实原文作为旁白/字幕；长度超过 8 字时长可能变化，随后统一配平。
                shots.append(
                    {
                        "visual": f"画面 {index + 1}：手机竖屏展示功能",
                        "narration": text,
                        "caption": text,
                        "citations": [
                            {"material_id": material_id, "fact_id": fact_id, "quote": text}
                        ],
                    }
                )
            else:
                shots.append(
                    {
                        "visual": f"画面 {index + 1}：生活场景记录",
                        "narration": _filler(4 * duration),
                        "caption": "",
                        "citations": [],
                    }
                )
        self._balance_total(shots, total_seconds)
        return {"title": DEFAULT_TITLE, "shots": shots}

    def _balance_total(self, shots: list[dict[str, Any]], expected_total: int) -> None:
        shot_objects = [
            Shot(visual=shot["visual"], narration=shot["narration"], caption=shot["caption"])
            for shot in shots
        ]
        actual_total = total_duration(shot_objects)
        diff = expected_total - actual_total
        if diff == 0:
            return
        # 找一个非引用镜头配平：每加/减 1 秒对应 4 个旁白字符（最短 2 秒）
        for shot in reversed(shots):
            if shot["citations"]:
                continue
            current_chars = len(shot["narration"])
            current_seconds = max(2, (current_chars + 3) // 4)
            new_seconds = current_seconds + diff
            if new_seconds < 2 or new_seconds > 30:
                continue
            shot["narration"] = _filler(4 * new_seconds)
            return

    # ---- 响应构造 ----

    def _submit(self) -> LLMResponse:
        assert self._draft is not None
        return self._respond("提交当前脚本草稿", [("submit_script", dict(self._draft))])

    def _ask_user(self) -> LLMResponse:
        self._terminal_emitted = True
        args = {
            "question": "素材库没有覆盖该主题，请补充产品功能、卖点或可引用的素材信息。",
            "missing_fields": ["topic"],
            "reason": f"已尝试 {len(self._queries_sent)} 次检索均无结果",
        }
        return self._respond("请求补充素材信息", [("ask_user", args)])

    def _finish(self, outcome: str, message: str) -> LLMResponse:
        self._terminal_emitted = True
        return self._respond(
            "收尾",
            [("finish", {"outcome": outcome, "message": _short_note(message)})],
        )

    def _respond(
        self,
        note: str,
        calls: list[tuple[str, dict[str, Any]]],
    ) -> LLMResponse:
        self._response_seq += 1
        tool_calls = tuple(
            ToolCall(
                id=f"mock-call-{self._response_seq}-{index + 1}",
                name=name,
                arguments=args,
                raw_arguments=json.dumps(args, ensure_ascii=False),
            )
            for index, (name, args) in enumerate(calls)
        )
        return LLMResponse(
            content=_short_note(note),
            tool_calls=tool_calls,
            finish_reason="tool_calls",
            usage=Usage(),
            model=self.model_name,
        )

    def _initial_draft_estimate(self, draft: dict[str, Any]) -> int:
        """测试辅助：对草稿做一次估算得到总时长。"""
        shot_objects = [
            Shot(visual=shot["visual"], narration=shot["narration"], caption=shot["caption"])
            for shot in draft["shots"]
        ]
        return estimate(shot_objects, self.requirements.target_duration_s).total_duration_s
