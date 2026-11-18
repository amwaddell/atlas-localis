"""Local terminal styling; no remote fonts or browser assets."""

import streamlit as st


def apply_theme():
    st.html("""
    <style>
    :root { color-scheme: dark; }
    .stApp { background: #070b09; color: #bcefc8; }
    .stApp, .stApp input, .stApp textarea, .stApp button,
    .stApp [data-testid="stMarkdownContainer"], .stApp pre {
        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .stApp h1, .stApp h2, .stApp h3 { color: #7dff9b; font-family: inherit; }
    .stApp h1 { letter-spacing: -.045em; font-size: clamp(2rem, 5vw, 3.4rem); }
    .stApp a { color: #7dff9b; }
    [data-testid="stHeader"] { background: #070b09; }
    [data-testid="stSidebar"] { background: #0c130e; border-right: 1px solid #294532; }
    [data-testid="stSidebar"] .stButton button { width: 100%; }
    .block-container { max-width: 1040px; padding-top: 3rem; padding-bottom: 3rem; }
    [data-testid="stChatMessage"] {
        background: #0c130e; border: 1px solid #294532;
        border-left: 3px solid #548d62; border-radius: 4px;
        padding: 1.25rem; margin-bottom: 1rem;
    }
    [data-testid="stChatMessage"] pre { white-space: pre-wrap; overflow-wrap: anywhere; }
    [data-testid="stChatInput"] { border: 1px solid #548d62; border-radius: 4px; }
    [data-testid="stChatInput"] textarea { color: #d4ffde; caret-color: #7dff9b; }
    .stButton button {
        background: #101a13; color: #bcefc8; border: 1px solid #365d42;
        border-radius: 3px; min-height: 2.75rem;
    }
    .stButton button:hover { color: #7dff9b; border-color: #7dff9b; background: #16251b; }
    .stButton button:focus-visible { outline: 2px solid #7dff9b; outline-offset: 3px; }
    [data-testid="stExpander"] { border-color: #294532; background: #0c130e; }
    [data-testid="stCaptionContainer"] { color: #8eb89a; }
    hr { border-color: #294532; }
    .atlas-kicker { color: #8eb89a; font-size: .75rem; letter-spacing: .16em; }
    .atlas-welcome { border-top: 1px solid #294532; padding: 1.5rem 0; margin-top: 1rem; }
    .atlas-welcome strong { color: #7dff9b; }
    </style>
    """)
