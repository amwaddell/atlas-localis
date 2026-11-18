# Local data lifecycle

Only `scripts/setup.sh` and explicit `scripts/update_wikipedia.sh update` acquire
assets. The old moving-`latest` downloader now exits with a setup/update instruction.
UI, native model launchers, application construction and preflight never
install packages or download missing data/models. All paths below are local.

```bash
# CPython 3.12, macOS arm64, Xcode CLI tools and native llama.cpp
# build 8130 / ed4837891 must already be available on PATH.
./scripts/setup.sh --check-only
./scripts/setup.sh
./scripts/update_wikipedia.sh status
./scripts/update_wikipedia.sh update
```

Setup creates `.atlas-venv`, preserving the existing `.venv`. Launchers prefer
`.atlas-venv` when present; `ATLAS_PYTHON` overrides that choice. An unowned venv,
a symlink, the invoking environment, or an environment with a different setup
receipt/lock is refused. Choose a new `--venv` path rather than deleting an old
one. A custom venv requires `ATLAS_PYTHON=/absolute/path/bin/python` for launch.
Setup does not run brew, uninstall packages, or change global/Conda environments.
Its source interpreter may be Conda-packaged CPython 3.12; installation occurs
only in the dedicated new venv. Interrupted matching installs can be rerun.

The host check requires macOS arm64, Python 3.12, `xcode-select`, `xcrun clang`,
the validated native llama.cpp commit and a 40 GiB free-space reserve. This is a
conservative refusal threshold, **not a measured full-corpus disk/memory bound**.
Update requires another 20 GiB free beside retained data. Native toolchain
installation/build is currently a prerequisite, not automated provisioning.
No project license has been selected.

## Measured full setup

The isolated 20261001 full-corpus run completed with 283,622 articles and 447,218
chunks. Its manual resumed invocation took 6h 27m; the retained bundle occupies
about 12.49 GiB, and models/environment plus the isolated data tree about
22.17 GiB. The writer currently commits 32-row shards individually, creating
8.70 GB of Lance manifests and very slow assembly. Allow hours for this build;
a tiny smoke is not a timing proxy. Progress is recorded in
`.build-*/progress.json`; completion requires `Setup complete`, not merely the
end of embedding. Do not interrupt an active build to launch a competing copy.
The full run's peak unified memory was not measured. See
[testing and limitations](testing.md) for the current verification boundaries. No automatic pruning is performed.

## Artifact identities

`pyproject.toml` owns dependency declarations and distribution name
`atlas-localis`; imports remain `workbench`. `requirements*.txt` are installation
shims. `locks/*-macos-arm64-py312.txt` record exact transitive versions for runtime,
development and optional Phoenix. `scripts/generate_locks.py` computes closures
from an explicitly validated environment; it is not called by setup/update.
Locks pin versions and platform, not wheel hashes. Update never installs Python
dependencies or downloads/upgrades models.

`configs/artifacts/models.json` pins the complete locally used E5/BGE config,
tokenizer and safetensors files and the existing Ministral-3 Q4_K_M GGUF. Each
file has SHA-256, size, immutable revision URL and verification origin. Model
cards at those revisions are license references. Cached E5/BGE files were checked
against publisher Git blob or LFS hashes before recording SHA-256; GGUF's
publisher LFS hash matches existing local bytes. Setup copies verified cache
files to `data/models/<repo-with-->/<revision>` or downloads missing files;
it never mutates the owner's HF cache. Normal runtime prefers these provisioned
paths, with the historical cached-only loader retained for existing installations.

`configs/artifacts/wikipedia.json` pins Simple English Wikipedia **20261001**:
358,258,425 bytes, SHA-256
`2db94ff149adbbaf88efecb8f510a549eaa2a82d59df6278a4af88e13c1b99bd`.
This digest was computed after matching publisher dumpstatus size and SHA-1
`f55881650e447c39e8bdeba4b1bb8d5576342d4b`. The legacy `latest` file has
**unknown dump date** and is not relabelled. Refreshing to another date requires
an explicit reviewed `--dump-manifest PATH` with dated URL, identity, size,
SHA-256 and license reference; no moving `latest` discovery occurs.

Downloads use `.part` plus source metadata. Matching pinned spec and unchanged
strong ETag allow `Range`/`If-Range` resume. Status, exact range, ETag, final size
and hash are checked. A server ignoring ranges causes a restart of that owned
partial file. Missing/weak ETags cause restart. Unmatched partial sources,
corrupt completed files and invalid range responses fail and preserve evidence.
No filename is silently reused for another source. Online transport uses HTTPS.

## Build and activate

```text
data/indexes/
  .build-<fingerprint>/           # resumable, never used by normal runtime
    inputs.json, ingest.json, chunk.json
    articles.parquet, chunks.parquet
    vectors/<batch>.parquet, <batch>.json
    lancedb/
  snapshots/<fingerprint>/        # sealed after validation; not edited in place
    manifest.json + all matching artifacts above
  active -> snapshots/<fingerprint>
  citations/<pruned-snapshot>/    # article/chunk archives retained for old runs
  .build.lock, .lifecycle.lock, promotion.json, conversion.json, aliases.json
```

Input fingerprints include dump identity/hash, embedding revision/dimension/
prefix/normalization policy, paragraph settings, batch size, builder code and
runtime lock. Stable paragraph chunk IDs and existing chunking semantics remain:
character units, target 1000, max setting 1500, minimum 100; long paragraphs are
preserved. XML ingestion and paragraph construction reuse the existing builders
and still materialize their column buffers. Embeddings are checkpointed in
batches of 32 (internal E5 encode batches of four); only committed verified vector
shards are reused. An interrupted uncommitted batch is replayed. Corrupt
checkpoints fail rather than silently being accepted. LanceDB assembly can be
repeated from shards without re-embedding; it replaces only its owned staging
DB. Vector shards are retained, so allow disk for both shards and the DB.

Before sealing: nonempty/unique article IDs, chunk parent membership, unique
chunk IDs, exact index/chunk ID sets and text/metadata agreement, normalized finite
float32 fixed-size vectors of the declared dimension, real FTS and vector
queries, and a canonical application question with a cited complete/partial
result. Production smoke uses the selected local E5/BGE and a temporary owned
native server on a numeric loopback port. It reaps only that process group, never
an unrelated listener. Setup/update do not require an existing model server.
Failure retains the staged build and active data. Setup reports the failing stage.
A passing smoke establishes executable/citation integrity, not factual accuracy.
Structured planner, findings (including repair), and synthesis calls send the
Pydantic JSON schema to the pinned native server via `response_format`. Local
strict JSON parsing, evidence alias membership, excerpt provenance, date matching
and citation validation still run; fenced JSON is rejected without stripping.

After validation, the manifest hashes every committed bundle file. The stage is
renamed into `snapshots` on the same filesystem. Activation verifies the inventory,
journals intent and atomically replaces `active` with a relative symlink using
`os.replace`; the parent directory is fsynced. Shared application leases block
conversion/prune/promotion while resources are live; close the UI/CLI application
before switching. A pending promotion journal plus the actual symlink tells which
side of an interruption took effect. Retry a named activation or rollback.
The application resolves/pins paths and uses manifest snapshot IDs in new runs.

## Existing real active directory

Inspection on 18 October found `data/indexes/active` is a real directory with
`lancedb/chunks.lance`; articles/chunks are separate in `data/processed`. It was
**not converted by this implementation session**. Ordinary activation refuses
to remove/replace it. Conversion is explicit and requires stopping all old
applications (older processes do not hold the new leases):

```bash
./scripts/update_wikipedia.sh convert --app-stopped
```

Conversion writes a journal, renames the whole directory to
`snapshots/legacy-preserved`, copies matching source parquets without removing the
originals, inventories observed bytes with `legacy-unknown` provenance and creates
the symlink. A crash between rename and symlink leaves a recoverable gap, not
lost data: rerun the same command. Do not start the app while conversion is pending.
No publisher dump date, model revision or successful canonical smoke is invented
for the legacy index. A Stage 2/3 legacy snapshot-ID alias preserves access from
saved runs. The original index bytes stay in their renamed directory.

## Status, rollback, retention

```bash
./scripts/update_wikipedia.sh status
./scripts/update_wikipedia.sh verify --snapshot NAME
./scripts/update_wikipedia.sh activate --snapshot NAME
./scripts/update_wikipedia.sh rollback --snapshot NAME
./scripts/update_wikipedia.sh prune --snapshot NAME --confirm
```

Rollback verifies a retained target; explicit rollback may select the unknown
legacy bundle. Prune refuses active data and unsafe names/symlinks. It first
retains verified articles/chunks as a citation archive, then removes only the
named inactive bundle's index/checkpoints. Nothing is automatically pruned.
`citation_store(indexes, snapshot_id)` resolves retained bundles, legacy aliases
and archives for old-run chunk access. Saved canonical results already include
excerpt text. Citation archives themselves have no deletion command. A pruned
index cannot be rolled back without rebuilding, but its citations remain local.
Do not manually edit sealed bundles or archive hard links.

## Attribution and license boundary

- [E5 model card (MIT)](https://huggingface.co/intfloat/multilingual-e5-base)
- [BGE reranker model card (Apache 2.0)](https://huggingface.co/BAAI/bge-reranker-v2-m3)
- [Official Ministral GGUF model card (Apache 2.0)](https://huggingface.co/mistralai/Ministral-3-8B-Instruct-2512-GGUF)
- [llama.cpp license](https://github.com/ggml-org/llama.cpp/blob/master/LICENSE)
- [Wikimedia dump licensing](https://dumps.wikimedia.org/legal.html)
- [Wikimedia terms](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use)

Wikipedia text requires attribution and applicable share-alike compliance; source
article titles, page IDs, dump identity and transformation settings are retained.
Do not treat a model/data license as the repository's license. Distribution of
source, derived data or model artifacts requires reviewing their respective terms.
