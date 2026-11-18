"""Offline assets, explicit devices, bounded model reuse and native budgeting."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import numpy as np
import pytest

from workbench.data_build.embeddings import SentenceTransformerEmbedder
from workbench.models.errors import ModelResponseError
from workbench.models.llamacpp_server import LlamaCppServerModel
from workbench.models.local_inference import local_device
from workbench.retrieval.evidence_packing import token_bound, tokenizer_budget
from workbench.retrieval.rerankers import CrossEncoderReranker


@pytest.mark.parametrize(
    "available,requested,expected",
    [
        (True, "mps", "mps"),
        (False, "mps", "cpu"),
        (True, "cpu", "cpu"),
        (False, None, "cpu"),
        (True, "auto", "mps"),
    ],
)
def test_device_policy(monkeypatch, available, requested, expected):
    import torch

    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: available)
    assert local_device(requested) == expected
    with pytest.raises(ValueError, match="device"):
        local_device("cuda")


def test_local_path_preserves_e5_identity_and_normalization(monkeypatch, tmp_path):
    model = MagicMock()
    model.get_sentence_embedding_dimension.return_value = 768
    model.device = "cpu"
    model.encode.return_value = np.array([[1.0, 0.0]])
    loader = MagicMock(return_value=model)
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=loader),
    )
    embedder = SentenceTransformerEmbedder(
        model_path=str(tmp_path), device="cpu", batch_size=4
    )
    assert loader.call_args.args == (str(tmp_path),)
    assert loader.call_args.kwargs == dict(
        device="cpu", local_files_only=True, trust_remote_code=False
    )
    assert embedder.embed_query("Rome") == [1.0, 0.0]
    assert model.encode.call_args.args[0] == ["query: Rome"]
    embedder.embed_texts(["Rome"])
    assert model.encode.call_args.args[0] == ["passage: Rome"]
    assert model.encode.call_args.kwargs["normalize_embeddings"] is True
    assert loader.call_count == 1


def test_bounded_cross_encoder_local_load(monkeypatch, tmp_path):
    loader = MagicMock()
    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(CrossEncoder=loader)
    )
    reranker = CrossEncoderReranker(
        model_path=str(tmp_path), device="cpu", batch_size=4
    )
    assert loader.call_args.kwargs == dict(
        device="cpu", max_length=8192, local_files_only=True, trust_remote_code=False
    )
    with pytest.raises(ValueError, match="50"):
        reranker.rerank(None, [None] * 51)
    with pytest.raises(ValueError, match="batch_size"):
        CrossEncoderReranker(batch_size=0)


def test_run_scoped_tokenizer_cache_and_failure():
    count = MagicMock(return_value=2)
    assert token_bound("é") == 2
    with tokenizer_budget(count):
        assert token_bound("long text") == 2
        assert token_bound("long text") == 2
        assert count.call_count == 1
    assert token_bound("long text") == 9
    with (
        tokenizer_budget(
            MagicMock(side_effect=RuntimeError("offline tokenizer failed"))
        ),
        pytest.raises(RuntimeError),
    ):
        token_bound("x")


def test_native_identity_and_actual_tokenizer():
    model = LlamaCppServerModel(model_name="expected")
    seen = []

    def handle(request):
        seen.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "/health": {"status": "ok"},
                "/v1/models": {"data": [{"id": "expected"}]},
                "/tokenize": {"tokens": [1, 2, 3]},
            }[request.url.path],
        )

    model.client.close()
    model.client = httpx.Client(
        base_url=model.base_url,
        transport=httpx.MockTransport(handle),
        trust_env=False,
        follow_redirects=False,
    )
    model.check_identity()
    assert model.exact_token_count("measured text") == 3
    model.model_name = "wrong"
    with pytest.raises(ModelResponseError, match="identity"):
        model.check_identity()
    assert seen[:3] == ["/health", "/v1/models", "/tokenize"]
    model.close()


def test_device_failure_does_not_reload_or_retry(monkeypatch, tmp_path):
    model = MagicMock()
    model.device = "cpu"
    model.encode.side_effect = RuntimeError("allocation failure")
    loader = MagicMock(return_value=model)
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=loader),
    )
    embedder = SentenceTransformerEmbedder(model_path=str(tmp_path), device="cpu")
    with pytest.raises(RuntimeError, match="allocation failure"):
        embedder.embed_query("Q")
    assert loader.call_count == 1 and model.encode.call_count == 1


@pytest.mark.fixture_integration
def test_chunk_store_never_loads_vectors_and_bounds_cache(tmp_path, monkeypatch):
    import lancedb

    from workbench.stores.chunk_store import ChunkStore

    rows = [
        dict(
            chunk_id=f"c{i}",
            document_id="doc",
            title="Title",
            section="",
            text="Evidence",
            vector=[1.0, 0.0],
        )
        for i in range(300)
    ]
    lancedb.connect(str(tmp_path)).create_table("chunks", data=rows)
    store = ChunkStore(tmp_path)
    monkeypatch.setattr(
        store._table,
        "to_pandas",
        MagicMock(side_effect=AssertionError("full corpus load")),
    )
    chunks = store.get_many([f"c{i}" for i in range(300)])
    assert len(chunks) == 300 and all(c.section is None for c in chunks)
    assert len(store._lookup) == 256
    assert all("vector" not in row for row in store._lookup.values())
    assert store.get_many(["x' OR TRUE --", "c2", "c2"])[0].chunk_id == "c2"
    assert store.count() == 300


def test_owned_native_group_is_reaped_on_timeout(monkeypatch):
    import signal
    import subprocess

    from scripts import native_runtime

    child = MagicMock(pid=9876)
    child.wait.side_effect = [subprocess.TimeoutExpired("native", 10), 0]
    kill = MagicMock()
    monkeypatch.setattr(native_runtime.os, "killpg", kill)
    native_runtime.stop_owned(child)
    assert [c.args for c in kill.call_args_list] == [
        (9876, signal.SIGTERM),
        (9876, signal.SIGKILL),
    ]
    assert child.wait.call_count == 2


def test_cache_key_tracks_local_revision_without_loading_weights(tmp_path, monkeypatch):
    from tests.test_application import config_for, make_bundle

    from workbench.systems import runner

    snapshot = tmp_path / "snapshot"
    make_bundle(snapshot)
    assets = tmp_path / "weights"
    assets.mkdir()
    weight = assets / "model.safetensors"
    weight.write_bytes(b"first")
    monkeypatch.setattr(runner, "cached_model_path", lambda _: str(assets))
    cfg = config_for(snapshot)
    first = runner.application_key(cfg)
    weight.write_bytes(b"second revision")
    assert runner.application_key(cfg).models != first.models


def test_canonical_nested_workers_share_run_tokenizer_without_leaking():
    from tests.test_application import Model, Retriever, answer, draft, proposal

    from workbench.graphs.research_models import ConversationInput
    from workbench.systems.runner import Resources, create_application

    model = Model(proposal(), draft(), answer())
    calls = []

    def counter(text):
        calls.append(text)
        return (len(text.encode()) + 3) // 4

    model.exact_token_count = counter
    with create_application(
        resources=Resources(model, Retriever(), "snapshot-v1")
    ) as app:
        result = app.invoke(ConversationInput(question="Rome?"))
    assert result.outcome == "complete"
    assert any("derive-findings/v1" in text for text in calls)
    assert any("synthesize-findings/v1" in text for text in calls)
    assert token_bound("long text") == 9
