"""Frozen split, exact excerpt, incomplete-label and failure accounting contracts."""

import copy
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from workbench.evaluation.benchmark import (
    failure_counts,
    retrieval_scores,
    validate_run,
)
from workbench.evaluation.datasets import load_history_questions
from workbench.evaluation.suite import load_suite, validate_corpus, validate_suite
from workbench.graphs.research_models import ConversationInput


def test_seed_is_preserved_and_suite_split_is_frozen():
    assert len(load_history_questions()) == 20
    suite = load_suite()
    assert len(suite["cases"]) == 24
    assert sum(c["split"] == "held_out" for c in suite["cases"]) == 16
    assert sum(bool(c.get("history") or c.get("summary")) for c in suite["cases"]) == 4


@pytest.mark.parametrize(
    "fault", ["duplicate", "split", "hash", "offset", "snapshot", "no_evidence"]
)
def test_invalid_suite_cannot_be_scored(fault):
    suite = load_suite()
    case = suite["cases"][0]
    if fault == "duplicate":
        suite["cases"][1]["id"] = case["id"]
    elif fault == "split":
        case["split"] = "held_out"
    elif fault == "hash":
        case["evidence"][0]["excerpt"] += "injected"
    elif fault == "offset":
        case["evidence"][0]["end"] += 1
    elif fault == "snapshot":
        case["snapshot_id"] = "other"
    else:
        case["evidence"] = []
    with pytest.raises(ValueError):
        validate_suite(suite)


def test_corpus_mismatch_and_altered_source_are_rejected():
    suite = load_suite()
    with pytest.raises(ValueError, match="snapshot"):
        validate_corpus(suite, None, "other")
    with pytest.raises(ValueError, match="no longer resolves"):
        validate_corpus(
            suite,
            SimpleNamespace(get=lambda cid: SimpleNamespace(text="changed")),
            suite["snapshot_id"],
        )


def test_retrieval_denominators_keep_unknown_and_no_positive_distinct():
    row = retrieval_scores(["unknown", "a"], {"a": 2, "b": 1})
    assert row["recall_at_10"] == 0.5
    assert row["unjudged_at_10"] == 1
    assert row["positive_denominator"] == 2
    assert 0 < row["ndcg_at_10_partial_qrels"] < 1
    assert retrieval_scores(["x"], {})["recall_at_10"] is None
    with pytest.raises(ValueError, match="Duplicate"):
        retrieval_scores(["a", "a"], {"a": 2})


def test_fallback_repair_and_coverage_are_separate():
    from tests.test_supervisor_orchestration import graph

    result = graph().invoke(ConversationInput(question="Rome?"))
    observed = {
        "spans": [{"name": "researcher.repair_findings"}, {"name": "repair_synthesis"}]
    }
    counts = failure_counts(result, observed)
    assert counts["worker_repair_attempts"] == counts["synthesis_repair_attempts"] == 1
    assert counts["planner_fallback"] == counts["synthesis_fallback"] == 0


def test_report_round_trip_rejects_tampered_excerpts_and_snapshot():
    from tests.test_supervisor_orchestration import graph

    result = graph().invoke(ConversationInput(question="Rome?"))
    report = {
        "format": 1,
        "metadata": {"snapshot_id": result.citations[0].snapshot_id},
        "results": [
            {
                "result": result.model_dump(mode="json"),
                "failures": {},
                "observation": {},
            }
        ],
    }
    validate_run(json.loads(json.dumps(report)))
    altered = copy.deepcopy(report)
    altered["metadata"]["snapshot_id"] = "other"
    with pytest.raises(ValueError, match="mixes snapshots"):
        validate_run(altered)
    report["results"][0]["result"]["citations"][0]["excerpts"][0]["text"] += "made up"
    with pytest.raises(ValueError):
        validate_run(report)


def test_validation_cli_needs_no_assets_or_server(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/run_researcher_eval.py"),
            "--validate-only",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "24 frozen cases" in result.stdout


def test_agent_review_cannot_fabricate_human_count_or_unknown_runs():
    from workbench.evaluation.benchmark import validate_semantic_review

    review = {
        "version": "semantic-review-v1",
        "rubric": "history-rubric-v1",
        "reviewer": "codex-agent",
        "independent_human_reviewers": 1,
        "judgments": [],
    }
    with pytest.raises(ValueError, match="human"):
        validate_semantic_review(review, [])
    review["independent_human_reviewers"] = 0
    review["judgments"] = [{"run_id": "not-a-run", "question_id": "f01"}]
    with pytest.raises(ValueError, match="unknown"):
        validate_semantic_review(review, [])


def test_forced_single_planner_fallback_cannot_be_hidden_by_replaced_warnings():
    from tests.test_supervisor_orchestration import graph

    result = graph().invoke(ConversationInput(question="Rome?"))
    counts = failure_counts(
        result,
        {
            "spans": [{"name": "plan"}],
            "calls": [{"schema": "PlanProposal", "response": "invalid"}],
        },
    )
    assert counts["planner_fallback"] == 1


def test_observer_captures_actual_schema_calls_without_changing_result():
    from tests.test_supervisor_orchestration import graph

    from workbench.evaluation.benchmark import observe, review_packet

    app = graph()
    original = app.resources.model.generate
    with observe(app) as observation:
        result = app.invoke(ConversationInput(question="Rome?"))
    assert app.resources.model.generate == original
    packets = review_packet(observation)
    assert [p["schema"] for p in packets] == ["PlanProposal", "Findings", "AnswerDraft"]
    assert packets[1]["input"][0]["evidence"]
    assert result.citations


def test_published_pool_labels_and_scores_cannot_be_tampered():
    from workbench.evaluation.benchmark import validate_pooled_review

    path = (
        Path(__file__).resolve().parents[1]
        / "docs/dev/evidence/pooled-relevance-v1.json"
    )
    review = json.loads(path.read_text())
    validate_pooled_review(review)
    review["scores"]["keyword"]["recall_at_10"] = 1.0
    with pytest.raises(ValueError, match="reproduce"):
        validate_pooled_review(review)


def test_summary_rejects_duplicate_cases_and_mixed_snapshot():
    from tests.test_supervisor_orchestration import graph

    from workbench.evaluation.summarize import baseline_rows

    result = graph().invoke(ConversationInput(question="Rome?"))
    snapshot = result.citations[0].snapshot_id
    row = {
        "question_id": "f01",
        "repeat": 1,
        "result": result.model_dump(mode="json"),
        "failures": {},
        "observation": {},
    }
    report = {
        "format": 1,
        "metadata": {
            "snapshot_id": snapshot,
            "models_manifest": {},
            "cached_revisions": {},
            "source_sha256": {},
        },
        "effective_config": {"model": {"base_url": "http://127.0.0.1:8080"}},
        "selection": {"controls": {"forced_single_task": False, "rerank": None}},
        "results": [row],
    }
    suite = {"snapshot_id": snapshot, "cases": [{"id": "f01"}]}
    assert baseline_rows([report], suite) == [row]
    with pytest.raises(ValueError, match="Duplicate"):
        baseline_rows([report, report], suite)
    suite["snapshot_id"] = "other"
    with pytest.raises(ValueError, match="snapshot"):
        baseline_rows([report], suite)
