"""Deterministic barrier/packet admission, structured synthesis and finalization."""

from __future__ import annotations

import time
from itertools import zip_longest

from workbench.graphs.constrained_evidence import answer_schema
from workbench.graphs.evidence_support import support_errors
from workbench.graphs.planning import messages, structured
from workbench.graphs.research_models import (
    AnswerDraft,
    AnswerUnit,
    ApplicationResult,
    CanonicalUnit,
    Diagnostics,
    Notice,
    SynthesisPacket,
    TaskCoverage,
    ValidationReport,
    finding_aliases,
)
from workbench.models.structured_json import json_instruction
from workbench.prompting.citations import render_canonical_units
from workbench.retrieval.evidence_packing import token_bound


def registry(packet):
    records = {}
    for result in packet.results:
        for record in result.evidence:
            previous = records.get(record.key)
            if previous:
                if previous.model_dump(exclude={"presented_to"}) != record.model_dump(
                    exclude={"presented_to"}
                ):
                    raise ValueError("conflicting evidence")
                record = record.model_copy(
                    update={
                        "presented_to": tuple(
                            sorted(set(previous.presented_to + record.presented_to))
                        )
                    }
                )
            records[record.key] = record
    return records


def synthesis_messages(packet, errors=()):
    aliases = finding_aliases(packet)
    records = registry(packet)
    used = dict.fromkeys(k for f in aliases.values() for k in f.evidence_refs)
    prompt = messages(
        "synthesize_findings_v1.txt",
        {
            "original_intent": packet.input.model_dump(),
            "standalone_question": packet.normalized_question.standalone_question,
            "tasks": [
                {
                    "task": t.model_dump(),
                    "status": r.status,
                    "gaps": r.gaps,
                    "error": r.error.code if r.error else None,
                }
                for t, r in zip(packet.plan.tasks, packet.results, strict=True)
            ],
            "findings": [
                {"alias": a, **f.model_dump(mode="json")} for a, f in aliases.items()
            ],
            "excerpts": [records[k].model_dump(mode="json") for k in used],
            "budget_omissions": packet.budget_omissions,
        },
    )
    if errors:
        prompt[0]["content"] += (
            "\nRepair against SAME packet; remove unsupported claims. Errors: "
            + "; ".join(errors)[:300]
        )
    return prompt


def aggregate(value, intent, plan, results, snapshot_id, limits):
    expected = {t.task_id for t in plan.tasks}
    if set(results) != expected or any(
        r.snapshot_id != snapshot_id for r in results.values()
    ):
        raise ValueError("barrier IDs/snapshot mismatch")
    ordered = tuple(results[t.task_id] for t in plan.tasks)
    packet = SynthesisPacket(
        input=value,
        normalized_question=intent,
        plan=plan,
        results=ordered,
        admitted_finding_ids=(),
    )
    registry(packet)  # Reject conflicts even in evidence omitted by budgets.
    admitted, omitted = [], []
    for row in zip_longest(*(r.findings for r in ordered)):
        for finding in row:
            if finding is None:
                continue
            candidate = packet.model_copy(
                update={"admitted_finding_ids": tuple((*admitted, finding.finding_id))}
            )
            prompt = synthesis_messages(candidate)
            bound = (
                token_bound(
                    json_instruction(
                        AnswerDraft, generation_schema=answer_schema(candidate)
                    )
                )
                + 128
                + sum(
                    token_bound(m["content"]) + token_bound(m["role"]) + 16
                    for m in prompt
                )
            )
            if (
                len(admitted) >= 12
                or bound + limits.output_tokens + 512 > limits.prompt_tokens
            ):
                omitted.append(
                    f"{finding.finding_id}: synthesis budget omitted supported material."
                )
            else:
                admitted.append(finding.finding_id)
    # Alias order is plan/finding order, independently of admission/completion order.
    admitted = tuple(
        f.finding_id for r in ordered for f in r.findings if f.finding_id in admitted
    )
    packet = SynthesisPacket(
        **{
            **packet.model_dump(),
            "admitted_finding_ids": admitted,
            "budget_omissions": tuple(omitted),
        }
    )
    # Omissions themselves have a cost; remove from the most represented task
    # until the complete final packet and repair reserve fit.
    while packet.admitted_finding_ids:
        bound = (
            token_bound(
                json_instruction(AnswerDraft, generation_schema=answer_schema(packet))
            )
            + 128
            + sum(
                token_bound(m["content"]) + token_bound(m["role"]) + 16
                for m in synthesis_messages(packet)
            )
        )
        if bound + limits.output_tokens + 512 <= limits.prompt_tokens:
            break
        counts = {
            r.task_id: sum(
                fid.startswith(r.task_id + ".") for fid in packet.admitted_finding_ids
            )
            for r in ordered
        }
        victim_task = max(reversed(ordered), key=lambda r: counts[r.task_id]).task_id
        victim = next(
            fid
            for fid in reversed(packet.admitted_finding_ids)
            if fid.startswith(victim_task + ".")
        )
        packet = packet.model_copy(
            update={
                "admitted_finding_ids": tuple(
                    fid for fid in packet.admitted_finding_ids if fid != victim
                ),
                "budget_omissions": (
                    *packet.budget_omissions,
                    f"{victim}: synthesis budget omitted supported material.",
                ),
            }
        )
    return packet


def validate_draft(packet, draft):
    if draft is None:
        return ValidationReport(
            status="failed", errors=("No structured answer draft.",)
        )
    try:
        aliases = finding_aliases(packet)
        referenced = {a for u in draft.units for a in u.finding_ids}
        if sum(len(u.finding_ids) for u in draft.units) != len(referenced):
            raise ValueError(
                "answer repeats a finding reference; use each finding once"
            )
        if not referenced <= aliases.keys():
            raise ValueError("unknown finding reference")
        for i, unit in enumerate(draft.units):
            expected_text = " ".join(aliases[a].statement for a in unit.finding_ids)
            if unit.text != expected_text:
                raise ValueError(
                    f"units.{i}: copy the cited finding statements verbatim in finding_ids order, separated by one space"
                )
            errors = support_errors(
                unit.text, tuple(aliases[a].statement for a in unit.finding_ids)
            )
            if errors:
                raise ValueError(f"units.{i}: " + "; ".join(errors))
        for group in draft.conflict_groups:
            if not set(group) <= referenced:
                raise ValueError("conflict references must appear in answer units")
        expected = {f.finding_id.split(".")[0] for f in aliases.values()}
        covered = {aliases[a].finding_id.split(".")[0] for a in referenced}
        if expected != covered:
            raise ValueError("answer omits a task with admitted findings")
        records = registry(packet)
        for a in referenced:
            f = aliases[a]
            for key in f.evidence_refs:
                if (
                    key not in records
                    or f.finding_id.split(".")[0] not in records[key].presented_to
                ):
                    raise ValueError("unpresented evidence")
        return ValidationReport(status="passed")
    except ValueError as exc:
        return ValidationReport(status="failed", errors=(str(exc)[:1000],))


def synthesize(packet, model, limits, errors=()):
    return structured(
        model,
        AnswerDraft,
        synthesis_messages(packet, errors),
        limits,
        generation_schema=answer_schema(packet),
    )


def finalize(packet, draft, report, execution):
    delivery = "synthesis"
    if packet.admitted_finding_ids and report.status != "passed":
        draft = AnswerDraft(
            units=tuple(
                AnswerUnit(text=f.statement, finding_ids=(a,))
                for a, f in finding_aliases(packet).items()
            )
        )
        report = validate_draft(packet, draft)
        delivery = "evidence_fallback"
    if packet.admitted_finding_ids and report.status != "passed":
        raise ValueError("fallback failed validation")
    aliases = finding_aliases(packet)
    units = (
        tuple(
            CanonicalUnit(
                text=u.text,
                finding_ids=tuple(
                    dict.fromkeys(aliases[a].finding_id for a in u.finding_ids)
                ),
                evidence_refs=tuple(
                    dict.fromkeys(
                        k for a in u.finding_ids for k in aliases[a].evidence_refs
                    )
                ),
            )
            for u in draft.units
        )
        if draft and report.status == "passed"
        else ()
    )
    used = {fid for u in units for fid in u.finding_ids}
    coverage = tuple(
        TaskCoverage(
            task_id=r.task_id,
            status=r.status,
            admitted_findings=sum(
                f.finding_id in packet.admitted_finding_ids for f in r.findings
            ),
            used_findings=sum(f.finding_id in used for f in r.findings),
            gaps=(
                *r.gaps,
                *(o for o in packet.budget_omissions if o.startswith(r.task_id + ".")),
                *(("Task failed: " + r.error.code,) if r.error else ()),
            ),
        )
        for r in packet.results
    )
    gaps = tuple(g for c in coverage for g in c.gaps)
    if len(gaps) > 24:
        gaps = (*gaps[:23], "Additional task gaps are listed in coverage.")
    failed = any(r.status == "failed" for r in packet.results)
    if not units:
        outcome = "failed" if failed else "insufficient_evidence"
        text = (
            "Research failed; the available corpus cannot be assessed."
            if failed
            else "There is not enough evidence to answer this question."
        )
        from workbench.graphs.research_models import StageError

        error = (
            StageError(
                stage="research",
                code="tasks_failed",
                message="One or more tasks failed without usable findings.",
            )
            if failed
            else None
        )
        delivery = "message"
        citations = ()
    else:
        outcome = (
            "partial"
            if delivery == "evidence_fallback"
            or gaps
            or any(c.status != "complete" or not c.used_findings for c in coverage)
            else "complete"
        )
        text, citations = render_canonical_units(
            units, tuple(registry(packet).values())
        )
        error = None
    return ApplicationResult(
        run_id=execution.run_id,
        outcome=outcome,
        delivery=delivery,
        units=units,
        answer_text=text,
        citations=citations,
        coverage=coverage,
        gaps=gaps,
        conflicts=tuple(
            tuple(aliases[a].finding_id for a in group)
            for group in draft.conflict_groups
        )
        if units
        else (),
        warnings=tuple(
            (
                *packet.plan.warnings,
                *(w for r in packet.results for w in r.warnings),
                *(
                    (
                        Notice(
                            code="synthesis_fallback",
                            message="Synthesis failed validation; displaying validated findings.",
                        ),
                    )
                    if delivery == "evidence_fallback"
                    else ()
                ),
            )
        )[:24],
        error=error,
        final_validation=report,
        diagnostics=Diagnostics(
            retrieved=sum(r.diagnostics.retrieved for r in packet.results),
            opened=sum(r.diagnostics.opened for r in packet.results),
            presented=sum(r.diagnostics.presented for r in packet.results),
            elapsed_ms=(time.monotonic() - execution.started) * 1000,
            tokens_in=execution.tokens_in,
            tokens_out=execution.tokens_out,
        ),
    )
