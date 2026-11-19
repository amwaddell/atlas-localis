"""Browser review only: run with Streamlit from the repository root.

Uses the same injected graph/model fixtures as AppTest; never loads real models.
Query parameters: mode=missing|partial|failed|insufficient_evidence|cancelled,
delay=SECONDS (per fixture model call), long=1 (long source and identifiers).
"""

import json
import time
from unittest.mock import patch

import streamlit as st
from tests.test_evidence_worker import Model, chunk, draft
from tests.test_supervisor_orchestration import Retriever, answer, proposal

from workbench.graphs.constrained_evidence import source_statements
from workbench.graphs.research_models import Limits
from workbench.retrieval.hybrid import HydratedRetrieval
from workbench.systems.runner import Resources, create_application
from workbench.ui.streamlit_app import main

PARAGRAPH = (
    "Rome changed in 27 BC. The Senate granted Octavian the title Augustus. "
    "Republican offices continued under the new settlement, while authority "
    "became concentrated in the princeps. The transition followed decades of "
    "civil conflict and changes to the institutions of the Republic. "
    "This model-free fixture demonstrates the reading layout and local evidence."
)


class PreviewModel(Model):
    def generate(self, messages, **settings):
        time.sleep(float(study_delay))
        return super().generate(messages, **settings)


class LongRetriever:
    def retrieve_hydrated(self, *args, **kwargs):
        return HydratedRetrieval(
            (chunk(cid="local-chunk-" + "abcdef0123456789" * 8, text=PARAGRAPH),),
            1,
            1,
        )


def application():
    if mode == "missing":
        raise FileNotFoundError(
            "Provisioned local index missing: browser-review fixture"
        )
    failures = {"partial": ("Question 2",), "failed": ("Question 1", "Question 2")}
    empty = ("Question 1", "Question 2") if mode == "insufficient_evidence" else ()
    task_count = 1 if mode in {"complete", "cancelled"} else 2
    model = PreviewModel(proposal(task_count), draft(), answer())
    retriever = Retriever(failed=failures.get(mode, ()), empty=empty)
    if long_source:
        statements = source_statements(PARAGRAPH)
        model = PreviewModel(
            proposal(),
            json.dumps(
                {
                    "findings": [
                        {"statement": text, "evidence_refs": ["E1"], "date_text": None}
                        for text in statements
                    ],
                    "gaps": [],
                }
            ),
            json.dumps(
                {
                    "units": [
                        {"text": text, "finding_ids": [f"F{index}"]}
                        for index, text in enumerate(statements, 1)
                    ]
                }
            ),
        )
        retriever = LongRetriever()
    app = create_application(
        resources=Resources(
            model, retriever, "fixture-snapshot-" + "0123456789abcdef" * 8
        ),
        limits=Limits(prompt_tokens=20000),
    )
    if mode == "cancelled":
        original = app.stream

        def cancelled(value, *, cancel_event):
            cancel_event.set()
            yield from original(value, cancel_event=cancel_event)

        app.stream = cancelled
    return app


mode = st.query_params.get("mode", "complete")
study_delay = min(10.0, max(0.0, float(st.query_params.get("delay", "0"))))
long_source = st.query_params.get("long") == "1"
with patch("workbench.ui.streamlit_app.get_application", application):
    main()
