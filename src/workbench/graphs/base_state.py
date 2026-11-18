"""Private reducer-backed application and isolated worker channels."""

from __future__ import annotations

from typing import Annotated, TypedDict

from workbench.graphs.research_models import (
    AnswerDraft,
    ApplicationResult,
    ConversationInput,
    Diagnostics,
    EvidenceRecord,
    Findings,
    NormalizedIntent,
    Notice,
    ResearchPlan,
    ResearchResult,
    StageError,
    SynthesisPacket,
    ValidationReport,
    WorkerInput,
    merge_task_results,
)
from workbench.retrieval.hybrid import HydratedRetrieval


class ApplicationInput(TypedDict):
    input: ConversationInput


class ApplicationOutput(TypedDict):
    result: ApplicationResult


class ApplicationState(TypedDict):
    error: StageError | None
    input: ConversationInput
    normalized_question: NormalizedIntent | None
    plan: ResearchPlan | None
    results_by_task: Annotated[dict[str, ResearchResult], merge_task_results]
    synthesis_packet: SynthesisPacket | None
    draft: AnswerDraft | None
    final_validation: ValidationReport
    repair_count: int
    result: ApplicationResult | None


class WorkerChannels(TypedDict):
    input: WorkerInput


class WorkerOutput(TypedDict):
    result: ResearchResult


class WorkerState(TypedDict):
    input: WorkerInput
    retrieval: HydratedRetrieval | None
    packed_evidence: tuple[EvidenceRecord, ...]
    findings_draft: Findings | None
    validation: ValidationReport
    repair_count: int
    result: ResearchResult | None
    error: StageError | None
    warnings: tuple[Notice, ...]
    diagnostics: Diagnostics
    started: float


def application_state(value: ConversationInput) -> ApplicationState:
    return ApplicationState(
        error=None,
        input=value,
        normalized_question=None,
        plan=None,
        results_by_task={},
        synthesis_packet=None,
        draft=None,
        final_validation=ValidationReport(),
        repair_count=0,
        result=None,
    )


def worker_state(value: WorkerInput) -> WorkerState:
    return WorkerState(
        input=value,
        retrieval=None,
        packed_evidence=(),
        findings_draft=None,
        validation=ValidationReport(),
        repair_count=0,
        result=None,
        error=None,
        warnings=(),
        diagnostics=Diagnostics(),
        started=0.0,
    )
