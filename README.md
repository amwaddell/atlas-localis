# Atlas Localis

**Wikipedia research, entirely on your machine.**

Atlas Localis combines a local Wikipedia database, semantic search and a Mistral language model in one research application. Download the knowledge and models, build the index, and ask questions **without an internet connection**. Search, retrieval, inference and orchestration all run locally.

I wanted a local research assistant. I may have overprepared for a Wi-Fi outage.

![Atlas Localis welcome screen with green terminal lettering and pixel-art Atlas](docs/images/retro-ui/welcome-1440.png)

*The local research terminal, ready for a question. [View the mobile layout](docs/images/retro-ui/welcome-390.png)*

## From a question to its sources

Ask about an event, compare developments or trace a chronology. Atlas Localis plans the research, retrieves passages and assembles a cited answer. Follow-up questions retain context. Expand a numbered **Local source** to read the exact supporting excerpts and their snapshot and chunk identifiers.

Research uses the installed **Simple English Wikipedia** snapshot. Missing evidence can produce a partial answer, explicit gaps or a request for clarification.

![Atlas Localis research view with a numbered answer, research tasks and local source disclosures](docs/images/retro-ui/research-1440.png)

*Research view with test-fixture content. [View expanded source evidence](docs/images/retro-ui/evidence-1440.png)*

## What runs locally

| Part | Implementation |
| --- | --- |
| **Knowledge pipeline** | Scripts download Wikipedia and verified model files, chunk articles, generate embeddings and build the local indexes. |
| **Search and retrieval** | **LanceDB** stores text and vectors. **E5** supplies semantic embeddings; keyword and vector results are combined with reciprocal rank fusion, then reranked by **BGE**. |
| **Language model** | Mistral's **Ministral-3-8B-Instruct** runs in 4-bit GGUF format through **llama.cpp**, with Apple Metal acceleration. |
| **Research orchestration** | **Python, LangGraph and Pydantic** coordinate up to three research tasks, validate structured findings and assemble the answer with numbered citations. |
| **Application** | **Streamlit** provides a green terminal interface with locally bundled artwork and fonts. The CLI shares its execution engine and result format. |
| **Diagnostics** | Local **JSONL logs, SQLite metrics and OpenTelemetry spans** make runs inspectable. |

```mermaid
flowchart TD
    S["Setup: download Wikipedia and models"] --> B["Chunk articles and build local indexes"]
    B --> D[(Wikipedia / LanceDB)]
    subgraph R["Research — on your machine"]
        Q[Question + conversation] --> P[LangGraph research plan]
        P --> F[Semantic + keyword search / BGE reranking]
        F --> M[Local Ministral: select and organize evidence]
        M --> V[Validate findings and citations]
        V --> A[Answer + source excerpts]
    end
    D --> F
```

Internet access belongs to installation, downloads and explicit updates. During research, models load from local files and configured runtime services use loopback connections. No hosted LLM, cloud database or API key is required.

## Engineering choices that matter

- **Make evidence inspectable.** Source-bound findings, excerpt offsets and hashes connect each citation to local text. Generation favors faithful source wording over free-form paraphrase.
- **Work within one machine's resources.** Shared model caches, bounded concurrency, deadlines and cancellation control execution. UI, CLI and evaluation use the same lifecycle.
- **Keep updates recoverable.** Checkpointed builds, validated snapshots, atomic activation and rollback keep unfinished indexes out of the running application.
- **Test the system without loading the models.** Real temporary LanceDB databases and injected models exercise retrieval, graphs and process ownership. The latest recorded checks passed **767 model-free tests**; real-model acceptance is separate.

[Architecture](docs/dev/architecture.md) · [Data lifecycle](docs/dev/data-lifecycle.md) · [Tests and evaluation](docs/dev/testing.md)

## Run it

Supported target: **macOS on Apple Silicon, CPython 3.12**. Follow the [setup guide](docs/dev/setup.md) for prerequisites, including the pinned native llama.cpp build. Then, from the repository root:

```bash
# First-time setup: install dependencies, download assets and build the database.
ATLAS_SETUP_PYTHON=python3.12 bash scripts/setup.sh
```

Setup checks for a **40 GiB free-space reserve**; a full corpus build takes hours. After it completes, start the local services in separate terminals:

```bash
# Terminal 1
bash scripts/start_model_stack.sh

# Terminal 2
bash scripts/run_ui.sh
```

Open **http://127.0.0.1:8501** and try “When did the Roman Republic end?” The [developer guide](docs/dev/README.md) covers CLI usage, configuration and troubleshooting.

## A note on scope

Citations establish where an answer came from, not whether it is historically correct. Coverage depends on the installed snapshot, and source context can be incomplete. Python-side offline safeguards are tested; full browser/native-process network-isolation testing remains pending. [Known limitations](docs/dev/testing.md#known-limitations) also cover the pinned native runtime issue.
