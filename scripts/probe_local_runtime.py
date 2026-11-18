"""Read-only cached-asset parity/latency probe. Never provisions or rebuilds data."""

import argparse
import gc
import json
import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main():
    import lancedb
    import numpy as np
    import torch

    from workbench.core.config import load_config
    from workbench.core.types import Query
    from workbench.data_build.embeddings import SentenceTransformerEmbedder
    from workbench.graphs.research_models import ConversationInput
    from workbench.models.llamacpp_server import LlamaCppServerModel
    from workbench.retrieval.rerankers import CrossEncoderReranker
    from workbench.stores.chunk_store import ChunkStore
    from workbench.systems.runner import EvaluationControls, create_application

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--device", choices=["mps", "cpu"], default="mps")
    parser.add_argument(
        "--answers",
        action="store_true",
        help="Also run simple/complex canonical answers on/off",
    )
    parser.add_argument(
        "--retrieval", action="store_true", help="Inspect real on/off hybrid candidates"
    )
    args = parser.parse_args()

    def emit(label, value):
        print(json.dumps({label: value}, ensure_ascii=False), flush=True)

    def timed(label, operation):
        started = time.monotonic()
        value = operation()
        emit(label + "_seconds", time.monotonic() - started)
        return value

    def memory():
        return {
            "process_peak_rss_bytes": resource.getrusage(
                resource.RUSAGE_SELF
            ).ru_maxrss,
            "mps_driver_bytes": torch.mps.driver_allocated_memory()
            if torch.backends.mps.is_available()
            else None,
        }

    cfg = load_config(
        overrides={
            "model": {"base_url": args.base_url},
            "embeddings": {"provider": "local", "device": args.device, "batch_size": 4},
            "reranking": {
                "provider": "cross_encoder",
                "device": args.device,
                "batch_size": 1,
                "max_length": 8192,
            },
            "observability": {"enable_tracing": False, "enable_metrics": False},
        }
    )
    table = lancedb.connect(
        str(Path(cfg.paths["active_index"]) / "lancedb")
    ).open_table("chunks")
    rows = table.head(8).to_pylist()
    emit(
        "index",
        {
            "version": table.version,
            "rows": table.count_rows(),
            "sample_ids": [r["chunk_id"] for r in rows],
        },
    )
    embedder = timed(
        "e5_load", lambda: SentenceTransformerEmbedder(device=args.device, batch_size=4)
    )
    emit(
        "e5_identity",
        {
            "model": embedder.model_name,
            "path": embedder.model_path,
            "device": embedder.device,
        },
    )
    for phase in ("first", "warm"):
        vectors = np.array(
            timed(
                "e5_" + phase,
                lambda e=embedder: e.embed_texts([r["text"] for r in rows]),
            )
        )
        stored = np.array([r["vector"] for r in rows])
        emit(
            "parity_" + phase,
            {
                "max_abs": float(abs(stored - vectors).max()),
                "min_cosine": float(
                    (
                        np.sum(stored * vectors, axis=1)
                        / (
                            np.linalg.norm(stored, axis=1)
                            * np.linalg.norm(vectors, axis=1)
                        )
                    ).min()
                ),
                "norms": np.linalg.norm(vectors, axis=1).tolist(),
            },
        )
    emit("e5_memory", memory())
    del embedder
    gc.collect()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()
    reranker = timed(
        "bge_load", lambda: CrossEncoderReranker(device=args.device, batch_size=1)
    )
    emit(
        "bge_identity",
        {
            "model": reranker.model_name,
            "path": reranker.model_path,
            "device": reranker.device,
        },
    )
    for phase in ("first", "warm"):
        scores = timed(
            "bge_" + phase,
            lambda r=reranker: r.rerank(
                Query(text="What is astronomy?"),
                [ChunkStore._row_to_chunk(r) for r in rows],
                top_n=8,
            ),
        )
        emit("bge_" + phase, [s.model_dump() for s in scores])
    emit("bge_memory", memory())
    del reranker
    gc.collect()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()
    with LlamaCppServerModel(
        base_url=args.base_url, model_name=cfg.model["model_name"]
    ) as model:
        model.check_identity()
        for row in rows:
            emit(
                "tokenizer",
                {
                    "chunk_id": row["chunk_id"],
                    "bytes": len(row["text"].encode()),
                    "tokens": model.exact_token_count(row["text"]),
                },
            )
    if args.answers or args.retrieval:
        for enabled in (True, False):
            with create_application(
                config=cfg, evaluation=EvaluationControls(rerank=enabled)
            ) as app:
                for question in (
                    "When did the Roman Republic end?",
                    "Compare the causes of the French Revolution and the Russian Revolution of 1917.",
                ):
                    if args.retrieval:
                        retrieved = timed(
                            "retrieval",
                            lambda q=question: (
                                app.resources.retriever.retrieve_hydrated(
                                    Query(text=q), top_k=50
                                )
                            ),
                        )
                        emit(
                            "retrieval",
                            {
                                "rerank": enabled,
                                "question": question,
                                "retrieved": retrieved.retrieved,
                                "top_five": [
                                    {
                                        "chunk_id": c.chunk_id,
                                        "title": c.title,
                                        "text": c.text[:500],
                                    }
                                    for c in retrieved.chunks[:5]
                                ],
                            },
                        )
                    if args.answers:
                        result = timed(
                            "answer",
                            lambda q=question: app.invoke(
                                ConversationInput(question=q)
                            ),
                        )
                        emit(
                            "answer",
                            {
                                "rerank": enabled,
                                "question": question,
                                "result": result.model_dump(mode="json"),
                                "memory": memory(),
                            },
                        )


if __name__ == "__main__":
    main()
