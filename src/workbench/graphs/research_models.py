"""Stage 2 contracts. Domain objects contain no services or model-only aliases.

Construction is trusted application code; untrusted JSON uses the separate wire
schemas below. Frozen tuples prevent published nested collections being mutated.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]
Question = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)
]
TaskID = Annotated[str, Field(pattern=r"^t[1-3]$")]
FindingID = Annotated[str, Field(pattern=r"^t[1-3]\.f[1-8]$")]
FindingAlias = Annotated[str, Field(pattern=r"^F([1-9]|1[0-9]|2[0-4])$")]
WorkerStatus = Literal["complete", "insufficient_evidence", "failed"]


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Notice(Payload):
    code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    message: Text


class StageError(Notice):
    stage: Annotated[str, Field(min_length=1, max_length=64)]


class ConversationTurn(Payload):
    role: Literal["user", "assistant"]
    content: Question


class ConversationInput(Payload):
    # Original bytes are authoritative; validation must not strip them.
    question: Annotated[str, Field(min_length=1, max_length=8000)]
    history: tuple[ConversationTurn, ...] = Field(default=(), max_length=100)
    summary: Annotated[str, Field(max_length=8000)] = ""
    history_truncated: bool = False

    @model_validator(mode="after")
    def nonblank(self):
        if not self.question.strip():
            raise ValueError("blank question")
        return self


class RewritePayload(Payload):
    standalone_question: Question | None


class NormalizedIntent(Payload):
    status: Literal["identity", "resolved", "unresolved", "failed"]
    standalone_question: Question | None
    error: StageError | None = None

    @model_validator(mode="after")
    def consistency(self):
        if (self.status in {"identity", "resolved"}) != (
            self.standalone_question is not None
        ):
            raise ValueError("normalization status/question mismatch")
        if (self.status == "failed") != (self.error is not None):
            raise ValueError("normalization error mismatch")
        return self


class TaskProposal(Payload):
    label: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    question: Question
    needs_timeline: Annotated[bool, Field(strict=True)]


class PlanProposal(Payload):
    tasks: tuple[TaskProposal, ...] = Field(min_length=1, max_length=3)


class ResearchTask(TaskProposal):
    task_id: TaskID


class ResearchPlan(Payload):
    tasks: tuple[ResearchTask, ...] = Field(min_length=1, max_length=3)
    warnings: tuple[Notice, ...] = Field(default=(), max_length=12)

    @model_validator(mode="after")
    def ordered_ids(self):
        if [t.task_id for t in self.tasks] != [
            f"t{i}" for i in range(1, len(self.tasks) + 1)
        ]:
            raise ValueError("task IDs must be assigned in plan order")
        keys = [question_key(t.question) for t in self.tasks]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate task questions")
        return self


def question_key(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def assign_plan(proposal: PlanProposal) -> ResearchPlan:
    unique: dict[str, TaskProposal] = {}
    for task in proposal.tasks:
        key = question_key(task.question)
        if key in unique:
            task = unique[key].model_copy(
                update={
                    "needs_timeline": unique[key].needs_timeline or task.needs_timeline
                }
            )
        unique[key] = task
    return ResearchPlan(
        tasks=tuple(
            ResearchTask(**t.model_dump(), task_id=f"t{i}")
            for i, t in enumerate(unique.values(), 1)
        )
    )


class EvidenceKey(Payload):
    snapshot_id: Text
    chunk_id: Text
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)

    @model_validator(mode="after")
    def span(self):
        if self.end <= self.start:
            raise ValueError("empty/reversed excerpt")
        return self


class EvidenceRecord(Payload):
    key: EvidenceKey
    document_id: Text
    title: Text
    section: Text | None = None
    text: Annotated[str, Field(min_length=1, max_length=16000)]
    text_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_url: Annotated[str, Field(max_length=2000)] = ""
    presented_to: tuple[TaskID, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def integrity(self):
        if (
            len(self.text) != self.key.end - self.key.start
            or hashlib.sha256(self.text.encode()).hexdigest() != self.text_hash
        ):
            raise ValueError("excerpt length/hash mismatch")
        if len(set(self.presented_to)) != len(self.presented_to):
            raise ValueError("duplicate provenance")
        return self

    def verify_chunk(self, full_text: str) -> None:
        if full_text[self.key.start : self.key.end] != self.text or self.key.end > len(
            full_text
        ):
            raise ValueError("excerpt does not match immutable chunk")


def excerpt(
    *,
    snapshot_id: str,
    chunk_id: str,
    full_text: str,
    start: int,
    end: int,
    task_id: str,
    document_id: str,
    title: str,
    section: str | None = None,
    source_url: str = "",
) -> EvidenceRecord:
    text = full_text[start:end]
    record = EvidenceRecord(
        key=EvidenceKey(
            snapshot_id=snapshot_id, chunk_id=chunk_id, start=start, end=end
        ),
        document_id=document_id,
        title=title,
        section=section,
        text=text,
        text_hash=hashlib.sha256(text.encode()).hexdigest(),
        source_url=source_url,
        presented_to=(task_id,),
    )
    record.verify_chunk(full_text)
    return record


class Finding(Payload):
    finding_id: FindingID
    statement: Text
    evidence_refs: tuple[EvidenceKey, ...] = Field(min_length=1, max_length=5)
    date_text: Annotated[str, Field(min_length=1, max_length=120)] | None = None


class FindingProposal(Payload):
    statement: Text
    evidence_refs: tuple[Annotated[str, Field(pattern=r"^E[1-5]$")], ...] = Field(
        min_length=1, max_length=5
    )
    date_text: Annotated[str, Field(min_length=1, max_length=120)] | None = None

    @model_validator(mode="after")
    def atomic_plain(self):
        if re.search(r"[\[\]]|https?://|\n|^\s*#", self.statement):
            raise ValueError(
                "finding must be one plain atomic statement without markers"
            )
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("duplicate evidence references")
        return self


class Findings(Payload):
    findings: tuple[FindingProposal, ...] = Field(max_length=8)
    gaps: tuple[Text, ...] = Field(default=(), max_length=3)


class Diagnostics(Payload):
    retrieved: int = Field(default=0, ge=0)
    opened: int = Field(default=0, ge=0)
    presented: int = Field(default=0, ge=0)
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    elapsed_ms: float = Field(default=0, ge=0, allow_inf_nan=False)


class ValidationReport(Payload):
    status: Literal["passed", "failed", "not_run"] = "not_run"
    errors: tuple[Text, ...] = Field(default=(), max_length=24)

    @model_validator(mode="after")
    def consistency(self):
        if (self.status == "failed") != bool(self.errors):
            raise ValueError("validation errors/status mismatch")
        return self


class ResearchResult(Payload):
    task_id: TaskID
    snapshot_id: Text
    status: WorkerStatus
    findings: tuple[Finding, ...] = Field(default=(), max_length=8)
    evidence: tuple[EvidenceRecord, ...] = Field(default=(), max_length=5)
    gaps: tuple[Text, ...] = Field(default=(), max_length=3)
    warnings: tuple[Notice, ...] = Field(default=(), max_length=12)
    error: StageError | None = None
    diagnostics: Diagnostics = Field(default_factory=Diagnostics)

    @model_validator(mode="after")
    def consistency(self):
        if (self.status == "failed") != (self.error is not None):
            raise ValueError("task error/status mismatch")
        if self.status == "failed" and self.findings:
            raise ValueError("failed task exports findings")
        if self.status == "complete" and (not self.findings or self.gaps):
            raise ValueError("complete requires findings and no gaps")
        keys = {e.key for e in self.evidence}
        if len(keys) != len(self.evidence):
            raise ValueError("duplicate evidence")
        for e in self.evidence:
            if (
                e.key.snapshot_id != self.snapshot_id
                or self.task_id not in e.presented_to
            ):
                raise ValueError("evidence snapshot/task provenance mismatch")
        for i, f in enumerate(self.findings, 1):
            if (
                f.finding_id != f"{self.task_id}.f{i}"
                or not set(f.evidence_refs) <= keys
            ):
                raise ValueError("finding identity/reference mismatch")
        return self


def admit_findings(
    task_id: str, draft: Findings, evidence: tuple[EvidenceRecord, ...]
) -> tuple[Finding, ...]:
    aliases = {
        f"E{i}": e.key for i, e in enumerate(evidence, 1) if task_id in e.presented_to
    }
    if any(alias not in aliases for f in draft.findings for alias in f.evidence_refs):
        raise ValueError("unknown or unpresented evidence alias")
    return tuple(
        Finding(
            finding_id=f"{task_id}.f{i}",
            statement=f.statement,
            evidence_refs=tuple(aliases[a] for a in f.evidence_refs),
            date_text=f.date_text,
        )
        for i, f in enumerate(draft.findings, 1)
    )


class Limits(Payload):
    max_evidence: int = Field(default=5, ge=1, le=5)
    max_per_document: int = Field(default=3, ge=1, le=3)
    prompt_tokens: int = Field(default=6000, gt=0)
    output_tokens: int = Field(default=1200, gt=0)
    request_seconds: float = Field(default=90, gt=0, allow_inf_nan=False)
    task_seconds: float = Field(default=210, gt=0, allow_inf_nan=False)
    run_seconds: float = Field(default=900, gt=0, allow_inf_nan=False)


class WorkerInput(Payload):
    task: ResearchTask
    snapshot_id: Text
    limits: Limits = Field(default_factory=Limits)


class SynthesisPacket(Payload):
    input: ConversationInput
    normalized_question: NormalizedIntent
    plan: ResearchPlan
    results: tuple[ResearchResult, ...] = Field(min_length=1, max_length=3)
    # Admitted IDs only; aliases are derived from this order, never persisted.
    admitted_finding_ids: tuple[FindingID, ...] = Field(max_length=24)
    budget_omissions: tuple[Text, ...] = Field(default=(), max_length=24)

    @model_validator(mode="after")
    def integrity(self):
        if self.normalized_question.status not in {"identity", "resolved"}:
            raise ValueError("synthesis requires resolved intent")
        if tuple(r.task_id for r in self.results) != tuple(
            t.task_id for t in self.plan.tasks
        ):
            raise ValueError("barrier result set/order mismatch")
        if len({r.snapshot_id for r in self.results}) != 1:
            raise ValueError("mixed snapshots")
        available = {f.finding_id for r in self.results for f in r.findings}
        if (
            len(set(self.admitted_finding_ids)) != len(self.admitted_finding_ids)
            or not set(self.admitted_finding_ids) <= available
        ):
            raise ValueError("unknown/duplicate admitted finding")
        records = {}
        for r in self.results:
            for e in r.evidence:
                previous = records.get(e.key)
                if previous and previous.model_dump(
                    exclude={"presented_to"}
                ) != e.model_dump(exclude={"presented_to"}):
                    raise ValueError("conflicting evidence record")
                records[e.key] = e
        return self


def finding_aliases(packet: SynthesisPacket) -> dict[str, Finding]:
    available = {f.finding_id: f for r in packet.results for f in r.findings}
    return {
        f"F{i}": available[fid] for i, fid in enumerate(packet.admitted_finding_ids, 1)
    }


class AnswerUnit(Payload):
    text: Text
    finding_ids: tuple[
        Annotated[str, Field(pattern=r"^F([1-9]|1[0-9]|2[0-4])$")], ...
    ] = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def plain(self):
        if re.search(r"[\[\]]|https?://|^\s*#", self.text):
            raise ValueError("model must not emit citation markers, links or headings")
        return self


class AnswerDraft(Payload):
    units: tuple[AnswerUnit, ...] = Field(min_length=1, max_length=12)
    conflict_groups: tuple[tuple[FindingAlias, ...], ...] = Field(
        default=(), max_length=6
    )

    @model_validator(mode="after")
    def conflicts(self):
        if any(len(set(group)) < 2 for group in self.conflict_groups):
            raise ValueError("conflict requires two distinct findings")
        return self


class CanonicalUnit(Payload):
    text: Text
    finding_ids: tuple[FindingID, ...] = Field(min_length=1, max_length=24)
    evidence_refs: tuple[EvidenceKey, ...] = Field(min_length=1, max_length=120)

    _plain = model_validator(mode="after")(AnswerUnit.plain)


class Citation(Payload):
    number: int = Field(gt=0)
    snapshot_id: Text
    chunk_id: Text
    excerpts: tuple[EvidenceRecord, ...] = Field(min_length=1)
    finding_ids: tuple[FindingID, ...] = Field(min_length=1)


class TaskCoverage(Payload):
    task_id: TaskID
    status: WorkerStatus
    admitted_findings: int = Field(ge=0, le=8)
    used_findings: int = Field(ge=0, le=8)
    gaps: tuple[Text, ...] = Field(default=(), max_length=24)

    @model_validator(mode="after")
    def counts(self):
        if self.used_findings > self.admitted_findings:
            raise ValueError("used findings exceed admission")
        return self


class Fingerprint(Payload):
    component: Text
    identity: Text


class ApplicationResult(Payload):
    final_validation: ValidationReport = Field(default_factory=ValidationReport)
    run_id: Text
    outcome: Literal[
        "complete", "partial", "insufficient_evidence", "failed", "cancelled"
    ]
    delivery: Literal["synthesis", "evidence_fallback", "message"]
    units: tuple[CanonicalUnit, ...] = Field(default=(), max_length=24)
    answer_text: Annotated[str, Field(max_length=64000)]
    citations: tuple[Citation, ...] = ()
    coverage: tuple[TaskCoverage, ...] = Field(default=(), max_length=3)
    gaps: tuple[Text, ...] = Field(default=(), max_length=24)
    conflicts: tuple[tuple[FindingID, ...], ...] = Field(default=(), max_length=6)
    warnings: tuple[Notice, ...] = Field(default=(), max_length=24)
    error: StageError | None = None
    diagnostics: Diagnostics = Field(default_factory=Diagnostics)
    fingerprints: tuple[Fingerprint, ...] = ()

    @model_validator(mode="after")
    def terminal(self):
        from workbench.prompting.citations import render_canonical_units

        if len({c.task_id for c in self.coverage}) != len(self.coverage):
            raise ValueError("duplicate task coverage")
        used = {fid for u in self.units for fid in u.finding_ids}
        if self.units:
            text, citations = render_canonical_units(
                self.units, tuple(e for c in self.citations for e in c.excerpts)
            )
            if text != self.answer_text or citations != self.citations:
                raise ValueError(
                    "rendered answer/citations differ from canonical units"
                )
            if self.delivery == "message":
                raise ValueError("factual units require answer delivery")
            for c in self.coverage:
                if (
                    len({f for f in used if f.startswith(c.task_id + ".")})
                    != c.used_findings
                ):
                    raise ValueError("coverage differs from canonical units")
            if {f.split(".")[0] for f in used} - {c.task_id for c in self.coverage}:
                raise ValueError("missing task coverage")
        elif self.citations or self.delivery != "message":
            raise ValueError("message outcome cannot carry citations")
        if any(
            len(set(group)) < 2 or not set(group) <= used for group in self.conflicts
        ):
            raise ValueError("invalid canonical conflict group")
        if (self.outcome == "failed") != (self.error is not None):
            raise ValueError("run error/outcome mismatch")
        if self.outcome in {"failed", "cancelled", "insufficient_evidence"} and (
            self.units or self.citations
        ):
            raise ValueError("non-answer outcome exports facts")
        if self.outcome == "partial" and not self.units:
            raise ValueError("partial requires usable supported material")
        if self.outcome == "insufficient_evidence" and any(
            c.status == "failed" for c in self.coverage
        ):
            raise ValueError("technical failure is not corpus absence")
        if self.delivery == "evidence_fallback" and self.outcome != "partial":
            raise ValueError("fallback must be partial")
        if self.outcome == "complete" and (
            not self.units
            or self.gaps
            or any(
                c.status != "complete"
                or c.gaps
                or (c.admitted_findings and not c.used_findings)
                for c in self.coverage
            )
        ):
            raise ValueError("complete requires covered findings and no known gaps")
        return self


class ApplicationEvent(Payload):
    kind: Literal[
        "run_started",
        "cancelling",
        "normalizing",
        "planning",
        "plan_ready",
        "task_started",
        "task_stage",
        "task_finished",
        "synthesizing",
        "validating",
        "run_finished",
    ]
    run_id: Text
    sequence: int = Field(ge=0)
    task_id: TaskID | None = None
    message: Text | None = None
    stage: (
        Literal[
            "retrieve",
            "pack_evidence",
            "derive_findings",
            "validate_findings",
            "repair_findings",
            "finish_task",
        ]
        | None
    ) = None
    result: ApplicationResult | None = None

    @model_validator(mode="after")
    def terminal(self):
        if (self.kind == "run_finished") != (self.result is not None):
            raise ValueError("only terminal events carry a result")
        if self.result and self.result.run_id != self.run_id:
            raise ValueError("event run mismatch")
        return self


def merge_task_results(
    left: dict[str, ResearchResult], right: dict[str, ResearchResult]
) -> dict[str, ResearchResult]:
    merged: dict[str, ResearchResult] = {}
    for key, result in (*left.items(), *right.items()):
        if key != result.task_id:
            raise ValueError("task result key mismatch")
        if key in merged and merged[key] != result:
            raise ValueError(f"conflicting terminal write for {key}")
        merged[key] = result
    return merged
