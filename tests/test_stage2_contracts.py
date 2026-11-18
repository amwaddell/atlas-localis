import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from workbench.graphs.base_state import application_state, worker_state
from workbench.graphs.research_models import (
    AnswerDraft,
    ApplicationEvent,
    ApplicationResult,
    ConversationInput,
    EvidenceRecord,
    Findings,
    NormalizedIntent,
    PlanProposal,
    ResearchPlan,
    ResearchResult,
    SynthesisPacket,
    ValidationReport,
    WorkerInput,
    admit_findings,
    assign_plan,
    excerpt,
    finding_aliases,
    merge_task_results,
)
from workbench.models.structured_json import generate_validated_json
from workbench.prompting.citations import project_answer, render_canonical_units


def plan():
    return assign_plan(
        PlanProposal(
            tasks=[
                {
                    "label": "History",
                    "question": "What happened?",
                    "needs_timeline": False,
                }
            ]
        )
    )


def evidence(start=0, end=5, task="t1", snapshot="s1"):
    return excerpt(
        snapshot_id=snapshot,
        chunk_id="chunk",
        full_text="αβ🙂de second",
        start=start,
        end=end,
        task_id=task,
        document_id="doc",
        title="History",
    )


def result(task="t1", records=None):
    records = (evidence(task=task),) if records is None else records
    draft = Findings(
        findings=[{"statement": "An event happened.", "evidence_refs": ["E1"]}]
    )
    return ResearchResult(
        task_id=task,
        snapshot_id="s1",
        status="complete",
        evidence=records,
        findings=admit_findings(task, draft, records),
    )


def packet(results=None, research_plan=None):
    results = (result(),) if results is None else results
    return SynthesisPacket(
        input=ConversationInput(question="What happened?"),
        normalized_question=NormalizedIntent(
            status="identity", standalone_question="What happened?"
        ),
        plan=research_plan or plan(),
        results=results,
        admitted_finding_ids=tuple(f.finding_id for r in results for f in r.findings),
    )


def test_plan_bounds_ids_dedupe_and_wire_boundary():
    p = PlanProposal(
        tasks=[
            {"label": "A", "question": "Ａ  Question", "needs_timeline": False},
            {"label": "B", "question": "a question", "needs_timeline": True},
        ]
    )
    assigned = assign_plan(p)
    assert len(assigned.tasks) == 1
    assert assigned.tasks[0].task_id == "t1" and assigned.tasks[0].needs_timeline
    for tasks in [
        [],
        [p.tasks[0].model_dump()] * 4,
        [{**p.tasks[0].model_dump(), "task_id": "t1"}],
        [{**p.tasks[0].model_dump(), "needs_timeline": "false"}],
        [{**p.tasks[0].model_dump(), "question": "  "}],
    ]:
        with pytest.raises(ValidationError):
            PlanProposal(tasks=tasks)
    with pytest.raises(ValidationError):
        ResearchPlan(tasks=[assigned.tasks[0].model_dump() | {"task_id": "t2"}])


def test_immutable_serialization_and_defaults():
    r = result()
    assert ResearchResult.model_validate_json(r.model_dump_json()) == r
    with pytest.raises(ValidationError):
        r.status = "failed"
    assert isinstance(r.findings, tuple) and isinstance(r.evidence, tuple)
    c = ConversationInput(question="  original  ")
    assert c.question == "  original  "
    a, b = application_state(c), application_state(c)
    a["results_by_task"]["t1"] = r
    assert not b["results_by_task"]
    w = WorkerInput(task=plan().tasks[0], snapshot_id="s1")
    assert worker_state(w)["validation"].status == "not_run"
    assert worker_state(w)["packed_evidence"] == ()


@pytest.mark.parametrize("start,end", [(0, 3), (2, 5), (6, 12)])
def test_unicode_offsets_roundtrip(start, end):
    e = evidence(start, end)
    e.verify_chunk("αβ🙂de second")
    assert e.text == "αβ🙂de second"[start:end]
    assert EvidenceRecord.model_validate_json(e.model_dump_json()) == e
    with pytest.raises(ValueError):
        e.verify_chunk("wrong chunk")
    with pytest.raises(ValidationError):
        EvidenceRecord(**(e.model_dump() | {"text_hash": "0" * 64}))


@pytest.mark.parametrize("start,end", [(-1, 2), (2, 2), (4, 3), (0, 100)])
def test_invalid_offsets(start, end):
    with pytest.raises(ValueError):
        evidence(start, end)


def test_status_error_provenance_contracts():
    good = result()
    for update in [
        {"status": "failed"},
        {"status": "complete", "gaps": ["missing"]},
        {"snapshot_id": "other"},
        {"task_id": "t2"},
        {"findings": []},
    ]:
        with pytest.raises(ValidationError):
            ResearchResult.model_validate(good.model_dump() | update)
    failed = ResearchResult(
        task_id="t1",
        snapshot_id="s1",
        status="failed",
        error={
            "stage": "retrieve",
            "code": "retrieval_failed",
            "message": "Local retrieval failed",
        },
    )
    with pytest.raises(ValidationError):
        ResearchResult.model_validate(failed.model_dump() | {"findings": good.findings})
    partial = ResearchResult.model_validate(
        good.model_dump()
        | {"status": "insufficient_evidence", "gaps": ["Scope missing"]}
    )
    assert partial.findings
    for status, errors in [("passed", ["bad"]), ("failed", []), ("not_run", ["bad"])]:
        with pytest.raises(ValidationError):
            ValidationReport(status=status, errors=errors)


def test_reducer_laws_no_mutation_or_last_writer():
    a, b, c = (
        {
            f"t{i}": ResearchResult(
                task_id=f"t{i}", snapshot_id="s1", status="insufficient_evidence"
            )
        }
        for i in range(1, 4)
    )
    before = copy.deepcopy(a)
    assert merge_task_results(a, a) == a and a == before
    assert merge_task_results(a, b) == merge_task_results(b, a)
    assert merge_task_results(merge_task_results(a, b), c) == merge_task_results(
        a, merge_task_results(b, c)
    )
    assert merge_task_results(a, {}) is not a
    with pytest.raises(ValueError, match="conflicting"):
        merge_task_results(a, {"t1": result()})
    with pytest.raises(ValueError, match="key mismatch"):
        merge_task_results({}, {"t2": result()})


def test_aliases_are_transient_and_only_presented_records_admitted():
    r = result()
    p = packet()
    assert finding_aliases(p)["F1"] == r.findings[0]
    assert "E1" not in r.model_dump_json() and "F1" not in p.model_dump_json()
    for alias in ["E2", "E0", "1", "", "invented"]:
        with pytest.raises((ValueError, KeyError)):
            admit_findings(
                "t1",
                Findings(findings=[{"statement": "Claim", "evidence_refs": [alias]}]),
                r.evidence,
            )
    with pytest.raises(ValueError):
        admit_findings(
            "t2",
            Findings(findings=[{"statement": "Claim", "evidence_refs": ["E1"]}]),
            r.evidence,
        )
    with pytest.raises(ValidationError):
        SynthesisPacket.model_validate(
            p.model_dump() | {"admitted_finding_ids": ["invented"]}
        )


def test_pure_numbering_shared_chunk_distinct_spans():
    records = (evidence(), evidence(6, 12))
    p = packet(results=(result(records=records),))
    # Include two findings with distinct spans into the same chunk.
    r = p.results[0]
    f2 = r.findings[0].model_dump() | {
        "finding_id": "t1.f2",
        "evidence_refs": [records[1].key],
    }
    r = ResearchResult.model_validate(r.model_dump() | {"findings": [*r.findings, f2]})
    p = packet(results=(r,))
    draft = AnswerDraft(
        units=[
            {"text": "Later", "finding_ids": ["F2"]},
            {"text": "Earlier", "finding_ids": ["F1", "F2"]},
        ]
    )
    before = p.model_dump_json()
    units, text, citations = project_answer(draft, p)
    assert text == "Later [1]\n\nEarlier [1]"
    assert len(citations) == 1 and len(citations[0].excerpts) == 2
    assert citations[0].finding_ids == ("t1.f2", "t1.f1")
    assert p.model_dump_json() == before
    assert render_canonical_units(units, r.evidence) == (text, citations)
    with pytest.raises(ValueError, match="unknown evidence"):
        render_canonical_units(units, ())


@pytest.mark.parametrize("alias", ["F2", "F0", "", "1", "t1.f1"])
def test_invented_final_alias_rejected(alias):
    with pytest.raises(ValueError):
        project_answer(
            AnswerDraft(units=[{"text": "Claim", "finding_ids": [alias]}]), packet()
        )


@pytest.mark.parametrize(
    "text",
    ["Claim [1]", "Claim [invented]", "[ ]", "See https://example.org", "# Heading"],
)
def test_draft_markers_rejected(text):
    with pytest.raises(ValidationError):
        AnswerDraft(units=[{"text": text, "finding_ids": ["F1"]}])


def test_barrier_and_task_omission_rejected():
    pp = assign_plan(
        PlanProposal(
            tasks=[
                {"label": str(i), "question": f"Question {i}", "needs_timeline": False}
                for i in range(2)
            ]
        )
    )
    p = packet(results=(result(), result(task="t2")), research_plan=pp)
    with pytest.raises(ValueError, match="omits"):
        project_answer(AnswerDraft(units=[{"text": "Claim", "finding_ids": ["F1"]}]), p)
    for results in [(result(),), tuple(reversed(p.results))]:
        with pytest.raises(ValidationError):
            packet(results=results, research_plan=pp)


def test_json_fallback_schema_settings_and_no_message_mutation():
    class Fake:
        def generate(self, messages, **settings):
            self.messages, self.settings = messages, settings
            return SimpleNamespace(
                text='{"tasks":[{"label":"A","question":"Why?","needs_timeline":false}]}',
                finish_reason="stop",
            )

    fake = Fake()
    messages = [{"role": "user", "content": "Original"}]
    parsed, response = generate_validated_json(fake, messages, PlanProposal)
    assert parsed.tasks[0].question == "Why?" and response.finish_reason == "stop"
    assert fake.settings == {
        "temperature": 0,
        "max_tokens": 1200,
        "stream": False,
        "json_schema": PlanProposal.model_json_schema(),
    }
    assert (
        json.loads(fake.messages[0]["content"].split("\n", 1)[1])[
            "additionalProperties"
        ]
        is False
    )
    assert messages == [{"role": "user", "content": "Original"}]


@pytest.mark.parametrize(
    "text,reason",
    [
        ("```json\n{}\n```", "stop"),
        ('prose {"tasks": []}', "stop"),
        ('{"tasks": []}', "stop"),
        ("{}", "length"),
        ("x" * 64001, "stop"),
    ],
)
def test_json_fallback_rejects_without_salvage_or_retry(text, reason):
    class Fake:
        calls = 0

        def generate(self, messages, **settings):
            self.calls += 1
            return SimpleNamespace(text=text, finish_reason=reason)

    fake = Fake()
    with pytest.raises(ValueError):
        generate_validated_json(fake, [{"role": "user", "content": "q"}], PlanProposal)
    assert fake.calls == 1


def test_actual_versioned_templates_and_schemas():
    root = Path(__file__).parents[1] / "src/workbench/prompting/templates"
    pairs = [
        (
            "normalize_intent_v1",
            "normalize-intent/v1",
            __import__(
                "workbench.graphs.research_models", fromlist=["RewritePayload"]
            ).RewritePayload,
        ),
        ("plan_research_v1", "plan-research/v1", PlanProposal),
        ("derive_findings_v1", "derive-findings/v1", Findings),
        ("synthesize_findings_v1", "synthesize-findings/v1", AnswerDraft),
    ]
    for name, version, schema in pairs:
        rendered = (
            (root / f"{name}.txt")
            .read_text()
            .format(input_json='{"question":"history"}')
        )
        assert (
            f"Contract: {version}" in rendered and '{"question":"history"}' in rendered
        )
        assert schema.model_json_schema()["additionalProperties"] is False


def test_normalization_and_terminal_events():
    for status, question in [
        ("unresolved", "guess"),
        ("identity", None),
        ("failed", None),
    ]:
        with pytest.raises(ValidationError):
            NormalizedIntent(status=status, standalone_question=question)
    r = ApplicationResult(
        run_id="run",
        outcome="insufficient_evidence",
        delivery="message",
        answer_text="No evidence",
    )
    assert (
        ApplicationEvent(kind="run_finished", run_id="run", sequence=1, result=r).result
        == r
    )
    with pytest.raises(ValidationError):
        ApplicationEvent(kind="task_stage", run_id="run", sequence=1, result=r)
    with pytest.raises(ValidationError):
        ApplicationResult.model_validate(r.model_dump() | {"outcome": "complete"})


def test_real_llamacpp_adapter_forwards_schema():
    import httpx

    from workbench.models.llamacpp_server import LlamaCppServerModel

    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": '{"standalone_question":null}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            },
        )

    from workbench.graphs.research_models import RewritePayload

    with LlamaCppServerModel() as model:
        model.client.close()
        model.client = httpx.Client(
            base_url=model.base_url, transport=httpx.MockTransport(respond)
        )
        parsed, response = generate_validated_json(
            model,
            [{"role": "user", "content": "Who was he?"}],
            RewritePayload,
            max_tokens=200,
        )
    assert parsed.standalone_question is None and response.tokens_out == 2
    assert captured[0]["stream"] is False and captured[0]["max_tokens"] == 200
    from workbench.models.native_schema import native_schema

    assert captured[0]["response_format"]["json_schema"]["schema"] == native_schema(
        RewritePayload.model_json_schema()
    )
    assert (
        RewritePayload.model_json_schema()["properties"]["standalone_question"][
            "anyOf"
        ][0]["maxLength"]
        == 8000
    )


def test_application_result_projection_coverage_and_error_consistency():
    p = packet()
    units, text, citations = project_answer(
        AnswerDraft(units=[{"text": "Event", "finding_ids": ["F1"]}]), p
    )
    r = ApplicationResult(
        run_id="run",
        outcome="complete",
        delivery="synthesis",
        units=units,
        answer_text=text,
        citations=citations,
        coverage=[
            {
                "task_id": "t1",
                "status": "complete",
                "admitted_findings": 1,
                "used_findings": 1,
            }
        ],
    )
    assert ApplicationResult.model_validate_json(r.model_dump_json()) == r
    for change in [
        {"answer_text": "altered"},
        {"citations": []},
        {"coverage": []},
        {
            "coverage": [
                {
                    "task_id": "t1",
                    "status": "complete",
                    "admitted_findings": 1,
                    "used_findings": 0,
                }
            ]
        },
        {"outcome": "failed"},
        {"delivery": "evidence_fallback"},
    ]:
        with pytest.raises(ValidationError):
            ApplicationResult.model_validate(r.model_dump() | change)


def test_pure_numbering_snapshot_grouping_and_shared_provenance():
    from workbench.graphs.research_models import CanonicalUnit

    first, other_task, other_snapshot = (
        evidence(),
        evidence(task="t2"),
        evidence(snapshot="s2"),
    )
    units = (
        CanonicalUnit(
            text="Other snapshot",
            finding_ids=("t1.f1",),
            evidence_refs=(other_snapshot.key,),
        ),
        CanonicalUnit(
            text="Shared", finding_ids=("t1.f2", "t2.f1"), evidence_refs=(first.key,)
        ),
    )
    text, citations = render_canonical_units(units, (first, other_task, other_snapshot))
    assert text == "Other snapshot [1]\n\nShared [2]"
    assert citations[1].excerpts[0].presented_to == ("t1", "t2")
    assert citations[0].snapshot_id == "s2" and citations[1].snapshot_id == "s1"
    conflict = EvidenceRecord.model_validate(first.model_dump() | {"title": "Conflict"})
    with pytest.raises(ValueError, match="conflicting"):
        render_canonical_units(units, (first, conflict, other_snapshot))


def test_templates_render_through_existing_builder_with_versions():
    from workbench.prompting.prompt_builder import PromptBuilder

    builder = PromptBuilder()
    for name in [
        "normalize_intent_v1",
        "plan_research_v1",
        "derive_findings_v1",
        "synthesize_findings_v1",
    ]:
        rendered = builder.render(name, input_json='{"context":"untrusted {data}"}')
        assert "untrusted {data}" in rendered
        assert builder.get_version(name).startswith("v-")
