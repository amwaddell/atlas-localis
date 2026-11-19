# Setup and troubleshooting

Run commands from the repository root. Supported runtime: **macOS arm64, CPython 3.12**, Xcode command-line tools, and native Metal llama.cpp **build 8130 / commit `ed4837891`** on PATH. Provisioning checks the native identity; an arbitrary current Homebrew build is not equivalent. Native toolchain installation is a prerequisite, not automated by setup.

## First installation

```bash
git clone https://github.com/amwaddell/atlas-localis.git
cd atlas-localis
ATLAS_SETUP_PYTHON=python3.12 bash scripts/setup.sh --check-only
ATLAS_SETUP_PYTHON=python3.12 bash scripts/setup.sh
```

Setup creates `.atlas-venv`, installs the pinned runtime closure and acquires verified models and the dated Simple English Wikipedia dump. It preserves an existing `.venv`. Setup requires a 40 GiB free-space reserve; this is a conservative admission threshold, not a peak disk estimate. A previously recorded full build took over six hours. Allow hours and wait for `Setup complete`; embedding completion alone is not a finished index.

Models are Ministral-3-8B-Instruct Q4_K_M, multilingual E5 base and BGE reranker v2 m3. Exact revisions/hashes live in `configs/artifacts/models.json`; dump identity is in `configs/artifacts/wikipedia.json`. Runtime never downloads missing assets. See [data lifecycle](data-lifecycle.md) for checkpoint recovery, custom environments, legacy index conversion and updates.

## Launch an existing installation

```bash
# Terminal 1: native llama.cpp server
bash scripts/start_model_stack.sh

# Terminal 2: chat UI
bash scripts/run_ui.sh
# Optional alternate UI port:
# bash scripts/run_ui.sh --port 8502
```

Open http://127.0.0.1:8501. Launchers choose `.atlas-venv/bin/python` when present, otherwise `.venv/bin/python`. Set `ATLAS_PYTHON` to an absolute interpreter path for a custom environment. E5 and BGE load inside the application; only the LLM needs a separate server.

For CLI and checks, activate the same environment (`source .atlas-venv/bin/activate`, or `source .venv/bin/activate` for an existing checkout):

```bash
python scripts/preflight.py --quick  # model-free graph execution
python scripts/preflight.py          # provisioned runtime question
python scripts/run_agent_chat.py "When did the Roman Republic end?"
```

Close the UI process before running another application consumer. The model server can stay running; the application resource lease prevents competing UI/CLI/evaluation owners. Use Ctrl+C in each launcher terminal to stop it.

## Using the research interface

Type a question into the field at the bottom of the welcome screen. Once research starts, Atlas moves into the sidebar and the main area shows progress followed by the answer. Ask a follow-up in the same field to retain the conversation context.

Expand **Research tasks** to inspect task coverage and evidence gaps. Each numbered **Local source** contains the exact excerpt, title, section, snapshot identifier and chunk identifier. The sidebar's **Session details** shows the snapshot used for the current session.

**Clear chat** clears the transcript and conversation summary and restores the welcome screen. On a narrow screen, use the sidebar toggle to reach session controls. **Cancel active research** requests cancellation; an interrupted run can leave the question without a final cancellation message. See [known limitations](testing.md#known-limitations).

## Common problems

| Symptom | Action |
| --- | --- |
| `resource_busy` | Stop the other UI/CLI/evaluation process; do not delete a live lease. |
| Missing local models/index | Run explicit setup; use `bash scripts/update_wikipedia.sh status` to inspect snapshots. |
| Setup refuses an existing environment | Use a new `--venv` path and set `ATLAS_PYTHON` for launch. Do not delete the old environment. |
| Wrong native build or occupied port | Check `llama-server --version` and the existing listener; stop only a process you own. |
| Offline-policy configuration error | Remove remote endpoints/cloud telemetry settings; runtime accepts loopback services only. |
| Native HTTP 500 on a short completion | Known pinned-runtime issue; see [limitations](testing.md#known-limitations). |
| Partial answer or raw Wikipedia markup | Inspect local excerpts and evidence gaps; these are known corpus/output limitations. |

No minimum-memory guarantee has been established. Metal errors fail explicitly rather than silently retrying on CPU. Full browser/subprocess disconnected acceptance and a clean installation on a second machine remain unverified.
