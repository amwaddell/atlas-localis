"""Local terminal styling; pinned Streamlit 1.54 hooks, no remote browser assets."""

import streamlit as st

from workbench.ui.branding import bundled_data_uri


def _font_faces():
    faces = []
    for family, filename, weight in (
        ("VT323", "VT323-Regular.ttf", 400),
        ("IBM Plex Mono", "IBMPlexMono-Regular.ttf", 400),
        ("IBM Plex Mono", "IBMPlexMono-Medium.ttf", 500),
    ):
        source = bundled_data_uri(f"fonts/{filename}", "font/ttf")
        if source:
            faces.append(
                f'@font-face {{ font-family: "{family}"; '
                f'src: url("{source}") format("truetype"); '
                f"font-weight: {weight}; font-style: normal; font-display: swap; }}"
            )
    return "\n".join(faces)


# atlas_answer_N / atlas_question_N are owned transcript containers. Test IDs
# below are the small set of native Streamlit 1.54 hooks checked in the browser.
_STYLES = """
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
.stApp { background: var(--atlas-bg); color: var(--atlas-text); }
.stApp, .stApp input, .stApp textarea, .stApp button,
.stApp [data-testid="stMarkdownContainer"], .stApp code,
.stApp [data-testid="stText"] > span {
    font-family: var(--atlas-font-body);
}
.stApp h1, .stApp h2, .stApp h3 {
    font-family: var(--atlas-font-display); font-weight: 400;
    color: var(--atlas-accent); font-synthesis: none;
}
.stApp h2, .stApp h3 { font-size: 2rem; line-height: 1.1; }
.stApp a { color: var(--atlas-accent); }
.stApp :is(button, a, summary, input, textarea):focus-visible {
    outline: 2px solid var(--atlas-accent); outline-offset: 3px;
}
[data-testid="stHeader"] { background: var(--atlas-bg); }
[data-testid="stSidebar"] {
    background: var(--atlas-panel); border-right: 1px solid var(--atlas-rule);
}
.stApp [data-testid="stSidebar"] h3 {
    font-size: 1.75rem; overflow-wrap: normal; word-break: normal;
}
[data-testid="stSidebar"] [data-testid="stButton"] button { width: 100%; }
[data-testid="stMainBlockContainer"] {
    max-width: 1040px; padding: 5rem 2.5rem 2rem;
    container-type: inline-size;
}
.atlas-header { padding-bottom: 1rem; }
.stApp .atlas-wordmark {
    font-size: clamp(3.5rem, 4.5vw, 4rem); font-weight: 400;
    line-height: 1; letter-spacing: .01em; padding: .5rem 0;
    margin: 0; color: var(--atlas-accent);
    text-shadow: 0 0 8px rgb(125 255 155 / 12%);
}
.stApp .atlas-wordmark--compact { font-size: 2.5rem; line-height: 1.05; }
.atlas-kicker {
    font-family: var(--atlas-font-body); font-size: .75rem;
    font-weight: 500; line-height: 1.4; letter-spacing: .1em;
    color: var(--atlas-muted);
}
.atlas-tagline {
    font-size: .875rem; line-height: 1.5; margin: .5rem 0 0;
    color: var(--atlas-muted);
}
.atlas-welcome {
    display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr);
    align-items: center; gap: 2rem; padding-block: 1.5rem;
    border-top: 1px solid var(--atlas-rule);
}
.atlas-invitation h2 { margin: 0 0 1rem; padding: 0; }
.atlas-invitation p { font-size: 1rem; line-height: 1.65; margin: 0; }
.atlas-example { border-left: 2px solid var(--atlas-rule); padding-left: 1rem; margin-top: 2rem; }
.atlas-example p { margin-top: .5rem; color: var(--atlas-muted); }
.atlas-art {
    display: grid; place-items: center;
    background-image: repeating-linear-gradient(
        to bottom, rgb(125 255 155 / 1.5%) 0 1px, transparent 1px 4px
    );
}
.atlas-sprite {
    display: block; height: auto; max-width: 100%; object-fit: contain;
    image-rendering: crisp-edges; image-rendering: pixelated;
}
.atlas-sprite--hero { width: 384px; }
.atlas-sprite--sidebar { width: 160px; margin: 1rem auto 0; }
.atlas-text-brand {
    font-family: var(--atlas-font-display); font-size: 2rem;
    line-height: 1.1; color: var(--atlas-accent); margin-top: 1rem;
}
/* Native text spans preserve inert output and whitespace, including excerpts. */
.stApp [data-testid="stText"] > span {
    color: var(--atlas-text); font-weight: 400; line-height: 1.65;
    white-space: pre-wrap; overflow-wrap: anywhere;
}
[class*="st-key-atlas_question_"] [data-testid="stChatMessage"] {
    background: var(--atlas-bg); padding: 1rem;
    border-left-color: var(--atlas-rule);
}
[class*="st-key-atlas_answer_"] [data-testid="stText"] > span {
    font-size: 1rem;
}
[data-testid="stChatMessage"] {
    background: var(--atlas-panel); border: 1px solid var(--atlas-rule);
    border-left: 3px solid var(--atlas-control-border); border-radius: 2px;
    padding: 1.5rem; margin-bottom: .5rem; min-width: 0;
}
[data-testid="stChatMessageContent"] { min-width: 0; }
[data-testid="stChatMessageAvatarUser"],
[data-testid="stChatMessageAvatarAssistant"] {
    background: var(--atlas-raised); color: var(--atlas-muted);
    border: 1px solid var(--atlas-rule); border-radius: 2px;
}
[data-testid="stChatInput"] {
    background: var(--atlas-raised); border: 1px solid var(--atlas-control-border);
    border-radius: 2px;
}
[data-testid="stChatInput"]:focus-within {
    outline: 2px solid var(--atlas-accent); outline-offset: 3px;
}
[data-testid="stChatInput"] textarea {
    color: var(--atlas-text); caret-color: var(--atlas-accent);
    font-size: 1rem; line-height: 1.5;
}
[data-testid="stChatInput"] textarea::placeholder { color: var(--atlas-muted); opacity: 1; }
[data-testid="stChatInputSubmitButton"] { min-width: 44px; min-height: 44px; }
[data-testid="stButton"] button {
    background: var(--atlas-raised); color: var(--atlas-text);
    border: 1px solid var(--atlas-control-border); border-radius: 2px;
    min-height: 44px; font-weight: 500;
    transition: border-color 120ms, background-color 120ms;
}
[data-testid="stButton"] button:hover {
    color: var(--atlas-accent); border-color: var(--atlas-accent);
    background: var(--atlas-panel);
}
[data-testid="stButton"] button p { font-size: .875rem; font-weight: 500; }
[data-testid="stExpander"] details {
    border: 1px solid var(--atlas-control-border); border-radius: 2px;
    background: var(--atlas-panel);
}
[data-testid="stExpander"] summary { min-height: 44px; padding: .5rem .75rem; }
[data-testid="stExpander"] summary p {
    font-size: .875rem; font-weight: 500; line-height: 1.4;
    overflow-wrap: anywhere;
}
[data-testid="stExpander"] summary:hover { color: var(--atlas-accent); }
[class*="st-key-atlas_answer_"] [data-testid="stExpander"] [data-testid="stText"] > span {
    font-size: .9375rem; line-height: 1.6;
}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {
    color: var(--atlas-muted); font-size: .8125rem; line-height: 1.5;
    overflow-wrap: anywhere;
}
.st-key-atlas_session_details [data-testid="stText"] > span {
    font-size: .8125rem; line-height: 1.5;
}
hr { border-color: var(--atlas-rule); }
@container (max-width: 650px) {
    .atlas-welcome { grid-template-columns: 1fr; gap: 1rem; padding-block: 1rem; }
    .atlas-sprite--hero { width: 160px; }
    .atlas-example { margin-top: 1.5rem; }
}
@media (max-width: 640px) {
    [data-testid="stMainBlockContainer"] { padding-inline: 1rem; }
    [data-testid="stChatMessage"] { padding: 1rem; gap: .5rem; }
    .stApp .atlas-wordmark { font-size: 3rem; }
    .stApp .atlas-wordmark--compact { font-size: 2.25rem; }
}
@media (prefers-reduced-motion: reduce) {
    [data-testid="stButton"] button { transition: none; }
    .stApp .atlas-wordmark { text-shadow: none; }
    .atlas-art { background-image: none; }
}
"""


def apply_theme():
    st.html(f"<style>{_font_faces()}{_STYLES}</style>")
