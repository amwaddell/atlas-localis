"""
Reranker adapters.

A reranker takes a query and a set of candidate chunks, scores each
(query, chunk_text) pair with a cross-encoder, and returns the
candidates sorted by relevance.

Cross-encoders are much more accurate than bi-encoders for scoring
individual pairs but are too slow to run over the entire index — that's
why we rerank only the small candidate set produced by hybrid retrieval.

Satisfies the ``Reranker`` protocol from ``workbench.core.interfaces``.

Usage:
    from workbench.retrieval.rerankers import CrossEncoderReranker

    reranker = CrossEncoderReranker(model_name="BAAI/bge-reranker-v2-m3")
    results  = reranker.rerank(query, candidates, top_n=5)
"""

from __future__ import annotations

import time

from workbench.core.offline import cached_model_path
from workbench.core.types import Chunk, Query, RerankResult
from workbench.models.local_inference import inference, local_device
from workbench.observability.tracing import add_span_attributes, start_span


class CrossEncoderReranker:
    """
    Reranker backed by a sentence-transformers ``CrossEncoder`` model.

    Args:
        model_name: HuggingFace model ID (e.g. ``"BAAI/bge-reranker-v2-m3"``).
        device: ``"cpu"``, ``"mps"`` or ``"auto"``; unavailable Metal uses CPU.
        model_path: Optional asset path, separate from semantic model_name.
        max_length: Bounded pair token window (default 8192).
        batch_size: Batch size for ``model.predict``.
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        device: str | None = None,
        batch_size: int = 1,
        model_path: str | None = None,
        max_length: int = 8192,
    ) -> None:
        if not 1 <= batch_size <= 32 or not 1 <= max_length <= 8192:
            raise ValueError("Reranker batch_size must be 1..32 and max_length 1..8192")
        self.model_path = cached_model_path(model_path or model_name)
        self.device = local_device(device)
        from sentence_transformers import CrossEncoder

        self.model_name = model_name
        self.batch_size = batch_size
        try:
            self._model = CrossEncoder(
                self.model_path,
                device=self.device,
                max_length=max_length,
                local_files_only=True,
                trust_remote_code=False,
            )
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"Cannot load local model assets: {model_name}. "
                "Verify/provision the complete cache during explicit online setup/update."
            ) from exc

    def rerank(
        self,
        query: Query,
        candidates: list[Chunk],
        top_n: int = 5,
    ) -> list[RerankResult]:
        """
        Rerank candidate chunks by cross-encoder relevance.

        Args:
            query: Query object.
            candidates: List of candidate Chunk objects.
            top_n: Number of top results to return.

        Returns:
            List of RerankResult sorted by rerank_score descending.
        """
        if len(candidates) > 50:
            raise ValueError("Reranker accepts at most 50 candidates")
        if top_n < 1:
            raise ValueError("top_n must be positive")
        if not candidates:
            return []

        with start_span(
            "rerank.cross_encoder",
            attributes={
                "model_name": self.model_name,
                "query_text": query.text[:200],
                "input_count": len(candidates),
                "top_n": top_n,
            },
        ):
            t0 = time.time()

            # Build (query, passage) pairs
            pairs = [(query.text, chunk.text) for chunk in candidates]

            # Score all pairs
            with inference():
                scores = self._model.predict(
                    pairs,
                    batch_size=self.batch_size,
                    show_progress_bar=False,
                )

            # Pair each candidate with its score
            scored = list(zip(candidates, scores, strict=True))

            # Sort by score descending
            scored.sort(key=lambda x: float(x[1]), reverse=True)

            # Build results (take top_n)
            results: list[RerankResult] = []
            for chunk, score in scored[:top_n]:
                results.append(
                    RerankResult(
                        chunk_id=chunk.chunk_id,
                        rerank_score=float(score),
                        original_score=None,
                    )
                )

            duration_ms = (time.time() - t0) * 1000

            add_span_attributes(
                {
                    "output_count": len(results),
                    "duration_ms": round(duration_ms, 1),
                    "top_score": round(float(results[0].rerank_score), 4)
                    if results
                    else 0.0,
                }
            )

            return results

    def __repr__(self) -> str:
        return f"CrossEncoderReranker(model={self.model_name!r})"
