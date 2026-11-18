"""Isolated failure-injected lifecycle acceptance: real XML, Arrow, Lance and FTS."""

import hashlib
import io
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from workbench.data_build.acquisition import download
from workbench.data_build.lifecycle import Lease, atomic_json, sha256
from workbench.data_build.pipeline import build_bundle
from workbench.data_build.snapshots import (
    activate,
    citation_store,
    convert,
    prune,
    status,
    verify,
)

MODEL = {"id": "fixture", "revision": "a" * 40, "dimension": 3, "normalize": True}


class Embedder:
    def __init__(self, fail=None):
        self.calls = 0
        self.fail = fail

    def embed_texts(self, texts):
        self.calls += 1
        if self.calls == self.fail:
            raise InterruptedError("injected interruption")
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, query):
        return [1.0, 0.0, 0.0]


@pytest.fixture
def source(tmp_path):
    dump = tmp_path / "tiny.xml"
    pages = "".join(
        f"<page><title>Rome {i}</title><ns>0</ns><id>{i}</id><revision><text>Roman Republic ended in 27 BC. Augustus became emperor. "
        + "History of Rome. " * 20
        + "</text></revision></page>"
        for i in range(1, 5)
    )
    dump.write_text(
        '<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">'
        + pages
        + "</mediawiki>"
    )
    return dump, {"identity": "fixture-20261018", "sha256": sha256(dump)}


def smoke(path):
    # Actual canonical supervisor/researcher/synthesis tested with local fixture
    # resources in the existing application suite; here verify stage admission.
    assert (path / "lancedb").is_dir()
    return {"outcome": "complete", "citations": 1, "kind": "injected-test-smoke"}


def bundle(tmp_path, source, embedder=None, smoke_fn=smoke, model=None):
    dump, spec = source
    return build_bundle(
        tmp_path / "indexes",
        dump,
        spec,
        model or MODEL,
        embedder or Embedder(),
        smoke_fn,
        batch_size=2,
    )


def test_build_resume_corruption_activation_rollback_and_prune(tmp_path, source):
    interrupted = Embedder(fail=2)
    with pytest.raises(InterruptedError):
        bundle(tmp_path, source, interrupted)
    stage = next((tmp_path / "indexes").glob(".build-*"))
    assert len(list((stage / "vectors").glob("*.json"))) == 1
    resumed = Embedder()
    first = bundle(tmp_path, source, resumed)
    assert resumed.calls == 1  # first two chunks are durable, no duplicate inference
    manifest = verify(first)
    assert manifest["validation"]["chunks"] == 4
    assert bundle(tmp_path, source, Embedder(fail=1)) == first
    indexes = tmp_path / "indexes"
    activate(indexes, first.name)
    second = bundle(tmp_path, source, model={**MODEL, "revision": "b" * 40})
    activate(indexes, second.name)
    activate(indexes, first.name, rollback=True)
    assert (indexes / "active").resolve() == first
    with pytest.raises(ValueError, match="active"):
        prune(indexes, first.name, confirm=True)
    cid = (
        __import__("pyarrow.parquet", fromlist=["read_table"])
        .read_table(second / "chunks.parquet")["chunk_id"][0]
        .as_py()
    )
    before = citation_store(indexes, second.name).get(cid)
    with pytest.raises(ValueError, match="confirm"):
        prune(indexes, second.name)
    prune(indexes, second.name, confirm=True)
    assert not second.exists()
    assert citation_store(indexes, second.name).get(cid).text == before.text
    (first / "articles.parquet").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="inventory"):
        activate(indexes, first.name, rollback=True)
    assert (indexes / "active").resolve() == first


def test_checkpoint_corruption_and_failed_smoke_never_activate(tmp_path, source):
    with pytest.raises(InterruptedError):
        bundle(tmp_path, source, Embedder(fail=2))
    stage = next((tmp_path / "indexes").glob(".build-*"))
    shard = next((stage / "vectors").glob("*.parquet"))
    original = shard.read_bytes()
    shard.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checkpoint corrupt"):
        bundle(tmp_path, source)
    shard.write_bytes(original)
    with pytest.raises(ValueError, match="Canonical smoke"):
        bundle(tmp_path, source, smoke_fn=lambda _: {"outcome": "failed"})
    assert not (tmp_path / "indexes/active").exists()
    assert bundle(tmp_path, source).exists()


def test_schema_or_source_change_isolated_and_path_traversal_denied(tmp_path, source):
    from workbench.data_build.snapshots import managed

    with pytest.raises(ValueError, match="dimension"):
        bundle(tmp_path, source, model={**MODEL, "dimension": 4})
    for name in ["../active", "/tmp/foo", ".hidden", "a/b"]:
        with pytest.raises(ValueError):
            managed(tmp_path / "indexes", name)
    source[0].write_text("changed")
    with pytest.raises(ValueError, match="checksum"):
        bundle(tmp_path, source)


def test_real_directory_conversion_recovers_after_rename(tmp_path, source, monkeypatch):
    built = bundle(tmp_path, source)
    indexes = tmp_path / "indexes"
    processed = tmp_path / "processed"
    processed.mkdir()
    import shutil

    shutil.copyfile(
        built / "articles.parquet", processed / "wikipedia_articles.parquet"
    )
    shutil.copyfile(built / "chunks.parquet", processed / "chunks.parquet")
    (indexes / "active").mkdir()
    shutil.copytree(built / "lancedb", indexes / "active/lancedb")
    with pytest.raises(ValueError, match="real directory"):
        activate(indexes, built.name)
    with pytest.raises(ValueError, match="app-stopped"):
        convert(indexes, processed)
    from workbench.data_build import snapshots

    real_point = snapshots.point
    monkeypatch.setattr(
        snapshots, "point", lambda *_: (_ for _ in ()).throw(InterruptedError())
    )
    with pytest.raises(InterruptedError):
        convert(indexes, processed, app_stopped=True)
    assert not (indexes / "active").exists()
    assert (indexes / "snapshots/legacy-preserved/lancedb").is_dir()
    monkeypatch.setattr(snapshots, "point", real_point)
    legacy = convert(indexes, processed, app_stopped=True)
    assert verify(legacy)["provenance"] == "legacy-unknown"
    assert convert(indexes, processed, app_stopped=True) == legacy
    activate(indexes, built.name)
    activate(indexes, legacy.name, rollback=True)
    assert status(indexes)["layout"] == "symlink"
    assert (processed / "chunks.parquet").exists()


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {"ETag": '"immutable"'}

    def geturl(self):
        return "https://fixture.example/20261018/tiny.xml"


def artifact(data=b"abcde"):
    return {
        "url": "https://fixture.example/20261018/tiny.xml",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }


def test_acquisition_matching_range_resume_and_mismatched_source(tmp_path):
    target = tmp_path / "tiny.xml"
    spec = artifact()
    part = target.with_name(target.name + ".part")
    meta = target.with_name(target.name + ".download.json")
    part.write_bytes(b"ab")
    atomic_json(meta, {"spec": spec, "etag": '"immutable"'})

    def opener(req, **kwargs):
        assert req.get_header("Range") == "bytes=2-"
        return Response(
            b"cde", 206, {"ETag": '"immutable"', "Content-Range": "bytes 2-4/5"}
        )

    assert download(spec, target, opener=opener).read_bytes() == b"abcde"
    assert (
        download(spec, target, opener=lambda *_: pytest.fail("idempotent download"))
        == target
    )
    target.unlink()
    part.write_bytes(b"ab")
    with pytest.raises(ValueError, match="Unmatched"):
        download(artifact(b"other"), target, opener=opener)
    with pytest.raises(ValueError, match="Range/ETag"):
        download(
            spec,
            target,
            opener=lambda *a, **kw: Response(b"cde", 206, {"ETag": '"changed"'}),
        )
    assert part.read_bytes() == b"ab"


def test_download_corrupt_restart_and_interruption(tmp_path):
    target = tmp_path / "tiny.xml"
    with pytest.raises(ValueError, match="checksum"):
        download(artifact(), target, opener=lambda *a, **kw: Response(b"xxxxx"))
    assert not target.exists()
    # Server ignores range -> safe restart, never append the full body.
    assert (
        download(
            artifact(), target, opener=lambda *a, **kw: Response(b"abcde")
        ).read_bytes()
        == b"abcde"
    )
    target.unlink()

    class Broken(Response):
        def read(self, size):
            if self.tell():
                raise ConnectionError("injected network loss")
            return super().read(2)

    with pytest.raises(ConnectionError):
        download(artifact(), target, opener=lambda *a, **kw: Broken(b"abcde"))
    assert target.with_name("tiny.xml.part").read_bytes() == b"ab"
    with pytest.raises(ValueError, match="immutable"):
        download({**artifact(), "url": "https://fixture.example/latest/tiny"}, target)


def test_kernel_lease_released_on_process_kill(tmp_path):
    path = tmp_path / "lease"
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            'from pathlib import Path; from workbench.data_build.lifecycle import Lease; import time,sys; lease=Lease(Path(sys.argv[1])); print("ready",flush=True); time.sleep(60)',
            str(path),
        ],
        stdout=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(RuntimeError, match="busy"):
            Lease(path)
        child.send_signal(signal.SIGKILL)
        child.wait(timeout=5)
        with Lease(path):
            pass
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_shared_app_lease_blocks_mutations(tmp_path):
    with Lease(tmp_path / "lease", shared=True), Lease(tmp_path / "lease", shared=True):
        with pytest.raises(RuntimeError, match="busy"):
            Lease(tmp_path / "lease")


def test_bootstrap_never_replaces_existing_venv(tmp_path):
    from scripts.provision import provision_environment

    env = tmp_path / "venv"
    env.mkdir()
    marker = env / "keep"
    marker.write_text("user asset")
    lock = tmp_path / "lock.txt"
    lock.write_text("")
    with pytest.raises(ValueError, match="unowned"):
        provision_environment(env, lock)
    assert marker.read_text() == "user asset"


def test_built_bundle_runs_actual_canonical_graph(tmp_path, source):
    from tests.test_evidence_worker import Model, draft
    from tests.test_supervisor_orchestration import proposal

    from workbench.graphs.research_models import ConversationInput
    from workbench.retrieval.hybrid import HydratedRetrieval
    from workbench.systems.runner import Resources, create_application

    def canonical(path):
        import lancedb

        from workbench.stores.chunk_store import ChunkStore

        table = lancedb.connect(str(path / "lancedb")).open_table("chunks")
        row = table.search("Roman", query_type="fts").limit(1).to_list()[0]
        evidence = ChunkStore(path / "lancedb").get(row["chunk_id"])

        class Retriever:
            def retrieve_hydrated(self, query, top_k):
                return HydratedRetrieval((evidence,), 1, 1)

        with create_application(
            resources=Resources(
                Model(
                    proposal(),
                    draft(statement="Roman Republic ended in 27 BC."),
                    json.dumps(
                        {
                            "units": [
                                {
                                    "text": "Roman Republic ended in 27 BC.",
                                    "finding_ids": ["F1"],
                                }
                            ]
                        }
                    ),
                ),
                Retriever(),
                path.name,
            )
        ) as app:
            result = app.invoke(
                ConversationInput(question="When did the Roman Republic end?")
            )
        assert result.outcome == "complete"
        assert result.citations[0].excerpts[0].key.chunk_id == row["chunk_id"]
        return {
            "outcome": result.outcome,
            "citations": len(result.citations),
            "kind": "real-graph-fixture-model",
        }

    assert (
        verify(bundle(tmp_path, source, smoke_fn=canonical))["canonical_smoke"]["kind"]
        == "real-graph-fixture-model"
    )


def test_symlink_failure_leaves_active_and_journal_recoverable(
    tmp_path, source, monkeypatch
):
    first = bundle(tmp_path, source)
    second = bundle(tmp_path, source, model={**MODEL, "revision": "c" * 40})
    indexes = tmp_path / "indexes"
    activate(indexes, first.name)
    real_replace = os.replace

    def failure(src, dst):
        if Path(dst).name == "active":
            raise OSError("injected promotion failure")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", failure)
    with pytest.raises(OSError, match="promotion failure"):
        activate(indexes, second.name)
    assert (indexes / "active").resolve() == first
    assert status(indexes)["promotion"]["state"] == "pending"
    monkeypatch.setattr(os, "replace", real_replace)
    activate(indexes, second.name)
    assert (indexes / "active").resolve() == second


def test_prune_interruption_keeps_citations_and_named_retry_finishes(
    tmp_path, source, monkeypatch
):
    import pyarrow.parquet as pq

    from workbench.data_build import snapshots

    first = bundle(tmp_path, source)
    indexes = tmp_path / "indexes"
    cid = pq.read_table(first / "chunks.parquet")["chunk_id"][0].as_py()
    real_rmtree = snapshots.shutil.rmtree
    monkeypatch.setattr(
        snapshots.shutil, "rmtree", lambda _: (_ for _ in ()).throw(InterruptedError())
    )
    with pytest.raises(InterruptedError):
        prune(indexes, first.name, confirm=True)
    assert citation_store(indexes, first.name).get(cid)
    monkeypatch.setattr(snapshots.shutil, "rmtree", real_rmtree)
    prune(indexes, first.name, confirm=True)
    assert not (indexes / (".prune-" + first.name)).exists()
    assert citation_store(indexes, first.name).get(cid)


def test_runtime_rejects_new_bundle_embedding_identity_drift(
    tmp_path, source, monkeypatch
):
    from workbench.core.config import load_config
    from workbench.systems import runner

    built = bundle(tmp_path, source)
    cfg = load_config()
    cfg.paths["active_index"] = str(built)
    with pytest.raises(ValueError, match="model mismatch"):
        runner.application_key(cfg)
    cfg.embeddings["model_name"] = "fixture"
    wrong_revision = tmp_path / "wrong-revision"
    wrong_revision.mkdir()
    monkeypatch.setattr(runner, "cached_model_path", lambda _: str(wrong_revision))
    with pytest.raises(ValueError, match="revision mismatch"):
        runner.application_key(cfg)


def test_killed_embedding_process_resumes_committed_shard(tmp_path, source):
    import select

    dump, spec = source
    code = """
import sys,time
from pathlib import Path
from tests.test_data_lifecycle import Embedder, MODEL, smoke
from workbench.data_build.pipeline import build_bundle
from workbench.data_build.lifecycle import sha256
class Interrupted(Embedder):
    def embed_texts(self,texts):
        if self.calls == 1:
            print('checkpoint-ready',flush=True)
            time.sleep(60)
        return super().embed_texts(texts)
build_bundle(Path(sys.argv[1])/'indexes',Path(sys.argv[2]),{'identity':'fixture-20261018','sha256':sha256(Path(sys.argv[2]))},MODEL,Interrupted(),smoke,batch_size=2)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path), str(dump)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
    )
    try:
        assert select.select([child.stdout], [], [], 15)[0]
        assert child.stdout.readline().strip() == "checkpoint-ready"
        child.kill()
        child.wait(timeout=5)
        resumed = Embedder()
        result = bundle(tmp_path, source, resumed)
        assert resumed.calls == 1
        assert verify(result)["validation"]["chunks"] == 4
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_checked_in_artifact_pins_and_exact_locks_are_well_formed():
    import json
    import re
    import tomllib

    from workbench.data_build.acquisition import validate_artifact

    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    assert project["name"] == "atlas-localis"
    assert project["requires-python"] == ">=3.12,<3.13"
    manifest = json.loads((root / "configs/artifacts/models.json").read_text())
    for model in manifest["models"]:
        assert re.fullmatch("[0-9a-f]{40}", model["revision"])
        assert model["license_reference"] and model["license"]
        for spec in model["files"].values():
            validate_artifact(spec)
            assert "/resolve/" + model["revision"] + "/" in spec["url"]
    validate_artifact(
        json.loads((root / "configs/artifacts/wikipedia.json").read_text())
    )
    for path in (root / "locks").glob("*.txt"):
        entries = [
            line
            for line in path.read_text().splitlines()
            if line and not line.startswith(("#", "--extra-index-url "))
        ]
        from packaging.requirements import Requirement

        for line in entries:
            requirement = Requirement(line)
            assert len(requirement.specifier) == 1
            pin = next(iter(requirement.specifier))
            assert pin.operator == "==" and "*" not in pin.version
            if requirement.marker:
                assert path.name == "ci-py312.txt"
                assert requirement.name == "torch"
                assert str(requirement.marker) in {
                    'sys_platform == "darwin"',
                    'sys_platform == "linux"',
                }
        assert len(entries) == len(set(entries))
        assert "lancedb==0.29.2" in entries


def test_staged_and_sealed_canonical_snapshot_id_match(tmp_path, source):
    from workbench.core.config import load_config
    from workbench.systems.runner import application_key

    cfg = load_config()
    seen = []

    def inspect_stage(path):
        cfg.paths["active_index"] = str(path)
        seen.append(application_key(cfg).snapshot)
        return smoke(path)

    result = bundle(tmp_path, source, smoke_fn=inspect_stage)
    assert seen == [result.name]


def test_update_cli_activates_fixture_without_model_provisioning(
    tmp_path, source, monkeypatch, capsys
):
    from workbench.data_build import provisioning

    built = bundle(tmp_path, source)
    monkeypatch.setattr(
        provisioning,
        "provision_models",
        lambda *_: pytest.fail("Update must never provision/upgrade models"),
    )
    from types import SimpleNamespace

    monkeypatch.setattr(
        provisioning.shutil, "disk_usage", lambda _: SimpleNamespace(free=100 * 1024**3)
    )
    monkeypatch.setattr(provisioning, "build", lambda *_: built)
    monkeypatch.setattr(
        sys, "argv", ["provisioning", "update", "--data", str(tmp_path)]
    )
    provisioning.main()
    assert "Activated " + built.name in capsys.readouterr().out
    assert (tmp_path / "indexes/active").resolve() == built


def test_download_without_validator_restarts_and_existing_corruption_preserved(
    tmp_path,
):
    spec = artifact()
    target = tmp_path / "tiny.xml"
    part = target.with_name("tiny.xml.part")
    part.write_bytes(b"ab")
    atomic_json(
        target.with_name("tiny.xml.download.json"), {"spec": spec, "etag": 'W/"weak"'}
    )

    def restart(req, **_):
        assert req.get_header("Range") is None
        return Response(b"abcde")

    download(spec, target, opener=restart)
    target.write_bytes(b"xxxxx")
    with pytest.raises(ValueError, match="Existing artifact differs"):
        download(
            spec,
            target,
            opener=lambda *_: pytest.fail("Must preserve corrupt existing artifact"),
        )
    assert target.read_bytes() == b"xxxxx"


def test_create_only_vector_primitive_preserves_existing_table(tmp_path):
    from workbench.data_build.index_vector import build_vector_index

    destination = tmp_path / "existing"
    destination.mkdir()
    keep = destination / "keep"
    keep.write_text("owner data")
    with pytest.raises(ValueError, match="Existing index preserved"):
        build_vector_index(
            tmp_path / "missing.parquet", destination, Embedder(), overwrite=True
        )
    assert keep.read_text() == "owner data"


def test_native_smoke_failure_reaps_only_owned_child(tmp_path, monkeypatch):
    from workbench.models import native_process
    from workbench.models.llamacpp_server import LlamaCppServerModel

    gguf = tmp_path / "fixture.gguf"
    gguf.write_bytes(b"fixture")
    real_popen = subprocess.Popen
    children = []

    def spawn(*_, **kwargs):
        assert kwargs["start_new_session"] is True
        child = real_popen(
            [sys.executable, "-c", "import time; time.sleep(60)"], **kwargs
        )
        children.append(child)
        return child

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def bind(self, address):
            assert address == ("127.0.0.1", 0)

        def getsockname(self):
            return ("127.0.0.1", 54321)

    monkeypatch.setattr(native_process.socket, "socket", Socket)
    monkeypatch.setattr(native_process.shutil, "which", lambda _: sys.executable)
    monkeypatch.setattr(
        native_process.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, "build 8130 ed4837891", ""),
    )
    monkeypatch.setattr(native_process.subprocess, "Popen", spawn)
    monkeypatch.setattr(LlamaCppServerModel, "check_identity", lambda _: None)
    with (
        pytest.raises(RuntimeError, match="injected smoke failure"),
        native_process.native_smoke_server(gguf, tmp_path / "native.log") as endpoint,
    ):
        assert endpoint == "http://127.0.0.1:54321"
        raise RuntimeError("injected smoke failure")
    assert children[0].poll() is not None


def test_unrelated_pointer_temporary_and_partial_symlink_are_preserved(
    tmp_path, source
):
    first = bundle(tmp_path, source)
    indexes = tmp_path / "indexes"
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    temporary = indexes / ".active-next"
    temporary.symlink_to(unrelated)
    with pytest.raises(ValueError, match="Unmatched .active-next"):
        activate(indexes, first.name)
    assert temporary.resolve() == unrelated
    target = tmp_path / "artifact"
    partial = target.with_name("artifact.part")
    keep = tmp_path / "owner"
    keep.write_bytes(b"preserve")
    partial.symlink_to(keep)
    with pytest.raises(ValueError, match="symlinks"):
        download(artifact(), target)
    assert keep.read_bytes() == b"preserve"


def test_legacy_download_and_keyword_builder_cannot_overwrite_active(tmp_path):
    from workbench.data_build.index_keyword import build_keyword_index

    active = tmp_path / "active/lancedb"
    active.mkdir(parents=True)
    keep = active / "keep"
    keep.write_text("owner index")
    with pytest.raises(ValueError, match="immutable"):
        build_keyword_index(active)
    assert keep.read_text() == "owner index"
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["bash", str(root / "scripts/download_wikipedia.sh")],
        capture_output=True,
        text=True,
        timeout=5,
        cwd=tmp_path,
    )
    assert result.returncode == 2
    assert "retired" in result.stderr
    assert not (tmp_path / "data").exists()


pytestmark = pytest.mark.fixture_integration
