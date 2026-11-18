"""Canonical local application factory and safe execution/event boundary.

Native calls drain before admission is released. Closing a stream cancels and
joins its producer; raw graph updates never cross this public boundary.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import queue
import tempfile
import threading
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opentelemetry import context as otel_context

from workbench.core.config import WorkbenchConfig, load_config
from workbench.core.offline import cached_model_path, validate_environment
from workbench.core.run_context import run_context
from workbench.graphs import execution as execution_module
from workbench.graphs.base_state import application_state
from workbench.graphs.execution import Execution, RunStopped
from workbench.graphs.planning import bounded_conversation
from workbench.graphs.research_models import (
    ApplicationResult,
    ConversationInput,
    Fingerprint,
    Limits,
)
from workbench.graphs.supervisor_graph import _message, build_supervisor_graph
from workbench.models.llamacpp_server import LlamaCppServerModel
from workbench.observability.tracing import start_span
from workbench.retrieval.evidence_packing import tokenizer_budget
from workbench.retrieval.hybrid import HybridRetriever
from workbench.retrieval.keyword_search import LanceDBKeywordSearcher
from workbench.retrieval.vector_search import LanceDBVectorSearcher
from workbench.stores.chunk_store import ChunkStore

_RUN_GATE = threading.Lock()


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()


@dataclass(frozen=True)
class CacheKey:
    config: str
    models: str
    snapshot: str
    path: str


def application_key(config: WorkbenchConfig) -> CacheKey:
    """Resolve the active pointer before cache lookup. No mutable config keys."""
    pinned = Path(config.paths["active_index"]).resolve(strict=True)
    db = pinned / "lancedb"
    if not db.is_dir():
        raise FileNotFoundError(f"Provisioned local index missing: {db}")
    # Legacy bundles lack manifests. Identify their actual path and immutable
    # Lance version metadata honestly; Stage 4 supplies full provenance.
    manifests = sorted(db.glob("**/_versions/*.manifest"))
    identity = [
        (str(p.relative_to(pinned)), p.stat().st_size, p.stat().st_mtime_ns)
        for p in manifests
    ]
    manifest = pinned / "manifest.json"
    if manifest.is_file():
        identity.append(("manifest.json", manifest.read_text()))
    bundle_manifest = json.loads(manifest.read_text()) if manifest.is_file() else {}
    embedding_pin = None
    if bundle_manifest.get("provenance") == "verified-build":
        embedding_pin = bundle_manifest["inputs"]["embedding"]
        if config.embeddings["model_name"] != embedding_pin["id"]:
            raise ValueError(
                "Snapshot embedding model mismatch; build a matching snapshot"
            )
        asset = Path(
            cached_model_path(
                config.embeddings.get("model_path") or config.embeddings["model_name"]
            )
        )
        if asset.name != embedding_pin["revision"]:
            raise ValueError(
                "Snapshot embedding revision mismatch; use the pinned provisioned model"
            )
    assets = []
    for section in (config.embeddings, config.reranking):
        if section.get("provider") == "http" or not section.get("enabled", True):
            continue
        try:
            asset = Path(
                cached_model_path(section.get("model_path") or section["model_name"])
            )
            # Resolve cached revision and file metadata without reading weight contents.
            assets.append(
                (
                    str(asset),
                    [
                        (
                            str(p.relative_to(asset)),
                            p.stat().st_size,
                            p.stat().st_mtime_ns,
                        )
                        for p in sorted(asset.rglob("*"))
                        if p.is_file()
                    ],
                )
            )
        except (FileNotFoundError, ImportError):
            assets.append("not-provisioned")  # Actual load still fails explicitly.
    snapshot_id = bundle_manifest.get("id", "local-" + digest((str(pinned), identity)))
    stage_inputs = pinned / "inputs.json"
    if (
        not manifest.is_file()
        and pinned.name.startswith(".build-")
        and stage_inputs.is_file()
    ):
        input_fingerprint = digest(json.loads(stage_inputs.read_text()))
        if pinned.name != ".build-" + input_fingerprint:
            raise ValueError("Staging bundle fingerprint mismatch")
        # Canonical build-smoke citations retain their identity after sealing/rename.
        snapshot_id = input_fingerprint
    return CacheKey(
        digest(config.model_dump()),
        digest((config.model, config.embeddings, config.reranking, assets)),
        snapshot_id,
        str(pinned),
    )


class ResourceBusy(RuntimeError):
    pass


class ResourceOwner:
    """Host-local ownership is acquired before clients/heavy models load."""

    def __init__(self, path: Path | None = None):
        self.file = (
            path
            or Path(tempfile.gettempdir()) / f"workbench-resources-{os.getuid()}.lock"
        ).open("a+")
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.file.close()
            raise ResourceBusy(
                "resource_busy: close the resident application first"
            ) from exc

    def close(self):
        if not self.file.closed:
            fcntl.flock(self.file, fcntl.LOCK_UN)
            self.file.close()


@dataclass(frozen=True)
class Resources:
    model: Any
    retriever: Any
    snapshot_id: str


@dataclass(frozen=True)
class EvaluationControls:
    forced_single_task: bool = False
    rerank: bool | None = None


def build_embedder(cfg: WorkbenchConfig) -> Any:
    """
    Build an embedder based on config ``embeddings.provider``.

    - ``"http"`` → temporary baseline probe adapter; pending answer acceptance
    - ``"local"`` or ``"sentence_transformers"`` → heavyweight
      :class:`SentenceTransformerEmbedder` (loads model in-process)

    Args:
        cfg: Validated workbench config.

    Returns:
        Object satisfying the ``Embedder`` protocol.
    """
    emb_cfg = cfg.embeddings
    provider = emb_cfg.get("provider", "local")

    if provider == "http":
        from workbench.data_build.http_embedder import HTTPEmbedder

        return HTTPEmbedder(
            base_url=emb_cfg.get("base_url", "http://127.0.0.1:8081"),
            model_name=emb_cfg["model_name"],
            timeout_seconds=emb_cfg.get("timeout_seconds", 120),
        )

    if provider not in {"local", "sentence_transformers"}:
        raise ValueError("Unsupported embedding provider")
    from workbench.data_build.embeddings import SentenceTransformerEmbedder

    return SentenceTransformerEmbedder(
        model_name=emb_cfg["model_name"],
        model_path=emb_cfg.get("model_path"),
        device=emb_cfg.get("device", "mps"),
        batch_size=emb_cfg.get("batch_size", 4),
    )


def build_reranker(cfg: WorkbenchConfig) -> Any | None:
    """
    Build a reranker based on config ``reranking.provider``.

    - ``"http"`` → temporary baseline probe adapter; pending answer acceptance
    - ``"cross_encoder"``  → heavyweight :class:`CrossEncoderReranker`
    - disabled             → ``None``

    Args:
        cfg: Validated workbench config.

    Returns:
        Object satisfying the ``Reranker`` protocol, or None.
    """
    rr_cfg = cfg.reranking
    if not rr_cfg.get("enabled", True):
        return None

    provider = rr_cfg.get("provider", "cross_encoder")

    if provider == "http":
        from workbench.retrieval.http_reranker import HTTPReranker

        return HTTPReranker(
            base_url=rr_cfg.get("base_url", "http://127.0.0.1:8082"),
            model_name=rr_cfg["model_name"],
            timeout_seconds=rr_cfg.get("timeout_seconds", 60),
        )

    if provider != "cross_encoder":
        raise ValueError("Unsupported reranking provider")
    from workbench.retrieval.rerankers import CrossEncoderReranker

    return CrossEncoderReranker(
        model_name=rr_cfg["model_name"],
        model_path=rr_cfg.get("model_path"),
        device=rr_cfg.get("device", "mps"),
        batch_size=rr_cfg.get("batch_size", 1),
        max_length=rr_cfg.get("max_length", 8192),
    )


class Application:
    def __init__(
        self,
        resources: Resources,
        *,
        config=None,
        key=None,
        limits=None,
        evaluation=None,
        owner=None,
        telemetry=None,
        event_sink=None,
    ):
        self.resources = resources
        self.config = copy.deepcopy(config)
        self.key = key
        self.limits = limits or Limits()
        self.evaluation = evaluation or EvaluationControls()
        self._owner, self._telemetry, self._sink = owner, telemetry, event_sink
        self._closed = False
        self._lifecycle = threading.Lock()
        self._active = None
        self._graph = build_supervisor_graph(
            model=resources.model,
            retriever=resources.retriever,
            snapshot_id=resources.snapshot_id,
            limits=self.limits,
            forced_single_task=self.evaluation.forced_single_task,
        )
        self.fingerprints = (
            Fingerprint(
                component="code",
                identity=digest(
                    [
                        (str(p.relative_to(Path(__file__).parents[1])), p.read_text())
                        for p in sorted(Path(__file__).parents[1].rglob("*.py"))
                    ]
                ),
            ),
            Fingerprint(component="snapshot", identity=resources.snapshot_id),
            Fingerprint(component="models", identity=key.models if key else "injected"),
            Fingerprint(
                component="config",
                identity=digest(
                    (key.config if key else "injected", self.limits.model_dump())
                ),
            ),
            Fingerprint(
                component="evaluation_controls",
                identity=json.dumps(self.evaluation.__dict__, sort_keys=True),
            ),
            Fingerprint(
                component="prompts",
                identity=digest(
                    [
                        (p.name, p.read_text())
                        for p in sorted(
                            (Path(__file__).parents[1] / "prompting/templates").glob(
                                "*_v1.txt"
                            )
                        )
                    ]
                ),
            ),
        )

    def _publish(self, result, execution):
        return ApplicationResult.model_validate(
            result.model_dump()
            | {
                "fingerprints": tuple(f.model_dump() for f in self.fingerprints),
                "diagnostics": result.diagnostics.model_dump()
                | {
                    "elapsed_ms": (time.monotonic() - execution.started) * 1000,
                    "tokens_in": execution.tokens_in,
                    "tokens_out": execution.tokens_out,
                },
            }
        )

    def stream(self, value: ConversationInput, config=None, *, cancel_event=None):
        """Yield only typed progress and exactly one terminal result.

        Consume to completion or close the generator. A synchronous hung call
        keeps close/join waiting and holds admission; it cannot be preempted.
        """
        value = bounded_conversation(ConversationInput.model_validate(value))
        config = copy.copy(config or {})
        cancel = cancel_event if cancel_event is not None else threading.Event()
        events = queue.Queue()

        def sink(event):
            events.put(event)
            if self._sink:
                try:
                    self._sink(event)
                except Exception:
                    pass

        execution = Execution(
            self.resources.snapshot_id, self.limits, sink, cancel=cancel
        )
        with self._lifecycle:
            admitted = not self._closed and _RUN_GATE.acquire(blocking=False)
            if admitted:
                self._active = execution
        if not admitted:
            code = "application_closed" if self._closed else "resource_busy"
            result = self._publish(
                _message(
                    execution,
                    "failed",
                    code,
                    "Application closed."
                    if self._closed
                    else "Another research run is active.",
                ),
                execution,
            )
            execution.emit("run_finished", result=result)
            yield events.get()
            return
        parent = otel_context.get_current()

        def produce():
            result = None
            stop = execution.watch_cancellation()
            try:
                with ExitStack() as stack:
                    if self._telemetry:
                        stack.enter_context(self._telemetry.run(execution.run_id))
                    stack.enter_context(
                        run_context({}, run_type="application", run_id=execution.run_id)
                    )
                    token = otel_context.attach(parent)
                    stack.callback(otel_context.detach, token)
                    stack.enter_context(
                        start_span(
                            "atlas_localis.question",
                            attributes={
                                "run_id": execution.run_id,
                                "snapshot_id": execution.snapshot_id,
                            },
                        )
                    )
                    counter = getattr(self.resources.model, "exact_token_count", None)
                    if callable(counter):
                        stack.enter_context(tokenizer_budget(counter))
                    execution.parent = otel_context.get_current()
                    execution.emit("run_started")
                    execution.check()
                    runnable = {
                        **config,
                        "configurable": {
                            **(config.get("configurable") or {}),
                            "atlas_execution": execution,
                        },
                        "max_concurrency": max(4, config.get("max_concurrency") or 4),
                    }
                    with execution.bind():
                        for state in self._graph.stream(
                            application_state(value),
                            config=runnable,
                            stream_mode="values",
                        ):
                            if state.get("result"):
                                result = ApplicationResult.model_validate(
                                    state["result"]
                                )
                                if result.run_id != execution.run_id or any(
                                    c.snapshot_id != execution.snapshot_id
                                    for c in result.citations
                                ):
                                    raise ValueError("terminal identity mismatch")
                        execution.check()  # reject cancellation/late finalization too
                    if result is None:
                        raise RuntimeError("missing terminal result")
            except Exception as exc:
                outcome = "cancelled" if cancel.is_set() else "failed"
                code = (
                    "run_deadline"
                    if isinstance(exc, RunStopped)
                    else "application_failed"
                )
                if execution_module._UNCONFIRMED.is_set():
                    outcome, code = "failed", "resource_not_drained"
                result = _message(
                    execution,
                    outcome,
                    code,
                    "Research cancelled after active operations drained."
                    if outcome == "cancelled"
                    else "Research could not complete safely.",
                )
            finally:
                stop()
                try:
                    if cancel.is_set() and not execution_module._UNCONFIRMED.is_set():
                        result = _message(
                            execution,
                            "cancelled",
                            "cancelled",
                            "Research cancelled after active operations drained.",
                        )
                    result = self._publish(result, execution)
                    execution.emit("run_finished", result=result)
                    if self._telemetry:
                        try:
                            with (
                                self._telemetry.run(execution.run_id),
                                run_context(
                                    {}, run_type="application", run_id=execution.run_id
                                ),
                            ):
                                self._telemetry.record(result)
                        except Exception:
                            pass
                finally:
                    with self._lifecycle:
                        self._active = None
                    _RUN_GATE.release()

        thread = threading.Thread(
            target=produce, name="workbench-application", daemon=True
        )
        thread.start()
        try:
            while True:
                event = events.get()
                yield event
                if event.kind == "run_finished":
                    break
        finally:
            if thread.is_alive():
                cancel.set()
            thread.join()

    def invoke(
        self, value: ConversationInput, config=None, *, cancel_event=None
    ) -> ApplicationResult:
        terminal = None
        for event in self.stream(value, config, cancel_event=cancel_event):
            if event.kind == "run_finished":
                terminal = event.result
        return terminal

    def close(self):
        with self._lifecycle:
            if self._active is not None:
                raise ResourceBusy(
                    "resource_busy: drain the active stream before closing resources"
                )
            self._closed = True
            self._graph = None
            self.resources = Resources(None, None, self.resources.snapshot_id)
            if self._owner:
                self._owner.close()
            elif self._telemetry:
                self._telemetry.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def create_application(
    *,
    config=None,
    config_path=None,
    resources=None,
    key=None,
    limits=None,
    evaluation=None,
    event_sink=None,
):
    """One factory, with explicit resources for model-free consumers/tests."""
    validate_environment()
    evaluation = evaluation or EvaluationControls()
    if resources is not None:
        return Application(
            resources,
            config=config,
            key=key,
            limits=limits,
            evaluation=evaluation,
            event_sink=event_sink,
        )
    cfg = (config or load_config(config_path=config_path)).model_copy(deep=True)
    if evaluation.rerank is not None:
        cfg.reranking["enabled"] = evaluation.rerank
    actual_key = application_key(cfg)
    if key is not None and key != actual_key:
        raise ValueError(
            "Application cache key no longer matches configuration/snapshot"
        )
    context = cfg.model_extra.get("context", {})
    limits = limits or Limits(
        max_evidence=cfg.retrieval.get("final_top_k", 5),
        max_per_document=context.get("max_chunks_per_doc", 3),
        prompt_tokens=context.get("prompt_tokens", 6000),
        output_tokens=context.get("output_tokens", 1200),
    )
    owner = ResourceOwner()
    cleanup = ExitStack()
    cleanup.callback(owner.close)
    telemetry = None
    try:
        from workbench.observability.lifecycle import Telemetry

        telemetry = Telemetry(cfg)
        cleanup.callback(telemetry.close)
        # Shared lease blocks conversion/prune/promotion while resources are live.
        from workbench.data_build.lifecycle import Lease

        index_root = Path(cfg.paths["indexes_dir"])
        lease = Lease(index_root / ".lifecycle.lock", shared=True)
        cleanup.callback(lease.close)
        if application_key(cfg) != actual_key:
            raise ValueError("Snapshot changed before lifecycle lease acquisition")
        pinned = Path(actual_key.path) / "lancedb"
        model = LlamaCppServerModel(
            base_url=cfg.model["base_url"],
            model_name=cfg.model["model_name"],
            timeout_seconds=cfg.model.get("timeout_seconds", 60),
            default_temperature=cfg.model.get("temperature", 0.7),
            default_max_tokens=cfg.model.get("max_tokens", 2048),
        )
        if callable(getattr(model, "close", None)):
            cleanup.callback(model.close)
        check = getattr(model, "check_identity", None)
        if callable(check):
            try:
                check()
            except Exception as exc:
                raise ValueError(
                    "Native llama-server unavailable or model identity mismatch. "
                    "Start the provisioned scripts/start_model_stack.sh; preserve any unowned listener."
                ) from exc
        embedder = build_embedder(cfg)
        if callable(getattr(embedder, "close", None)):
            cleanup.callback(embedder.close)
        bundle_manifest_path = Path(actual_key.path) / "manifest.json"
        if bundle_manifest_path.is_file():
            bundle_manifest = json.loads(bundle_manifest_path.read_text())
            if bundle_manifest.get("provenance") == "verified-build":
                pin = bundle_manifest["inputs"]["embedding"]
                if (
                    getattr(embedder, "dimension", None) != pin["dimension"]
                    or getattr(embedder, "_query_prefix", None) != pin["query_prefix"]
                    or getattr(embedder, "_passage_prefix", None)
                    != pin["passage_prefix"]
                    or pin.get("normalize") is not True
                ):
                    raise ValueError(
                        "Snapshot vector schema/prefix/normalization incompatibility"
                    )
        reranker = build_reranker(cfg)
        if callable(getattr(reranker, "close", None)):
            cleanup.callback(reranker.close)
        store = ChunkStore(db_path=pinned)
        keyword = LanceDBKeywordSearcher(db_path=pinned)
        vector = LanceDBVectorSearcher(db_path=pinned, embedder=embedder)
        # Bind every handle to the same existing Lance version, never latest.
        version = store._table.version
        for table in (store._table, keyword._table, vector._table):
            table.checkout(version)
        if application_key(cfg) != actual_key:
            raise ValueError("Snapshot changed while resources were opening")
        retriever = HybridRetriever(
            keyword_searcher=keyword,
            vector_searcher=vector,
            reranker=reranker,
            chunk_store=store,
            **{
                k: cfg.retrieval.get(k, v)
                for k, v in {
                    "keyword_top_k": 20,
                    "vector_top_k": 20,
                    "merge_top_n": 50,
                    "rrf_k": 60,
                }.items()
            },
        )
        return Application(
            Resources(model, retriever, actual_key.snapshot),
            config=cfg,
            key=actual_key,
            limits=limits,
            evaluation=evaluation,
            owner=cleanup,
            telemetry=telemetry,
            event_sink=event_sink,
        )
    except BaseException:
        cleanup.close()
        raise


class ApplicationCache:
    """Keep one resource owner; invalidate only while idle, by explicit identity."""

    def __init__(self, factory=create_application):
        self.factory = factory
        self.application = None
        self.key = None
        self.lock = threading.Lock()

    def get(self, config, key):
        with self.lock:
            if (
                self.application is not None
                and not self.application._closed
                and self.key == key
            ):
                return self.application
            if self.application is not None:
                self.application.close()  # busy close fails before loading another stack
                self.application = None
                self.key = None
            self.application = self.factory(config=config, key=key)
            self.key = key
            return self.application

    def close(self):
        with self.lock:
            if self.application is not None:
                self.application.close()
                self.application = None
                self.key = None
