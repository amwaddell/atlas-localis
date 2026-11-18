"""Canonical application regressions, through real Send and nested workers."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from tests.test_evidence_worker import Model, chunk, draft

from workbench.core.types import ModelResponse
from workbench.graphs.execution import (
    _ACCELERATOR,
    AdmittedModel,
    AdmittedRetriever,
    Execution,
    TaskContext,
    current_task,
)
from workbench.graphs.planning import bounded_conversation
from workbench.graphs.research_models import (
    ConversationInput,
    ConversationTurn,
    Finding,
    Limits,
    NormalizedIntent,
    ResearchPlan,
    ResearchResult,
    ResearchTask,
    excerpt,
)
from workbench.graphs.synthesis import aggregate, registry
from workbench.retrieval.hybrid import HydratedRetrieval
from workbench.systems.runner import Resources, create_application


def proposal(n=1):
    return json.dumps(
        {
            "tasks": [
                {
                    "label": f"Task {i}",
                    "question": f"Question {i}",
                    "needs_timeline": False,
                }
                for i in range(1, n + 1)
            ]
        }
    )


def answer(*aliases):
    aliases = aliases or ("F1",)
    return json.dumps(
        {
            "units": [
                {
                    "text": " ".join("Rome changed in 27 BC." for _ in aliases),
                    "finding_ids": list(aliases),
                }
            ]
        }
    )


class Retriever:
    def __init__(self, failed=(), empty=()):
        self.failed, self.empty, self.calls = failed, empty, []

    def retrieve_hydrated(self, query, top_k):
        self.calls.append((query.text, current_task.get()))
        if query.text in self.failed:
            raise OSError("fixture unavailable")
        if query.text in self.empty:
            return HydratedRetrieval((), 0, 0)
        return HydratedRetrieval((chunk(),), 1, 1)


def graph(model=None, retriever=None, **kwargs):
    return create_application(
        resources=Resources(
            model or Model(proposal(), draft(), answer()),
            retriever or Retriever(),
            "snapshot-v1",
        ),
        **kwargs,
    )


def conversation(value):
    return ConversationInput(
        question=value["question"],
        history=value.get("chat_history") or (),
        summary=value.get("conversation_summary") or "",
    )


def run(g, **value):
    return g.invoke(conversation(value or {"question": "Original question"}))


def test_one_task_always_synthesizes_and_none_safe_input():
    model = Model(proposal(), draft(), answer())
    result = run(
        graph(model),
        question="Original question",
        chat_history=None,
        conversation_summary=None,
    )
    assert result.outcome == "complete"
    assert len(model.calls) == 3
    assert result.answer_text == "Rome changed in 27 BC. [1]"
    assert result.final_validation.status == "passed"
    assert "Original question" in model.calls[0][0][1]["content"]
    assert "synthesize-findings/v1" in model.calls[-1][0][1]["content"]


@pytest.mark.parametrize(
    "context",
    [
        {
            "chat_history": [
                {"role": "user", "content": "Rome before 30 BC excluding kings"}
            ]
        },
        {"conversation_summary": "Rome before 30 BC excluding kings"},
    ],
)
def test_context_original_constraints_reach_normalizer_and_planner(context):
    model = Model(
        '{"standalone_question":"Rome before 30 BC excluding kings"}',
        proposal(),
        draft(),
        answer(),
    )
    result = run(graph(model), question="What about its end?", **context)
    assert result.outcome == "complete"
    assert len(model.calls) == 4
    for call in model.calls[:2]:
        assert "What about its end?" in call[0][1]["content"]
        assert "Rome before 30 BC excluding kings" in call[0][1]["content"]
    assert "What about its end?" not in model.calls[2][0][1]["content"]


@pytest.mark.parametrize(
    "response,outcome",
    [
        ('{"standalone_question":null}', "insufficient_evidence"),
        ("bad JSON", "failed"),
        (OSError("offline"), "failed"),
    ],
)
def test_unresolved_and_failed_normalization_stop_research(response, outcome):
    retriever = Retriever()
    model = Model(response)
    result = run(
        graph(model, retriever),
        question="What about it?",
        conversation_summary="Earlier topic unavailable",
    )
    assert result.outcome == outcome
    assert result.final_validation.status == "not_run"
    assert not result.units and not retriever.calls and len(model.calls) == 1


@pytest.mark.parametrize(
    "bad",
    [
        "bad",
        '{"tasks":[]}',
        proposal(4),
        '{"tasks":[{"label":"x","question":"q","needs_timeline":"false"}]}',
        OSError("planner"),
    ],
)
def test_malformed_plan_one_full_question_fallback(bad):
    model = Model(bad, draft(date="27 BC"), answer())
    retriever = Retriever()
    result = run(
        graph(model, retriever), question="Original before 30 BC excluding kings"
    )
    assert result.outcome == "complete"
    assert retriever.calls[0][0] == "Original before 30 BC excluding kings"
    assert result.warnings[0].code == "planner_fallback"
    assert len(model.calls) == 3


def test_deduplication_ids_and_timeline_or():
    tasks = [
        {"label": "First", "question": "ROME  history", "needs_timeline": False},
        {"label": "Duplicate", "question": "ＲＯＭＥ history", "needs_timeline": True},
    ]
    model = Model(json.dumps({"tasks": tasks}), draft(date="27 BC"), answer())
    retriever = Retriever()
    result = run(graph(model, retriever))
    assert len(result.coverage) == 1 and result.coverage[0].task_id == "t1"
    assert len(retriever.calls) == 1
    assert '"needs_timeline": true' in model.calls[1][0][1]["content"]


@pytest.mark.parametrize(
    "failed,empty,outcome,calls",
    [
        (("Question 2",), (), "partial", 3),
        (("Question 1", "Question 2"), (), "failed", 1),
        ((), ("Question 1", "Question 2"), "insufficient_evidence", 1),
        (("Question 1",), ("Question 2",), "failed", 1),
    ],
)
def test_partial_all_failure_and_one_survivor(failed, empty, outcome, calls):
    model = Model(proposal(2), draft(), answer())
    result = run(graph(model, Retriever(failed, empty)))
    assert result.outcome == outcome and len(model.calls) == calls
    assert len(result.coverage) == 2
    if outcome == "partial":
        assert result.delivery == "synthesis" and result.coverage[1].status == "failed"
        assert result.gaps
    else:
        assert result.final_validation.status == "not_run" and not result.citations


@pytest.mark.parametrize(
    "invalid",
    [
        answer("F9"),
        '{"units":[{"text":"Bad [fake]","finding_ids":["F1"]}]}',
        "bad JSON",
        OSError("synthesis"),
    ],
)
def test_repair_exhaustion_uses_validated_evidence_fallback(invalid):
    model = Model(proposal(), draft(), invalid, invalid)
    result = run(graph(model))
    assert len(model.calls) == 4
    assert result.delivery == "evidence_fallback" and result.outcome == "partial"
    assert result.final_validation.status == "passed"
    assert result.answer_text == "Rome changed in 27 BC. [1]"
    before, after = (
        call[0][1]["content"].split("\nRepair against SAME")[0]
        for call in model.calls[-2:]
    )
    assert before == after


def test_omitted_task_repaired_and_conflicts_retained():
    corrected = json.loads(answer("F1", "F2"))
    corrected["conflict_groups"] = [["F1", "F2"]]
    model = Model(proposal(2), draft(), draft(), answer("F1"), json.dumps(corrected))
    result = run(graph(model))
    assert len(model.calls) == 5 and result.outcome == "complete"
    assert result.conflicts == (("t1.f1", "t2.f1"),)
    assert result.citations[0].excerpts[0].presented_to == ("t1", "t2")


def test_impossible_budget_no_model_calls_or_factual_answer():
    model = Model()
    result = run(graph(model, limits=Limits(prompt_tokens=10)))
    assert result.outcome == "failed" and not result.units and not model.calls


def test_history_budget_retains_whole_recent_exchanges():
    history = tuple(
        ConversationTurn(role=r, content=str(i) + "x" * 1000)
        for i in range(10)
        for r in ("user", "assistant")
    )
    value = bounded_conversation(
        ConversationInput(question="Q", history=history, summary="s" * 4000)
    )
    assert value.history_truncated and len(value.summary) == 2000
    assert value.history[0].role == "user" and value.history[-1] == history[-1]
    assert sum(len(t.content) for t in value.history) + len(value.summary) <= 6000


def packet_fixture(n=3, findings=8):
    plan = ResearchPlan(
        tasks=tuple(
            ResearchTask(
                task_id=f"t{i}", label=str(i), question=f"Q{i}", needs_timeline=False
            )
            for i in range(1, n + 1)
        )
    )
    results = {}
    for task in plan.tasks:
        evidence = excerpt(
            snapshot_id="s",
            chunk_id="shared",
            full_text="source",
            start=0,
            end=6,
            task_id=task.task_id,
            document_id="d",
            title="T",
        )
        results[task.task_id] = ResearchResult(
            task_id=task.task_id,
            snapshot_id="s",
            status="complete",
            evidence=(evidence,),
            findings=tuple(
                Finding(
                    finding_id=f"{task.task_id}.f{i}",
                    statement=f"Statement {i}",
                    evidence_refs=(evidence.key,),
                )
                for i in range(1, findings + 1)
            ),
        )
    return plan, results


def test_round_robin_budget_and_registry_conflicts():
    plan, results = packet_fixture()
    args = (
        ConversationInput(question="Q"),
        NormalizedIntent(status="identity", standalone_question="Q"),
        plan,
    )
    packet = aggregate(
        *args, dict(reversed(list(results.items()))), "s", Limits(prompt_tokens=24000)
    )
    assert len(packet.admitted_finding_ids) == 12
    assert [
        sum(fid.startswith(t.task_id + ".") for fid in packet.admitted_finding_ids)
        for t in plan.tasks
    ] == [4, 4, 4]
    assert len(packet.budget_omissions) == 12
    assert next(iter(registry(packet).values())).presented_to == ("t1", "t2", "t3")
    with pytest.raises(ValueError, match="barrier"):
        aggregate(*args, {"t1": results["t1"]}, "s", Limits())
    other = excerpt(
        snapshot_id="s",
        chunk_id="shared",
        full_text="ALTER!",
        start=0,
        end=6,
        task_id="t2",
        document_id="d",
        title="T",
    )
    results["t2"] = results["t2"].model_copy(update={"evidence": (other,)})
    with pytest.raises(ValueError, match="conflicting"):
        aggregate(*args, results, "s", Limits())


def nested_stream_probe():
    # Worker gate serializes resource use, but order may vary; responses use
    # task context to remain independent of scheduler order.
    class TaskModel:
        def __init__(self):
            self.counts = {}

        def generate(self, messages, **settings):
            content = messages[-1]["content"]
            if "plan-research/v1" in content:
                text = proposal(3)
            elif "derive-findings/v1" in content:
                task = current_task.get()
                count = self.counts.get(task.task_id, 0)
                self.counts[task.task_id] = count + 1
                text = "bad" if task.task_id == "t2" and count == 0 else draft()
            else:
                text = answer("F1", "F2", "F3")
            return ModelResponse(text=text, tokens_in=1, tokens_out=1, latency_ms=0)

    model = TaskModel()
    events = []
    g = graph(model, event_sink=events.append, limits=Limits(prompt_tokens=16000))
    for _ in range(2):
        updates = list(
            g.stream(ConversationInput(question="Q"), {"max_concurrency": 1})
        )
        assert sum(e.kind == "task_finished" for e in updates) == 3
        assert sum(e.kind == "synthesizing" for e in updates) == 1
        final = updates[-1].result
        assert final.outcome == "complete"
        assert [c.task_id for c in final.coverage] == ["t1", "t2", "t3"]
    assert len([e for e in events if e.kind == "run_finished"]) == 2
    assert len({e.run_id for e in events}) == 2
    assert all(e.message is None for e in events)


def test_cancellation_retains_gate_until_native_result_drains():
    entered, release, cancel = threading.Event(), threading.Event(), threading.Event()

    class Blocking(Model):
        def generate(self, *args, **kwargs):
            entered.set()
            assert release.wait(3)
            return super().generate(*args, **kwargs)

    events = []
    cancelling = threading.Event()

    def sink(event):
        events.append(event)
        if event.kind == "cancelling":
            cancelling.set()

    g = graph(Blocking(proposal()), event_sink=sink)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(
            g.invoke, ConversationInput(question="Q"), cancel_event=cancel
        )
        assert entered.wait(3)
        cancel.set()
        assert cancelling.wait(3)
        assert not _ACCELERATOR.acquire(blocking=False)
        assert not future.done()
        release.set()
        result = future.result(timeout=5)
    assert result.outcome == "cancelled" and not result.units
    assert events[-1].kind == "run_finished"
    assert len([e for e in events if e.kind == "run_finished"]) == 1
    assert _ACCELERATOR.acquire(blocking=False)
    _ACCELERATOR.release()


def test_request_timeout_discards_late_output():
    class Slow(Model):
        def generate(self, *args, **kwargs):
            time.sleep(0.015)
            return super().generate(*args, **kwargs)

    result = run(graph(Slow(proposal(), draft()), limits=Limits(request_seconds=0.005)))
    assert result.outcome == "failed" and not result.units


def test_shared_model_retrieval_accelerator_and_context_isolation():
    active = 0
    peak = 0
    lock = threading.Lock()

    class Operations:
        def generate(self, *args, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.01)
            with lock:
                active -= 1
            return ModelResponse(text="{}", tokens_in=0, tokens_out=0, latency_ms=0)

        def retrieve_hydrated(self, *args, **kwargs):
            return self.generate()

    operations = Operations()

    def work(task_id, model):
        execution = Execution("s", Limits())
        task = TaskContext(execution.run_id, task_id, "s", time.monotonic() + 5)
        with execution.bind(task):
            if model:
                AdmittedModel(operations).generate([])
            else:
                AdmittedRetriever(operations).retrieve_hydrated(None)
            assert current_task.get() == task
        assert current_task.get() is None

    with ThreadPoolExecutor(3) as pool:
        futures = [pool.submit(work, f"t{i}", i != 2) for i in range(1, 4)]
        for future in futures:
            future.result(timeout=5)
    assert peak == 1


def test_canonical_out_of_order_completion_preserves_barrier_and_isolation(monkeypatch):
    import workbench.graphs.supervisor_graph as supervisor
    from workbench.graphs.researcher_graph import build_evidence_worker

    real_builder = build_evidence_worker

    def build_delayed(**kwargs):
        actual = real_builder(**kwargs)

        class DelayedWorker:
            def invoke(self, value, config):
                output = actual.invoke(value, config=config)
                # Hold each workflow slot through its complete terminal return.
                time.sleep(
                    {"t1": 0.20, "t2": 0.10, "t3": 0}[value["input"].task.task_id]
                )
                return output

        return DelayedWorker()

    monkeypatch.setattr(supervisor, "build_evidence_worker", build_delayed)
    # Measured opt-in simulation only; production remains one workflow.
    monkeypatch.setattr(supervisor, "_WORKER", threading.BoundedSemaphore(3))
    events = []
    model = Model(proposal(3), draft(), draft(), draft(), answer("F1", "F2", "F3"))
    retriever = Retriever()
    result = run(
        graph(
            model,
            retriever,
            limits=Limits(prompt_tokens=16000),
            event_sink=events.append,
        )
    )
    assert result.outcome == "complete"
    completion = [e.task_id for e in events if e.kind == "task_finished"]
    assert completion == ["t3", "t2", "t1"]
    assert [c.task_id for c in result.coverage] == ["t1", "t2", "t3"]
    assert {context.task_id for _, context in retriever.calls} == {"t1", "t2", "t3"}
    assert all(
        context.snapshot_id == "snapshot-v1" and context.run_id == result.run_id
        for _, context in retriever.calls
    )


def test_registry_invariant_returns_no_factual_answer():
    class Conflicting(Retriever):
        def retrieve_hydrated(self, query, top_k):
            text = (
                "Rome changed in 27 BC."
                if query.text == "Question 1"
                else "Rime changed in 27 BC."
            )
            return HydratedRetrieval((chunk(text=text),), 1, 1)

    model = Model(
        proposal(2),
        draft(),
        draft(statement="Rime changed in 27 BC."),
        answer("F1", "F2"),
    )
    result = run(graph(model, Conflicting()))
    assert result.outcome == "failed" and result.error.code == "barrier_failed"
    assert not result.units and not result.citations and len(model.calls) == 3


def test_worker_task_deadline_allows_safe_sibling_completion():
    class SlowTask(Retriever):
        def retrieve_hydrated(self, query, top_k):
            if query.text == "Question 1":
                time.sleep(0.02)
            return super().retrieve_hydrated(query, top_k)

    model = Model(proposal(2), draft(), answer())
    result = run(graph(model, SlowTask(), limits=Limits(task_seconds=0.01)))
    assert result.outcome == "partial"
    assert [c.status for c in result.coverage] == ["failed", "complete"]
    assert len(model.calls) == 3


def test_run_deadline_discards_late_plan_and_skips_workers():
    class SlowPlan(Model):
        def generate(self, *args, **kwargs):
            time.sleep(0.02)
            return super().generate(*args, **kwargs)

    retriever = Retriever()
    result = run(
        graph(SlowPlan(proposal()), retriever, limits=Limits(run_seconds=0.01))
    )
    assert result.outcome == "failed" and not result.units and not retriever.calls


def test_synthesis_budget_omits_whole_support_and_reports_gaps():
    plan, results = packet_fixture(findings=2)
    for key, result in results.items():
        results[key] = result.model_copy(
            update={
                "findings": tuple(
                    f.model_copy(
                        update={"statement": "Supported wording " + "evidence " * 60}
                    )
                    for f in result.findings
                )
            }
        )
    value = ConversationInput(question="Q")
    intent = NormalizedIntent(status="identity", standalone_question="Q")
    roomy = aggregate(value, intent, plan, results, "s", Limits(prompt_tokens=24000))
    bounded = aggregate(value, intent, plan, results, "s", Limits(prompt_tokens=6000))
    assert len(bounded.admitted_finding_ids) < len(roomy.admitted_finding_ids)
    assert bounded.budget_omissions
    assert all(
        set(f.evidence_refs) <= registry(bounded).keys()
        for f in bounded.results[0].findings
    )


def test_allowlisted_events_sink_failure_does_not_change_outcome():
    def failing_sink(event):
        assert set(event.model_dump()) <= {
            "kind",
            "run_id",
            "sequence",
            "task_id",
            "message",
            "stage",
            "result",
        }
        raise RuntimeError("viewer unavailable")

    result = run(graph(event_sink=failing_sink))
    assert result.outcome == "complete"


def test_transport_timeout_quarantines_unknown_native_work(monkeypatch):
    import workbench.graphs.execution as execution_module
    from workbench.models.errors import ModelTimeoutError

    unconfirmed = threading.Event()
    accelerator = threading.BoundedSemaphore(1)
    monkeypatch.setattr(execution_module, "_UNCONFIRMED", unconfirmed)
    monkeypatch.setattr(execution_module, "_ACCELERATOR", accelerator)
    result = run(graph(Model(ModelTimeoutError("server may still be decoding"))))
    assert result.outcome == "failed" and result.error.code == "resource_not_drained"
    assert not accelerator.acquire(blocking=False)
    second = run(graph())
    assert second.outcome == "failed" and not second.units


def test_fallback_integrity_failure_never_exports_draft(monkeypatch):
    import workbench.graphs.synthesis as synthesis_module
    from workbench.graphs.research_models import ValidationReport

    monkeypatch.setattr(
        synthesis_module,
        "validate_draft",
        lambda *args: ValidationReport(status="failed", errors=("invariant",)),
    )
    result = run(graph(Model(proposal(), draft(), "invalid", "invalid")))
    assert result.outcome == "failed" and not result.units and not result.citations


def test_second_task_survivor_gets_first_alias_and_preserves_task_id():
    model = Model(proposal(2), draft(), answer())
    result = run(graph(model, Retriever(failed=("Question 1",))))
    assert result.outcome == "partial" and result.units[0].finding_ids == ("t2.f1",)
    assert (
        result.coverage[0].used_findings == 0 and result.coverage[1].used_findings == 1
    )


def test_actual_http_adapter_receives_remaining_request_deadline():
    import httpx

    from workbench.models.llamacpp_server import LlamaCppServerModel

    seen = []

    def handle(request):
        seen.append(request.extensions["timeout"])
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": proposal()}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    model = LlamaCppServerModel()
    model.client.close()
    model.client = httpx.Client(
        base_url="http://127.0.0.1:8080",
        transport=httpx.MockTransport(handle),
        trust_env=False,
    )
    execution = Execution("s", Limits(request_seconds=5, run_seconds=3))
    with execution.bind():
        AdmittedModel(model).generate([{"role": "user", "content": "Q"}])
    model.close()
    assert 0 < seen[0]["read"] <= 3
    assert seen[0]["connect"] <= 3
