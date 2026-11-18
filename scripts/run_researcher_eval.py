#!/usr/bin/env python3
"""Bounded offline canonical evaluation. No acquisition, cloud judge or tuning."""

from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

from workbench.core.config import load_config
from workbench.evaluation.benchmark import (
    failure_counts,
    memory,
    observe,
    provenance,
    retrieval_ablation,
    sample_native,
    sanitized,
    validate_pooled_review,
    validate_run,
    validate_semantic_review,
)
from workbench.evaluation.measures import compute_all_measures
from workbench.evaluation.reports import generate_report, save_report
from workbench.evaluation.suite import DATA, load_suite, validate_corpus
from workbench.graphs.planning import conversation_input
from workbench.models.native_process import native_smoke_server
from workbench.systems.runner import EvaluationControls, create_application


def run_single_question(question_data, application):
    value = conversation_input(
        question=question_data["question"],
        history=question_data.get("history") or (),
        summary=question_data.get("summary") or "",
    )
    result = application.invoke(value)
    return {
        "question_id": question_data["id"],
        "question": value.question,
        "question_meta": question_data,
        "result": result,
        "metrics": compute_all_measures(result),
        "error": result.error.model_dump() if result.error else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path)
    parser.add_argument(
        "--split", choices=["development", "held_out", "all"], default="development"
    )
    parser.add_argument("--questions", type=int)
    parser.add_argument("--question-id", help="Comma-separated frozen case IDs")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--forced-single-task", action="store_true", help="Still pays planner cost"
    )
    parser.add_argument("--rerank", choices=["on", "off"])
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--validate-report", type=Path, action="append", default=[])
    parser.add_argument(
        "--owned-native",
        action="store_true",
        help="Start/reap only our pinned native process",
    )
    args = parser.parse_args()
    suite = load_suite(args.suite)
    rubric = json.loads((DATA / "history-rubric-v1.json").read_text())
    if rubric["version"] != suite["rubric"] or not rubric["frozen_before_runs"]:
        parser.error("Rubric identity mismatch")
    published = Path(__file__).resolve().parents[1] / "docs/dev/evidence"
    paths = list(published.glob("selected-*.json")) + args.validate_report
    reports = [validate_run(json.loads(path.read_text())) for path in paths]
    review = published / "semantic-review-v1.json"
    if reports and review.exists():
        validate_semantic_review(json.loads(review.read_text()), reports)
    pool_review = published / "pooled-relevance-v1.json"
    if pool_review.exists():
        validate_pooled_review(json.loads(pool_review.read_text()))
    if args.validate_only:
        print(
            f"Validated {len(suite['cases'])} frozen cases, rubric and {len(reports)} reports"
        )
        return 0
    if args.repetitions not in range(1, 4) or (
        args.questions is not None and args.questions < 1
    ):
        parser.error("Use 1..3 repetitions and a positive case count")
    cases = [
        c for c in suite["cases"] if args.split == "all" or c["split"] == args.split
    ]
    if args.question_id:
        ids = args.question_id.split(",")
        cases = [c for c in cases if c["id"] in ids]
        if set(ids) != {c["id"] for c in cases}:
            parser.error("Unknown ID or ID outside selected split")
    if args.questions:
        cases = cases[: args.questions]
    config = load_config(config_path=args.config)
    # Local SDK export captures phases; no Phoenix viewer/export endpoint needed.
    config.observability.update(enable_tracing=True, enable_metrics=False)
    config.observability.pop("phoenix_export_endpoint", None)
    directory = args.output or Path("runs/reports") / datetime.now(UTC).strftime(
        "stage5-%Y%m%dT%H%M%SZ"
    )
    directory.mkdir(parents=True, exist_ok=False)
    config.runs.update(output_dir=str(directory), logs_dir=str(directory / "logs"))
    controls = EvaluationControls(
        args.forced_single_task, None if args.rerank is None else args.rerank == "on"
    )
    native = (
        native_smoke_server(
            Path("data/models/Ministral-3-8B-Instruct-2512-Q4_K_M.gguf"),
            directory / "native.log",
        )
        if args.owned_native
        else nullcontext(config.model["base_url"])
    )
    report = {
        "format": 1,
        "metadata": provenance(config, suite, sys.argv),
        "selection": {
            "split": args.split,
            "case_ids": [c["id"] for c in cases],
            "repetitions": args.repetitions,
            "controls": controls.__dict__,
        },
        "results": [],
        "retrieval": [],
        "errors": [],
    }

    def checkpoint():
        output = directory / "report.json"
        temporary = output.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(sanitized(report), ensure_ascii=False, indent=2) + "\n"
        )
        temporary.replace(output)

    try:
        native_started = time.perf_counter()
        with native as endpoint, sample_native(endpoint) as native_resources:
            report["native_startup_wall_ms"] = (
                (time.perf_counter() - native_started) * 1000
                if args.owned_native
                else None
            )
            report["native_resources"] = native_resources
            config.model["base_url"] = endpoint
            start = time.perf_counter()
            with create_application(config=config, evaluation=controls) as app:
                report["model_load_wall_ms"] = (time.perf_counter() - start) * 1000
                report["checked_excerpt_count"] = validate_corpus(
                    suite,
                    app.resources.retriever.chunk_store,
                    app.resources.snapshot_id,
                )
                report["effective_config"] = app.config.model_dump()
                report["fingerprints"] = [f.model_dump() for f in app.fingerprints]
                checkpoint()
                for repeat in range(args.repetitions):
                    for case in cases:
                        if args.retrieval_only:
                            row = retrieval_ablation(case, app.resources.retriever)
                            row["repeat"] = repeat + 1
                            report["retrieval"].append(row)
                        else:
                            with observe(app) as observation:
                                row = run_single_question(case, app)
                            result = row["result"]
                            row.update(
                                observation=observation,
                                failures=failure_counts(result, observation),
                                resources=memory(),
                                repeat=repeat + 1,
                            )
                            save_report(
                                generate_report(
                                    result, row["metrics"], question_meta=case
                                ),
                                directory / f"{case['id']}-{repeat + 1}.md",
                            )
                            row["result"] = result.model_dump(mode="json")
                            report["results"].append(row)
                        report["resources"] = memory()
                        checkpoint()
                        print(f"{case['id']} repeat {repeat + 1} recorded", flush=True)
        validate_run(report)
    except Exception as exc:
        report["errors"].append({"type": type(exc).__name__, "message": str(exc)})
        checkpoint()
        print(f"Evaluation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    checkpoint()
    print(directory)
    return int(any(row["error"] for row in report["results"]))


if __name__ == "__main__":
    raise SystemExit(main())
