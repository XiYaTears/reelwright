"""LangGraph 图：agent → tools → agent … → finalize。"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.agent.nodes import make_agent_node, make_finalize_node, make_tool_node
from src.agent.state import Budgets, RunState
from src.agent.trace import TraceWriter
from src.dataset import Dataset
from src.domain.models import Requirements
from src.llm.base import LLMClient
from src.tools.registry import ToolRegistry


def graph_recursion_limit(budgets: Budgets) -> int:
    """LangGraph 最后保险：每轮 agent/tools 两个超步，另预留收尾余量。"""
    return 2 * budgets.max_steps + 5


def build_graph(
    *,
    llm: LLMClient,
    registry: ToolRegistry,
    dataset: Dataset,
    requirements: Requirements,
    trace: TraceWriter,
    budgets: Budgets,
):
    graph = StateGraph(RunState)
    graph.add_node("agent", make_agent_node(llm=llm, trace=trace))
    graph.add_node(
        "tools",
        make_tool_node(registry=registry, trace=trace, budgets=budgets),
    )
    graph.add_node(
        "finalize",
        make_finalize_node(
            dataset=dataset,
            requirements=requirements,
            trace=trace,
            budgets=budgets,
        ),
    )
    graph.add_edge(START, "agent")

    def route_after_agent(state: RunState) -> str:
        messages = state.get("messages") or []
        last = messages[-1] if messages else {}
        tool_calls = last.get("tool_calls") if isinstance(last, dict) else None
        return "tools" if tool_calls else "finalize"

    def route_after_tools(state: RunState) -> str:
        if state.get("terminal") in ("finish", "ask_user", "invalid"):
            return "finalize"
        if state.get("skipped_tool_calls"):
            return "finalize"
        if int(state.get("step", 0)) >= budgets.max_steps:
            return "finalize"
        if int(state.get("tool_calls_used", 0)) >= budgets.max_tool_calls:
            return "finalize"
        return "agent"

    graph.add_conditional_edges(
        "agent",
        route_after_agent,
        {"tools": "tools", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "tools",
        route_after_tools,
        {"agent": "agent", "finalize": "finalize"},
    )
    graph.add_edge("finalize", END)
    return graph.compile()
