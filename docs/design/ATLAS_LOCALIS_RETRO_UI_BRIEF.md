# Atlas Localis: retro terminal UI implementation brief

**For:** Codex in VS Code, working in `amwaddell/atlas-localis`  
**Prepared:** 18 November 2026  
**Repository inspected:** `main` at `1d68cffa55adf5b386cf6f5054f12f079d3a132d`  
**Deliverable:** a design and implementation brief; the application has not been modified by this document.

## 1. The intended result

Give Atlas Localis the character of a late-1980s green-phosphor research terminal: near-black backgrounds, pale green reading text, distinctive old terminal headings, thin rectangular borders, and the supplied pixel-art Atlas carrying the Earth. The result should feel quiet, scholarly, and deliberate. Long research answers and their evidence must remain comfortable to read.

The owner has approved these decisions:

- **Effects:** subtle static texture and restrained green glow.
- **Atlas placement:** large on the welcome screen; smaller in the sidebar once research begins.
- **Layout:** refine the existing sidebar and chat layout while preserving the familiar workflow.

Use the accompanying concept image as a visual direction. The exact fonts, measurements, behavior, and source-rendering rules in this document take precedence over details in the generated concept. The original uploaded PNG is the production artwork; do not extract or regenerate the figure from the concept image.

### Visual priorities, in order

1. Make the typography distinctive and readable.
2. Give the supplied Atlas a clear place in both welcome and conversation states.
3. Improve spacing, hierarchy, and the placement of session metadata.
4. Make controls, source disclosures, and message panels feel like one terminal interface.
5. Add the faint texture and title glow after the above are working.

## 2. Repository facts and scope

This is an existing **Python 3.12 / Streamlit 1.54.0** application. Its UI is already green on black. The improvement should come from composition, typography, artwork, and more intentional component styling. Keep the implementation in the current Streamlit presentation layer.

| File | Existing responsibility | Intended work |
| --- | --- | --- |
| `src/workbench/ui/theme.py` | `apply_theme()` injects CSS with `st.html()` | Define the palette, load local font faces, centralize typography, spacing, widget styling, and restrained effects. |
| `src/workbench/ui/streamlit_app.py` | `main()` owns the shell, sidebar, welcome, transcript, input, and progress presentation | Add the two Atlas treatments and replaceable header/welcome/sidebar slots; refine the shell without changing the research execution flow. |
| `src/workbench/ui/renderers.py` | `render_result()` renders canonical results and citations | Add narrowly scoped layout wrappers if useful; preserve inert text output and complete provenance. |
| `.streamlit/config.toml` | Base theme, local server binding, telemetry settings | Align the base theme with the new fonts and colors; retain the existing runtime settings. |
| `pyproject.toml` | Dependency pins and package data | Include the new artwork, font files, and license files in packaged distributions. |
| `src/workbench/ui/branding.py` | Proposed small new module | Keep trusted brand markup, package-relative artwork loading, and reusable header/Atlas rendering out of `main()`. |
| `src/workbench/ui/assets/` | Proposed new data directory | Store the supplied PNG, optional pixel-size derivatives, and local fonts/licenses. |

Read `chat_state.py`, `components.py`, and `graph_runner.py` before editing the shell so their existing contracts stay intact. The redesign does not require changes to retrieval, research graphs, model configuration, canonical result models, or resource caching.

Source inspection is anchored to the commit above. Before making changes, read the current checkout and any applicable `AGENTS.md`; adapt names if the repository has moved since this review. Source links are collected in section 16.

## 3. Screen composition

### 3.1 Shared frame

Keep the native Streamlit sidebar on the left and the research area on the right. Preserve native sidebar collapse behavior and the existing bottom chat input.

At a desktop viewport around 1440 pixels wide, use these starting proportions:

- Sidebar: approximately **260–290 px**, subject to native resizing and available space.
- Main content: keep the existing **1040 px maximum width** as the starting point.
- Main horizontal padding: **32–48 px** on roomy screens, **16–24 px** on narrower screens.
- Main top padding: approximately **32–48 px of visible breathing room**, allowing for the native Streamlit header. The current `5rem` rule should be reviewed rather than copied unchanged.
- Component spacing: an **8 px rhythm**, mainly 8, 16, 24, and 32 px.
- Corners: **0–2 px**, with consistent thin borders.

Avoid a separate scroll box inside each answer. The conversation should read naturally down the page. Keep long evidence readable and let the page handle normal scrolling.

### 3.2 Welcome state

The welcome should look like an inviting terminal awaiting a question.

At the top of the main area:

1. Small kicker: `LOCAL RESEARCH TERMINAL / WIKIPEDIA`.
2. Large wordmark: `ATLAS LOCALIS`.
3. Existing tagline: `Historical research. Local evidence. Traceable answers.`

Below this, use a balanced two-column welcome composition:

| Left side | Right side |
| --- | --- |
| `READY FOR A QUESTION` in the display font | The supplied Atlas, large and fully visible |
| Short invitation to explore an event or trace a chronology | Transparent background blending into the application |
| One example question, such as the existing Roman Republic example | Enough dark space around the globe and body to make the silhouette legible |

Use approximately **55% text / 45% artwork** within the main welcome region. Target an artwork box of roughly **320–384 px wide**, preserving its aspect ratio. The original file includes transparent space, so assess the visible figure rather than treating the entire PNG rectangle as the figure's width.

Keep the input visually close enough to the invitation that the next action is clear. Do not force a full-screen hero height that pushes the question field out of view on a laptop.

Render the example as clearly styled example text in the first pass. If it is made into a real button later, it must use the same submission path as the chat input; a decorative HTML element must not look interactive without doing anything.

### 3.3 Research state

After the first question is submitted:

- Remove the large welcome illustration immediately.
- Reduce the main wordmark to **36–44 px**.
- Show the compact Atlas in the sidebar, approximately **128–160 px wide** and **140–176 px high**, preserving the image's proportions.
- Give the answer, its sources, and the next question field priority in the main column.

Treat the user question as a quieter panel and the assistant answer as the primary reading surface. Use a slim left rule or a clear role label to distinguish them. Keep their widths aligned; this is a research transcript, not a collection of floating speech bubbles.

Keep the existing expandable **Research tasks** and numbered **Local source** controls. Their borders, chevrons, spacing, and font should match the rest of the application.

### 3.4 Sidebar hierarchy

Recommended order:

1. `ATLAS / SESSION` label.
2. Compact Atlas when a transcript exists; a compact text brand in the empty state.
3. `Local research` and the existing source/inference description.
4. A `Session details` disclosure containing the full snapshot identifier.
5. The existing research controls.
6. The current numbered-source guidance and provenance caveat, separated by a thin rule.

Moving a long snapshot identifier into a disclosure reduces visual noise while preserving access to the complete value. Keep it selectable and allow long identifiers to wrap. Preserve the current cancellation behavior; this styling task is not a reason to redesign its lifecycle.

## 4. Typography

### Recommended pair

Use **VT323 Regular** for the distinctive display treatment and **IBM Plex Mono Regular / Medium** for reading and controls.

VT323 is based on the glyphs of the DEC VT320 terminal. That is a particularly good match for this brief. IBM Plex Mono is the complementary reading face: use it where users need to compare dates, read paragraphs, inspect identifiers, and follow citations. This pairing is a design recommendation, not a requirement imposed by Streamlit.

| Element | Typeface | Starting size / line height | Weight |
| --- | --- | --- | --- |
| Welcome wordmark | VT323 | 56–64 px / 1.0 | 400 |
| Conversation wordmark | VT323 | 36–44 px / 1.05 | 400 |
| Welcome/section heading | VT323 | 24–32 px / 1.1 | 400 |
| Short terminal labels | IBM Plex Mono | 12–13 px / 1.4 | 500 |
| Research answer | IBM Plex Mono | 16–17 px / 1.65 | 400 |
| Source excerpts | IBM Plex Mono | 15–16 px / 1.6 | 400 |
| Metadata and guidance | IBM Plex Mono | 13–14 px / 1.5 | 400 |
| Buttons and disclosure summaries | IBM Plex Mono | 14–16 px / 1.4 | 500 |
| Question input | IBM Plex Mono | 16 px / 1.5 | 400 |

Use uppercase for the wordmark and short terminal labels. Keep research prose in its original case. Limit tracking to short labels, around `0.08em–0.12em`; do not add tracking to paragraphs or source excerpts.

Remove the current `h1` tracking of `-.045em`. VT323 needs its own spacing, starting at `0` to `0.02em`. Avoid simulated bold on VT323; its size and color should establish hierarchy.

Do not force the display font onto long answers. Do not apply a universal `* { font-family: ... }` rule: it can replace icon fonts and break native control symbols. Style known text regions and preserve icon rendering.

Official acquisition sources:

- [VT323 project](https://github.com/phoikoi/VT323).
- [VT323 font and license distribution](https://github.com/google/fonts/tree/main/ofl/vt323): `VT323-Regular.ttf`, `OFL.txt`.
- [IBM Plex project](https://github.com/IBM/plex).
- [IBM Plex Mono font and license distribution](https://github.com/google/fonts/tree/main/ofl/ibmplexmono): `IBMPlexMono-Regular.ttf`, `IBMPlexMono-Medium.ttf`, `OFL.txt`.

Bundle the unmodified files and their corresponding licenses. The browser should load fonts from the installed application, with no Google Fonts stylesheet or CDN request at runtime. TTF is sufficient for this local application; WOFF2 conversion is optional. The proposed faces cover weights 400 and 500. Explicitly use those weights in native controls, or bundle the appropriate additional face if the implementation needs true bold or italic text.

## 5. Color and component tokens

Keep the green family already present in the application, but assign each shade a clear role.

| Token | Value | Use |
| --- | --- | --- |
| `--atlas-bg` | `#070B09` | Main background |
| `--atlas-panel` | `#0C130E` | Sidebar and reading panels |
| `--atlas-raised` | `#101A13` | Controls and slightly raised surfaces |
| `--atlas-text` | `#C8E8CE` | Primary reading text |
| `--atlas-muted` | `#90B99A` | Metadata, supporting labels, guidance |
| `--atlas-accent` | `#7DFF9B` | Wordmark, focus, selection accents |
| `--atlas-rule` | `#294532` | Decorative separators and panel rules |
| `--atlas-control-border` | `#548D62` | Interactive boundaries that need to be easy to see |

The faint decorative rule is not the sole indicator of an actionable control. Use the stronger control border, a clear label, and a visible focus outline for input fields and buttons.

For opaque foregrounds on `#0C130E`, calculated contrast ratios are approximately **14.25:1** for primary text, **8.60:1** for muted text, and **4.81:1** for the control border. These are color-pair calculations, not a claim that the finished application has been accessibility-audited. Recheck the actual rendered result if opacity, backgrounds, or effects change.

Normal text remains green. Existing warnings and errors may retain restrained semantic accents, accompanied by explicit text; their meaning must not depend on color alone.

## 6. Atlas artwork: preserve the supplied asset

The supplied file is `Pixel Atlas Holding the Earth(1).png`, an **1199 × 1312 RGBA PNG with transparency**.

Copy it into the repository as:

`src/workbench/ui/assets/atlas-green.png`

Retain this master unchanged. Keep the globe, arms, kneeling pose, and full silhouette visible. Avoid a black rectangular mat, circular avatar crop, recoloring, hue filters, or a substitute illustration.

### Pixel treatment

Start with the provided artwork and inspect it at the intended on-screen sizes. Apply pixel-preserving image rendering:

```css
.atlas-sprite {
  display: block;
  height: auto;
  max-width: 100%;
  object-fit: contain;
  image-rendering: crisp-edges;
  image-rendering: pixelated;
}
```

CSS `image-rendering` does not turn every high-resolution source into a deliberately coarse pixel grid. If the figure becomes too finely detailed when reduced, prepare separate lower-resolution derivatives from the original and inspect their faces, fingers, and globe before choosing them. A useful starting experiment is a **192 px logical-width hero sprite shown at 384 px**, and an **80 px logical-width sidebar sprite shown at 160 px**. Preserve aspect ratio and alpha, and use nearest-neighbor sampling for the pixel-art treatment. These are asset-preparation suggestions, not a requirement to add image processing to every application rerun.

Keep any derived images alongside the master under clearly named files. Never overwrite the master or add a runtime image-generation dependency. Check narrower screens at a smaller whole-pixel scale where possible. Do not crop the art merely to fill a fixed-height box.

The welcome illustration may have descriptive alt text such as `Pixel-art Atlas holding the Earth`. If the sidebar repeats the same art next to an equivalent visible brand label, it can be decorative with empty alt text. The illustration should not capture clicks, receive focus, or obstruct controls.

## 7. Texture, glow, and geometry

Use flat surfaces and fine rules. The period feel should be apparent even if every effect is disabled.

- Put a very faint, static horizontal texture behind the welcome artwork or exposed background space. Start with green at **1–2% alpha**, one line every **4 px**.
- Keep reading panels opaque so texture does not run across research paragraphs and evidence.
- Apply a small text shadow only to the wordmark or a short decorative heading, for example `0 0 8px rgb(125 255 155 / 12%)`.
- Keep body text, citations, input text, and source excerpts sharp and free of glow.
- Use a clear `2px` keyboard focus outline with a `3px` offset.
- Hover may brighten the border and slightly lift the background. A short color transition is sufficient.
- Keep the display static. Continuous flicker, moving scanlines, distorted text, and boot sequences are outside the approved direction.

A background texture should be a background or a noninteractive decoration. Do not lay a fixed translucent sheet across the whole application: it would also affect text, dialogs, selection, and interaction.

## 8. Local assets and package integration

Use one small, explicit asset structure:

| Repository path | Contents |
| --- | --- |
| `src/workbench/ui/assets/atlas-green.png` | Original supplied PNG |
| `src/workbench/ui/assets/atlas-hero-pixel.png` | Optional reviewed hero derivative |
| `src/workbench/ui/assets/atlas-sidebar-pixel.png` | Optional reviewed sidebar derivative |
| `src/workbench/ui/assets/fonts/VT323-Regular.ttf` | Display font |
| `src/workbench/ui/assets/fonts/IBMPlexMono-Regular.ttf` | Reading face |
| `src/workbench/ui/assets/fonts/IBMPlexMono-Medium.ttf` | Controls and selected labels |
| `src/workbench/ui/assets/fonts/OFL-VT323.txt` | Unmodified VT323 license contents |
| `src/workbench/ui/assets/fonts/OFL-IBMPlexMono.txt` | Unmodified IBM Plex license contents |

### Recommended loading route

For this small set of assets, read bundled bytes through `importlib.resources` and embed them as data URLs in trusted shell markup and `@font-face` declarations. This works with the existing `st.html()` styling approach and does not require enabling a new static directory.

Illustrative helper for developer-controlled asset names:

```python
from base64 import b64encode
from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=16)
def bundled_data_uri(relative_name: str, media_type: str) -> str:
    asset = files("workbench.ui").joinpath("assets", relative_name)
    encoded = b64encode(asset.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"
```

Call this only with fixed application-owned names, such as `fonts/VT323-Regular.ttf` with `font/ttf`, or `atlas-green.png` with `image/png`. Do not use question text, source titles, retrieved paths, or model output to construct an asset path or HTML fragment.

Build three `@font-face` blocks in `apply_theme()` or a helper called from it, using the corresponding data URL, `format("truetype")`, `font-style: normal`, `font-display: swap`, and weights 400/400/500 respectively. Declare families exactly as `VT323` and `IBM Plex Mono`.

If a decorative asset is missing in a development checkout, preserve a usable system-monospace/text-brand fallback and emit a concise developer diagnostic. A missing font should not replace the existing, actionable missing-model/index guidance with a raw file exception.

Extend the existing package-data table; keep its current entries:

```toml
[tool.setuptools.package-data]
"workbench.evaluation" = ["data/*.json"]
"workbench.prompting" = ["templates/*.txt"]
"workbench.ui" = ["assets/*.png", "assets/fonts/*.ttf", "assets/fonts/*.txt"]
```

If the chosen deliverable includes WOFF2 files or a separate CSS file, add those actual file patterns too. Inspect the built distribution so an installed copy can still find its fonts and artwork.

### Base theme

Merge the following design values into `.streamlit/config.toml`, keeping its existing server and browser sections:

```toml
[theme]
base = "dark"
primaryColor = "#7DFF9B"
backgroundColor = "#070B09"
secondaryBackgroundColor = "#101A13"
textColor = "#C8E8CE"
font = "IBM Plex Mono, monospace"
headingFont = "VT323, monospace"
headingFontWeights = 400
codeFont = "IBM Plex Mono, monospace"
baseFontSize = 16
baseFontWeight = 400

[theme.sidebar]
headingFontWeights = 400
```

These family names must match the injected `@font-face` declarations. The explicit sidebar heading weight prevents a native sidebar heading from synthesizing a heavier VT323 treatment; the main heading-weight setting is not inherited there in the pinned release. Retain `address = "127.0.0.1"`, `headless = true`, and `gatherUsageStats = false`.

Streamlit 1.54 also supports native `theme.fontFaces` with local static URLs. That is a viable alternative if the current checkout already uses it. Do not mix two competing font-loading systems. With that alternative, verify the app-relative static directory, the real `app/static/...` route, package inclusion, and local-only requests. A bare `/static/font.ttf` URL is not a complete implementation.

## 9. CSS structure and stable hooks

Centralize the stylesheet in `theme.py`. Start with CSS variables for the color and font roles. Use developer-owned `atlas-*` classes for static brand markup and keyed Streamlit containers for native regions.

Streamlit 1.54 documents that `st.container(key="atlas_answer")` receives an `st-key-atlas_answer` class. Use that mechanism where a scoped native container is needed. Repeated message containers need unique keys derived from their transcript position or other stable application-owned identity.

Example token foundation:

```css
:root {
  color-scheme: dark;
  --atlas-bg: #070b09;
  --atlas-panel: #0c130e;
  --atlas-raised: #101a13;
  --atlas-text: #c8e8ce;
  --atlas-muted: #90b99a;
  --atlas-accent: #7dff9b;
  --atlas-rule: #294532;
  --atlas-control-border: #548d62;
  --atlas-font-display: "VT323", monospace;
  --atlas-font-body: "IBM Plex Mono", ui-monospace, SFMono-Regular,
    Menlo, Consolas, monospace;
}

.atlas-wordmark {
  font-family: var(--atlas-font-display);
  font-weight: 400;
  font-size: clamp(2.75rem, 4.5vw, 4rem);
  line-height: 1;
  letter-spacing: 0.01em;
  color: var(--atlas-accent);
  text-shadow: 0 0 8px rgb(125 255 155 / 12%);
}

.atlas-wordmark--compact {
  font-size: clamp(2.25rem, 3vw, 2.75rem);
}

.atlas-kicker {
  font-family: var(--atlas-font-body);
  font-size: 0.75rem;
  font-weight: 500;
  line-height: 1.4;
  letter-spacing: 0.1em;
  color: var(--atlas-muted);
}

.atlas-welcome {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr);
  align-items: center;
  gap: 2rem;
  padding-block: 2rem;
  border-top: 1px solid var(--atlas-rule);
}

.atlas-art {
  display: grid;
  place-items: center;
  background-image: repeating-linear-gradient(
    to bottom,
    rgb(125 255 155 / 1.5%) 0 1px,
    transparent 1px 4px
  );
}

.atlas-sprite--hero { width: 384px; }
.atlas-sprite--sidebar { width: 160px; margin-inline: auto; }

@media (max-width: 1000px) {
  .atlas-welcome { grid-template-columns: 1fr; gap: 1.5rem; }
  .atlas-sprite--hero { width: 192px; }
}

@media (prefers-reduced-motion: reduce) {
  .atlas-wordmark { text-shadow: none; }
  .atlas-art { background-image: none; }
}
```

This is a starting stylesheet, not a complete patch. The welcome grid applies to one trusted HTML fragment. If it is built with native `st.columns()` instead, adapt the layout to those actual wrappers; do not assume an HTML opening tag in one `st.html()` call can wrap unrelated Streamlit widgets rendered by later calls.

The 1000 px breakpoint is a starting point. Verify available main-column width with the sidebar open, closed, and resized. Use an appropriate container query if the viewport rule proves too coarse. At small widths, readable text and a working input take priority over a large decorative image.

### Native text and widgets

The pinned Streamlit 1.54 implementation renders `st.text` with an outer `data-testid="stText"` and an inner styled `span`; it is not simply a `pre`. Inspect the actual rendered DOM before finalizing selectors. A scoped rule can target the inner text span to set the reading font, line height, and wrapping, without changing the text renderer.

For example, if a unique keyed answer wrapper is `atlas_answer_0`:

```css
.st-key-atlas_answer_0 [data-testid="stText"] > span {
  font-family: var(--atlas-font-body);
  font-size: 1rem;
  line-height: 1.65;
  color: var(--atlas-text);
  overflow-wrap: anywhere;
}
```

For the real implementation, use a documented wrapper convention and a narrowly scoped shared selector covering all relevant messages. Do not hard-code a single transcript index as the final styling solution. Preserve native whitespace behavior, or deliberately use `white-space: pre-wrap` if exact source spacing needs preserving and has been checked.

The repository already uses test-ID selectors for chat messages, sidebar, input, captions, and expanders. Keep those overrides small and grouped. Treat test IDs as pinned-version implementation hooks, and avoid generated Emotion class names or positional selectors such as `nth-child(7)`.

Use 20–24 px padding around answers, 12–16 px around a question, and at least 44 px control height. Keep the textarea's native editing behavior, visible caret, focus ring, and bottom docking. Do not replace it with a simulated terminal input.

Use restrained monochrome role markers. A simple native avatar/icon is sufficient; the full Atlas is too detailed for the tiny chat-avatar area. In the pinned release, the default user and assistant avatar backgrounds are red and orange. Their verified hooks are `stChatMessageAvatarUser` and `stChatMessageAvatarAssistant`; narrowly restyle these to the muted green palette if retaining the default icons. If changing avatars, preserve nonempty author names for accessibility and use supported local/native options.

## 10. Correct state transitions

There is an important detail in the current `main()` order: the welcome is drawn before `st.chat_input()` returns a submitted question. A simple `if not messages` block can leave the large Atlas visible during the first research run, until the final rerun.

Use replaceable placeholders for the welcome, sidebar branding, and compact/large header. Clear or update them immediately upon submission, before the research stream starts.

Conceptual structure to adapt to the current function:

```python
init_session_state()
header_slot = st.empty()

# Keep existing application initialization and actionable error handling.
# If initialization fails, render a normal header and the current guidance.

with st.sidebar:
    sidebar_brand_slot = st.empty()
    # Render existing metadata and controls.
    # Let Clear chat reset messages and conversation_summary here.

has_transcript = bool(st.session_state.messages)
render_header(header_slot, compact=has_transcript)
render_sidebar_brand(sidebar_brand_slot, show_atlas=has_transcript)

welcome_slot = st.empty()
if not has_transcript:
    with welcome_slot.container():
        render_welcome()

# Render the existing transcript using its canonical results.

if question := st.chat_input("Ask history a question…"):
    value = conversation(
        question,
        st.session_state.messages,
        st.session_state.conversation_summary,
    )
    st.session_state.messages.append({"role": "user", "content": question})

    welcome_slot.empty()
    render_header(header_slot, compact=True)
    render_sidebar_brand(sidebar_brand_slot, show_atlas=True)

    # Continue the existing user-message rendering, cancellation event,
    # research stream, result rendering, assistant append, and final rerun.
```

The rendering helper names above are proposed, not existing APIs. Implement them as small trusted-shell helpers. Do not copy the outline as a replacement for the omitted canonical execution block.

Compute the empty/transcript state **after** the Clear chat action has been handled. Otherwise clearing can leave the wrong artwork visible for a rerun. Make replacement helpers idempotent so reruns cannot append duplicate titles or sprites.

| State/action | Expected presentation |
| --- | --- |
| Empty, resources available | Large welcome Atlas; clear invitation; question input available |
| First question submitted | Large welcome disappears immediately; header compacts; sidebar Atlas appears |
| Research running | Compact sidebar art; genuine progress events visible |
| Answer complete, partial, insufficient-evidence, failed, or cancelled | Transcript remains; truthful canonical outcome and available evidence remain visible |
| Clear chat | Existing conversation reset; large welcome returns |
| Resources unavailable | Existing actionable error/help; no invented ready state or working input |
| Narrow screen with sidebar collapsed | Main reading/input remain usable; decorative sidebar art is allowed to be hidden with the sidebar |

## 11. Preserve research semantics and inert rendering

The research renderer is intentionally conservative. `tests/test_offline_runtime.py::test_provenance_and_untrusted_markdown_render_as_inert_text` verifies that model/source content remains inert, including a payload containing a remote Markdown image.

Keep answers, evidence gaps, source titles, sections, and excerpts rendered with **`st.text()`**. Obtain typography and spacing through the theme and surrounding containers. Do not send that material to `st.html()`, raw HTML interpolation, `st.markdown()`, or image rendering to make it look more polished.

Trusted, fixed application labels and the bundled Atlas may use developer-owned HTML in the shell. Keep that boundary clear in the helper APIs.

Preserve all of the following:

- Canonical citation numbers, their order, and the underlying excerpts.
- The existing outcome, delivery, run identifier, evidence gaps, and task coverage.
- Full snapshot and chunk identifiers somewhere accessible and selectable.
- The existing `conversation()` construction and bounded history behavior.
- The cancellation event lifecycle and final assistant-result append.
- The cached application resources in `components.py`.
- Progress-display failure isolation in `run_application_streaming()`.

Compact metadata visually if useful, but do not replace an actual partial/failed result with a generic green success label. The historical text in the design concept is illustrative; actual screen content must come from canonical results.

### Honest terminal-style progress

The runner does not expose a queue. A busy admission produces the existing `resource_busy` failure. Do not introduce fictional queue positions, percentages, token counters, or model status lamps.

You may map existing event kinds to clearer text:

| Existing event | Suggested displayed phrase |
| --- | --- |
| `normalizing` | Preparing question |
| `planning` | Planning research |
| `plan_ready` | Research plan ready |
| `task_started` / `task_stage` | Working on the current task, with the real task/stage where useful |
| `synthesizing` | Synthesizing findings |
| `validating` | Checking the answer |
| `cancelling` | Cancelling research |

Keep the final outcome derived from the canonical result. Existing raw event labels are also acceptable for the first styling pass.

## 12. Responsive behavior and accessibility

Check at least **1440 × 900**, **1024 × 768**, and **390 × 844** viewport sizes, plus **200% text/browser zoom**.

At compact widths, stack the welcome content, reduce the sprite, and keep the input obvious. Avoid fixed widths on the question field or message panels. Keep the sidebar's native open/close control available; do not hide the entire Streamlit header merely to remove one chrome element.

Research paragraphs and evidence should wrap. Long identifiers may wrap anywhere; normal words should not be broken unnecessarily. Keep labels readable without relying on a tooltip.

Verify keyboard traversal through sidebar controls, disclosures, input, and send. Preserve a visible focus outline. At least one of text, icon shape, or position must distinguish important states in addition to color.

Use descriptive alt text for meaningful artwork and empty alt text for redundant decoration. Do not add repetitive screen-reader announcements for the same figure.

The approved effects are static. Honor reduced-motion preferences for any small transitions that are added, and make sure removing the texture/glow leaves the design complete.

## 13. Implementation sequence

### Pass A: inspect and establish the foundation

Read the current UI files and relevant tests. Confirm the checked-out Streamlit version. Add the original Atlas and font/license files. Set up package-relative asset loading and package data. Ensure a missing decorative asset cannot break the existing resource-error screen.

### Pass B: make typography and surfaces coherent

Implement the font faces, base theme, tokens, title hierarchy, reading text, button/input styles, and square-edged panels. Replace the generic font override in the current stylesheet. Check actual `st.text` answers and excerpts, not only headings or a Markdown sample.

### Pass C: build both compositions

Create the welcome composition and compact sidebar emblem. Add the replaceable header/welcome/sidebar slots and verify the first-submission and Clear chat transitions. Keep native widgets for real interactions.

### Pass D: refine the research view

Improve answer spacing, role markers, source disclosures, and session metadata placement. Keep all canonical evidence available. Use the existing cancellation and progress behavior.

### Pass E: add the finishing effects

Add only the subtle static texture and restrained title glow. Review desktop, small-screen, and zoomed layouts. Tune the pixel-art size using the actual supplied PNG.

### Pass F: verify and report

Run the relevant existing checks and a focused visual review. Report changed files, screenshots, commands actually run, and any material limitations. Distinguish verification with a model-free fixture from verification against provisioned local models.

## 14. Verification and acceptance criteria

Use the repository's configured Python interpreter and development environment. The commands below assume `python` resolves to that environment; use the project's documented interpreter if it does not.

Start with the existing checks relevant to this presentation change:

```bash
python -m ruff check src/workbench/ui tests/test_ui_imports.py tests/test_application.py tests/test_offline_runtime.py
python -m pytest tests/test_ui_imports.py tests/test_application.py tests/test_offline_runtime.py
git diff --check
```

The UI behavior suite is not limited to `test_ui_imports.py`. `tests/test_application.py` includes a Streamlit `AppTest` walkthrough with injected model-free resources and an actionable missing-assets test. Reuse those fixtures rather than downloading model weights merely to inspect styling.

Relevant existing tests include:

- `test_fake_streamlit_walkthrough_has_canonical_sources_and_no_selector`.
- `test_fake_streamlit_missing_assets_is_actionable`.
- `test_streamlit_preserves_existing_summary_without_summarization_call`.
- `test_progress_display_failure_cannot_change_terminal_answer`.
- `test_provenance_and_untrusted_markdown_render_as_inert_text`.

The source-level UI guard also checks for retired APIs. Read it before adding helpers; do not weaken it to accommodate a visual redesign.

If a narrowly scoped behavioral test is needed, focus on the new welcome/compact transition or asset availability in packaging. Avoid brittle tests that merely assert CSS color strings. `AppTest` checks behavior; browser inspection is still needed for font loading, pixel rendering, layout, and the mid-run visual transition.

When the project's normal merge gate applies, follow `docs/dev/testing.md` and `.github/workflows/model-free.yml`. Their inspected model-free gate includes:

```bash
python -m ruff check src tests scripts
python -m ruff format --check tests src/workbench/evaluation scripts/run_researcher_eval.py
python scripts/run_researcher_eval.py --validate-only
python -m pytest
python scripts/preflight.py --quick
```

### Acceptance checklist

- [ ] Welcome and research states clearly match the approved composition.
- [ ] The production image is the supplied Atlas, with the full globe and body visible.
- [ ] The large illustration disappears on first submission, before research completes.
- [ ] Clear chat restores the welcome state without duplicate artwork or headings.
- [ ] VT323 is visibly used in display headings; IBM Plex Mono is visibly used in real answers and source text. Verify DevTools Rendered Fonts or an equivalent actual-font inspection, not only the declared `font-family` string.
- [ ] Browser font/image loading uses only packaged data or verified local assets; no external CDN requests appear.
- [ ] The installed distribution contains artwork, fonts, and licenses.
- [ ] Text remains selectable, readable, and correctly wrapped; source numbering/content is unchanged.
- [ ] The inert-text rendering test still passes.
- [ ] Cancel, input, research progress, result rendering, and existing error guidance still behave correctly.
- [ ] Complete/partial/insufficient-evidence/failed/cancelled results retain their true canonical outcomes and available evidence, including the distinct `insufficient_evidence` outcome.
- [ ] Expanded sources and long identifiers fit at narrow widths and 200% zoom.
- [ ] Keyboard focus is visible and all interactive controls remain usable.
- [ ] Texture and glow are restrained; the research text itself is crisp.
- [ ] The browser's sidebar toggle, native input behavior, and essential runtime controls remain available.

Capture a welcome screenshot and a populated research screenshot using consistent viewport dimensions. Also inspect an expanded source and a narrow-screen layout. If local research resources are unavailable, use the existing injected-resource testing approach and state what was and was not verified.

## 15. Ready-to-paste Codex instruction

Save this file into the repository, for example as `docs/design/ATLAS_LOCALIS_RETRO_UI_BRIEF.md`, and give Codex the following instruction after placing the supplied PNG at the asset path described above:

```text
Implement the Atlas Localis retro terminal UI described in
docs/design/ATLAS_LOCALIS_RETRO_UI_BRIEF.md.

Read the brief, current UI source, relevant tests, and any applicable
AGENTS.md before editing. Adapt to the current checkout. Work within the
existing Streamlit presentation layer and follow the brief's implementation
sequence.

The approved design is a near-black and green research terminal with VT323
display headings, readable IBM Plex Mono body text, subtle static texture,
restrained title glow, and the supplied pixel-art Atlas. Use the large Atlas
on the welcome screen and the smaller sidebar Atlas during research. Preserve
the familiar sidebar/chat workflow.

Handle the first submission and Clear chat transitions with replaceable
presentation slots. Keep real controls native. Preserve canonical results,
citation numbering, inert st.text rendering of research/source content,
cancellation, resource caching, and the current offline behavior.

Bundle the artwork, fonts, and their licenses locally, include them in package
data, and keep paths independent of the current working directory. Use the
original supplied PNG as the artwork source. The generated concept image is
only a composition reference.

Complete the implementation, run the relevant existing checks, and inspect
the welcome, active research, populated conversation, expanded evidence,
Clear chat, missing-resource, and narrow-screen states. Reuse existing
model-free fixtures where provisioned research resources are unavailable.

Finish with the changed files, a concise explanation of the resulting UI,
screenshots where available, commands actually run, and any remaining
limitations. Do not claim a visual or real-model check that was not performed.
```

## 16. Source references

### Inspected repository

- [Main UI and state flow](https://github.com/amwaddell/atlas-localis/blob/1d68cffa55adf5b386cf6f5054f12f079d3a132d/src/workbench/ui/streamlit_app.py).
- [Current theme implementation](https://github.com/amwaddell/atlas-localis/blob/1d68cffa55adf5b386cf6f5054f12f079d3a132d/src/workbench/ui/theme.py).
- [Canonical result renderer](https://github.com/amwaddell/atlas-localis/blob/1d68cffa55adf5b386cf6f5054f12f079d3a132d/src/workbench/ui/renderers.py).
- [Dependency pins and package data](https://github.com/amwaddell/atlas-localis/blob/1d68cffa55adf5b386cf6f5054f12f079d3a132d/pyproject.toml).
- [Streamlit base configuration](https://github.com/amwaddell/atlas-localis/blob/1d68cffa55adf5b386cf6f5054f12f079d3a132d/.streamlit/config.toml).
- [Offline behavior and inert-rendering tests](https://github.com/amwaddell/atlas-localis/blob/1d68cffa55adf5b386cf6f5054f12f079d3a132d/tests/test_offline_runtime.py).
- [Application and Streamlit behavior tests](https://github.com/amwaddell/atlas-localis/blob/1d68cffa55adf5b386cf6f5054f12f079d3a132d/tests/test_application.py).
- [UI import and source guards](https://github.com/amwaddell/atlas-localis/blob/1d68cffa55adf5b386cf6f5054f12f079d3a132d/tests/test_ui_imports.py).
- [Documented testing workflow](https://github.com/amwaddell/atlas-localis/blob/1d68cffa55adf5b386cf6f5054f12f079d3a132d/docs/dev/testing.md).
- [Research event model](https://github.com/amwaddell/atlas-localis/blob/1d68cffa55adf5b386cf6f5054f12f079d3a132d/src/workbench/graphs/research_models.py).
- [Canonical runner and busy admission](https://github.com/amwaddell/atlas-localis/blob/1d68cffa55adf5b386cf6f5054f12f079d3a132d/src/workbench/systems/runner.py).

### Streamlit and implementation references

- [Streamlit 1.54 `st.html`](https://docs.streamlit.io/1.54.0/develop/api-reference/text/st.html).
- [Streamlit 1.54 keyed containers](https://docs.streamlit.io/1.54.0/develop/api-reference/layout/st.container).
- [Streamlit 1.54 chat messages and avatars](https://docs.streamlit.io/1.54.0/develop/api-reference/chat/st.chat_message).
- [Streamlit 1.54 configuration source](https://github.com/streamlit/streamlit/blob/1.54.0/lib/streamlit/config.py).
- [Streamlit custom-font documentation](https://docs.streamlit.io/develop/concepts/configuration/theming-customize-fonts).
- [Streamlit static-file serving documentation](https://docs.streamlit.io/develop/concepts/configuration/serving-static-files).
- [Pinned static-file handler](https://github.com/streamlit/streamlit/blob/1.54.0/lib/streamlit/web/server/app_static_file_handler.py).
- [Pinned plain-text element markup](https://github.com/streamlit/streamlit/blob/1.54.0/frontend/lib/src/components/elements/TextElement/TextElement.tsx).
- [Pinned plain-text element styling](https://github.com/streamlit/streamlit/blob/1.54.0/frontend/lib/src/components/elements/TextElement/styled-components.ts).
- [Pinned chat-message and avatar markup](https://github.com/streamlit/streamlit/blob/1.54.0/frontend/lib/src/components/elements/ChatMessage/ChatMessage.tsx).
- [Pinned font-family/source parsing](https://github.com/streamlit/streamlit/blob/1.54.0/lib/streamlit/runtime/theme_util.py).
- [MDN: image-rendering](https://developer.mozilla.org/en-US/docs/Web/CSS/image-rendering).
- [W3C: understanding text contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html).

Use the pinned Streamlit source when the current unversioned documentation differs. The sizes, palette, layout proportions, font pairing, and effects in this brief are proposed design decisions; repository and API claims are grounded in the sources above.
