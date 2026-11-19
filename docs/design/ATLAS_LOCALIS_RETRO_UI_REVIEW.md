# Atlas Localis retro UI implementation review

18 November 2026. Implemented against Python 3.12.7 and Streamlit 1.54.0 in the existing `.venv`.

## Result and changed files

The presentation now uses locally bundled VT323 display lettering and IBM Plex Mono reading/control faces, the supplied transparent Atlas, rectangular green panels, restrained title glow, and static texture confined to the welcome artwork. The 1040 px main frame, native resizable sidebar, bottom chat input, and page scrolling remain. The native sidebar starts at 300 px; its resize/collapse behavior is preserved. The stacked welcome uses a 160 px image to keep the full figure and input visible on a laptop. Desktop artwork is 384 px; the research sidebar image is 160 px.

| Files | Change |
| --- | --- |
| `src/workbench/ui/branding.py` | Cached package-relative data URLs, graceful decorative-asset fallback, trusted header/welcome/sidebar markup. |
| `src/workbench/ui/theme.py` | Three local font faces, palette and typography tokens, native widget hooks, transcript surfaces, focus outlines, responsive composition, static effects and reduced-motion rules. |
| `src/workbench/ui/streamlit_app.py` | Replaceable presentation slots; immediate welcome removal on first submission; state computed after Clear chat; session disclosure; keyed transcript wrappers; full-width native sidebar buttons. |
| `src/workbench/ui/renderers.py` | Short numbered source summaries; full snapshot and chunk identifiers inside disclosures using inert selectable text. |
| `.streamlit/config.toml`, `pyproject.toml` | Matching font/color settings and packaged artwork/font/license patterns. Existing server, telemetry and package-data settings retained. |
| `src/workbench/ui/assets/` | Original supplied PNG retained unchanged; three unmodified font files and two OFL licenses added. |
| `tests/test_application.py`, `tests/test_ui_branding.py` | Welcome/conversation/Clear chat regression coverage, package-relative resource loading and missing-decoration error guidance. |
| `tests/fixtures/ui_preview.py` | Explicit browser-only model-free fixture using the existing graph/model/retriever fixtures, with delayed progress, long evidence and canonical outcome variants. |
| `docs/images/retro-ui/` | Screenshots from the local Chrome review. |

Research graphs, retrieval, canonical models, history construction, resource caching and progress failure isolation were not changed. Questions, answers, gaps, titles, sections and excerpts still use `st.text()`. Citation numbers and source bytes remain canonical.

## Checks run

All commands used the repository's existing Python environment:

```bash
.venv/bin/python -m ruff check src/workbench/ui tests/test_ui_imports.py tests/test_application.py tests/test_offline_runtime.py
.venv/bin/python -m pytest tests/test_ui_imports.py tests/test_application.py tests/test_offline_runtime.py
.venv/bin/python -m pytest tests/test_ui_imports.py tests/test_ui_branding.py tests/test_application.py tests/test_offline_runtime.py
.venv/bin/python -m ruff check src tests scripts
.venv/bin/python -m ruff format --check tests src/workbench/evaluation scripts/run_researcher_eval.py
.venv/bin/python scripts/run_researcher_eval.py --validate-only
.venv/bin/python -m pytest
.venv/bin/python -m pytest -o addopts='' --strict-markers -m fixture_integration -q
.venv/bin/python scripts/preflight.py --quick
.venv/bin/python -m pip check
git diff --check
```

The initial relevant suite passed 112 tests. The full default suite passed **767 tests**, with 40 provisioned acceptance tests deselected. The separate fixture-integration selection passed **99 tests**, with 708 deselected. Ruff lint/format, 24-case/rubric and five-report validation, model-free preflight, dependency checks and whitespace checks passed. The final focused suite passed **114 tests**, including the two new branding tests.

Built the final wheel and sdist with `setuptools.build_meta.build_wheel()` / `build_sdist()` into `/tmp/atlas-retro-review/final-dist`. Inspected both archives and compared all six artwork/font/license files byte-for-byte with the checkout. Extracted the wheel and imported `workbench.ui.branding` and `theme` from it with `/tmp` as the working directory: artwork and all three font faces loaded successfully.

The supplied master remains 1199 × 1312 RGBA. Its SHA-256 is `121839b222f18b8ff68f35db43f867caacda04099ecb4fd8a9f2baad14d819fb`. No derivative or generated replacement was used. Font/license bytes were obtained from the official [VT323 distribution](https://github.com/google/fonts/tree/main/ofl/vt323) and [IBM Plex Mono distribution](https://github.com/google/fonts/tree/main/ofl/ibmplexmono).

## Browser review

Ran the fixture on loopback, using installed Google Chrome controlled by Playwright Core in an isolated headless profile:

```bash
.venv/bin/python -m streamlit run tests/fixtures/ui_preview.py --server.port 8539 --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false
node /tmp/atlas-retro-review/review.cjs
node /tmp/atlas-retro-review/final.cjs
node /tmp/atlas-retro-review/accessibility.cjs
```

The temporary browser drivers recorded DOM/layout/font assertions and screenshots. The retained Python fixture can be rerun with `?long=1`, `?delay=2`, or `?mode=partial`, `failed`, `insufficient_evidence`, `cancelled`, or `missing`. Its larger prompt budget permits a multi-paragraph fixture answer; production limits are unchanged.

- Visually inspected welcome, active research, short and long answers, expanded evidence, missing-resource guidance, and mobile sidebar states.
- Checked 1440 × 900, 1024 × 768 and 390 × 844. No document horizontal overflow or overflowing native text blocks was measured. Long snapshot/chunk identifiers wrap; excerpts remain selectable and use normal page scrolling.
- Checked 200% text zoom by changing the root text size from 16 to 32 px. Expanded evidence and identifiers wrap, controls remain accessible through scrolling, and the native sidebar toggle remains available. This was a text-zoom check, not Chrome's browser-menu zoom.
- Observed the first run before completion: zero hero images, one compact heading and the sidebar Atlas. Clear chat restored one welcome, one heading, one hero, zero sidebar images and zero messages.
- Chrome's `CSS.getPlatformFontsForNode` reported custom **VT323-Regular** for the wordmark, **IBMPlexMono-Regular** for real `st.text()` answers and expanded source text, and **IBMPlexMono-Medium** for button labels.
- No external browser resource requests or page errors were recorded. Images and fonts use packaged data URLs.
- Native keyboard traversal showed 2 px green outlines with 3 px offsets on controls/disclosures. The textarea remains enabled and in the tab order; Tab from it reaches Send. Source disclosures open with Enter, and source text can be selected.
- Reduced-motion emulation removes glow and texture and disables button transitions.
- Actual fixture results displayed their distinct `complete`, `partial`, `failed`, `insufficient_evidence` and `cancelled` outcomes. Missing-resource guidance retained the existing setup commands and omitted the question input.

## Screenshots

All research content below is model-free fixture output, not a new historical research/model-quality claim.

| State | Capture |
| --- | --- |
| Welcome, 1440 × 900 | [Welcome](../images/retro-ui/welcome-1440.png) |
| Active first run, 1440 × 900 | [Active research](../images/retro-ui/active-1440.png) |
| Populated transcript, 1440 × 900 | [Research](../images/retro-ui/research-1440.png) |
| Multi-paragraph reading view | [Long answer](../images/retro-ui/research-long-1440.png) |
| Expanded local evidence | [Evidence](../images/retro-ui/evidence-1440.png) |
| Welcome, 1024 × 768 | [Laptop](../images/retro-ui/welcome-1024.png) |
| Welcome, 390 × 844 | [Mobile](../images/retro-ui/welcome-390.png) |
| Expanded evidence, 390 × 844 | [Mobile source](../images/retro-ui/evidence-390.png) |
| Long identifiers, 390 × 844 | [Identifiers](../images/retro-ui/identifiers-390.png) |
| Native mobile sidebar | [Sidebar](../images/retro-ui/sidebar-390.png) |
| Identifiers, 200% text zoom | [Zoom](../images/retro-ui/evidence-text-zoom-200.png) |
| Source prose, 200% text zoom | [Zoomed source](../images/retro-ui/source-text-zoom-200.png) |
| Keyboard focus | [Focus](../images/retro-ui/keyboard-focus-1440.png) |
| Missing local resources | [Error guidance](../images/retro-ui/missing-resources-1440.png) |

## Material limitations

**Existing Cancel behavior:** clicking Cancel during a delayed run interrupts the Streamlit execution and leaves the user question without appending a final cancelled assistant message. Reproduced with the original `HEAD` shell and the identical fixture on loopback port 8540, as well as with the redesigned shell. Canonical cancelled results render correctly when returned by the runner. The existing event/stream/append lifecycle has been preserved as required by the design brief; fixing this pre-existing interaction needs a separate lifecycle change.

No provisioned local-model acceptance suite, real-model research, hosted CI, or full accessibility audit was performed in this redesign pass. Browser screenshots and interaction checks used injected model-free resources. Existing unrelated README edits and the supplied design brief were preserved.
