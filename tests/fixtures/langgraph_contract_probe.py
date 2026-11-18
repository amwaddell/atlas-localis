"""Subprocess-only fixture: a hard parent deadline can kill a starved executor."""

import json
import sys
import threading
import time
from typing import Annotated, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from workbench.graphs.research_models import ResearchResult, merge_task_results


class Input(TypedDict):
    count: int


class Output(TypedDict):
    terminal: tuple[str, ...]
    barriers: int


class Parent(TypedDict):
    count: int
    results: Annotated[dict[str, ResearchResult], merge_task_results]
    terminal: tuple[str, ...]
    barriers: int


class Worker(TypedDict):
    task_id: str
    remaining: int
    steps: int


worker_gate = threading.Semaphore(1)
model_gate = threading.Semaphore(1)
active = 0
peak = 0
model_active = 0
model_peak = 0
completed = []


def step(state):
    global model_active, model_peak
    with model_gate:
        model_active += 1
        model_peak = max(model_peak, model_active)
        time.sleep(0.005)
        model_active -= 1
    return {"remaining": state["remaining"] - 1, "steps": state["steps"] + 1}


worker = StateGraph(Worker)
worker.add_node("step", step)
worker.add_edge(START, "step")
worker.add_conditional_edges("step", lambda s: "step" if s["remaining"] > 0 else END)
inner = worker.compile()


def wrapper(state, config: RunnableConfig):
    global active, peak
    # Force out-of-plan completion while keeping one admitted worker.
    time.sleep({"t1": 0.15, "t2": 0.07, "t3": 0}[state["task_id"]])
    with worker_gate:
        active += 1
        peak = max(peak, active)
        try:
            result = inner.invoke(state, config)
            assert result["steps"] == int(state["task_id"][1:])
            completed.append(state["task_id"])
            terminal = ResearchResult(
                task_id=state["task_id"],
                snapshot_id="fixture",
                status="insufficient_evidence",
                gaps=("fixture only",),
            )
            return {"results": {terminal.task_id: terminal}}
        finally:
            active -= 1


def dispatch(state):
    return [
        Send("worker", {"task_id": f"t{i}", "remaining": i, "steps": 0})
        for i in range(1, state["count"] + 1)
    ]


def barrier(state):
    expected = tuple(f"t{i}" for i in range(1, state["count"] + 1))
    assert set(state["results"]) == set(expected)
    return {"terminal": expected, "barriers": state.get("barriers", 0) + 1}


builder = StateGraph(Parent, input_schema=Input, output_schema=Output)
builder.add_node("worker", wrapper)
builder.add_node("aggregate", barrier)
builder.add_conditional_edges(START, dispatch)
builder.add_edge("worker", "aggregate")
builder.add_edge("aggregate", END)
graph = builder.compile()
capacity = int(sys.argv[1])
mode = sys.argv[2]
config = {"max_concurrency": capacity}
if mode == "stream":
    events = list(
        graph.stream(
            {"count": 3}, config, stream_mode=["updates", "values"], subgraphs=True
        )
    )
    values = [
        value for namespace, kind, value in events if not namespace and kind == "values"
    ]
    terminal = values[-1]
    nested = sum(bool(namespace) for namespace, _, _ in events)
else:
    terminal = graph.invoke({"count": 3}, config)
    nested = 0
# A second run establishes fresh terminal/reducer state.
again = graph.invoke({"count": 1}, {"max_concurrency": 4})
assert again["terminal"] == ("t1",) and again["barriers"] == 1
print(
    json.dumps(
        {
            "terminal": terminal["terminal"],
            "barriers": terminal["barriers"],
            "nested": nested,
            "peak_workers": peak,
            "peak_model": model_peak,
            "completion": completed[:3],
        }
    )
)
