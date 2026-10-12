"""Minimal LangGraph smoke graph to prove the dependency is wired."""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class EchoState(TypedDict):
    message: str
    echo: str


def _echo_node(state: EchoState) -> EchoState:
    return {"message": state["message"], "echo": state["message"]}


def build_echo_graph():
    """Scaffold graph: START -> echo -> END. Real skills come next."""
    graph = StateGraph(EchoState)
    graph.add_node("echo", _echo_node)
    graph.add_edge(START, "echo")
    graph.add_edge("echo", END)
    return graph.compile()


class DurableState(TypedDict, total=False):
    """One run on the durable graph. The orchestrator stores this in PostgreSQL.

    `messages` is the dialogue. Tool payloads stay in `task_results`.
    `long_term` is context loaded for this turn, not the execution stack.
    """

    message: str
    messages: list[dict[str, str]]
    task_results: dict[str, dict[str, object]]
    long_term: list[dict[str, object]]
    completed: list[str]
    draft: str
    final: str


def _results(state: DurableState) -> dict[str, dict[str, object]]:
    current = state.get("task_results") or {}
    return {key: dict(value) for key, value in current.items() if isinstance(value, dict)}


def _dialogue(state: DurableState) -> list[dict[str, str]]:
    current = state.get("messages")
    if isinstance(current, list):
        return [dict(item) for item in current if isinstance(item, dict)]
    text = state.get("message") or ""
    if not text:
        return []
    return [{"role": "user", "content": text}]


def _collect(state: DurableState) -> DurableState:
    completed = [*state.get("completed", []), "collect"]
    results = _results(state)
    previous = results.get("collect") or {}
    results["collect"] = {"visits": int(previous.get("visits") or 0) + 1}
    return {"completed": completed, "task_results": results, "messages": _dialogue(state)}


def _draft(state: DurableState) -> DurableState:
    completed = [*state.get("completed", []), "draft"]
    body = state.get("message", "")
    results = _results(state)
    results["draft"] = {"body": body}
    return {
        "completed": completed,
        "draft": body,
        "task_results": results,
        "messages": _dialogue(state),
    }


def _join(state: DurableState) -> DurableState:
    completed = [*state.get("completed", []), "join"]
    final = state.get("draft", "")
    messages = _dialogue(state)
    messages.append({"role": "assistant", "content": final})
    return {"completed": completed, "final": final, "messages": messages}


def build_durable_graph(checkpointer):
    """collect -> draft -> join. Stops after collect and draft so each step can be saved.

    Resuming passes the same thread id. The checkpointer must outlive the process.
    """
    graph = StateGraph(DurableState)
    graph.add_node("collect", _collect)
    graph.add_node("draft", _draft)
    graph.add_node("join", _join)
    graph.add_edge(START, "collect")
    graph.add_edge("collect", "draft")
    graph.add_edge("draft", "join")
    graph.add_edge("join", END)
    return graph.compile(checkpointer=checkpointer, interrupt_after=["collect", "draft"])
