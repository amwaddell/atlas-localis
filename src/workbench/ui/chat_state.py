"""UI transcript storage; bounded context selection belongs to the application."""

import streamlit as st

from workbench.graphs.planning import conversation_input


def init_session_state():
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "conversation_summary" not in st.session_state:
        st.session_state.conversation_summary = ""


def conversation(question, messages, summary=""):
    return conversation_input(
        question,
        history=tuple({"role": m["role"], "content": m["content"]} for m in messages),
        summary=summary,
    )
