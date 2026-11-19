"""Local chat presentation over the canonical application."""

import threading

import streamlit as st

from workbench.systems.runner import ResourceBusy
from workbench.ui.branding import (
    render_header,
    render_sidebar_brand,
    render_welcome,
)
from workbench.ui.chat_state import conversation, init_session_state
from workbench.ui.components import get_application
from workbench.ui.graph_runner import run_application_streaming
from workbench.ui.renderers import render_result
from workbench.ui.theme import apply_theme


def main():
    st.set_page_config(page_title="Atlas Localis", page_icon="▣", layout="wide")
    apply_theme()
    init_session_state()
    header_slot = st.empty()
    try:
        app = get_application()
    except (ResourceBusy, OSError, ValueError) as exc:
        render_header(header_slot, compact=False)
        st.error(f"Local research is unavailable: {exc}")
        st.info(
            "Provision local assets, start the model stack, and close any other research application before retrying."
        )
        st.code(
            "bash scripts/start_model_stack.sh\nbash scripts/run_ui.sh", language="bash"
        )
        st.caption("Setup and troubleshooting: docs/dev/setup.md")
        return
    with st.sidebar:
        sidebar_brand_slot = st.empty()
        st.subheader("Local research")
        st.caption("Wikipedia · hybrid retrieval · local inference")
        with st.container(key="atlas_session_details"):
            with st.expander("Session details"):
                st.text(f"Snapshot: {app.resources.snapshot_id}")
        if st.button("Cancel active research", width="stretch"):
            cancel = st.session_state.get("cancel_event")
            if cancel:
                cancel.set()
        if st.button("Clear chat", width="stretch"):
            st.session_state.messages = []
            st.session_state.conversation_summary = ""
        st.divider()
        st.caption(
            "Answers include numbered sources. Expand a source to inspect the exact local evidence."
        )
        st.caption("Source provenance does not establish historical correctness.")
    has_transcript = bool(st.session_state.messages)
    render_header(header_slot, compact=has_transcript)
    render_sidebar_brand(sidebar_brand_slot, show_atlas=has_transcript)
    welcome_slot = st.empty()
    if not has_transcript:
        with welcome_slot.container():
            render_welcome()
    for index, message in enumerate(st.session_state.messages):
        role = "answer" if message["role"] == "assistant" else "question"
        with st.container(key=f"atlas_{role}_{index}"):
            with st.chat_message(message["role"]):
                if message.get("result"):
                    render_result(message["result"])
                else:
                    st.text(message["content"])
    if question := st.chat_input("Ask history a question…"):
        value = conversation(
            question, st.session_state.messages, st.session_state.conversation_summary
        )
        st.session_state.messages.append({"role": "user", "content": question})
        welcome_slot.empty()
        render_header(header_slot, compact=True)
        render_sidebar_brand(sidebar_brand_slot, show_atlas=True)
        with st.container(key=f"atlas_question_{len(st.session_state.messages) - 1}"):
            with st.chat_message("user"):
                st.text(question)
        cancel = threading.Event()
        st.session_state.cancel_event = cancel
        with st.container(key=f"atlas_answer_{len(st.session_state.messages)}"):
            with st.chat_message("assistant"):
                progress = st.empty()
                result = run_application_streaming(
                    app,
                    value,
                    cancel_event=cancel,
                    on_progress=lambda event: progress.text(
                        " · ".join(
                            v for v in (event.kind, event.task_id, event.stage) if v
                        )
                    ),
                )
                progress.empty()
                render_result(result)
        st.session_state.messages.append(
            {"role": "assistant", "content": result.answer_text, "result": result}
        )
        st.session_state.cancel_event = None
        st.rerun()


if __name__ == "__main__":
    main()
