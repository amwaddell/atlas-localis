"""Model-free runtime boundary tests: forbidden attempts fail before transport."""

from __future__ import annotations

import socket
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
import requests

from workbench.core.config import load_config
from workbench.core.local_http import LocalSession
from workbench.core.offline import cached_model_path, local_path, local_url
from workbench.data_build.http_embedder import HTTPEmbedder
from workbench.models.llamacpp_server import LlamaCppServerModel
from workbench.observability.phoenix import (
    is_phoenix_available,
    setup_phoenix_tracing,
)
from workbench.observability.tracing import setup_tracing
from workbench.retrieval.http_reranker import HTTPReranker


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Even a failed connection attempt is a test failure; no DNS or sockets."""

    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected network attempt")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org",
        "http://192.168.1.1:8080",
        "http://0.0.0.0:80",
        "http://[::]:80",
        "http://[::ffff:127.0.0.1]:80",
        "http://127.0.0.1.evil:80",
        "http://localhost.evil:80",
        "http://2130706433:80",
        "http://127.1:80",
        "http://user:pass@127.0.0.1:80",
        "http://127.0.0.1:80?next=evil",
        "http://127.0.0.1:80/#x",
        "http://[::1%25en0]:80",
        "ftp://127.0.0.1",
        "file:///tmp/model",
        "//127.0.0.1:80",
        "http://127.0.0.1:99999",
    ],
)
def test_remote_or_ambiguous_endpoints_rejected_before_io(url):
    for factory in (local_url, LlamaCppServerModel, HTTPEmbedder, HTTPReranker):
        with pytest.raises(ValueError, match="loopback"):
            factory(url)
    with pytest.raises(ValueError, match="loopback"):
        is_phoenix_available(url)
    with pytest.raises(ValueError, match="loopback"):
        setup_phoenix_tracing(endpoint=url)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://127.0.0.1:8080/", "http://127.0.0.1:8080"),
        ("http://localhost:8080", "http://127.0.0.1:8080"),
        ("http://[::1]:8080", "http://[::1]:8080"),
    ],
)
def test_loopback_without_dns(url, expected):
    assert local_url(url) == expected


@pytest.mark.parametrize(
    "section,key",
    [
        ("model", "base_url"),
        ("embeddings", "base_url"),
        ("reranking", "base_url"),
        ("observability", "phoenix_endpoint"),
    ],
)
def test_config_rejects_remote_endpoints(section, key):
    with pytest.raises(ValueError, match="loopback"):
        load_config(overrides={section: {key: "https://example.org"}})


@pytest.mark.parametrize(
    "key,value",
    [
        ("PHOENIX_ENDPOINT", "https://example.org/v1/traces"),
        ("PHOENIX_COLLECTOR_ENDPOINT", "https://example.org"),
        ("OTEL_EXPORTER_OTLP_ENDPOINT", "https://example.org"),
        ("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "https://example.org"),
        ("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "https://example.org"),
        ("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "https://example.org"),
        ("LANGCHAIN_TRACING_V2", "true"),
        ("LANGSMITH_TRACING", "true"),
        ("LANGCHAIN_ENDPOINT", "https://example.org"),
        ("LANGSMITH_ENDPOINT", "https://example.org"),
        ("LANGSMITH_API_KEY", "fake"),
        ("PHOENIX_API_KEY", "fake"),
        ("OTEL_TRACES_EXPORTER", "otlp"),
        ("OTEL_EXPERIMENTAL_RESOURCE_DETECTORS", "aws_ec2"),
    ],
)
def test_environment_rejected_even_with_disabled_tracing(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        load_config(overrides={"observability": {"enable_tracing": False}})
    with pytest.raises(ValueError):
        setup_tracing()


@pytest.mark.parametrize("section", ["web_search", "source_fetch"])
def test_retired_config_fails_explicitly(section):
    with pytest.raises(ValueError, match="removed"):
        load_config(overrides={section: {"enabled": False}})


@pytest.mark.parametrize(
    "value",
    [
        "s3://bucket/db",
        "gs://bucket/db",
        "https://example.org/db",
        "db://cloud",
        "//server/share",
    ],
)
def test_storage_and_asset_urls_rejected(value):
    with pytest.raises(ValueError, match="local filesystem"):
        local_path(value)
    with pytest.raises(ValueError, match="local filesystem"):
        load_config(overrides={"paths": {"active_index": value}})


def test_remote_exporter_rejected():
    exporter = SimpleNamespace(_endpoint="https://example.org/v1/traces")
    with pytest.raises(ValueError, match="loopback"):
        setup_tracing(exporter=exporter)
    with pytest.raises(ValueError, match="proxy-free"):
        setup_tracing(
            exporter=SimpleNamespace(_endpoint="http://127.0.0.1:6006/v1/traces")
        )


@pytest.mark.parametrize("factory", [LlamaCppServerModel, HTTPEmbedder, HTTPReranker])
def test_http_clients_ignore_proxies_and_refuse_redirects(monkeypatch, factory):
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.setenv(key, "http://proxy.example.org:8000")
    monkeypatch.setenv("NO_PROXY", "")
    client_owner = factory()
    client = getattr(client_owner, "client", None) or client_owner._client
    assert client.trust_env is False
    assert not client.follow_redirects
    assert not client._mounts  # No proxy transports were created.
    seen = []

    def redirect(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://example.org/steal"})

    client._transport = httpx.MockTransport(redirect)
    response = client.post(client_owner.base_url + "/test")
    with pytest.raises(httpx.HTTPStatusError):
        response.raise_for_status()
    assert len(seen) == 1 and seen[0].startswith("http://127.0.0.1:")
    client.close()


def test_exporter_session_blocks_proxy_and_redirect(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.org:8000")
    session = LocalSession()
    seen = []

    class Adapter(requests.adapters.BaseAdapter):
        def send(self, request, **kwargs):
            seen.append((request.url, kwargs["proxies"]))
            response = requests.Response()
            response.status_code = 307
            response.headers["Location"] = "https://example.org"
            response._content = b""
            response.request = request
            return response

        def close(self):
            pass

    session.mount("http://", Adapter())
    with pytest.raises(requests.HTTPError, match="redirects"):
        session.post("http://localhost:6006/v1/traces", data=b"trace")
    assert seen == [("http://127.0.0.1:6006/v1/traces", {})]
    with pytest.raises(ValueError, match="loopback"):
        session.post("https://example.org", data=b"trace")
    assert len(seen) == 1


def test_phoenix_exporter_uses_guarded_session(monkeypatch):
    capture = MagicMock()
    monkeypatch.setattr("workbench.observability.phoenix.setup_tracing", capture)
    setup_phoenix_tracing(endpoint="http://localhost:6006/v1/traces")
    exporter = capture.call_args.kwargs["exporter"]
    assert isinstance(exporter._session, LocalSession)
    assert exporter._session.trust_env is False
    assert exporter._endpoint == "http://127.0.0.1:6006/v1/traces"
    exporter.shutdown()


def test_real_empty_hf_cache_never_attempts_download(monkeypatch, tmp_path):
    from huggingface_hub import constants

    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="explicit online setup/update"):
        cached_model_path("atlas-fixture/definitely-not-cached")


@pytest.mark.parametrize("kind", ["embedding", "reranker"])
def test_loaders_use_local_path_and_download_disabled(monkeypatch, tmp_path, kind):
    factory = MagicMock()
    factory.return_value.get_sentence_embedding_dimension.return_value = 768
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(
            SentenceTransformer=factory,
            CrossEncoder=factory,
        ),
    )
    snapshot = tmp_path / "snapshot"  # Path deliberately lacks semantic E5 name.
    snapshot.mkdir()
    import huggingface_hub

    resolver = MagicMock(return_value=str(snapshot))
    monkeypatch.setattr(huggingface_hub, "snapshot_download", resolver)
    if kind == "embedding":
        from workbench.data_build.embeddings import SentenceTransformerEmbedder

        model = SentenceTransformerEmbedder()
        assert model._query_prefix == "query: " and model._passage_prefix == "passage: "
    else:
        from workbench.retrieval.rerankers import CrossEncoderReranker

        CrossEncoderReranker()
    assert resolver.call_args.kwargs["local_files_only"] is True
    assert factory.call_args.args == (str(snapshot),)
    assert factory.call_args.kwargs["local_files_only"] is True
    assert factory.call_args.kwargs["trust_remote_code"] is False


@pytest.mark.parametrize("kind", ["embedding", "reranker"])
def test_missing_explicit_model_fails_before_loader_import(tmp_path, kind):
    if kind == "embedding":
        from workbench.data_build.embeddings import SentenceTransformerEmbedder

        factory = SentenceTransformerEmbedder
    else:
        from workbench.retrieval.rerankers import CrossEncoderReranker

        factory = CrossEncoderReranker
    with pytest.raises(FileNotFoundError, match="Provision"):
        factory(model_name=str(tmp_path / "missing"))


def test_low_evidence_application_stays_local():
    from tests.test_supervisor_orchestration import (
        Model,
        Retriever,
        graph,
        proposal,
        run,
    )

    model = Model(proposal(2))
    retriever = Retriever(empty=("Question 1", "Question 2"))
    result = run(graph(model, retriever))
    assert result.outcome == "insufficient_evidence"
    assert not result.citations
    assert len(retriever.calls) == 2 and len(model.calls) == 1


def test_one_local_hit_is_used_without_count_fallback():
    from tests.test_supervisor_orchestration import graph, run

    result = run(graph())
    assert result.outcome == "complete"
    assert [c.chunk_id for c in result.citations] == ["a"]
    assert result.citations[0].excerpts[0].text == "Rome changed in 27 BC."


def test_retired_capability_is_absent_from_exports():
    import workbench.graphs as graphs
    import workbench.tools as tools

    for name in ("WebSearchTool", "SourceFetchTool", "extract_web_citations"):
        assert not hasattr(tools, name) and not hasattr(graphs, name)


@pytest.mark.parametrize(
    "key,value",
    [
        (
            "PHOENIX_SERVER_INSTRUMENTATION_OTLP_TRACE_COLLECTOR_HTTP_ENDPOINT",
            "https://example.org",
        ),
        ("PHOENIX_SQL_DATABASE_URL", "postgresql://example.org/db"),
        ("PHOENIX_POSTGRES_HOST", "example.org"),
    ],
)
def test_viewer_environment_cannot_enable_remote_services(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        load_config()


@pytest.mark.parametrize("kind", ["embedding", "reranker"])
def test_incomplete_local_model_reports_setup_error(monkeypatch, tmp_path, kind):
    factory = MagicMock(side_effect=OSError("missing tokenizer"))
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(
            SentenceTransformer=factory,
            CrossEncoder=factory,
        ),
    )
    if kind == "embedding":
        from workbench.data_build.embeddings import SentenceTransformerEmbedder

        loader = SentenceTransformerEmbedder
    else:
        from workbench.retrieval.rerankers import CrossEncoderReranker

        loader = CrossEncoderReranker
    with pytest.raises(
        RuntimeError, match="complete cache.*explicit online setup/update"
    ):
        loader(model_name=str(tmp_path))
    assert factory.call_count == 1


def test_provenance_and_untrusted_markdown_render_as_inert_text(monkeypatch):
    from workbench.ui import renderers

    ui = MagicMock()
    monkeypatch.setattr(renderers, "st", ui)
    payload = "![remote](https://example.org/track.png)"
    from tests.test_evidence_worker import chunk
    from tests.test_supervisor_orchestration import graph, run

    from workbench.retrieval.hybrid import HydratedRetrieval

    class Retriever:
        def retrieve_hydrated(self, *args, **kwargs):
            return HydratedRetrieval(
                (chunk(title=payload, text=payload + "\nRome changed in 27 BC."),), 1, 1
            )

    result = run(graph(retriever=Retriever()))
    renderers.render_result(result)
    ui.markdown.assert_not_called()
    ui.image.assert_not_called()
    assert ui.text.call_count >= 3
    assert result.citations[0].excerpts[0].title == payload


def test_launchers_reject_remote_binding_and_missing_cache(tmp_path):
    import os
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    fake = binary_dir / "infinity_emb"
    marker = tmp_path / "started"
    fake.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    fake.chmod(0o700)
    env = dict(
        os.environ,
        PATH=str(binary_dir) + os.pathsep + os.environ["PATH"],
        HF_HOME=str(tmp_path / "hf"),
        HF_HUB_CACHE=str(tmp_path / "hf" / "hub"),
        ATLAS_PYTHON=sys.executable,
    )
    for name in ("embedding", "reranker"):
        script = str(root / "scripts" / f"start_{name}_server.sh")
        remote = subprocess.run(
            ["bash", script, "--host", "0.0.0.0"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert remote.returncode != 0 and "bind host must" in remote.stderr
        missing = subprocess.run(
            ["bash", script, "--model", str(tmp_path / "missing")],
            env=env,
            capture_output=True,
            text=True,
        )
        assert missing.returncode != 0 and "Provision" in missing.stderr
    assert not marker.exists()


def test_multitask_grounded_flow_with_real_prompts_and_no_egress():
    from tests.test_supervisor_orchestration import (
        Model,
        answer,
        draft,
        graph,
        proposal,
        run,
    )

    result = run(graph(Model(proposal(2), draft(), draft(), answer("F1", "F2"))))
    assert result.outcome == "complete"
    assert len(result.coverage) == 2
    assert result.answer_text == "Rome changed in 27 BC. Rome changed in 27 BC. [1]"
    assert result.citations[0].excerpts[0].presented_to == ("t1", "t2")


def test_urls_cannot_supply_synthesis_evidence():
    from tests.test_supervisor_orchestration import Retriever, graph, run

    result = run(graph(retriever=Retriever(empty=("Original question",))))
    assert not result.units and not result.citations


@pytest.mark.parametrize(
    "question", ["What happened today?", "Who is the current president?"]
)
def test_recency_has_only_local_research(question):
    from tests.test_supervisor_orchestration import graph, run

    result = run(graph(), question=question)
    assert result.citations[0].snapshot_id == "snapshot-v1"


def test_viewer_launcher_disables_wildcard_grpc_and_browser_egress(tmp_path):
    import json
    import os
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    fake = binary_dir / "atlas-python"
    output = tmp_path / "arguments.json"
    fake.write_text(
        f"#!{sys.executable}\nimport json,os,sys\n"
        f"if '-m' not in sys.argv:\n os.execv({sys.executable!r}, [{sys.executable!r}]+sys.argv[1:])\n"
        f"with open({str(output)!r}, 'w') as f:\n"
        " json.dump({'args':sys.argv[sys.argv.index('-m')+2:], 'env':{k:os.environ[k] for k in "
        "['PHOENIX_HOST','PHOENIX_TELEMETRY_ENABLED','PHOENIX_ALLOW_EXTERNAL_RESOURCES']}}, f)\n"
    )
    fake.chmod(0o700)
    env = dict(
        os.environ,
        PATH=str(binary_dir) + os.pathsep + os.environ["PATH"],
        ATLAS_PYTHON=str(fake),
        PHOENIX_HOST="0.0.0.0",
        PHOENIX_TELEMETRY_ENABLED="true",
        PHOENIX_ALLOW_EXTERNAL_RESOURCES="true",
    )
    result = subprocess.run(
        ["bash", str(root / "scripts/run_phoenix.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    captured = json.loads(output.read_text())
    assert captured["args"] == [
        "--host",
        "127.0.0.1",
        "--read-only",
        "--no-internet",
        "serve",
    ]
    assert captured["env"] == {
        "PHOENIX_HOST": "127.0.0.1",
        "PHOENIX_TELEMETRY_ENABLED": "false",
        "PHOENIX_ALLOW_EXTERNAL_RESOURCES": "false",
    }


@pytest.mark.parametrize(
    "name,model_id",
    [
        ("embedding", "intfloat/multilingual-e5-base"),
        ("reranker", "BAAI/bge-reranker-v2-m3"),
    ],
)
def test_infinity_launcher_preserves_api_identity_and_offline_flags(
    tmp_path, name, model_id
):
    import json
    import os
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    cache = tmp_path / "hub"
    repo = cache / ("models--" + model_id.replace("/", "--"))
    snapshot = repo / "snapshots" / "fixture"
    snapshot.mkdir(parents=True)
    (repo / "refs").mkdir()
    (repo / "refs" / "main").write_text("fixture")
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    output = tmp_path / "arguments.json"
    fake = binary_dir / "infinity_emb"
    fake.write_text(
        f"#!{sys.executable}\nimport json,os,sys\n"
        f"with open({str(output)!r}, 'w') as f:\n"
        " json.dump({'args':sys.argv[1:], 'env':{k:os.environ[k] for k in "
        "['HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','DO_NOT_TRACK',"
        "'INFINITY_EMBEDDING_DTYPE','INFINITY_TRUST_REMOTE_CODE']}}, f)\n"
    )
    fake.chmod(0o700)
    env = dict(
        os.environ,
        PATH=str(binary_dir) + os.pathsep + os.environ["PATH"],
        ATLAS_PYTHON=sys.executable,
        HF_HUB_CACHE=str(cache),
        INFINITY_EMBEDDING_DTYPE="int8",
        INFINITY_TRUST_REMOTE_CODE="true",
    )
    result = subprocess.run(
        ["bash", str(root / f"scripts/start_{name}_server.sh")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    captured = json.loads(output.read_text())
    args = captured["args"]
    assert args[args.index("--model-id") + 1] == str(snapshot)
    assert args[args.index("--served-model-name") + 1] == model_id
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert captured["env"] == {
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "DO_NOT_TRACK": "1",
        "INFINITY_EMBEDDING_DTYPE": "float32",
        "INFINITY_TRUST_REMOTE_CODE": "false",
    }
