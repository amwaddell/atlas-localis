"""Successors to local-label, None-state and partial-result regressions.

Planning/synthesis contracts replace prose substitution and decomposer wrappers.
"""

import pytest
from tests.test_evidence_worker import Model, draft
from tests.test_supervisor_orchestration import Retriever, answer, graph, proposal, run

from workbench.graphs.planning import conversation_input
from workbench.systems.presentation import project_result


def test_local_evidence_labels_never_become_persistent_ids():
    result = run(graph())
    assert result.answer_text == "Rome changed in 27 BC. [1]"
    assert result.units[0].finding_ids == ("t1.f1",)
    assert result.citations[0].chunk_id == "a"
    assert result.citations[0].snapshot_id == "snapshot-v1"
    assert "REF_" not in result.model_dump_json()


def test_none_history_and_summary_use_fresh_initialized_channels():
    result = graph().invoke(conversation_input("Rome?", history=None, summary=None))
    assert result.outcome == "complete"
    assert result.gaps == () and result.error is None


@pytest.mark.parametrize("failed", [("Question 1",), ("Question 2",)])
def test_one_survivor_preserves_missing_task_and_synthesizes(failed):
    model = Model(proposal(2), draft(), answer())
    result = run(graph(model, Retriever(failed=failed)))
    assert result.outcome == "partial"
    assert len(result.coverage) == 2
    assert [c.status for c in result.coverage].count("failed") == 1
    assert len(model.calls) == 3
    assert result.gaps
    assert result.citations


@pytest.mark.parametrize("bad", ["", "REF_A1", "[a]", "999", "F9"])
def test_unknown_or_raw_citation_labels_do_not_become_answers(bad):
    result = run(graph(Model(proposal(), draft(), answer(bad), answer(bad))))
    assert result.delivery == "evidence_fallback"
    assert result.answer_text == "Rome changed in 27 BC. [1]"
    assert result.final_validation.status == "passed"


def test_pure_projection_does_not_mutate_numbering_or_partial_coverage():
    result = run(
        graph(Model(proposal(2), draft(), answer()), Retriever(failed=("Question 2",)))
    )
    before = result.model_dump_json()
    first, second = project_result(result), project_result(result)
    first["citations"][0]["excerpts"][0]["text"] = "Changed display copy"
    assert second["citations"][0]["excerpts"][0]["text"] != "Changed display copy"
    assert result.model_dump_json() == before
    assert second["coverage"][1]["status"] == "failed"
