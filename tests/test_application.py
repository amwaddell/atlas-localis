"""Stage 2d consumer, isolation, resource and terminal lifecycle contracts."""

import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from tests.test_evidence_worker import Model, draft
from tests.test_supervisor_orchestration import Retriever, answer, graph, proposal

from workbench.core.config import load_config
from workbench.graphs.research_models import ApplicationResult, ConversationInput
from workbench.systems.presentation import project_result
from workbench.systems.runner import (
    ApplicationCache,
    EvaluationControls,
    ResourceBusy,
    ResourceOwner,
    Resources,
    application_key,
    create_application,
)
from workbench.ui.graph_runner import run_application_streaming


def normalized(result):
    data = result.model_dump(mode="json")
    data.pop("run_id")
    data["diagnostics"].pop("elapsed_ms")
    return data


@pytest.mark.parametrize(
    "model,retriever",
    [
        (lambda: Model(proposal(), draft(), answer()), lambda: Retriever()),
        (lambda: Model(proposal()), lambda: Retriever(empty=("Question 1",))),
        (lambda: Model(proposal()), lambda: Retriever(failed=("Question 1",))),
        (lambda: Model(proposal(), draft(), "bad", "bad"), lambda: Retriever()),
    ],
)
def test_invoke_stream_terminal_equivalence_and_redaction(model, retriever):
    value = ConversationInput(question="Rome?")
    invoked = graph(model(), retriever()).invoke(value)
    events = list(graph(model(), retriever()).stream(value))
    assert normalized(invoked) == normalized(events[-1].result)
    assert sum(e.kind == "run_finished" for e in events) == 1
    assert [e.sequence for e in events] == list(range(len(events)))
    for event in events[:-1]:
        assert event.result is None and event.message is None
        assert set(event.model_dump()) == {
            "kind",
            "run_id",
            "sequence",
            "task_id",
            "message",
            "stage",
            "result",
        }
        assert not any(
            private in event.model_dump_json()
            for private in (
                "draft",
                "prompt",
                "findings_draft",
                "results_by_task",
                "token_stream",
                "reasoning",
            )
        )


def test_fake_cli_ui_evaluation_parity_and_pure_projection():
    from scripts.run_agent_chat import run_question
    from scripts.run_researcher_eval import run_single_question

    expected = graph().invoke(ConversationInput(question="Rome?"))
    cli = run_question(graph(), "Rome?")
    ui = run_application_streaming(graph(), ConversationInput(question="Rome?"))
    evaluation = run_single_question({"id": "fixture", "question": "Rome?"}, graph())
    assert (
        normalized(cli)
        == normalized(ui)
        == normalized(evaluation["result"])
        == normalized(expected)
    )
    assert project_result(cli)["citations"] == project_result(ui)["citations"]
    assert (
        ApplicationResult.model_validate_json(evaluation["result"].model_dump_json())
        == evaluation["result"]
    )


@pytest.mark.parametrize("consumer", ["cli", "ui", "evaluation"])
def test_consumers_share_bounded_history_and_summary(consumer):
    from scripts.run_agent_chat import run_question
    from scripts.run_researcher_eval import run_single_question

    history = [
        {"role": "assistant", "content": "ORPHAN"},
        {"role": "user", "content": "OLD" + "x" * 7000},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "Recent Rome?"},
        {"role": "assistant", "content": "Recent answer"},
    ]
    summary = "Summary: Rome."
    model = Model('{"standalone_question":"Rome?"}', proposal(), draft(), answer())
    app = graph(model)
    if consumer == "cli":
        result = run_question(app, "Then?", history=history, summary=summary)
    elif consumer == "ui":
        result = run_application_streaming(
            app, ConversationInput(question="Then?", history=history, summary=summary)
        )
    else:
        result = run_single_question(
            {"id": "q", "question": "Then?", "history": history, "summary": summary},
            app,
        )["result"]
    assert result.outcome == "complete"
    prompts = str(model.calls)
    assert "ORPHAN" not in prompts and "OLD" not in prompts
    assert "Recent Rome?" in prompts and summary in prompts
    assert len(model.calls) == 4  # one shared normalization, no UI summary model


def test_history_is_not_retained_across_application_runs():
    model = Model(
        '{"standalone_question":"Rome?"}',
        proposal(),
        draft(),
        answer(),
        proposal(),
        draft(),
        answer(),
    )
    app = graph(model)
    first = app.invoke(
        ConversationInput(question="Then?", summary="PRIVATE EARLIER CONTEXT")
    )
    second = app.invoke(ConversationInput(question="New question"))
    assert first.run_id != second.run_id
    assert second.outcome == "complete"
    assert "PRIVATE EARLIER CONTEXT" not in str(model.calls[4:])


def blocking_application():
    entered, release = threading.Event(), threading.Event()

    class Blocking(Model):
        def generate(self, *args, **kwargs):
            entered.set()
            assert release.wait(5)
            return super().generate(*args, **kwargs)

    return graph(Blocking(proposal(), draft(), answer())), entered, release


def test_busy_admission_does_not_enter_graph_or_load_another_run():
    app, entered, release = blocking_application()
    other_model = Model(proposal(), draft(), answer())
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(app.invoke, ConversationInput(question="First"))
        assert entered.wait(3)
        events = list(graph(other_model).stream(ConversationInput(question="Second")))
        assert len(events) == 1 and events[0].result.error.code == "resource_busy"
        assert not other_model.calls
        release.set()
        assert future.result(timeout=4).outcome == "complete"


def test_stream_close_cancels_and_drains_before_releasing_admission():
    events = []
    app, entered, release = blocking_application()
    app._sink = events.append
    stream = app.stream(ConversationInput(question="Cancel"))
    assert next(stream).kind == "run_started"
    assert entered.wait(3)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(stream.close)
        # Wait for an explicit cancelling notification, not a sleep assumption.
        cancel_seen = threading.Event()
        app._sink = lambda e: (
            events.append(e),
            cancel_seen.set() if e.kind == "cancelling" else None,
        )
        assert cancel_seen.wait(3)
        assert not future.done()
        busy = graph().invoke(ConversationInput(question="Other"))
        assert busy.error.code == "resource_busy"
        release.set()
        future.result(timeout=4)
    terminal = [e for e in events if e.kind == "run_finished"]
    assert len(terminal) == 1 and terminal[0].result.outcome == "cancelled"
    assert not terminal[0].result.units and not terminal[0].result.citations
    assert (
        graph().invoke(ConversationInput(question="After drain")).outcome == "complete"
    )


def test_pre_cancelled_run_has_one_terminal_and_no_model_calls():
    cancel = threading.Event()
    cancel.set()
    model = Model()
    events = list(
        graph(model).stream(ConversationInput(question="Q"), cancel_event=cancel)
    )
    assert events[-1].result.outcome == "cancelled"
    assert sum(e.kind == "run_finished" for e in events) == 1 and not model.calls


def test_fatal_graph_failure_redacts_exception_and_private_state():
    app = graph()

    class Broken:
        def stream(self, *args, **kwargs):
            raise RuntimeError("SECRET_PROMPT_DRAFT")

    app._graph = Broken()
    events = list(app.stream(ConversationInput(question="Question")))
    assert events[-1].result.outcome == "failed"
    assert sum(e.kind == "run_finished" for e in events) == 1
    assert "SECRET_PROMPT_DRAFT" not in str(events)


@pytest.mark.fixture_integration
def test_resource_ownership_lock_across_processes(tmp_path):
    path = tmp_path / "owner.lock"
    program = "from pathlib import Path; from workbench.systems.runner import ResourceOwner; ResourceOwner(Path(__import__('sys').argv[1]))"
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    owner = ResourceOwner(path)
    try:
        child = subprocess.run(
            [sys.executable, "-B", "-c", program, str(path)],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert child.returncode != 0 and "resource_busy" in child.stderr
    finally:
        owner.close()
    child = subprocess.run(
        [sys.executable, "-B", "-c", program, str(path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert child.returncode == 0, child.stderr


def config_for(path):
    return load_config(overrides={"paths": {"active_index": str(path)}})


def make_bundle(path):
    manifests = path / "lancedb/chunks.lance/_versions"
    manifests.mkdir(parents=True)
    (manifests / "1.manifest").write_text("fixture")


def test_cache_keys_model_config_snapshot_and_symlink_pinning(tmp_path):
    first, second, active = tmp_path / "first", tmp_path / "second", tmp_path / "active"
    make_bundle(first)
    make_bundle(second)
    active.symlink_to(first, target_is_directory=True)
    cfg = config_for(active)
    key = application_key(cfg)
    assert key.path == str(first)
    cfg.model["model_name"] = "different-local-model"
    changed = application_key(cfg)
    assert changed.models != key.models and changed.config != key.config
    cfg.retrieval["final_top_k"] = 2
    assert application_key(cfg).config != changed.config
    assert application_key(cfg).models == changed.models
    active.unlink()
    active.symlink_to(second, target_is_directory=True)
    assert application_key(cfg).snapshot != key.snapshot
    assert key.path == str(first)
    (second / "manifest.json").write_text('{"snapshot":"v2"}')
    assert application_key(cfg).snapshot != application_key(config_for(first)).snapshot


def test_cache_reuses_explicit_key_and_closes_old_owner():
    built = []

    def factory(**kwargs):
        app = graph()
        built.append(app)
        return app

    cache = ApplicationCache(factory)
    a = cache.get(None, "first")
    assert cache.get(None, "first") is a and len(built) == 1
    b = cache.get(None, "second")
    assert b is not a and a._closed and len(built) == 2
    cache.close()
    assert b._closed


def test_cache_cannot_replace_busy_resources():
    app, entered, release = blocking_application()
    cache = ApplicationCache(lambda **kwargs: app)
    cache.get(None, "first")
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(app.invoke, ConversationInput(question="Q"))
        assert entered.wait(3)
        with pytest.raises(ResourceBusy, match="drain"):
            cache.get(None, "second")
        release.set()
        assert future.result(timeout=4).outcome == "complete"
    cache.close()


def test_evaluation_control_is_recorded_and_single_task_still_synthesizes():
    model = Model(proposal(3), draft(), answer())
    app = create_application(
        resources=Resources(model, Retriever(), "snapshot-v1"),
        evaluation=EvaluationControls(forced_single_task=True),
    )
    result = app.invoke(ConversationInput(question="Whole intent"))
    assert len(result.coverage) == 1 and len(model.calls) == 3
    assert app.resources.retriever.calls[0][0] == "Whole intent"
    controls = next(
        f for f in result.fingerprints if f.component == "evaluation_controls"
    )
    assert json.loads(controls.identity)["forced_single_task"] is True


def test_pinned_injected_snapshots_cannot_mix_evidence():
    from tests.test_supervisor_orchestration import graph as first_graph

    first = first_graph().invoke(ConversationInput(question="Q"))
    second = create_application(
        resources=Resources(
            Model(proposal(), draft(), answer()), Retriever(), "snapshot-v2"
        )
    ).invoke(ConversationInput(question="Q"))
    assert first.citations[0].snapshot_id == "snapshot-v1"
    assert second.citations[0].snapshot_id == "snapshot-v2"
    assert first.citations[0].excerpts[0].key != second.citations[0].excerpts[0].key


@pytest.mark.fixture_integration
def test_fake_streamlit_walkthrough_has_canonical_sources_and_no_selector(monkeypatch):
    from streamlit.testing.v1 import AppTest

    from workbench.ui import components

    app = graph()
    monkeypatch.setattr(components, "get_application", lambda: app)
    path = Path(__file__).parents[1] / "src/workbench/ui/streamlit_app.py"
    ui = AppTest.from_file(str(path)).run(timeout=10)
    assert not ui.exception and not ui.selectbox
    assert sum('class="atlas-welcome"' in e.proto.body for e in ui.get("html")) == 1
    ui.chat_input[0].set_value("When did Rome change?").run(timeout=10)
    assert not ui.exception
    text = [element.value for element in ui.text]
    assert "Rome changed in 27 BC. [1]" in text
    assert "Rome changed in 27 BC." in text
    assert not any("derive-findings/v1" in item for item in text)
    assert ui.session_state.messages[-1]["result"].outcome == "complete"
    assert not any('class="atlas-welcome"' in e.proto.body for e in ui.get("html"))
    assert (
        sum(
            'class="atlas-sprite atlas-sprite--sidebar"' in e.proto.body
            for e in ui.get("html")
        )
        == 1
    )
    ui.session_state.conversation_summary = "Earlier research"
    next(button for button in ui.button if button.label == "Clear chat").click().run()
    assert not ui.exception
    assert not ui.session_state.messages and not ui.session_state.conversation_summary
    assert sum('class="atlas-welcome"' in e.proto.body for e in ui.get("html")) == 1
    assert not ui.chat_message


@pytest.mark.fixture_integration
def test_fake_streamlit_missing_assets_is_actionable(monkeypatch):
    from streamlit.testing.v1 import AppTest

    from workbench.ui import components

    def missing():
        raise FileNotFoundError("Provisioned local index missing: fixture")

    monkeypatch.setattr(components, "get_application", missing)
    ui = AppTest.from_file(
        str(Path(__file__).parents[1] / "src/workbench/ui/streamlit_app.py")
    ).run(timeout=10)
    assert not ui.exception
    assert "Provisioned local index missing" in ui.error[0].value
    assert not ui.chat_input


@pytest.mark.fixture_integration
def test_factory_pins_real_fixture_lance_handles_and_does_not_follow_pointer(
    tmp_path, monkeypatch
):
    import lancedb
    import pyarrow as pa

    import workbench.systems.runner as runner

    first, second, active = tmp_path / "one", tmp_path / "two", tmp_path / "active"
    for directory in (first, second):
        directory.mkdir()
        db = lancedb.connect(str(directory / "lancedb"))
        db.create_table(
            "chunks",
            data=pa.table(
                {
                    "chunk_id": ["a"],
                    "document_id": ["doc"],
                    "title": [directory.name],
                    "section": ["History"],
                    "text": ["Local history."],
                    "token_count": [2],
                    "start_offset": [0],
                    "end_offset": [14],
                    "vector": [[1.0, 0.0]],
                }
            ),
        )
    active.symlink_to(first, target_is_directory=True)
    cfg = config_for(active)
    cfg.observability["enable_tracing"] = False
    cfg.observability["enable_metrics"] = False
    cfg.runs["logs_dir"] = str(tmp_path / "logs")
    monkeypatch.setattr(
        runner, "LlamaCppServerModel", lambda **kwargs: Model(proposal())
    )
    monkeypatch.setattr(runner, "build_embedder", lambda cfg: object())
    monkeypatch.setattr(runner, "build_reranker", lambda cfg: None)
    key = application_key(cfg)
    with create_application(config=cfg, key=key) as app:
        retriever = app.resources.retriever
        assert retriever.chunk_store.get("a").title == "one"
        active.unlink()
        active.symlink_to(second, target_is_directory=True)
        assert retriever.chunk_store.get("a").title == "one"
        assert app.resources.snapshot_id == key.snapshot
        assert application_key(cfg).snapshot != key.snapshot
        with pytest.raises(ValueError, match="cache key"):
            create_application(config=cfg, key=key)


def test_factory_releases_owner_when_loading_fails(tmp_path, monkeypatch):
    import workbench.systems.runner as runner

    make_bundle(tmp_path / "bundle")
    cfg = config_for(tmp_path / "bundle")
    cfg.observability["enable_tracing"] = False
    cfg.observability["enable_metrics"] = False
    cfg.runs["logs_dir"] = str(tmp_path / "logs")

    def fail(**kwargs):
        raise OSError("fixture missing model")

    monkeypatch.setattr(runner, "LlamaCppServerModel", fail)
    with pytest.raises(OSError, match="fixture missing model"):
        create_application(config=cfg)
    owner = ResourceOwner()
    owner.close()


def test_cancellation_after_graph_finalization_never_displays_late_answer():
    expected = graph().invoke(ConversationInput(question="Q"))
    app = graph()
    cancel = threading.Event()

    class LateFinal:
        def stream(self, initial, config, **kwargs):
            execution = config["configurable"]["atlas_execution"]
            cancel.set()
            yield {
                "result": ApplicationResult.model_validate(
                    expected.model_dump() | {"run_id": execution.run_id}
                )
            }

    app._graph = LateFinal()
    events = list(app.stream(ConversationInput(question="Q"), cancel_event=cancel))
    assert events[-1].result.outcome == "cancelled"
    assert not events[-1].result.units
    assert sum(e.kind == "run_finished" for e in events) == 1


def test_long_transcript_is_bounded_by_the_same_consumer_policy():
    from workbench.graphs.planning import conversation_input
    from workbench.ui.chat_state import conversation

    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": str(i)}
        for i in range(140)
    ]
    expected = conversation_input("Then?", history, "s" * 8000)
    ui = conversation("Then?", history, "s" * 8000)
    assert ui == expected and expected.history_truncated
    assert expected.history[0].role == "user"
    assert len(expected.history) <= 100 and len(expected.summary) == 2000


def test_progress_display_failure_cannot_change_terminal_answer():
    def broken(event):
        raise RuntimeError("viewer failed")

    result = run_application_streaming(
        graph(), ConversationInput(question="Q"), on_progress=broken
    )
    assert result.outcome == "complete"


@pytest.mark.parametrize("terminal", [None, {"draft": "PRIVATE INVALID RESULT"}])
def test_missing_or_invalid_terminal_is_one_redacted_failure(terminal):
    app = graph()

    class Broken:
        def stream(self, *args, **kwargs):
            if terminal:
                yield {"result": terminal}

    app._graph = Broken()
    events = list(app.stream(ConversationInput(question="Q")))
    assert sum(e.kind == "run_finished" for e in events) == 1
    assert events[-1].result.outcome == "failed"
    assert "PRIVATE INVALID RESULT" not in str(events)


def test_closed_application_returns_canonical_failure_without_reusing_clients():
    app = graph()
    model = app.resources.model
    app.close()
    result = app.invoke(ConversationInput(question="Q"))
    assert result.outcome == "failed" and result.error.code == "application_closed"
    assert not model.calls


@pytest.mark.parametrize("section", ["supervisor", "decomposer"])
def test_retired_public_graph_configuration_is_rejected(section):
    with pytest.raises(ValueError, match=section):
        load_config(overrides={section: {"enabled": True}})


def test_factory_refuses_busy_owner_before_model_loading(tmp_path, monkeypatch):
    import workbench.systems.runner as runner

    make_bundle(tmp_path / "bundle")

    def forbidden(**kwargs):
        raise AssertionError("competing model stack must not load")

    monkeypatch.setattr(runner, "LlamaCppServerModel", forbidden)
    owner = ResourceOwner()
    try:
        with pytest.raises(ResourceBusy, match="resource_busy"):
            create_application(config=config_for(tmp_path / "bundle"))
    finally:
        owner.close()


@pytest.mark.fixture_integration
def test_streamlit_preserves_existing_summary_without_summarization_call(monkeypatch):
    from streamlit.testing.v1 import AppTest

    from workbench.ui import components

    model = Model('{"standalone_question":"Rome?"}', proposal(), draft(), answer())
    app = graph(model)
    monkeypatch.setattr(components, "get_application", lambda: app)
    ui = AppTest.from_file(
        str(Path(__file__).parents[1] / "src/workbench/ui/streamlit_app.py")
    ).run(timeout=10)
    ui.session_state.conversation_summary = "Earlier Rome discussion"
    ui.chat_input[0].set_value("Then?").run(timeout=10)
    assert not ui.exception
    assert ui.session_state.messages[-1]["result"].outcome == "complete"
    assert "Earlier Rome discussion" in str(model.calls[0])
    assert len(model.calls) == 4
