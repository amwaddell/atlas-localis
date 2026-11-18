"""
Context builder: select and pack retrieved chunks into a context string.

Rules applied:
    1.  Remove duplicate chunk_ids.
    2.  Prefer diversity — cap chunks per document so one article doesn't
        dominate the context window.
    3.  Pack until max token budget is reached.

Satisfies the ``ContextBuilder`` protocol from
``workbench.core.interfaces``.

Usage:
    from workbench.retrieval.context_builder import SimpleContextBuilder

    builder = SimpleContextBuilder(max_chunks_per_doc=3)
    context_text, chunk_ids = builder.build_context(chunks, max_tokens=2048)
"""

from __future__ import annotations

from workbench.core.types import Chunk
from workbench.observability.tracing import add_span_attributes, start_span
from workbench.retrieval.evidence_packing import token_bound


def _estimate_tokens(text: str) -> int:
    """Use the run tokenizer, or a conservative UTF-8 byte bound offline."""
    return token_bound(text)


class SimpleContextBuilder:
    """
    Greedy context packer with deduplication and diversity cap.

    Args:
        max_chunks_per_doc: Maximum chunks from any single document_id.
        separator: String placed between chunks in the output.
        header_template: Per-chunk header template.
            Placeholders: ``{title}``, ``{section}``, ``{chunk_id}``.
            Set to ``""`` to disable headers.
    """

    def __init__(
        self,
        max_chunks_per_doc: int = 3,
        separator: str = "\n\n---\n\n",
        header_template: str = "[{title}] (chunk {chunk_id})\n",
    ) -> None:
        self.max_chunks_per_doc = max_chunks_per_doc
        self.separator = separator
        self.header_template = header_template

    def build_context(
        self,
        chunks: list[Chunk],
        max_tokens: int = 2048,
    ) -> tuple[str, list[str]]:
        """
        Build a context string from an ordered list of chunks.

        The input list should already be ranked (best first).  This method
        walks the list in order and greedily includes chunks until the
        token budget is exhausted.

        Args:
            chunks: Ranked list of Chunk objects (best first).
            max_tokens: Maximum token budget for the context.

        Returns:
            Tuple of (context_text, list_of_included_chunk_ids).
        """
        with start_span(
            "context.build",
            attributes={
                "input_chunks": len(chunks),
                "max_tokens": max_tokens,
                "max_chunks_per_doc": self.max_chunks_per_doc,
            },
        ):
            seen_ids: set[str] = set()
            doc_counts: dict[str, int] = {}
            selected: list[Chunk] = []
            tokens_used = 0

            for chunk in chunks:
                # 1. Deduplicate
                if chunk.chunk_id in seen_ids:
                    continue

                # 2. Diversity cap
                doc_id = chunk.document_id
                if doc_counts.get(doc_id, 0) >= self.max_chunks_per_doc:
                    continue

                # 3. Token budget
                header = self.header_template.format(
                    title=chunk.title,
                    section=chunk.section or "",
                    chunk_id=chunk.chunk_id,
                )
                rendered = self.separator.join(
                    [
                        *(
                            self.header_template.format(
                                title=c.title,
                                section=c.section or "",
                                chunk_id=c.chunk_id,
                            )
                            + c.text
                            for c in selected
                        ),
                        header + chunk.text,
                    ]
                )
                if _estimate_tokens(rendered) > max_tokens:
                    continue

                # Accept this chunk
                seen_ids.add(chunk.chunk_id)
                doc_counts[doc_id] = doc_counts.get(doc_id, 0) + 1
                selected.append(chunk)
                tokens_used = _estimate_tokens(rendered)

            # Assemble context string
            parts: list[str] = []
            for chunk in selected:
                header = self.header_template.format(
                    title=chunk.title,
                    section=chunk.section or "",
                    chunk_id=chunk.chunk_id,
                )
                parts.append(header + chunk.text)

            context_text = self.separator.join(parts)
            included_ids = [c.chunk_id for c in selected]

            add_span_attributes(
                {
                    "selected_chunks": len(selected),
                    "tokens_used": tokens_used,
                    "unique_docs": len(doc_counts),
                }
            )

            return context_text, included_ids

    def __repr__(self) -> str:
        return f"SimpleContextBuilder(max_chunks_per_doc={self.max_chunks_per_doc})"
