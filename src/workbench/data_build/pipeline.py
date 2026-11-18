"""Fingerprint-bound, resumable snapshot construction using existing primitives."""

from __future__ import annotations

import math
import os
import re
import shutil
from pathlib import Path

import lancedb
import pyarrow as pa
import pyarrow.parquet as pq

from workbench.data_build import chunking, embeddings, index_keyword, ingest_wikipedia
from workbench.data_build.index_keyword import build_keyword_index
from workbench.data_build.index_vector import vector_schema, write_vector_batches
from workbench.data_build.lifecycle import (
    atomic_json,
    fingerprint,
    read_json,
    sha256,
    sync_dir,
)


def inventory(root: Path) -> dict:
    files = {}
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            raise ValueError(f"Bundle cannot contain symlinks: {p}")
        if p.is_file() and p.name != "manifest.json" and not p.name.endswith(".part"):
            files[str(p.relative_to(root))] = {
                "size": p.stat().st_size,
                "sha256": sha256(p),
            }
    return files


def check_vectors(vectors, count, dimension):
    if len(vectors) != count:
        raise ValueError("Embedding count mismatch")
    for v in vectors:
        if len(v) != dimension or not all(math.isfinite(x) for x in v):
            raise ValueError("Invalid vector dimension/nonfinite component")
        if abs(sum(x * x for x in v) - 1) > 0.002:
            raise ValueError("Expected normalized vectors")


def validate_bundle(root: Path, model: dict, embedder) -> dict:
    articles = pq.read_table(root / "articles.parquet", columns=["page_id"])[
        "page_id"
    ].to_pylist()
    if not articles or len(set(articles)) != len(articles):
        raise ValueError("Empty/duplicate article IDs")
    set_articles = set(articles)
    expected = {}
    for batch in pq.ParquetFile(root / "chunks.parquet").iter_batches(batch_size=256):
        for row in batch.to_pylist():
            if row["document_id"] not in set_articles or row["chunk_id"] in expected:
                raise ValueError("Duplicate/orphan chunk")
            expected[row["chunk_id"]] = fingerprint(
                {k: row[k] for k in ("document_id", "title", "section", "text")}
            )
    table = lancedb.connect(str(root / "lancedb")).open_table("chunks")
    field = table.schema.field("vector").type
    if (
        not pa.types.is_fixed_size_list(field)
        or field.list_size != model["dimension"]
        or field.value_type != pa.float32()
    ):
        raise ValueError("Vector schema/model mismatch")
    seen = set()
    for batch in table.search().limit(None).to_batches(batch_size=256):
        rows = batch.to_pylist()
        check_vectors([r["vector"] for r in rows], len(rows), model["dimension"])
        for r in rows:
            cid = r["chunk_id"]
            if cid in seen or expected.get(cid) != fingerprint(
                {k: r[k] for k in ("document_id", "title", "section", "text")}
            ):
                raise ValueError("Index/chunk ID or content mismatch")
            seen.add(cid)
    if not seen or seen != expected.keys():
        raise ValueError("Index/chunk counts or ID sets mismatch")
    # Exercise both actual query engines before the canonical application smoke.
    sample = next(
        pq.ParquetFile(root / "chunks.parquet").iter_batches(batch_size=1)
    ).to_pylist()[0]
    query = max(re.findall(r"[^\W\d_]+", sample["text"]), key=len)
    if not table.search(query, query_type="fts").limit(3).to_list():
        raise ValueError("FTS retrieval smoke failed")
    vector = embedder.embed_query(sample["text"][:256])
    check_vectors([vector], 1, model["dimension"])
    if not table.search(vector).limit(3).to_list():
        raise ValueError("Vector retrieval smoke failed")
    return {
        "articles": len(articles),
        "chunks": len(seen),
        "dimension": model["dimension"],
        "retrieval": "passed",
    }


def build_bundle(
    indexes: Path,
    dump: Path,
    source: dict,
    model: dict,
    embedder,
    smoke,
    *,
    batch_size=32,
) -> Path:
    """Never write active data. A killed batch is replayed; committed shards persist."""
    if not 1 <= batch_size <= 256:
        raise ValueError("Embedding checkpoint batch must be 1..256")
    if sha256(dump) != source["sha256"]:
        raise ValueError("Dump checksum mismatch")
    inputs = {
        "source": source,
        "embedding": model,
        "batch_size": batch_size,
        "chunker": chunking.ParagraphAwareChunker().settings_str,
        "code": {
            p.name: sha256(p)
            for p in (
                Path(__file__),
                Path(chunking.__file__),
                Path(ingest_wikipedia.__file__),
                Path(embeddings.__file__),
                Path(index_keyword.__file__),
                Path(__file__).resolve().parents[3]
                / "locks/runtime-macos-arm64-py312.txt",
            )
        },
    }
    identity = fingerprint(inputs)
    snapshots = indexes / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    final = snapshots / identity
    if final.exists():
        from workbench.data_build.snapshots import verify

        verify(final)
        return final
    stage = indexes / (".build-" + identity)
    if stage.is_symlink():
        raise ValueError("Staging root cannot be a symlink")
    stage.mkdir(exist_ok=True)
    state_path = stage / "inputs.json"
    if state_path.exists() and read_json(state_path) != inputs:
        raise ValueError("Stage input mismatch")
    atomic_json(state_path, inputs)

    def progress(name, **details):
        atomic_json(stage / "progress.json", {"stage": name, **details})

    # Each expensive stage is committed only with a verified output hash.
    def run_stage(name, output, operation):
        receipt = stage / (name + ".json")
        if receipt.exists():
            if sha256(output) != read_json(receipt)["sha256"]:
                raise ValueError(f"{name} checkpoint corrupt; retained")
            return
        progress(name)
        part = output.with_suffix(output.suffix + ".part")
        operation(part)
        os.replace(part, output)
        atomic_json(receipt, {"sha256": sha256(output)})

    run_stage(
        "ingest",
        stage / "articles.parquet",
        lambda out: ingest_wikipedia.ingest_wikipedia_dump(
            dump, out, source_label=source["identity"]
        ),
    )
    run_stage(
        "chunk",
        stage / "chunks.parquet",
        lambda out: chunking.build_chunks_parquet(
            stage / "articles.parquet", out, chunking.ParagraphAwareChunker()
        ),
    )
    progress("embedding")
    vectors_dir = stage / "vectors"
    if vectors_dir.is_symlink():
        raise ValueError("Vector checkpoint directory cannot be a symlink")
    vectors_dir.mkdir(exist_ok=True)
    schema = vector_schema(model["dimension"])
    for number, batch in enumerate(
        pq.ParquetFile(stage / "chunks.parquet").iter_batches(batch_size=batch_size)
    ):
        shard = vectors_dir / f"{number:08d}.parquet"
        receipt = shard.with_suffix(".json")
        rows = batch.to_pylist()
        if receipt.exists():
            if not shard.exists() or sha256(shard) != read_json(receipt)["sha256"]:
                raise ValueError("Embedding checkpoint corrupt; retained")
            continue
        vectors = embedder.embed_texts([r["text"] for r in rows])
        check_vectors(vectors, len(rows), model["dimension"])
        data = [
            {**{k: r[k] for k in schema.names if k != "vector"}, "vector": v}
            for r, v in zip(rows, vectors, strict=True)
        ]
        part = shard.with_suffix(".part")
        pq.write_table(pa.Table.from_pylist(data, schema=schema), part)
        os.replace(part, shard)
        atomic_json(receipt, {"sha256": sha256(shard), "rows": len(rows)})
        progress("embedding", committed_batches=number + 1)
    # Reassemble only this owned staging DB from bounded committed shards.
    progress("index assembly")
    db_path = stage / "lancedb"
    if db_path.exists():
        shutil.rmtree(db_path)
    write_vector_batches(
        db_path,
        (pq.read_table(shard) for shard in sorted(vectors_dir.glob("*.parquet"))),
        model["dimension"],
    )
    build_keyword_index(db_path)
    progress("validation")
    report = validate_bundle(stage, model, embedder)
    progress("canonical smoke")
    smoke_report = smoke(stage)
    if smoke_report.get("outcome") not in {
        "complete",
        "partial",
    } or not smoke_report.get("citations"):
        raise ValueError("Canonical smoke must return a cited complete/partial result")
    progress("sealed")
    atomic_json(
        stage / "manifest.json",
        {
            "format": 1,
            "id": identity,
            "provenance": "verified-build",
            "inputs": inputs,
            "validation": report,
            "canonical_smoke": smoke_report,
            "files": inventory(stage),
        },
    )
    from workbench.data_build.snapshots import verify

    verify(stage)
    os.rename(stage, final)
    sync_dir(snapshots)
    return final
