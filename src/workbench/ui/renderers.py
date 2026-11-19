"""Render only canonical answers and their admitted local evidence."""

import streamlit as st

from workbench.systems.presentation import project_result


def render_result(result):
    view = project_result(result)
    st.text(view["answer_text"])
    st.caption(f"{view['outcome']} · {view['delivery']} · Run {view['run_id']}")
    for gap in view["gaps"]:
        st.text(f"Evidence gap: {gap}")
    if view["coverage"]:
        with st.expander("Research tasks"):
            for task in view["coverage"]:
                st.text(f"{task['task_id']}: {task['status']}")
                for gap in task["gaps"]:
                    st.text(gap)
    for citation in view["citations"]:
        with st.expander(f"[{citation['number']}] Local source"):
            st.text(f"Snapshot: {citation['snapshot_id']}")
            st.text(f"Chunk: {citation['chunk_id']}")
            for excerpt in citation["excerpts"]:
                st.text(excerpt["title"])
                if excerpt["section"]:
                    st.text(excerpt["section"])
                st.text(excerpt["text"])
