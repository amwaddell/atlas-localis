# Development and Stage 0 baseline

Recorded 18 October 2026 using the existing `.venv` (Python 3.12.7,
pytest 9.0.2, Ruff 0.15.0). No dependencies or assets were installed/downloaded;
no services were started. A clean installation, dependency lock and CI remain
future work. Keep the checkout and environment paths intact.

## Preservation and scope

Initial HEAD: `6f42433a008c3c205c7b4bc662c9e4026534da80`, branch `main`.
Only `decomposer.py` and `supervisor_graph.py` were modified; the audit and
migration plan were untracked. There were no staged changes or applicable
`AGENTS.md` files. Original graph diffs totalled 331 insertions / 196 deletions.

Checkpoint `c8aa577` on `migration/stage-0-baseline-20261018` explicitly includes
both graph files and both originally untracked documents. A second backup at
`/private/tmp/atlas-stage0-20261018-original` contains copies of all four files,
`tracked.patch`, initial status and base HEAD. Temporary backups are auxiliary;
the Git checkpoint is the durable repository recovery point. No stash, reset,
clean, blanket staging, remote changes or push was used. Git writes required
sandbox escalation and succeeded; no approval rejection occurred.

The nested `tools/wikiextractor` checkout was inspected read-only: clean `master`,
HEAD `8f1b434a80608e1e313d38d263ed7c79c9ee75a9`, matching the parent gitlink.
No fetch or upstream comparison was performed; clean does not establish that
all nested commits are published. It remains intact.

Stage 0 changes only decomposer synthesis correctness, its tests, the real
synthesis template and these three developer documents. The original supervisor
fixes, audit and plan remain byte-for-byte preserved. No files or assets were
removed. No unexpected source modifications were found. Existing architecture,
README, configuration, retrieval/data/observability code and package identity
remain as found.

## Exact checks

Run from the repository root using the existing environment. Full collection
must override `addopts`, which otherwise excludes integration tests:

```sh
.venv/bin/python -B -m pytest --collect-only -o addopts='' -p no:cacheprovider
.venv/bin/python -B -m pytest tests/test_decomposer.py tests/test_citations_numberize.py tests/test_citations_numbers.py tests/test_supervisor_routing.py tests/test_chat_multiturn.py -p no:cacheprovider --tb=short
.venv/bin/python -B -m ruff check --no-cache src/ tests/ scripts/ --output-format concise
.venv/bin/python -B -m ruff check --no-cache src/workbench/graphs/decomposer.py tests/test_decomposer.py
.venv/bin/python -B -m ruff format --check src/workbench/graphs/decomposer.py tests/test_decomposer.py
```

For full model-free execution and collection, the executed harness isolates
relative logs/metrics in a temporary CWD, retains project configuration and
prevents bytecode/cache writes. It does not modify test markers or assertions:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B - <<'PY'
import os
import pathlib
import tempfile
import pytest
root = pathlib.Path.cwd()
with tempfile.TemporaryDirectory(prefix="atlas-stage0-tests-") as directory:
    os.chdir(directory)
    raise SystemExit(pytest.main([
        "-c", str(root / "pyproject.toml"), str(root / "tests"),
        "-p", "no:cacheprovider", "--tb=short", "-ra",
        "--ignore=" + str(root / "tests/test_vector_search_small.py"),
        "--ignore=" + str(root / "tests/test_reranker_small.py"),
    ]))
PY
```

Collection used this same isolation with the two `--ignore` arguments replaced
by `--collect-only`, `-o`, `addopts=`. The focused command above ran directly
from the root. Offline environment flags constrain model libraries during tests;
they do not prove application egress prevention.

| Check | Result |
|---|---|
| Before edits: full collection | Exit 2; 805 collected, one ImportError for removed `_format_sub_answers` |
| After edits: full collection, all markers | Exit 0; 862 collected |
| Focused decomposer/citation/supervisor/chat | Exit 0; 203 passed |
| Complete model-free selection | Exit 0; 822 passed, 1 skipped, 23 deselected |
| Ruff inventory | Exit 1; 31 existing findings outside changed files |
| Changed Python files: Ruff lint/format | Exit 0; pass |
| `git diff --check` | Exit 0; pass |

The two excluded modules contain 16 tests that instantiate actual E5/BGE weights;
they are unmarked and are **not model-free**. No decomposer tests are excluded.
The 23 deselections are the existing `integration` marker. This is a complete
model-free selection, not a claim that default pytest or full model acceptance
has passed. Marker repair is deferred to Stage 5 because it exceeds Stage 0 scope.
The existing skip is `tests/test_tracing_spans.py:177`: `start_run_span` spans
are not captured by the exporter under global OTel context interference. No
assertion failures remain in the executed selection. Real models, services,
Phoenix viewer/export and offline traffic acceptance were not exercised.

Ruff's remaining inventory is recorded below; no broad lint fixes were applied.

```text
scripts/build_vector_index.py:39:1: E402 Module level import not at top of file
scripts/build_vector_index.py:40:1: E402 Module level import not at top of file
scripts/build_vector_index.py:41:1: E402 Module level import not at top of file
src/workbench/data_build/chunking.py:419:51: B905 `zip()` without an explicit `strict=` parameter
src/workbench/data_build/ingest_wikipedia.py:178:13: B007 Loop control variable `event` not used within loop body
src/workbench/retrieval/rerankers.py:94:27: B905 `zip()` without an explicit `strict=` parameter
src/workbench/tools/utility/calculator.py:147:31: B905 `zip()` without an explicit `strict=` parameter
tests/test_chat_multiturn.py:398:9: F841 Local variable `result` is assigned to but never used
tests/test_chunk_store.py:14:1: I001 [*] Import block is un-sorted or un-formatted
tests/test_chunking_deterministic.py:98:21: B905 `zip()` without an explicit `strict=` parameter
tests/test_chunking_deterministic.py:106:21: B905 `zip()` without an explicit `strict=` parameter
tests/test_chunking_deterministic.py:148:21: B905 `zip()` without an explicit `strict=` parameter
tests/test_chunking_deterministic.py:156:21: B905 `zip()` without an explicit `strict=` parameter
tests/test_hybrid_merge_rules.py:9:1: I001 [*] Import block is un-sorted or un-formatted
tests/test_ingest_fixture.py:197:29: B905 `zip()` without an explicit `strict=` parameter
tests/test_keyword_search_small.py:11:1: I001 [*] Import block is un-sorted or un-formatted
tests/test_language_model_unit.py:103:16: B011 Do not `assert False` (`python -O` removes these calls), raise `AssertionError()`
tests/test_language_model_unit.py:112:16: B011 Do not `assert False` (`python -O` removes these calls), raise `AssertionError()`
tests/test_language_model_unit.py:121:16: B011 Do not `assert False` (`python -O` removes these calls), raise `AssertionError()`
tests/test_language_model_unit.py:256:16: B011 Do not `assert False` (`python -O` removes these calls), raise `AssertionError()`
tests/test_language_model_unit.py:298:16: B011 Do not `assert False` (`python -O` removes these calls), raise `AssertionError()`
tests/test_language_model_unit.py:313:16: B011 Do not `assert False` (`python -O` removes these calls), raise `AssertionError()`
tests/test_metrics_writes.py:17:9: F841 Local variable `store` is assigned to but never used
tests/test_metrics_writes.py:26:9: F841 Local variable `store` is assigned to but never used
tests/test_registry_builds.py:186:5: F841 Local variable `test_registry` is assigned to but never used
tests/test_reranker_small.py:9:1: I001 [*] Import block is un-sorted or un-formatted
tests/test_researcher_graph_schema.py:446:9: F841 Local variable `opened_ids` is assigned to but never used
tests/test_tool_adapter.py:349:14: B017 Do not assert blind exception: `Exception`
tests/test_vector_search_small.py:121:23: B905 `zip()` without an explicit `strict=` parameter
tests/test_web_search.py:200:46: F401 `langchain_core.tools.BaseTool` imported but unused; consider using `importlib.util.find_spec` to test for availability
tests/test_web_search.py:211:46: F401 `langchain_core.tools.BaseTool` imported but unused; consider using `importlib.util.find_spec` to test for availability
Found 31 errors.
[*] 4 fixable with the `--fix` option (21 hidden fixes can be enabled with the `--unsafe-fixes` option).
```

## Existing asset identity (legacy provenance unverified)

The following bytes and SHA-256 digests were read locally during Stage 0.
Each file's size/mtime was checked unchanged across hashing. These identify
observed files; they do not verify publisher checksums, dump date, build inputs,
model licensing or embedding compatibility. Cache revision directory names are
observed identities, not reconstructed build provenance. Only model weight
files are hashed here, not the complete tokenizer/config snapshots. The Lance
manifest below is one observed metadata file, not a full index fingerprint or
an independently decoded table version. No index was opened for mutation or
rebuilt. The old alternative GGUF and all other assets remain untouched.

Cache paths are relative to `~/.cache/huggingface/hub/`.

| File | Bytes | SHA-256 |
|---|---:|---|
| `data/models/Ministral-3-8B-Instruct-2512-Q4_K_M.gguf` | 5198911904 | `33e7a72cf5e6e2cfc2f2847075acc013d68bba023e35310cef86b5cf8fdca761` |
| `data/raw/wikipedia/simplewiki-latest-pages-articles.xml.bz2` | 342917678 | `a2af6ce4c421c400f498740be8aefe77f71d52cc97fe50ed35ba28c27a3c9da6` |
| `data/processed/wikipedia_articles.parquet` | 170684868 | `d93341360aec70c197bd1ed42a6d0d42be0ae962cfd8e8f6739c8c22628900fa` |
| `data/processed/chunks.parquet` | 179851051 | `08cdbb6be2d2a4ff204936f8a518da6d430bd0d6a811f01f63360272feb6e1d9` |
| `data/processed/wikipedia_articles_manifest.yaml` | 312 | `8962405b89e554717941cb588e97ebe152e769ef3daa6c282f182c4e1c57bff3` |
| `data/processed/chunks_manifest.yaml` | 299 | `90669c5d2fbbed327e64e74d04b40db9b13bad14a33449f93b4e95fa550c8d83` |
| `models--intfloat--multilingual-e5-base/snapshots/835193815a3936a24a0ee7dc9e3d48c1fbb19c55/model.safetensors` | 1112201288 | `a18a44fad1d0b46ded15928144138cff1135d5cc8233bdd90be5f18822de09a7` |
| `models--BAAI--bge-reranker-v2-m3/snapshots/953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e/model.safetensors` | 2271071852 | `d9e3e081faff1eefb84019509b2f5558fd74c1a05a2c7db22f74174fcedb5286` |
| `data/indexes/active/lancedb/chunks.lance/_versions/18446744073709551614.manifest` | 955 | `fd4b6409a998acad23128056dc4c5f14a6c2fe55dbde38eba443a37ba34fec5f` |

The prior audit reports 430,910 chunks and index version 843; those are historical
observations, not counts revalidated by this hash inventory. Full bundle/config/
model manifests and safe snapshot activation remain Stage 4 work.

## Follow-up boundaries

Stage 1 removes online runtime capabilities and constrains endpoints/cache loads.
Stage 2 introduces typed research results and structured synthesis; see
[research and evidence](research-and-evidence.md) for the unresolved semantics.
Stage 5 fixes model-test tiering, the tracing skip and remaining Ruff inventory.
No current test result establishes factual accuracy or offline runtime operation.
