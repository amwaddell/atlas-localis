# Developer guide

Atlas Localis ships one research workflow. The distribution is `atlas-localis`; Python imports remain `workbench`.

| Guide | Contents |
| --- | --- |
| [Setup and troubleshooting](setup.md) | Prerequisites, provisioning, launch and common failures |
| [Architecture](architecture.md) | Graphs, typed contracts, evidence validation and resource ownership |
| [Configuration](configuration.md) | Current defaults and supported overrides |
| [Data lifecycle](data-lifecycle.md) | Acquisition, resumable indexing, snapshots and rollback |
| [Development and testing](testing.md) | Model-free checks, evaluation, acceptance and known limitations |
| [Observability](observability.md) | Logs, metrics, tracing and local Phoenix integration |

## Source map

- `src/workbench/systems/`: application factory, lifecycle and presentation projection.
- `src/workbench/graphs/`: planning, isolated researchers, synthesis and contracts.
- `src/workbench/retrieval/`: hybrid search, reranking and evidence packing.
- `src/workbench/data_build/`: acquisition, ingestion, indexes and snapshots.
- `src/workbench/models/`: local inference, native process and structured generation.
- `src/workbench/ui/`: Streamlit chat, progress and citation rendering.
- `tests/`: unit, fixture integration and explicit real-model acceptance.
- `configs/`, `locks/`: defaults, artifact manifests and exact dependency versions.

Extend the canonical workflow through its typed contracts and keep UI, CLI and evaluation on `create_application`. Library tools (`ToolSpec`, Wikipedia search, chunk opening and date normalization) remain available, but do not define alternate public graph modes. Preserve snapshot identity, bounded repair, cancellation and excerpt provenance when changing a worker.
