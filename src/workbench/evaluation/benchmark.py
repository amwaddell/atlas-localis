"""Evaluation-only observation, retrieval ablations and reproducibility records.

Never changes graph state or prompts. Captured synthetic conversation/excerpt
inputs make semantic review possible; no automated entailment judge is implied.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import platform
import resource
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from workbench.core.offline import cached_model_path
from workbench.core.types import Query
from workbench.graphs.execution import current_task
from workbench.graphs.research_models import ApplicationResult, PlanProposal
from workbench.retrieval.hybrid import reciprocal_rank_fusion

ROOT = Path(__file__).resolve().parents[3]


def digest_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sanitized(value):
    """Remove owner-specific filesystem names, preserving artifact fingerprints."""
    encoded = json.dumps(value, ensure_ascii=False)
    for path, replacement in [(str(ROOT), "<checkout>"), (str(Path.home()), "<home>")]:
        encoded = encoded.replace(path, replacement)
    return json.loads(encoded)


def provenance(config, suite, command):
    def output(cmd):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode:
            return "unavailable: " + result.stderr.strip()
        return result.stdout.strip()

    def revision(section):
        try:
            return Path(
                cached_model_path(section.get("model_path") or section["model_name"])
            ).name
        except (FileNotFoundError, ImportError) as exc:
            # Preserve setup failures in report.errors; the canonical factory
            # still fails closed when the actual resource cannot be loaded.
            return "unavailable: " + type(exc).__name__

    paths = [
        p
        for base in ("src/workbench", "scripts", "configs", "locks")
        for p in (ROOT / base).rglob("*")
        if p.is_file() and p.suffix in {".py", ".txt", ".json", ".yaml"}
    ]
    return sanitized(
        {
            "format": 1,
            "command": command,
            "commit": output(["git", "rev-parse", "HEAD"]),
            "dirty": bool(output(["git", "status", "--porcelain"])),
            "source_sha256": {
                str(p.relative_to(ROOT)): digest_file(p) for p in sorted(paths)
            },
            "config": config.model_dump(),
            "suite_version": suite["version"],
            "suite_sha256": hashlib.sha256(
                json.dumps(suite, sort_keys=True).encode()
            ).hexdigest(),
            "snapshot_id": suite["snapshot_id"],
            "corpus_provenance": suite["corpus_provenance"],
            "models_manifest": json.loads(
                (ROOT / "configs/artifacts/models.json").read_text()
            ),
            "cached_revisions": {
                s["model_name"]: revision(s)
                for s in (config.embeddings, config.reranking)
            },
            "native_manifest": json.loads(
                (ROOT / "configs/artifacts/native.json").read_text()
            ),
            "hardware": {
                "platform": platform.platform(),
                "machine": platform.machine(),
                "cpu": output(["sysctl", "-n", "machdep.cpu.brand_string"])
                if sys.platform == "darwin"
                else platform.processor(),
                "memory_bytes": output(["sysctl", "-n", "hw.memsize"])
                if sys.platform == "darwin"
                else None,
            },
            "python": platform.python_version(),
            "packages": {
                d.metadata["Name"]: d.version
                for d in importlib.metadata.distributions()
            },
            "limitations": [
                "Legacy corpus dump date and index build provenance unknown.",
                "No independent human review; agent labels are provisional.",
                "Parent RSS high-water excludes native server; Metal allocation is not additive physical memory.",
            ],
        }
    )


def memory():
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    row = {"parent_peak_rss_bytes": rss if sys.platform == "darwin" else rss * 1024}
    try:
        import torch

        if torch.backends.mps.is_available():
            row["metal_driver_allocated_bytes_at_sample"] = (
                torch.mps.driver_allocated_memory()
            )
    except ImportError:
        pass
    return row


@contextmanager
def observe(application):
    """Observe actual native calls and real SDK spans; restore the client on exit."""
    calls = []
    exporter = InMemorySpanExporter()
    provider = application._telemetry.provider if application._telemetry else None
    if provider:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    model = application.resources.model
    original = model.generate

    def generate(messages, **settings):
        task = current_task.get()
        row = {
            "schema": settings.get("json_schema", {}).get("title"),
            "task_id": task.task_id if task else None,
            "messages": messages,
            "settings": settings,
        }
        start = time.perf_counter()
        calls.append(row)
        try:
            response = original(messages, **settings)
            row["response"] = response.text
            row["finish_reason"] = response.finish_reason
            return response
        except Exception as exc:
            row["error"] = type(exc).__name__ + ": " + str(exc)
            raise
        finally:
            row["wall_ms"] = (time.perf_counter() - start) * 1000

    model.generate = generate
    observation = {"calls": calls, "spans": []}
    try:
        yield observation
    finally:
        model.generate = original
        observation["spans"] = [
            {
                "name": s.name,
                "elapsed_ms": (s.end_time - s.start_time) / 1e6,
                "attributes": dict(s.attributes or {}),
            }
            for s in exporter.get_finished_spans()
        ]
        # Each observation owns a processor; clear exporter data after copying.
        exporter.clear()
        exporter.shutdown()


def failure_counts(result, observation):
    spans = observation["spans"]
    planner_calls = [
        c for c in observation.get("calls", []) if c.get("schema") == "PlanProposal"
    ]
    fallback = any(w.code == "planner_fallback" for w in result.warnings)
    if any(s["name"] == "plan" for s in spans):
        # Forced-single control replaces plan warnings. Observe the real planner
        # response as well, including pre-call budget failure and truncation.
        fallback = fallback or not planner_calls
        for call in planner_calls:
            try:
                PlanProposal.model_validate_json(call.get("response", ""))
                if call.get("finish_reason") == "length" or call.get("error"):
                    fallback = True
            except ValueError:
                fallback = True
    return {
        "planner_fallback": int(fallback),
        "worker_repair_attempts": sum(
            s["name"].endswith("repair_findings") for s in spans
        ),
        "synthesis_repair_attempts": sum(
            s["name"] == "repair_synthesis" for s in spans
        ),
        "synthesis_fallback": int(result.delivery == "evidence_fallback"),
        "failed_tasks": sum(c.status == "failed" for c in result.coverage),
        "coverage_gaps": sum(
            bool(c.gaps) or c.admitted_findings > c.used_findings
            for c in result.coverage
        ),
        "final_validation_failure": int(result.final_validation.status == "failed"),
        "operational_error": result.error.code if result.error else None,
    }


def retrieval_scores(ids, labels, k=10):
    """Incomplete judgments: unjudged documents remain explicitly counted."""
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate retrieval IDs")
    positive = {cid for cid, grade in labels.items() if grade > 0}
    ranked = ids[:k]
    dcg = sum(
        (2 ** labels.get(cid, 0) - 1) / math.log2(i + 2) for i, cid in enumerate(ranked)
    )
    ideal = sum(
        (2**grade - 1) / math.log2(i + 2)
        for i, grade in enumerate(sorted(labels.values(), reverse=True)[:k])
    )
    return {
        "recall_at_10": len(positive.intersection(ranked)) / len(positive)
        if positive
        else None,
        "ndcg_at_10_partial_qrels": dcg / ideal if ideal else None,
        "positive_denominator": len(positive),
        "unjudged_at_10": sum(cid not in labels for cid in ranked),
        "note": "Unjudged gains set to zero relative to known qrels; incomplete labels can bias nDCG in either direction.",
    }


def retrieval_ablation(case, retriever):
    text = case.get("retrieval_query", case["question"])
    query = Query(text=text)
    rows = {}
    lists = []
    for name, searcher, top_k in [
        ("keyword", retriever.keyword_searcher, retriever.keyword_top_k),
        ("vector", retriever.vector_searcher, retriever.vector_top_k),
    ]:
        start = time.perf_counter()
        found = searcher.search(query, top_k=top_k)
        lists.append(found)
        rows[name] = {
            "ids": [r.chunk_id for r in found],
            "wall_ms": (time.perf_counter() - start) * 1000,
        }
    start = time.perf_counter()
    merged = reciprocal_rank_fusion(
        *lists, k=retriever.rrf_k, top_n=retriever.merge_top_n
    )
    rows["hybrid"] = {
        "ids": [r.chunk_id for r in merged],
        "wall_ms": (time.perf_counter() - start) * 1000,
        "timing_scope": "fusion only; keyword/vector costs recorded separately",
    }
    start = time.perf_counter()
    chunks = retriever.chunk_store.get_many(rows["hybrid"]["ids"])
    if retriever.reranker is None:
        raise ValueError("Retrieval ablation requires a configured reranker")
    ranked = retriever.reranker.rerank(query, chunks, top_n=10) if chunks else []
    rows["hybrid_reranked"] = {
        "ids": [r.chunk_id for r in ranked],
        "wall_ms": (time.perf_counter() - start) * 1000,
        "timing_scope": "hydration + reranking of same hybrid pool",
    }
    labels = {e["chunk_id"]: e["relevance"] for e in case["evidence"]}
    pool_ids = sorted({cid for r in rows.values() for cid in r["ids"][:10]})
    pool = [
        {
            "chunk_id": c.chunk_id,
            "article": c.title,
            "section": c.section,
            "text": c.text,
            "grade": labels.get(c.chunk_id),
        }
        for c in retriever.chunk_store.get_many(pool_ids)
    ]
    for row in rows.values():
        row["scores"] = retrieval_scores(row["ids"], labels)
    return {
        "question_id": case["id"],
        "query": text,
        "variants": rows,
        "pool": pool,
        "annotation_status": "Incomplete pooled judgments; exact gold hits are diagnostics.",
    }


def validate_run(report):
    if report["format"] != 1 or not report["metadata"]["snapshot_id"]:
        raise ValueError("Invalid report identity")
    for row in report["results"]:
        result = ApplicationResult.model_validate(row["result"])
        if any(
            c.snapshot_id != report["metadata"]["snapshot_id"] for c in result.citations
        ):
            raise ValueError("Report mixes snapshots")
        for citation in result.citations:
            for e in citation.excerpts:
                if hashlib.sha256(e.text.encode()).hexdigest() != e.text_hash:
                    raise ValueError("Report contains altered excerpt")
        if "failures" not in row or "observation" not in row:
            raise ValueError("Missing failure/observation record")
    return report


@contextmanager
def sample_native(endpoint):
    """Sample the matching native server RSS; never stop or modify any process."""
    import threading
    from urllib.parse import urlsplit

    port = str(urlsplit(endpoint).port)
    stop = threading.Event()
    samples = {
        "native_peak_sampled_rss_bytes": None,
        "sample_count": 0,
        "interval_seconds": 0.5,
        "errors": [],
        "scope": "RSS of llama-server matching benchmark endpoint port, sampled not exact peak",
    }

    def collect():
        while not stop.is_set():
            process = subprocess.run(
                ["ps", "-axo", "rss=,command="], capture_output=True, text=True
            )
            if process.returncode:
                samples["errors"] = ["ps unavailable: " + process.stderr.strip()]
                return
            values = []
            for line in process.stdout.splitlines():
                parts = line.strip().split(maxsplit=1)
                if len(parts) == 2 and "llama-server" in parts[1]:
                    args = parts[1].split()
                    if "--port" in args and args[args.index("--port") + 1] == port:
                        values.append(int(parts[0]) * 1024)
            if values:
                samples["native_peak_sampled_rss_bytes"] = max(
                    samples["native_peak_sampled_rss_bytes"] or 0, sum(values)
                )
                samples["sample_count"] += 1
            stop.wait(0.5)

    thread = threading.Thread(target=collect, daemon=True)
    thread.start()
    try:
        yield samples
    finally:
        stop.set()
        thread.join(timeout=5)


def review_packet(observation):
    """Extract exact model inputs/outputs for review, without repeated templates."""
    packets = []
    for call in observation["calls"]:
        inputs = []
        for message in call["messages"]:
            if "INPUT_JSON:\n" in message["content"]:
                raw = message["content"].split("INPUT_JSON:\n", 1)[1]
                value, _ = json.JSONDecoder().raw_decode(raw)
                inputs.append(value)
        try:
            response = json.loads(call.get("response", ""))
        except ValueError:
            response = {"invalid_json": call.get("response", "")}
        packets.append(
            {
                "schema": call["schema"],
                "settings": {
                    key: value
                    for key, value in call.get("settings", {}).items()
                    if key != "json_schema"
                },
                "schema_sha256": hashlib.sha256(
                    json.dumps(
                        call.get("settings", {}).get("json_schema"), sort_keys=True
                    ).encode()
                ).hexdigest(),
                "task_id": call["task_id"],
                "input": inputs,
                "response": response,
                "error": call.get("error"),
                "wall_ms": call["wall_ms"],
                "finish_reason": call.get("finish_reason"),
            }
        )
    return packets


def selected_report(report, case_ids):
    """Local sanitized review artifact. Complete bulk report stays under runs/."""
    rows = []
    for row in report["results"]:
        if row["question_id"] in case_ids:
            row = dict(row)
            observation = row["observation"]
            row["review_packet"] = review_packet(observation)
            row["observation"] = {
                "calls": [],
                "spans": observation["spans"],
                "note": "Call inputs/responses and generation settings projected into review_packet; full schemas/templates identified by hashes.",
            }
            rows.append(row)
    return sanitized(
        {
            **report,
            "results": rows,
            "retrieval": [],
            "projection_source_sha256": digest_file(Path(__file__)),
            "selected_from": {
                "total_results": len(report["results"]),
                "selected_ids": sorted(case_ids),
                "reason": "Fixed development/conversation/error examples; not an accuracy sample",
            },
        }
    )


def validate_semantic_review(review, reports):
    if (
        review["version"] != "semantic-review-v1"
        or review["rubric"] != "history-rubric-v1"
    ):
        raise ValueError("Unknown review rubric/version")
    if (
        review["reviewer"] == "codex-agent"
        and review["independent_human_reviewers"] != 0
    ):
        raise ValueError("Agent review cannot claim human reviewers")
    runs = {
        row["result"]["run_id"]: row for report in reports for row in report["results"]
    }
    for judgment in review["judgments"]:
        row = runs.get(judgment["run_id"])
        if row is None or row["question_id"] != judgment["question_id"]:
            raise ValueError("Review references an unknown run/case")
        expected = hashlib.sha256(
            json.dumps(row["result"], sort_keys=True).encode()
        ).hexdigest()
        if expected != judgment["result_sha256"]:
            raise ValueError("Reviewed result hash mismatch")
        packets = row.get("review_packet") or review_packet(row["observation"])
        finding_ids = {
            f["finding_id"]
            for packet in packets
            if packet["schema"] == "AnswerDraft"
            for data in packet["input"]
            for f in data["findings"]
        }
        for label in judgment.get("worker", []):
            if label["finding_id"] not in finding_ids or label["label"] not in {
                "supported",
                "partial",
                "unsupported",
            }:
                raise ValueError(
                    "Review references an unadmitted finding/invalid support label"
                )
        for label in judgment.get("units", []):
            units = {f"u{i}" for i in range(1, len(row["result"]["units"]) + 1)}
            if label["unit_id"] not in units or label["label"] not in {
                "supported",
                "partial",
                "unsupported",
            }:
                raise ValueError("Invalid reviewed final unit")
        if "rewrite" in judgment and judgment["rewrite"]["label"] not in {
            "preserved",
            "drift",
            "unresolved_appropriate",
            "unresolved_incorrect",
        }:
            raise ValueError("Invalid rewrite label")
    return review


def validate_pooled_review(review):
    if (
        review["version"] != "pooled-relevance-v1"
        or review["independent_human_reviewers"] != 0
    ):
        raise ValueError("Invalid provisional pooled review")
    labels = {p["chunk_id"]: p["grade"] for p in review["pool"]}
    if len(labels) != len(review["pool"]) or any(
        g not in {0, 1, 2} for g in labels.values()
    ):
        raise ValueError("Invalid or duplicate pool labels")
    for variant, ids in review["rankings"].items():
        if not set(ids[:10]) <= labels.keys():
            raise ValueError("Pooled top-10 judgment missing")
        if retrieval_scores(ids, labels) != review["scores"][variant]:
            raise ValueError("Pooled scores do not reproduce")
    return review
