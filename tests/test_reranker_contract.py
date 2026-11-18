"""Adapter ordering and score/pair transport without loading model weights."""

from unittest.mock import MagicMock

import pytest

from workbench.core.types import Query
from workbench.retrieval.rerankers import CrossEncoderReranker


def reranker():
    adapter = CrossEncoderReranker.__new__(CrossEncoderReranker)
    adapter.model_name = "fixture"
    adapter.batch_size = 1
    adapter._model = MagicMock()
    return adapter


def test_scores_stay_paired_with_chunks_and_top_n_is_applied():
    from tests.test_reranker_small import CANDIDATES

    adapter = reranker()
    adapter._model.predict.return_value = [-2.0, 0.5, 4.0]
    result = adapter.rerank(Query(text="fixture query"), CANDIDATES, top_n=2)
    assert [(r.chunk_id, r.rerank_score) for r in result] == [
        (CANDIDATES[2].chunk_id, 4.0),
        (CANDIDATES[1].chunk_id, 0.5),
    ]
    assert adapter._model.predict.call_args.args[0] == [
        ("fixture query", c.text) for c in CANDIDATES
    ]
    assert adapter._model.predict.call_args.kwargs["batch_size"] == 1


def test_wrong_score_count_is_a_failure_and_empty_never_calls_model():
    from tests.test_reranker_small import CANDIDATES

    adapter = reranker()
    assert adapter.rerank(Query(text="x"), [], top_n=5) == []
    adapter._model.predict.assert_not_called()
    adapter._model.predict.return_value = [1.0]
    with pytest.raises(ValueError):
        adapter.rerank(Query(text="x"), CANDIDATES)
