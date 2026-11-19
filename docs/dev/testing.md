# Development, testing and limitations

## Model-free checks

Use CPython 3.12. For an isolated development environment, install the CI dependency closure (macOS/Linux), then the editable package:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --only-binary=:all: --no-deps -r locks/ci-py312.txt
python -m pip install --no-deps --no-build-isolation -e .
python -m pip check
python -m ruff check src tests scripts
python -m ruff format --check tests src/workbench/evaluation scripts/run_researcher_eval.py
python scripts/run_researcher_eval.py --validate-only
python -m pytest
python scripts/preflight.py --quick
```

Use a fresh venv path if `.venv` already contains an environment you need to preserve. The default pytest selection excludes `acceptance` and legacy `integration`. It includes fixture integration: temporary Arrow/LanceDB data, deterministic embeddings, graph execution and bounded subprocesses. No model weights or Wikipedia download is needed. Tests enforce offline network behavior. The [CI workflow](../../.github/workflows/model-free.yml) runs on Linux and macOS; local success is not evidence of a hosted run.

## UI verification — 18 November 2026

The retro UI pass recorded **767 model-free tests passed; 40 real-model acceptance tests deselected**, using Python 3.12.7 and Streamlit 1.54.0. The separate fixture-integration selection passed 99 tests; the focused UI/application/offline selection passed 114. Source/test/script Ruff lint, the CI format selection, `pip check`, model-free preflight, 24-case/rubric validation and five retained report validations also passed.

Chrome review used injected model-free resources to exercise welcome, active research, all five canonical outcomes, expanded evidence, Clear chat and missing-resource guidance. Checks covered 1440 × 900, 1024 × 768, 390 × 844 and 200% text zoom, along with keyboard focus, rendered fonts and local asset loading. The wheel and source distribution contained the artwork, fonts and licenses. The [implementation review](../design/ATLAS_LOCALIS_RETRO_UI_REVIEW.md) records commands, results and limitations; current screenshots are in [`docs/images/retro-ui/`](../images/retro-ui/). These are the fixture screenshots used in the README.

An earlier release check recorded 765 model-free passes and a single-question smoke check with a provisioned local model. Its [research](../images/atlas-localis-research.png) and [source evidence](../images/atlas-localis-evidence.png) captures show the previous UI. The redesign pass did not run provisioned model acceptance or real-model research.

### Preview the UI without models

After installing the model-free development environment, run from the repository root:

```bash
python -m streamlit run tests/fixtures/ui_preview.py --server.address 127.0.0.1 --server.port 8539 --server.headless true --browser.gatherUsageStats false
```

Open http://127.0.0.1:8539 and submit a question to exercise the real UI with deterministic test data. Use `?long=1` for a longer answer and identifiers, `?delay=2` to inspect progress, or `?mode=partial`, `failed`, `insufficient_evidence`, `cancelled` or `missing` to inspect those states. The fixture uses a larger prompt budget for the long example; production limits are unchanged. Restart the preview after editing imported UI modules so captures reflect the current code.

## Real-model checks and evaluation

Stop any resident UI/CLI application first. Real acceptance requires provisioned assets and the configured native server; missing resources fail rather than skip.

```bash
python -m pytest -o addopts='' --strict-markers -m acceptance -q
python scripts/run_researcher_eval.py --split development --owned-native --output runs/reports/development-new
```

`--owned-native` starts and reaps only its own pinned native process. Output directories must be new. The frozen [24-case suite](../../src/workbench/evaluation/data/history-v1.json) has eight development and sixteen held-out cases across factual, comparative, temporal and insufficient-evidence categories. The [rubric](../../src/workbench/evaluation/data/history-rubric-v1.json) separates source integrity from correctness, completeness, chronology and uncertainty. Use held-out cases for confirmation, not prompt tuning.

Reports retain canonical results, source/model/configuration identities and execution diagnostics. `--validate-report PATH` validates additional saved reports. Selected historical baseline reports and review fixtures in [evidence/](evidence/) remain for validator regression coverage, **not as current performance claims**. Intermediate development logs and superseded stage documents are excluded from the public documentation. In the release-working checkout, originals are preserved under ignored `runs/release-archive/docs/`.

## Known limitations

- **Source integrity is not historical correctness.** Exact excerpts can omit context, select the wrong period or contain conflicting source claims. Independent historical/utility review is pending.
- **Output is deliberately extractive.** Source selection and verbatim synthesis limit invented wording, but Wikipedia templates/infobox markup can appear. Coverage can be partial; evidence gaps are not proofs of corpus-wide absence.
- **Cancel in the chat UI:** clicking Cancel during a run can leave the user question without a final cancelled assistant message. This behavior was reproduced in both the previous and redesigned shells. Canonical cancelled results render correctly when returned to the UI.
- **Pinned native runtime:** prior real-model acceptance recorded 38 passes and two failures caused by short-output HTTP 500 responses at a UTF-8/parser boundary in llama.cpp build 8130. These tests remain enabled in acceptance.
- **Deployment boundaries:** full disconnected browser/native-process acceptance, Phoenix viewer/export acceptance, hosted CI and a second-machine clean install have not been established. Python-side offline enforcement and fixture tests cover narrower contracts.
- **Resource costs:** full corpus assembly is slow and retains substantial shard/manifest data. No minimum-memory or thermal-performance guarantee has been measured.

Application outcome labels (`complete`, `partial`, `insufficient_evidence`) describe delivery, not measured factual accuracy. No accuracy, latency percentile or production-readiness claim is made.
