# Atlas Localis developer notes

Stage 0 baseline, 18 October 2026. The Python package remains `workbench`.

Start with [research and evidence](research-and-evidence.md) for the current
citation contract and its limitations, then [development](development.md) for
repeatable checks, preservation records and outstanding failures.

The intended application is one Apple Silicon historical research workflow:
supervisor → bounded tasks → local researcher workers → cited synthesis.
That architecture is a migration target. Today the application still exposes
RAG, researcher and supervisor workflows; the supervisor's decomposition path
fans out into RAG workers. Web fallback, configurable remote endpoints and
potential model-cache downloads remain. **The runtime is not yet offline.**

The [migration plan](../migration.md) specifies future stages and acceptance
criteria. The [status audit](../PROJECT_STATUS_AUDIT.md) records earlier
machine-specific observations, including real inference, rather than current
setup instructions. Both records are preserved unchanged in Stage 0.

Currently verified developer commands use the existing `.venv` and are listed
in development.md. Planned setup/update commands, immutable snapshots, shared
execution and final structured evidence validation have not shipped. No clean
installation or hardware resource envelope is established by this baseline.
