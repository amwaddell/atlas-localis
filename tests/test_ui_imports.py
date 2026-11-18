"""Presentation modules expose only the canonical application."""

import importlib
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "module", ["components", "chat_state", "graph_runner", "renderers", "streamlit_app"]
)
def test_ui_imports(module):
    assert importlib.import_module("workbench.ui." + module)


def test_no_graph_selection_reducer_replay_or_hidden_summary():
    root = Path(__file__).parents[1] / "src/workbench/ui"
    source = "\n".join(p.read_text() for p in root.glob("*.py"))
    for retired in (
        "selectbox",
        "dict.update",
        "accumulated.update",
        "numberize_result",
        "update_conversation_summary",
        "build_model",
        "build_researcher_graph",
    ):
        assert retired not in source
