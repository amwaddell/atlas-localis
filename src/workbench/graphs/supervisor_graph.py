"""Compose the private graph for the single research application.

Supervisor is the orchestration role, not a persona. Scheduler slots do not
control inference: independent shared gates admit one worker/model/accelerator.
"""

from __future__ import annotations

import time
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.types import Send

from workbench.core.offline import validate_environment
from workbench.graphs.base_state import (
    ApplicationInput,
    ApplicationOutput,
    ApplicationState,
    application_state,
)
from workbench.graphs.execution import (
    _WORKER,
    AdmittedModel,
    AdmittedRetriever,
    TaskContext,
)
from workbench.graphs.planning import normalize, plan
from workbench.graphs.research_models import (
    ApplicationResult,
    Limits,
    Notice,
    ResearchPlan,
    ResearchResult,
    ResearchTask,
    StageError,
    ValidationReport,
    WorkerInput,
)
from workbench.graphs.researcher_graph import build_evidence_worker
from workbench.graphs.synthesis import aggregate, finalize, synthesize, validate_draft
from workbench.observability.tracing import add_span_event, start_span


def _execution(config):
    return config["configurable"]["atlas_execution"]


def _message(execution, outcome, code, text):
    return ApplicationResult(
        run_id=execution.run_id,
        outcome=outcome,
        delivery="message",
        answer_text=text,
        warnings=(Notice(code=code, message=text),),
        error=StageError(stage="application", code=code, message=text)
        if outcome == "failed"
        else None,
    )


def build_supervisor_graph(
    *,
    retriever: Any,
    model: Any,
    snapshot_id: str,
    limits: Limits | None = None,
    forced_single_task: bool = False,
) -> Any:
    """Internal composition; public execution belongs to systems.runner.Application."""
    validate_environment()
    limits = limits or Limits()
    admitted_model = AdmittedModel(model)
    worker = build_evidence_worker(
        retriever=AdmittedRetriever(retriever),
        model=admitted_model,
        snapshot_id=snapshot_id,
    )

    def phase(name, fn):
        def node(state, config: RunnableConfig):
            execution = _execution(config)
            execution.check()
            with execution.bind(), start_span(name):
                return fn(state, execution)

        return node

    def normalize_node(state, execution):
        execution.emit("normalizing")
        return dict(
            **{
                k: v
                for k, v in application_state(state["input"]).items()
                if k != "normalized_question"
            },
            normalized_question=normalize(state["input"], admitted_model, limits),
        )

    def plan_node(state, execution):
        execution.emit("planning")
        proposal = plan(
            state["input"], state["normalized_question"], admitted_model, limits
        )
        if forced_single_task:
            proposal = ResearchPlan(
                tasks=(
                    ResearchTask(
                        task_id="t1",
                        label="Research",
                        question=state["normalized_question"].standalone_question,
                        needs_timeline=any(t.needs_timeline for t in proposal.tasks),
                    ),
                )
            )
        execution.emit("plan_ready")
        return {"plan": proposal}

    def dispatch(state):
        add_span_event("dispatch", {"tasks": len(state["plan"].tasks)})
        return [
            Send(
                "research_task",
                {"input": WorkerInput(task=t, snapshot_id=snapshot_id, limits=limits)},
            )
            for t in state["plan"].tasks
        ]

    def research_task(state, config: RunnableConfig):
        execution = _execution(config)
        value = WorkerInput.model_validate(state["input"])
        try:
            # Queue waits count toward the run deadline; task time starts at admission.
            with execution.bind(), execution.admit(_WORKER):
                task = TaskContext(
                    execution.run_id,
                    value.task.task_id,
                    snapshot_id,
                    time.monotonic() + limits.task_seconds,
                )
                with (
                    execution.bind(task),
                    start_span(
                        "researcher.task",
                        attributes={
                            "run_id": task.run_id,
                            "task_id": task.task_id,
                            "snapshot_id": task.snapshot_id,
                        },
                    ),
                ):
                    execution.emit("task_started", task_id=task.task_id)
                    output = worker.invoke({"input": value}, config=config)
                    execution.check()
                    result = ResearchResult.model_validate(output["result"])
                    if (
                        result.task_id != value.task.task_id
                        or result.snapshot_id != snapshot_id
                    ):
                        raise ValueError("worker identity mismatch")
        except (RuntimeError, ValueError, TimeoutError, OSError) as exc:
            result = ResearchResult(
                task_id=value.task.task_id,
                snapshot_id=snapshot_id,
                status="failed",
                error=StageError(
                    stage="research_task",
                    code="task_failed",
                    message=f"Task could not complete: {type(exc).__name__}.",
                ),
            )
        execution.emit("task_finished", task_id=value.task.task_id)
        return {"results_by_task": {value.task.task_id: result}}

    def aggregate_node(state, execution):
        try:
            return {
                "synthesis_packet": aggregate(
                    state["input"],
                    state["normalized_question"],
                    state["plan"],
                    state["results_by_task"],
                    snapshot_id,
                    limits,
                )
            }
        except ValueError:
            return {
                "error": StageError(
                    stage="aggregate",
                    code="barrier_failed",
                    message="Task barrier or evidence registry is inconsistent.",
                )
            }

    def synthesize_node(state, execution, repair=False):
        execution.emit("synthesizing")
        try:
            draft = synthesize(
                state["synthesis_packet"],
                admitted_model,
                limits,
                state["final_validation"].errors if repair else (),
            )
            return {"draft": draft, "repair_count": int(repair)}
        except Exception:
            return {
                "draft": None,
                "repair_count": int(repair),
                "final_validation": ValidationReport(
                    status="failed", errors=("Structured synthesis failed.",)
                ),
            }

    def validate_node(state, execution):
        execution.emit("validating")
        return {
            "final_validation": validate_draft(
                state["synthesis_packet"], state["draft"]
            )
        }

    def finalize_node(state, execution):
        intent = state["normalized_question"]
        if state.get("error"):
            result = _message(
                execution, "failed", state["error"].code, state["error"].message
            )
        elif intent.status in {"failed", "unresolved"}:
            result = _message(
                execution,
                "failed" if intent.status == "failed" else "insufficient_evidence",
                "rewrite_failed"
                if intent.status == "failed"
                else "ambiguous_reference",
                "Contextual question normalization failed."
                if intent.status == "failed"
                else "Please clarify the unresolved reference in your question.",
            )
        else:
            result = finalize(
                state["synthesis_packet"],
                state["draft"],
                state["final_validation"],
                execution,
            )
        return {"result": result}

    graph = StateGraph(
        ApplicationState, input_schema=ApplicationInput, output_schema=ApplicationOutput
    )
    for name, fn in [
        ("normalize_question", normalize_node),
        ("plan", plan_node),
        ("aggregate", aggregate_node),
        ("synthesize", synthesize_node),
        ("validate_final", validate_node),
        ("repair_synthesis", lambda s, e: synthesize_node(s, e, True)),
        ("finalize", finalize_node),
    ]:
        graph.add_node(name, phase(name, fn))
    graph.add_node("research_task", research_task)
    graph.set_entry_point("normalize_question")
    graph.add_conditional_edges(
        "normalize_question",
        lambda s: (
            "plan"
            if s["normalized_question"].status in {"identity", "resolved"}
            else "finalize"
        ),
    )
    graph.add_conditional_edges("plan", dispatch)
    graph.add_edge("research_task", "aggregate")
    graph.add_conditional_edges(
        "aggregate",
        lambda s: (
            "synthesize"
            if not s.get("error") and s["synthesis_packet"].admitted_finding_ids
            else "finalize"
        ),
    )
    graph.add_edge("synthesize", "validate_final")
    graph.add_conditional_edges(
        "validate_final",
        lambda s: (
            "repair_synthesis"
            if s["final_validation"].status == "failed" and s["repair_count"] == 0
            else "finalize"
        ),
    )
    graph.add_edge("repair_synthesis", "validate_final")
    graph.add_edge("finalize", END)
    return graph.compile()
