"""
ChunkStore: single source of truth for fetching chunk data by ID.

Consolidates the duplicated LanceDB lookup logic that previously
lived in both ``HybridRetriever._lookup_chunks`` and
``OpenChunkTool._ensure_lookup``.

All chunk-fetching now goes through this class, making it easy to
swap backends (LanceDB → SQLite → remote API) without touching
callers.

Usage:
    from workbench.stores.chunk_store import ChunkStore

    store = ChunkStore(db_path=Path("data/indexes/active/lancedb"))
    chunk = store.get("abc123")          # single lookup
    chunks = store.get_many(["a", "b"])  # batch lookup
"""

from __future__ import annotations

import time
from collections import OrderedDict
from pathlib import Path
from threading import RLock
from typing import Any

import lancedb

from workbench.core.offline import local_path
from workbench.core.types import Chunk
from workbench.observability.tracing import add_span_attributes, start_span


class ChunkStore:
    """
    Read-only store backed by a LanceDB chunks table.

    Fetches bounded metadata/text batches, never vector columns. A 256-record
    LRU retains recently opened evidence without copying the full corpus.

    Args:
        db_path: Path to the LanceDB database directory.
        table_name: Name of the chunks table (default ``"chunks"``).
    """

    def __init__(
        self,
        db_path: Path,
        table_name: str = "chunks",
    ) -> None:
        db_path = local_path(db_path)
        self.db_path = db_path
        self.table_name = table_name

        self._db = lancedb.connect(str(db_path))
        self._table = self._db.open_table(table_name)

        self._lookup: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = RLock()
        self._accessed = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, chunk_id: str) -> Chunk | None:
        """
        Fetch a single chunk by its ID.

        Args:
            chunk_id: The chunk identifier.

        Returns:
            A ``Chunk`` object, or ``None`` if the ID is not found.
        """
        chunks = self.get_many([chunk_id])
        return chunks[0] if chunks else None

    def get_many(self, chunk_ids: list[str]) -> list[Chunk]:
        """
        Fetch multiple chunks by ID, preserving the requested order.

        IDs that are not found are silently skipped.

        Args:
            chunk_ids: Ordered list of chunk identifiers.

        Returns:
            List of ``Chunk`` objects in the same order (missing IDs omitted).
        """
        if not chunk_ids:
            return []

        with start_span(
            "chunk_store.get_many",
            attributes={"requested_count": len(chunk_ids)},
        ):
            t0 = time.time()
            with self._lock:
                self._accessed = True
                found = {
                    cid: self._lookup[cid] for cid in chunk_ids if cid in self._lookup
                }
                missing = list(
                    dict.fromkeys(cid for cid in chunk_ids if cid not in found)
                )
                columns = [
                    name for name in self._table.schema.names if name != "vector"
                ]
                for offset in range(0, len(missing), 64):
                    batch = missing[offset : offset + 64]
                    literals = ",".join(
                        "'" + cid.replace("'", "''") + "'" for cid in batch
                    )
                    rows = (
                        self._table.search()
                        .where(f"chunk_id IN ({literals})", prefilter=True)
                        .select(columns)
                        .limit(len(batch))
                        .to_list()
                    )
                    found.update((row["chunk_id"], row) for row in rows)
                for cid in chunk_ids:
                    if cid in found:
                        self._lookup[cid] = found[cid]
                        self._lookup.move_to_end(cid)
                while len(self._lookup) > 256:
                    self._lookup.popitem(last=False)
                chunks = [
                    self._row_to_chunk(found[cid]) for cid in chunk_ids if cid in found
                ]

            duration_ms = (time.time() - t0) * 1000
            add_span_attributes(
                {
                    "found_count": len(chunks),
                    "duration_ms": round(duration_ms, 1),
                }
            )
            return chunks

    def contains(self, chunk_id: str) -> bool:
        """Check whether a chunk ID exists in the store."""
        return self.get(chunk_id) is not None

    def count(self) -> int:
        """Count rows without loading text or vectors into Python."""
        return self._table.count_rows()

    @staticmethod
    def _row_to_chunk(row: dict[str, Any]) -> Chunk:
        """Convert a LanceDB row dict to a ``Chunk`` object."""
        text = row.get("text", "")
        return Chunk(
            chunk_id=row["chunk_id"],
            document_id=row.get("document_id", ""),
            title=row.get("title", ""),
            section=row.get("section") or None,
            text=text,
            start_offset=row.get("start_char", 0),
            end_offset=row.get("end_char", 0),
            token_count=row.get("token_count", len(text) // 4),
            metadata={"url": row.get("url", "")},
        )

    def __repr__(self) -> str:
        loaded = self.count() if self._accessed else "not loaded"
        return (
            f"ChunkStore(db={self.db_path}, table={self.table_name!r}, chunks={loaded})"
        )
