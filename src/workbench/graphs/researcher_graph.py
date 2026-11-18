"""Isolated bounded evidence worker with typed task output."""

from __future__ import annotations

import time
from typing import Any

from langgraph.graph import END, StateGraph
from pydantic import ValidationError

from workbench.core.offline import validate_environment
from workbench.core.types import Query
from workbench.graphs.base_state import (
    WorkerChannels,
    WorkerOutput,
    WorkerState,
)
from workbench.graphs.constrained_evidence import findings_schema, source_statements
from workbench.graphs.evidence_support import support_errors
from workbench.graphs.execution import worker_phase
from workbench.graphs.research_models import (
    Diagnostics,
    Findings,
    Notice,
    ResearchResult,
    StageError,
    ValidationReport,
    WorkerInput,
    admit_findings,
)
from workbench.models.structured_json import json_instruction
from workbench.retrieval.evidence_packing import (
    REPAIR_RESERVE,
    findings_messages,
    pack_evidence,
    request_bound,
    token_bound,
)
from workbench.retrieval.hybrid import HydratedRetrieval


def _error(stage: str, code: str, message: str) -> StageError:
    return StageError(stage=stage, code=code, message=message[:1900] or code)


def build_evidence_worker(*, retriever: Any, model: Any, snapshot_id: str) -> Any:
    """Compile a worker with only WorkerInput in and ResearchResult out.

    The retriever/store is already pinned by the caller; the input pin must
    match. Services are injected, never channels. No checkpoint or chat memory.
    """
    validate_environment()
    if not snapshot_id.strip():
        raise ValueError("worker requires a pinned snapshot identity")

    def retrieve(state):
        value = WorkerInput.model_validate(state["input"])
        fresh = dict(
            input=value,
            started=time.monotonic(),
            packed_evidence=(),
            findings_draft=None,
            validation=ValidationReport(),
            repair_count=0,
            error=None,
            warnings=(),
            diagnostics=Diagnostics(),
            retrieval=HydratedRetrieval((), 0, 0),
        )
        if value.snapshot_id != snapshot_id:
            fresh["error"] = _error(
                "retrieve",
                "snapshot_mismatch",
                "Worker snapshot does not match pinned resources.",
            )
            return fresh
        try:
            result = retriever.retrieve_hydrated(
                Query(text=value.task.question), top_k=50
            )
            fresh["retrieval"] = result
            fresh["diagnostics"] = Diagnostics(
                retrieved=result.retrieved, opened=result.opened
            )
            if result.missing_ids:
                fresh["warnings"] = (
                    Notice(
                        code="hydration_partial",
                        message=f"{len(result.missing_ids)} candidate chunks unavailable.",
                    ),
                )
            if result.retrieved and not result.chunks:
                fresh["error"] = _error(
                    "retrieve",
                    "hydration_failed",
                    "Search matched candidates but no hydrated evidence survived.",
                )
        except Exception as exc:
            fresh["error"] = _error(
                "retrieve", "retrieval_failed", f"{type(exc).__name__}: {exc}"
            )
        return fresh

    def timed_out(state):
        return time.monotonic() - state["started"] >= state["input"].limits.task_seconds

    def pack(state):
        if state["error"]:
            return {}
        try:
            if timed_out(state):
                raise TimeoutError("task deadline exceeded")
            value = state["input"]
            # Fail an impossible request budget even when no chunk could fit.
            if (
                request_bound(findings_messages(value, ()))
                + value.limits.output_tokens
                + REPAIR_RESERVE
                > value.limits.prompt_tokens
            ):
                raise ValueError(
                    "instructions/question/schema/output exceed task budget"
                )
            evidence = pack_evidence(value, state["retrieval"].chunks)
            return {
                "packed_evidence": evidence,
                "diagnostics": state["diagnostics"].model_copy(
                    update={"presented": len(evidence)}
                ),
            }
        except Exception as exc:
            return {"error": _error("pack_evidence", "packing_failed", str(exc))}

    def generate(state, repairing=False):
        if state["error"] or not state["packed_evidence"]:
            return {}
        update = {"repair_count": int(repairing)}
        value = state["input"]
        errors = state["validation"].errors if repairing else ()
        messages = findings_messages(value, state["packed_evidence"], errors)
        try:
            if timed_out(state):
                raise TimeoutError("task deadline exceeded")
            if (
                request_bound(messages, state["packed_evidence"])
                + value.limits.output_tokens
                > value.limits.prompt_tokens
            ):
                raise ValueError("rendered request exceeds task budget")
            started = time.monotonic()
            response = model.generate(
                [
                    {
                        "role": "system",
                        "content": json_instruction(
                            Findings,
                            generation_schema=findings_schema(state["packed_evidence"]),
                        ),
                    },
                    *messages,
                ],
                temperature=0,
                max_tokens=min(value.limits.output_tokens, 4096),
                stream=False,
                json_schema=findings_schema(state["packed_evidence"]),
            )
            update["diagnostics"] = state["diagnostics"].model_copy(
                update={
                    "tokens_in": state["diagnostics"].tokens_in
                    + max(0, response.tokens_in),
                    "tokens_out": state["diagnostics"].tokens_out
                    + max(0, response.tokens_out),
                }
            )
            if (
                timed_out(state)
                or time.monotonic() - started > value.limits.request_seconds
            ):
                raise TimeoutError("model/task deadline exceeded")
        except Exception as exc:
            update["error"] = _error(
                "repair_findings" if repairing else "derive_findings",
                "model_failed",
                f"{type(exc).__name__}: {exc}",
            )
            return update
        try:
            if (
                response.finish_reason == "length"
                or response.tokens_out > value.limits.output_tokens
                or token_bound(response.text) > value.limits.output_tokens
            ):
                raise ValueError("truncated or over-budget findings output")
            draft = Findings.model_validate_json(response.text)
            update.update(findings_draft=draft, validation=ValidationReport())
        except ValueError as exc:
            if isinstance(exc, ValidationError):
                # Report schema locations/messages, never echo invalid model
                # prose or raw input values into the repair context.
                message = "; ".join(
                    f"{'.'.join(map(str, e['loc'])) or 'root'}: {e['msg']}"
                    for e in exc.errors(
                        include_input=False, include_context=False, include_url=False
                    )
                )
            else:
                message = str(exc)
            update.update(
                findings_draft=None,
                validation=ValidationReport(status="failed", errors=(message[:1900],)),
            )
        return update

    def validate(state):
        if (
            state["error"]
            or not state["packed_evidence"]
            or state["findings_draft"] is None
        ):
            return {}
        try:
            value, draft, evidence = (
                state["input"],
                state["findings_draft"],
                state["packed_evidence"],
            )
            # No IDs or alias translation until every proposal passes.
            aliases = {f"E{i}": e for i, e in enumerate(evidence, 1)}
            errors = []
            seen = set()
            for i, f in enumerate(draft.findings):
                location = f"findings.{i}"
                if f.statement in seen:
                    errors.append(
                        f"{location}.statement: duplicate source statement; select each statement once"
                    )
                seen.add(f.statement)
                if any(a not in aliases for a in f.evidence_refs):
                    errors.append(
                        f"{location}: unknown or not-presented evidence reference"
                    )
                    continue
                if f.date_text and (
                    f.date_text not in f.statement
                    or not any(f.date_text in aliases[a].text for a in f.evidence_refs)
                ):
                    errors.append(
                        f"{location}.date_text: date expression absent from referenced excerpt; copy ONE exact span or use null"
                    )
                if not any(
                    f.statement in source_statements(aliases[a].text)
                    for a in f.evidence_refs
                ):
                    errors.append(
                        f"{location}.statement: requires one offered verbatim span from a cited excerpt"
                    )
                errors.extend(
                    f"{location}.statement: {error}"
                    for error in support_errors(
                        f.statement, tuple(aliases[a].text for a in f.evidence_refs)
                    )
                )
            if errors:
                return {
                    "validation": ValidationReport(
                        status="failed", errors=tuple(errors[:24])
                    )
                }
            for e in evidence:
                if e.key.snapshot_id != snapshot_id or e.presented_to != (
                    value.task.task_id,
                ):
                    raise ValueError("invalid presented-span provenance")
                original = next(
                    c for c in state["retrieval"].chunks if c.chunk_id == e.key.chunk_id
                )
                e.verify_chunk(original.text)
            return {"validation": ValidationReport(status="passed")}
        except (ValueError, StopIteration) as exc:
            return {
                "validation": ValidationReport(
                    status="failed",
                    errors=(str(exc)[:1900] or "missing hydrated chunk",),
                )
            }

    def finish(state):
        value = state["input"]
        error = state["error"]
        if not error and state["validation"].status == "failed":
            error = _error(
                "validate_findings",
                "findings_invalid",
                "; ".join(state["validation"].errors),
            )
        if not error and timed_out(state):
            error = _error("finish_task", "task_timeout", "Task deadline exceeded.")
        findings, gaps = (), ()
        if not error:
            draft = state["findings_draft"]
            if state["validation"].status == "passed" and draft is not None:
                findings = admit_findings(
                    value.task.task_id, draft, state["packed_evidence"]
                )
                gaps = draft.gaps
            if not findings and not gaps:
                gaps = ("No supported findings in the presented evidence.",)
            if value.task.needs_timeline and not any(f.date_text for f in findings):
                gaps = (
                    *gaps,
                    "Requested chronology is not supported by dated findings.",
                )
            if state["warnings"]:
                gaps = (*gaps, "Some retrieved evidence could not be hydrated.")
            if state["retrieval"].chunks and not state["packed_evidence"]:
                gaps = (*gaps, "No evidence fits the task prompt budget.")
            gaps = tuple(dict.fromkeys(gaps))
            if len(gaps) > 3:
                # Wire output is byte-bounded; its three model gaps plus these
                # short code notices fit one Text field without losing gaps.
                gaps = (*gaps[:2], " ".join(gaps[2:]))
        result = ResearchResult(
            task_id=value.task.task_id,
            snapshot_id=value.snapshot_id,
            status="failed"
            if error
            else "insufficient_evidence"
            if gaps or not findings
            else "complete",
            findings=findings,
            evidence=state["packed_evidence"],
            gaps=gaps,
            warnings=state["warnings"],
            error=error,
            diagnostics=state["diagnostics"].model_copy(
                update={"elapsed_ms": (time.monotonic() - state["started"]) * 1000}
            ),
        )
        return {"result": result}

    graph = StateGraph(
        WorkerState, input_schema=WorkerChannels, output_schema=WorkerOutput
    )
    graph.add_node("retrieve", worker_phase("retrieve", retrieve))
    graph.add_node("pack_evidence", worker_phase("pack_evidence", pack))
    graph.add_node("derive_findings", worker_phase("derive_findings", generate))
    graph.add_node("validate_findings", worker_phase("validate_findings", validate))
    graph.add_node(
        "repair_findings",
        worker_phase("repair_findings", lambda s: generate(s, repairing=True)),
    )
    graph.add_node("finish_task", worker_phase("finish_task", finish))
    graph.set_entry_point("retrieve")
    graph.add_conditional_edges(
        "retrieve",
        lambda s: (
            "finish_task"
            if s["error"] or not s["retrieval"].chunks
            else "pack_evidence"
        ),
    )
    graph.add_conditional_edges(
        "pack_evidence",
        lambda s: (
            "finish_task"
            if s["error"] or not s["packed_evidence"]
            else "derive_findings"
        ),
    )
    graph.add_edge("derive_findings", "validate_findings")
    graph.add_conditional_edges(
        "validate_findings",
        lambda s: (
            "repair_findings"
            if not s["error"]
            and s["validation"].status == "failed"
            and s["repair_count"] == 0
            else "finish_task"
        ),
    )
    graph.add_edge("repair_findings", "validate_findings")
    graph.add_edge("finish_task", END)
    return graph.compile()
