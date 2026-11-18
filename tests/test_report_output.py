"""Canonical reports preserve evidence, errors, partial coverage and identity."""

import json

import pytest
from tests.test_evidence_worker import Model, draft
from tests.test_supervisor_orchestration import Retriever, answer, graph, proposal, run

from workbench.evaluation.measures import compute_all_measures
from workbench.evaluation.reports import (
    generate_report,
    generate_summary_report,
    save_report,
)
from workbench.graphs.research_models import ApplicationResult


@pytest.mark.parametrize("failed", [(), ("Question 2",), ("Question 1", "Question 2")])
def test_report_preserves_terminal_contract(failed, tmp_path):
    model = Model(proposal(2), draft(), draft(), answer("F1", "F2"))
    result = run(graph(model, Retriever(failed=failed)))
    metrics = compute_all_measures(result)
    before = result.model_dump_json()
    report = generate_report(
        result, metrics, question_meta={"question": "Rome?", "topic": "History"}
    )
    for expected in (
        "## Question",
        "Rome?",
        "History",
        "## Answer",
        "## Citations",
        "## Coverage and gaps",
        "## Validation",
        "## Metrics",
        "## Fingerprints",
        result.outcome,
        result.delivery,
        result.answer_text,
    ):
        assert expected in report
    if result.citations:
        assert result.citations[0].snapshot_id in report
        assert result.citations[0].excerpts[0].text in report
    if result.error:
        assert result.error.code in report
    assert save_report(report, tmp_path / "nested/report.md").read_text() == report
    restored = ApplicationResult.model_validate(json.loads(before))
    assert restored == result and result.model_dump_json() == before
    assert metrics["citation_count"] == len(result.citations)


def test_summary_contains_failures_and_empty_results():
    summary = generate_summary_report(
        [
            {"question": "Q|?", "metrics": {"outcome": "partial", "citation_count": 2}},
            {"question": "Failed", "error": "failure"},
        ],
        "run",
    )
    assert "partial" in summary and "failed" in summary and "Yes" in summary
    assert "Q\\|?" in summary
    assert "Questions evaluated: 0" in generate_summary_report([])
