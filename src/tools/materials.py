"""素材检索工具：按关键词在合成素材库中匹配。

算法（确定性）：
    query 按空白与中英文标点切分为 token（去重保序）；
    每个 token 与素材 title（权重 3）、tags（2）、facts[].text（1）做大小写不敏感子串匹配；
    每个 token 在每字段最多计一次；总分 > 0 才命中；
    按 (-score, material_id) 排序取前 top_k。
不做同义词/向量/模糊语义检索——查不到时由模型改写查询（动态决策观测点）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from src.dataset import Dataset
from src.tools.errors import ToolArgumentError

MAX_QUERY_LEN = 120
DEFAULT_TOP_K = 5
MAX_TOP_K = 10
MAX_MATERIAL_ID_LEN = 10

SPLIT_RE = re.compile(r"[\s,，。；;、！!？?：:（）()\[\]【】\"'“”‘’/\\|]+")

FIELD_WEIGHTS: tuple[tuple[str, int], ...] = (("title", 3), ("tags", 2), ("fact", 1))

TOOL_SPEC: dict[str, Any] = {
    "name": "search_materials",
    "description": "按关键词检索合成产品素材库，返回素材 ID、标题与事实原文。无结果时返回空列表。",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "检索词，建议用短关键词",
                "maxLength": MAX_QUERY_LEN,
            },
            "top_k": {
                "type": "integer",
                "description": "返回条数，1–10，默认 5",
                "minimum": 1,
                "maximum": MAX_TOP_K,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}


def tokenize_query(query: str) -> tuple[str, ...]:
    """按空白/标点切分并去重（保序）。"""
    tokens: list[str] = []
    seen: set[str] = set()
    for raw in SPLIT_RE.split(query):
        token = raw.strip()
        if not token:
            continue
        folded = token.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        tokens.append(token)
    return tuple(tokens)


def _match_material(material, tokens: tuple[str, ...]) -> tuple[int, list[str], list[str]]:
    score = 0
    matched_terms: list[str] = []
    matched_in: list[str] = []
    title_folded = material.title.casefold()
    tags_folded = [tag.casefold() for tag in material.tags]
    facts_folded = [fact.text.casefold() for fact in material.facts]

    for term in tokens:
        folded = term.casefold()
        hit_fields: list[tuple[str, int]] = []
        if folded in title_folded:
            hit_fields.append(("title", 3))
        if any(folded in tag for tag in tags_folded):
            hit_fields.append(("tags", 2))
        if any(folded in text for text in facts_folded):
            hit_fields.append(("fact", 1))
        if hit_fields:
            matched_terms.append(term)
            score += sum(weight for _, weight in hit_fields)
            for field_name, _ in hit_fields:
                if field_name not in matched_in:
                    matched_in.append(field_name)
    return score, matched_terms, matched_in


def search_materials(query: str, dataset: Dataset, top_k: int = DEFAULT_TOP_K) -> dict[str, Any]:
    """核心纯函数：返回素材 ID、标题、事实原文与命中信息。"""
    tokens = tokenize_query(query)
    if not tokens:
        raise ValueError("query 切分后为空")
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for index, material in enumerate(dataset.materials):
        score, matched_terms, matched_in = _match_material(material, tokens)
        if score <= 0:
            continue
        scored.append(
            (
                -score,
                index,
                {
                    "material_id": material.material_id,
                    "title": material.title,
                    "tags": list(material.tags),
                    "facts": [
                        {"fact_id": fact.fact_id, "text": fact.text, "kind": fact.kind}
                        for fact in material.facts
                    ],
                    "score": score,
                    "matched_terms": matched_terms,
                    "matched_in": matched_in,
                },
            )
        )
    scored.sort(key=lambda item: (item[0], item[2]["material_id"]))
    total_matched = len(scored)
    materials = [item[2] for item in scored[:top_k]]
    result: dict[str, Any] = {
        "ok": True,
        "query": query,
        "total_matched": total_matched,
        "materials": materials,
    }
    if total_matched == 0:
        result["hint"] = "未命中素材；可改写成更短的关键词重试，或向用户请求补充信息。"
    return result


def handle(args: Mapping[str, Any], *, dataset: Dataset) -> dict[str, Any]:
    """工具入口：校验模型参数 → 纯函数 → 返回 JSON 观察。"""
    if not isinstance(args, Mapping):
        raise ToolArgumentError("search_materials 参数必须是对象")
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ToolArgumentError("query 必须是非空字符串")
    query = query.strip()
    if len(query) > MAX_QUERY_LEN:
        raise ToolArgumentError(f"query 长度不能超过 {MAX_QUERY_LEN}")
    top_k = args.get("top_k", DEFAULT_TOP_K)
    if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= MAX_TOP_K:
        raise ToolArgumentError(f"top_k 必须是 1–{MAX_TOP_K} 的整数")
    try:
        return search_materials(query, dataset, top_k)
    except ValueError as exc:
        raise ToolArgumentError(str(exc)) from None
