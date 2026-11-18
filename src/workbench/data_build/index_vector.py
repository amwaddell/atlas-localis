"""Bounded LanceDB writer shared by fixture builders and snapshot provisioning.

Existing tables are never dropped. Resumable snapshot builds checkpoint vector
shards in pipeline.py, then assemble a new owned staging database here.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import lancedb
import pyarrow as pa
import pyarrow.parquet as pq

TABLE_NAME = "chunks"
COLUMNS = ("chunk_id", "document_id", "title", "section", "text")


def vector_schema(dimension):
    return pa.schema(
        [(k, pa.string()) for k in COLUMNS]
        + [("vector", pa.list_(pa.float32(), dimension))]
    )


def write_vector_batches(db_path: Path, batches, dimension: int):
    """Create-only writer; caller owns the staging path and committed batches."""
    if "active" in db_path.parts or any(
        p.is_symlink() for p in (db_path, db_path.parent)
    ):
        raise ValueError("Build beside active data, never through active/symlink paths")
    db = lancedb.connect(str(db_path))
    table = db.create_table(TABLE_NAME, schema=vector_schema(dimension))
    count = 0
    for batch in batches:
        table.add(batch)
        count += batch.num_rows
    return count


def build_vector_index(
    chunks_path: Path,
    db_path: Path,
    embedder,
    embed_batch_size: int = 32,
    write_batch_size: int = 32,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Legacy fixture API. Use the snapshot CLI for checkpointed real builds.

    ``overwrite`` remains accepted for callers, but never authorizes dropping a
    table. An existing destination is preserved and requires a fresh path.
    """
    if db_path.exists():
        raise ValueError("Existing index preserved. Build a new snapshot instead.")
    if not 1 <= embed_batch_size <= 256 or not 1 <= write_batch_size <= 256:
        raise ValueError("Vector batches must be 1..256")
    start = time.monotonic()
    from workbench.data_build.pipeline import check_vectors

    def batches():
        for batch in pq.ParquetFile(chunks_path).iter_batches(
            batch_size=embed_batch_size
        ):
            rows = batch.to_pylist()
            vectors = embedder.embed_texts([r["text"] for r in rows])
            check_vectors(vectors, len(rows), embedder.dimension)
            data = [
                {**{k: r[k] for k in COLUMNS}, "vector": v}
                for r, v in zip(rows, vectors, strict=True)
            ]
            table = pa.Table.from_pylist(data, schema=vector_schema(embedder.dimension))
            yield from (
                pa.Table.from_batches([b])
                for b in table.to_batches(max_chunksize=write_batch_size)
            )

    count = write_vector_batches(db_path, batches(), embedder.dimension)
    return {
        "num_chunks": count,
        "rows_written": count,
        "embedding_dim": embedder.dimension,
        "db_path": str(db_path),
        "table_name": TABLE_NAME,
        "duration_s": round(time.monotonic() - start, 2),
    }
