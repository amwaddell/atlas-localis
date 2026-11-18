"""Model-free Stage 2b isolation, budget, provenance and failure regressions."""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from workbench.core.types import Chunk, ModelResponse, RerankResult, RetrievalResult
from workbench.graphs.finding_dates import chronological_findings, historical_date
from workbench.graphs.research_models import Limits, ResearchTask, WorkerInput
from workbench.graphs.researcher_graph import (
    build_evidence_worker,
)
from workbench.retrieval.context_builder import SimpleContextBuilder
from workbench.retrieval.evidence_packing import (
    MARGIN,
    REPAIR_RESERVE,
    findings_messages,
    pack_evidence,
    request_bound,
)
from workbench.retrieval.hybrid import HybridRetriever


def chunk(cid="a", doc="doc", text="Rome changed in 27 BC.", **kwargs):
    return Chunk(
        chunk_id=cid,
        document_id=doc,
        title=kwargs.get("title", "Rome"),
        section="History",
        text=text,
        token_count=1,
        start_offset=100,
        end_offset=100 + len(text),
    )


def value(task_id="t1", question="When did Rome change?", timeline=False, **limits):
    return WorkerInput(
        task=ResearchTask(
            task_id=task_id, label="Rome", question=question, needs_timeline=timeline
        ),
        snapshot_id="snapshot-v1",
        limits=Limits(**limits),
    )


def draft(alias="E1", statement="Rome changed in 27 BC.", date=None, gaps=()):
    return json.dumps(
        {
            "findings": [
                {"statement": statement, "evidence_refs": [alias], "date_text": date}
            ],
            "gaps": list(gaps),
        }
    )


class Model:
    def __init__(self, *responses):
        self.responses = responses or (draft(),)
        self.calls = []

    def generate(self, messages, **settings):
        self.calls.append((messages, settings))
        response = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(response, Exception):
            raise response
        if isinstance(response, ModelResponse):
            return response
        return ModelResponse(text=response, tokens_in=100, tokens_out=60, latency_ms=1)


class Search:
    def __init__(self, ids):
        self.ids, self.calls = ids, []

    def search(self, query, top_k):
        self.calls.append((query, top_k))
        return [
            RetrievalResult(chunk_id=cid, score=1, source="keyword") for cid in self.ids
        ]


class Store:
    def __init__(self, chunks):
        self.chunks, self.calls = chunks, []

    def get_many(self, ids):
        self.calls.append(ids)
        return [c for c in self.chunks if c.chunk_id in ids]


class Reranker:
    def __init__(self):
        self.calls = []

    def rerank(self, query, chunks, top_n):
        self.calls.append((query, chunks, top_n))
        return [
            RerankResult(chunk_id=c.chunk_id, rerank_score=1, original_score=0)
            for c in reversed(chunks)
        ][:top_n]


def setup(chunks=None, ids=None, model=None, rerank=False, **kwargs):
    chunks = [chunk()] if chunks is None else chunks
    ids = [c.chunk_id for c in chunks] if ids is None else ids
    kw, vec, store, ranker = Search(ids), Search(ids), Store(chunks), Reranker()
    retriever = HybridRetriever(kw, vec, ranker if rerank else None, store, **kwargs)
    model = model or Model()
    graph = build_evidence_worker(
        retriever=retriever, model=model, snapshot_id="snapshot-v1"
    )
    return SimpleNamespace(
        graph=graph,
        retriever=retriever,
        model=model,
        kw=kw,
        vec=vec,
        store=store,
        ranker=ranker,
    )


def run(env, task=None, **extra):
    result = env.graph.invoke({"input": task or value(), **extra})
    assert set(result) == {"result"}
    return result["result"]


def test_isolated_fresh_task_state_and_code_assigned_ids():
    env = setup()
    first = run(
        env,
        value("t1", "First exact query"),
        chat_history=["SECRET"],
        sibling_results="SECRET",
        findings_draft="SECRET",
        repair_count=99,
    )
    second = run(env, value("t2", "Second exact query"))
    assert first.findings[0].finding_id == "t1.f1"
    assert second.findings[0].finding_id == "t2.f1"
    assert first.evidence[0].presented_to == ("t1",)
    assert second.evidence[0].presented_to == ("t2",)
    assert [call[0].text for call in env.kw.calls] == [
        "First exact query",
        "Second exact query",
    ]
    assert "SECRET" not in str(env.model.calls)
    assert "First exact query" not in str(env.model.calls[1])
    assert len(env.model.calls) == 2


def test_snapshot_mismatch_does_not_search_or_generate():
    env = setup()
    result = run(env, value().model_copy(update={"snapshot_id": "other"}))
    assert result.status == "failed"
    assert result.error.code == "snapshot_mismatch"
    assert not env.kw.calls and not env.model.calls


def test_worker_input_rejects_parent_fields():
    data = value().model_dump()
    data["history"] = []
    with pytest.raises(ValidationError):
        run(setup(), data)


def test_hybrid_bounds_and_single_hydration_reranker():
    chunks = [chunk(str(i), doc=str(i)) for i in range(100)]
    env = setup(
        chunks,
        model=Model(draft()),
        rerank=True,
        keyword_top_k=999,
        vector_top_k=999,
        merge_top_n=999,
    )
    result = run(env)
    assert (
        len(env.kw.calls)
        == len(env.vec.calls)
        == len(env.store.calls)
        == len(env.ranker.calls)
        == 1
    )
    assert env.kw.calls[0][1] == env.vec.calls[0][1] == 20
    assert len(env.store.calls[0]) <= 50
    assert result.diagnostics.opened == 20
    assert result.evidence[0].key.chunk_id == env.ranker.calls[0][1][-1].chunk_id
    assert result.diagnostics.presented <= 5


def test_hydration_reused_without_open_or_timeline_calls():
    env = setup(rerank=True, model=Model(draft(date="27 BC")))
    result = run(env, value(timeline=True))
    assert result.status == "complete"
    assert result.findings[0].date_text == "27 BC"
    assert len(env.store.calls) == len(env.ranker.calls) == len(env.model.calls) == 1


def test_exact_offsets_hash_and_document_diversity():
    chunks = [chunk(str(i), text=f"Exact ünicode {i} in 27 BC.") for i in range(6)]
    chunks += [chunk("other", doc="another")]
    env = setup(chunks)
    result = run(env)
    assert len(result.evidence) == 4
    assert sum(e.document_id == "doc" for e in result.evidence) == 3
    for e in result.evidence:
        original = next(c for c in chunks if c.chunk_id == e.key.chunk_id)
        assert e.key.start == 0 and e.key.end == len(original.text)
        e.verify_chunk(original.text)
        assert e.key.snapshot_id == "snapshot-v1"
    payload = json.loads(env.model.calls[0][0][1]["content"].split("INPUT_JSON:\n")[1])
    assert [e["text"] for e in payload["evidence"]] == [e.text for e in result.evidence]
    assert [e["alias"] for e in payload["evidence"]] == ["E1", "E2", "E3", "E4"]


def test_complete_rendered_budget_skips_oversized_first_and_long_headers():
    v = value(prompt_tokens=6000)
    huge = chunk("huge", text="x" * 15000)
    long_title = chunk("title", title="ü" * 1999)
    small = chunk("small", doc="different")
    packed = pack_evidence(v, (huge, long_title, small, small))
    assert [e.key.chunk_id for e in packed] == ["small"]
    assert (
        request_bound(findings_messages(v, packed))
        + v.limits.output_tokens
        + REPAIR_RESERVE
        <= v.limits.prompt_tokens
    )
    assert MARGIN > 0


def test_legacy_context_builder_has_no_oversized_first_exception():
    builder = SimpleContextBuilder(header_template="{title}: {chunk_id}\n")
    from workbench.retrieval.evidence_packing import tokenizer_budget

    # Calibrated token counts preserve the original oversized/header regression.
    with tokenizer_budget(lambda text: (len(text) + 3) // 4):
        text, ids = builder.build_context(
            [chunk("huge", text="x" * 1000), chunk("small")], max_tokens=30
        )
        assert ids == ["small"] and len(text) <= 120
    # Without a tokenizer the conservative byte bound must still hold.
    text, _ = builder.build_context([chunk("small")], max_tokens=30)
    assert len(text.encode()) <= 30
    assert builder.build_context([chunk()], max_tokens=0) == ("", [])
    assert builder.build_context([chunk(title="long" * 500)], max_tokens=10) == ("", [])


def test_impossible_base_request_budget_is_technical_failure():
    env = setup()
    result = run(env, value(prompt_tokens=100))
    assert result.status == "failed" and result.error.code == "packing_failed"
    assert not env.model.calls


def test_no_chunks_fit_skips_model_with_explicit_gap():
    env = setup([chunk(text="x" * 16001)])
    result = run(env)
    assert result.status == "insufficient_evidence" and not result.evidence
    assert "budget" in " ".join(result.gaps) and not env.model.calls


def test_no_hit_skips_generation_distinct_from_total_hydration_failure():
    empty = setup([])
    absent = run(empty)
    assert absent.status == "insufficient_evidence" and absent.error is None
    assert not empty.model.calls and not empty.store.calls
    missing = setup([], ids=["missing"])
    failed = run(missing)
    assert failed.status == "failed" and failed.error.code == "hydration_failed"
    assert not missing.model.calls and not failed.findings


def test_partial_hydration_retains_findings_with_gap():
    env = setup(ids=["a", "missing"])
    result = run(env)
    assert result.status == "insufficient_evidence" and result.findings and result.gaps
    assert result.warnings[0].code == "hydration_partial"
    assert result.diagnostics.retrieved == 2 and result.diagnostics.opened == 1


@pytest.mark.parametrize("stage", ["search", "hydrate", "rerank"])
def test_retrieval_technical_failure(stage):
    env = setup(rerank=True)

    def fail(*args, **kwargs):
        raise RuntimeError("broken")

    if stage == "search":
        env.kw.search = fail
    elif stage == "hydrate":
        env.store.get_many = fail
    else:
        env.ranker.rerank = fail
    result = run(env)
    assert result.status == "failed" and result.error.code == "retrieval_failed"
    assert not result.findings and not env.model.calls


@pytest.mark.parametrize(
    "payload",
    [
        "unvalidated prose [a]",
        "```json\n{}\n```",
        "{}",
        '{"findings":[],"extra":1}',
        draft("E2"),
        draft("E5"),
        draft("E9"),
        draft(statement="Bad marker [a]."),
        draft(date="44 BC"),
        draft(statement="One.\nTwo."),
        json.dumps({"findings": [json.loads(draft())["findings"][0]] * 9}),
        json.dumps({"findings": [{"statement": "Rome", "evidence_refs": []}]}),
    ],
)
def test_invalid_findings_repair_once_same_evidence_and_search(payload):
    env = setup(model=Model(payload, draft()))
    result = run(env, value(max_evidence=1))
    assert result.status == "complete" and result.findings[0].finding_id == "t1.f1"
    assert len(env.kw.calls) == len(env.store.calls) == 1
    assert len(env.model.calls) == 2
    first, second = [c[0][1]["content"] for c in env.model.calls]
    assert second.startswith(first)
    assert "SAME evidence" in second
    assert result.diagnostics.tokens_in == 200 and result.diagnostics.tokens_out == 120


def test_repair_exhaustion_fails_and_never_strips_markers():
    env = setup(model=Model(draft(statement="Unsupported [bogus].")))
    result = run(env)
    assert result.status == "failed" and not result.findings
    assert result.error.code == "findings_invalid" and len(env.model.calls) == 2
    assert not result.findings
    assert result.error.code == "findings_invalid"


@pytest.mark.parametrize(
    "responses, calls",
    [
        ([RuntimeError("offline model failure")], 1),
        (["{}", RuntimeError("repair failure")], 2),
    ],
)
def test_model_failures_are_technical(responses, calls):
    env = setup(model=Model(*responses))
    result = run(env)
    assert result.status == "failed" and result.error.code == "model_failed"
    assert len(env.model.calls) == calls and not result.findings


@pytest.mark.parametrize(
    "response",
    [
        ModelResponse(
            text=draft(),
            tokens_in=10,
            tokens_out=5,
            latency_ms=0,
            finish_reason="length",
        ),
        ModelResponse(text=draft(), tokens_in=10, tokens_out=9999, latency_ms=0),
        " " * 1201,
    ],
)
def test_output_budget_and_truncation_are_repaired_or_fail(response):
    env = setup(model=Model(response))
    result = run(env)
    assert result.status == "failed"
    assert len(env.model.calls) == 2
    for messages, settings in env.model.calls:
        from workbench.graphs.constrained_evidence import findings_schema

        assert settings == dict(
            temperature=0,
            max_tokens=1200,
            stream=False,
            json_schema=findings_schema(result.evidence),
        )
        assert sum(len(m["content"].encode()) for m in messages) + 1200 < 6000


@pytest.mark.parametrize(
    "payload, timeline, status, count",
    [
        ('{"findings":[],"gaps":[]}', False, "insufficient_evidence", 0),
        (
            '{"findings":[],"gaps":["No support for the requested event."]}',
            True,
            "insufficient_evidence",
            0,
        ),
        (draft(gaps=["Later period is missing."]), False, "insufficient_evidence", 1),
        (draft(), True, "insufficient_evidence", 1),
        (draft(date="27 BC"), True, "complete", 1),
    ],
)
def test_empty_partial_and_missing_chronology(payload, timeline, status, count):
    env = setup(model=Model(payload))
    result = run(env, value(timeline=timeline))
    assert result.status == status and len(result.findings) == count
    assert len(env.model.calls) == 1
    assert bool(result.gaps) == (status != "complete")


@pytest.mark.parametrize(
    "text, start, end, precision",
    [
        ("44 BCE", (-44, 0, 0), None, "year"),
        ("15 March 44 BC", (-44, 3, 15), None, "day"),
        ("March 1900", (1900, 3, 0), None, "month"),
        ("500–400 BCE", (-500, 0, 0), (-400, 0, 0), "range"),
        ("44 BCE–14 CE", (-44, 0, 0), (14, 0, 0), "range"),
        ("1914–1918", (1914, 0, 0), (1918, 0, 0), "range"),
        ("c. 500 BCE", None, None, "unknown"),
        ("early 1800s", None, None, "unknown"),
        ("44 BC?", None, None, "unknown"),
        ("unknown", None, None, "unknown"),
        ("0 CE", None, None, "unknown"),
        ("400–500 BCE", None, None, "unknown"),
        ("31 February 1900", None, None, "unknown"),
    ],
)
def test_historical_dates_preserve_era_precision_range_and_uncertainty(
    text, start, end, precision
):
    result = historical_date(text)
    assert result.original == text and result.start == start and result.end == end
    assert result.precision == precision


def test_chronology_orders_disjoint_intervals_and_preserves_overlap_preference():
    dates = ["27 BC", "c. 500 BCE", "509 BC", "1900–2000", "1950", "44 BCE"]
    findings = [SimpleNamespace(date_text=d) for d in dates]
    ordered = chronological_findings(findings)
    assert [f.date_text for f in ordered] == [
        "c. 500 BCE",
        "509 BC",
        "44 BCE",
        "27 BC",
        "1900–2000",
        "1950",
    ]


def test_timeline_dates_are_grounded_in_referenced_presented_excerpt():
    text = "The war lasted 500–400 BCE, perhaps c. 500 BCE at its start."
    env = setup(
        [chunk(text=text)],
        model=Model(draft(statement=text, date="500–400 BCE")),
    )
    result = run(env, value(timeline=True))
    assert result.status == "complete"
    assert chronological_findings(result.findings)[0].date_text == "500–400 BCE"


@pytest.mark.parametrize("stage", ["request", "task"])
def test_deadline_overrun_is_failure_without_further_generation(monkeypatch, stage):
    import workbench.graphs.researcher_graph as worker_module

    now = [0.0]
    env = setup()
    original = env.model.generate

    def delayed(*args, **kwargs):
        response = original(*args, **kwargs)
        now[0] = 100.0 if stage == "request" else 300.0
        return response

    env.model.generate = delayed
    monkeypatch.setattr(worker_module.time, "monotonic", lambda: now[0])
    result = run(env)
    assert result.status == "failed" and result.error.code == "model_failed"
    assert len(env.model.calls) == 1


def test_duplicate_references_and_unknown_reranker_records_fail():
    bad = json.loads(draft())
    bad["findings"][0]["evidence_refs"] = ["E1", "E1"]
    env = setup(model=Model(json.dumps(bad)))
    assert run(env).status == "failed" and len(env.model.calls) == 2
    env = setup(rerank=True)
    env.ranker.rerank = lambda *a, **kw: [
        RerankResult(chunk_id="unhydrated", rerank_score=1, original_score=0)
    ]
    result = run(env)
    assert result.status == "failed" and not env.model.calls


def test_invalid_repair_cannot_export_valid_first_attempt_fragments():
    data = json.loads(draft())
    data["findings"].append({"statement": "Not presented.", "evidence_refs": ["E2"]})
    env = setup(model=Model(json.dumps(data), "not JSON"))
    result = run(env, value(max_evidence=1))
    assert result.status == "failed" and result.findings == ()
    assert len(env.model.calls) == 2


def test_per_task_limits_apply_to_diversity_and_count():
    env = setup([chunk(str(i), doc=str(i // 2)) for i in range(8)])
    result = run(env, value(max_per_document=1, max_evidence=2))
    assert len(result.evidence) == 2
    assert len({e.document_id for e in result.evidence}) == 2


def test_all_eight_finding_ids_assigned_only_to_validated_order():
    data = {
        "findings": [
            {"statement": f"Fact {i}.", "evidence_refs": ["E1"]} for i in range(8)
        ],
        "gaps": [],
    }
    env = setup(
        [chunk(text=" ".join(f"Fact {i}." for i in range(8)))],
        model=Model(json.dumps(data)),
    )
    result = run(env, value("t3"))
    assert result.status == "complete" and len(env.model.calls) == 1
    assert [f.finding_id for f in result.findings] == [f"t3.f{i}" for i in range(1, 9)]


def test_partial_preserves_model_gaps_and_automatic_gaps():
    env = setup(
        ids=["a", "missing"],
        model=Model(draft(gaps=["Gap one.", "Gap two.", "Gap three."])),
    )
    result = run(env, value(timeline=True))
    assert result.status == "insufficient_evidence" and result.findings
    assert len(result.gaps) <= 3
    gaps = " ".join(result.gaps)
    assert all(
        g in gaps
        for g in ["Gap one.", "Gap two.", "Gap three.", "chronology", "hydrated"]
    )


def test_repair_feedback_does_not_promote_invalid_model_prose_to_context():
    env = setup(
        model=Model(draft(statement="Raw invented prose [SECRET_RAW_DRAFT]."), draft())
    )
    result = run(env)
    assert result.status == "complete" and len(env.model.calls) == 2
    assert "SECRET_RAW_DRAFT" not in str(env.model.calls[1])


def test_findings_schema_sent_on_generation_and_repair_without_alias_relaxation():
    from workbench.graphs.constrained_evidence import findings_schema

    model = Model(draft(alias="E2"), draft())
    result = run(setup(model=model))
    assert len(model.calls) == 2  # E2 is schema-valid but was never presented.
    assert result.findings
    assert all(
        settings["json_schema"] == findings_schema(result.evidence)
        for _, settings in model.calls
    )
