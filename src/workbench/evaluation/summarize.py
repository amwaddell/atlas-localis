"""Reproduce aggregate measurements without loading models or changing judgments."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path

from workbench.evaluation.benchmark import (
    failure_counts,
    retrieval_scores,
    sanitized,
    validate_run,
    validate_semantic_review,
)
from workbench.evaluation.suite import load_suite
from workbench.graphs.research_models import ApplicationResult


def distribution(values):
    return {
        "n": len(values),
        "median": statistics.median(values) if values else None,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def experiment_config(report):
    config = copy.deepcopy(report["effective_config"])
    # Owned servers and per-experiment report directories are observational.
    config["model"].pop("base_url", None)
    config.pop("runs", None)
    return config


def baseline_rows(reports, suite):
    rows = []
    ids = set()
    run_ids = set()
    runtime = None
    for report in reports:
        validate_run(report)
        metadata = report["metadata"]
        if metadata["snapshot_id"] != suite["snapshot_id"]:
            raise ValueError("Baseline snapshot mismatch")
        if report["selection"]["controls"] != {
            "forced_single_task": False,
            "rerank": None,
        }:
            raise ValueError("Ablation cannot enter baseline denominator")
        identity = {
            "config": experiment_config(report),
            "models": metadata["models_manifest"],
            "cached_revisions": metadata["cached_revisions"],
            "native_manifest": metadata.get("native_manifest"),
            "source": {
                p: h
                for p, h in metadata["source_sha256"].items()
                if p.startswith("src/workbench/") and "/evaluation/" not in p
            },
        }
        if runtime is not None and runtime != identity:
            raise ValueError("Baseline runtime/model identity changed")
        runtime = identity
        for row in report["results"]:
            if (
                row["question_id"] in ids
                or row["result"]["run_id"] in run_ids
                or row["repeat"] != 1
            ):
                raise ValueError("Duplicate or repeated baseline case")
            ids.add(row["question_id"])
            run_ids.add(row["result"]["run_id"])
            rows.append(row)
    expected = {c["id"] for c in suite["cases"]}
    if ids != expected:
        raise ValueError(
            f"Incomplete baseline: missing {sorted(expected - ids)}, extra {sorted(ids - expected)}"
        )
    return rows


def case_measurement(row):
    result = ApplicationResult.model_validate(row["result"])
    spans = row["observation"]["spans"]
    return {
        "question_id": row["question_id"],
        "run_id": result.run_id,
        "outcome": result.outcome,
        "delivery": result.delivery,
        "latency_ms": result.diagnostics.elapsed_ms,
        "planner_ms": sum(s["elapsed_ms"] for s in spans if s["name"] == "plan"),
        "tasks": len(result.coverage),
        "units": len(result.units),
        "citation_count_diagnostic": len(result.citations),
        "tokens_in": result.diagnostics.tokens_in,
        "tokens_out": result.diagnostics.tokens_out,
        "failures": failure_counts(result, row["observation"]),
        "error": row["error"],
        "gaps": result.gaps,
        "resources": row["resources"],
    }


def summarize(baselines, suite, retrieval=None, controls=(), review=None):
    rows = baseline_rows(baselines, suite)
    measurements = [case_measurement(r) for r in rows]
    by_id = {r["question_id"]: r for r in measurements}
    failure_names = [
        "planner_fallback",
        "worker_repair_attempts",
        "synthesis_repair_attempts",
        "synthesis_fallback",
        "failed_tasks",
        "coverage_gaps",
        "final_validation_failure",
    ]
    simple_ids = {c["id"] for c in suite["cases"] if c["category"] == "single"}
    simple = [r for r in measurements if r["question_id"] in simple_ids]
    summary = {
        "version": "stage5-measurements-v1",
        "suite_version": suite["version"],
        "snapshot_id": suite["snapshot_id"],
        "planned_case_count": 24,
        "completed_unique_cases": len(measurements),
        "denominators": {
            "completed_cases": len(rows),
            "planning_nodes": sum(
                s["name"] == "plan" for r in rows for s in r["observation"]["spans"]
            ),
            "native_planner_calls": sum(
                c.get("schema") == "PlanProposal"
                for r in rows
                for c in r["observation"]["calls"]
            ),
            "worker_terminal_records": sum(len(r["result"]["coverage"]) for r in rows),
            "worker_findings_calls_including_repair": sum(
                c.get("schema") == "Findings"
                for r in rows
                for c in r["observation"]["calls"]
            ),
            "synthesis_calls_including_repair": sum(
                c.get("schema") == "AnswerDraft"
                for r in rows
                for c in r["observation"]["calls"]
            ),
            "runs_with_synthesis_calls": sum(
                any(c.get("schema") == "AnswerDraft" for c in r["observation"]["calls"])
                for r in rows
            ),
            "displayed_citations": sum(len(r["result"]["citations"]) for r in rows),
            "final_units": sum(len(r["result"]["units"]) for r in rows),
        },
        "outcomes": dict(Counter(r["outcome"] for r in measurements)),
        "deliveries": dict(Counter(r["delivery"] for r in measurements)),
        "operational_errors": dict(
            Counter(r["error"]["code"] for r in measurements if r["error"])
        ),
        "failure_totals": {
            name: sum(r["failures"][name] for r in measurements)
            for name in failure_names
        },
        "latency_ms": distribution([r["latency_ms"] for r in measurements]),
        "simple_planning": {
            "planner_ms": distribution([r["planner_ms"] for r in simple]),
            "share_of_end_to_end": distribution(
                [r["planner_ms"] / r["latency_ms"] for r in simple]
            ),
            "cases": [
                {k: r[k] for k in ["question_id", "planner_ms", "latency_ms", "tasks"]}
                for r in simple
            ],
            "interpretation": "Incurred planning work, not a no-planner full-graph counterfactual.",
        },
        "cases": measurements,
        "sessions": [
            {
                "metadata": r["metadata"],
                "effective_config": r.get("effective_config"),
                "selection": r["selection"],
                "native_resources": r.get("native_resources"),
                "resources": r.get("resources"),
                "model_load_wall_ms": r.get("model_load_wall_ms"),
                "native_startup_wall_ms": r.get("native_startup_wall_ms"),
                "errors": r["errors"],
            }
            for r in baselines
        ],
        "comparisons": [],
        "limitations": [
            "Baseline spans two sessions after battery pause; no failed completed case was discarded or rerun.",
            "No independent human historical-accuracy score; citation counts are diagnostics.",
            "Inclusive phase spans overlap; no p95 or minimum-memory claim.",
            "Incidental desktop/test load and native prompt-cache state were not controlled.",
            "Evaluation observation/reporting evolved; each session retains exact source hashes. Runtime and prompts were unchanged.",
        ],
    }
    for control in controls:
        validate_run(control)
        config = experiment_config(control)
        baseline_config = experiment_config(baselines[0])
        rerank = control["selection"]["controls"]["rerank"]
        if rerank is not None:
            baseline_config["reranking"]["enabled"] = rerank

        def runtime_sources(report):
            return {
                p: h
                for p, h in report["metadata"]["source_sha256"].items()
                if p.startswith("src/workbench/") and "/evaluation/" not in p
            }

        if (
            config != baseline_config
            or runtime_sources(control) != runtime_sources(baselines[0])
            or control["metadata"].get("native_manifest")
            != baselines[0]["metadata"].get("native_manifest")
            or control["metadata"]["cached_revisions"]
            != baselines[0]["metadata"]["cached_revisions"]
            or control["metadata"]["snapshot_id"] != suite["snapshot_id"]
            or control["metadata"]["models_manifest"]
            != baselines[0]["metadata"]["models_manifest"]
        ):
            raise ValueError(
                "Control changes more than the declared evaluation variable"
            )
        comparisons = []
        for row in control["results"]:
            measured = case_measurement(row)
            original = by_id[measured["question_id"]]
            comparisons.append(
                {
                    "repeat": row["repeat"],
                    "question_id": measured["question_id"],
                    "baseline": original,
                    "control": measured,
                    "latency_delta_ms": measured["latency_ms"] - original["latency_ms"],
                }
            )
        summary["comparisons"].append(
            {
                "selection": control["selection"],
                "metadata": control["metadata"],
                "rows": comparisons,
                "errors": control["errors"],
                "note": "Small paired diagnostic; resource warmup/cache and generation can vary.",
            }
        )
    if retrieval is not None:
        if retrieval["metadata"]["snapshot_id"] != suite["snapshot_id"]:
            raise ValueError("Retrieval snapshot mismatch")
        case_ids = {r["question_id"] for r in retrieval["retrieval"]}
        if len(retrieval["retrieval"]) != 24 or case_ids != set(by_id):
            raise ValueError("Incomplete retrieval comparison")
        questions = {c["id"]: c for c in suite["cases"]}
        variants = {}
        for variant in ["keyword", "vector", "hybrid", "hybrid_reranked"]:
            scored = []
            for row in retrieval["retrieval"]:
                labels = {
                    e["chunk_id"]: e["relevance"]
                    for e in questions[row["question_id"]]["evidence"]
                }
                scored.append(
                    {
                        "question_id": row["question_id"],
                        **retrieval_scores(row["variants"][variant]["ids"], labels),
                    }
                )
            eligible = [r for r in scored if r["positive_denominator"]]
            variants[variant] = {
                "cases": 24,
                "positive_case_denominator": len(eligible),
                "known_evidence_recall_mean": statistics.mean(
                    r["recall_at_10"] for r in eligible
                ),
                "unjudged_top10_total": sum(r["unjudged_at_10"] for r in scored),
                "wall_ms": distribution(
                    [r["variants"][variant]["wall_ms"] for r in retrieval["retrieval"]]
                ),
                "per_case": scored,
            }
        summary["retrieval"] = {
            "metadata": retrieval["metadata"],
            "variants": variants,
            "limitations": [
                "Incomplete title-selected qrels: known-evidence diagnostics, not whole-suite relevance quality.",
                "Raw latest question used, including conversational pronouns.",
                "Hybrid timing is fusion only; reranked timing is hydration/scoring only.",
            ],
        }
    if review is not None:
        validate_semantic_review(review, baselines)
        conversation_ids = {
            c["id"] for c in suite["cases"] if c.get("history") or c.get("summary")
        }
        summary["semantic_review"] = {
            "reviewer": review["reviewer"],
            "independent_human_reviewers": review["independent_human_reviewers"],
            "reviewed_case_count": len(review["judgments"]),
            "conversation_rewrite": dict(
                Counter(
                    j["rewrite"]["label"]
                    for j in review["judgments"]
                    if "rewrite" in j and j["question_id"] in conversation_ids
                )
            ),
            "worker_support": dict(
                Counter(
                    label["label"]
                    for j in review["judgments"]
                    for label in j.get("worker", [])
                )
            ),
            "unit_faithfulness": dict(
                Counter(
                    label["label"]
                    for j in review["judgments"]
                    for label in j.get("units", [])
                )
            ),
            "rewrite": dict(
                Counter(
                    j["rewrite"]["label"] for j in review["judgments"] if "rewrite" in j
                )
            ),
        }
    return sanitized(summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", action="append", required=True, type=Path)
    parser.add_argument("--retrieval", type=Path)
    parser.add_argument("--control", action="append", default=[], type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    def read(path):
        return json.loads(path.read_text())

    result = summarize(
        [read(p) for p in args.baseline],
        load_suite(),
        read(args.retrieval) if args.retrieval else None,
        [read(p) for p in args.control],
        read(args.review) if args.review else None,
    )
    result["aggregation_source_sha256"] = hashlib.sha256(
        Path(__file__).read_bytes()
    ).hexdigest()
    result["review_source_sha256"] = (
        hashlib.sha256(args.review.read_bytes()).hexdigest() if args.review else None
    )
    result["source_reports"] = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [
            *args.baseline,
            *args.control,
            *([args.retrieval] if args.retrieval else []),
        ]
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
