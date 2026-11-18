"""Isolated real SDK/callback/nested-stream acceptance, with a parent timeout."""

import json

from langchain_core.callbacks import BaseCallbackHandler
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from tests.test_evidence_worker import Model, draft
from tests.test_supervisor_orchestration import (
    answer,
    graph,
    nested_stream_probe,
    proposal,
)

from workbench.graphs.research_models import ConversationInput
from workbench.observability import tracing

# No global provider resets and no conftest tracing mocks in this interpreter.
exporter = InMemorySpanExporter()
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(exporter))
tracing._tracer = provider.get_tracer("stage2c-probe")


class Callbacks(BaseCallbackHandler):
    def __init__(self):
        self.tags = []

    def on_chain_start(self, serialized, inputs, *, tags=None, **kwargs):
        self.tags.append(tags or [])


callbacks = Callbacks()
events = []
g = graph(
    Model(proposal(2), draft(), draft(), answer("F1", "F2")), event_sink=events.append
)
result = g.invoke(
    ConversationInput(question="Q"),
    {"callbacks": [callbacks], "tags": ["inherited-test-tag"]},
)
assert result.outcome == "complete"
spans = exporter.get_finished_spans()
root = next(s for s in spans if s.name == "atlas_localis.question")
tasks = [s for s in spans if s.name == "researcher.task"]
assert len(tasks) == 2
assert {s.attributes["task_id"] for s in tasks} == {"t1", "t2"}
assert all(s.parent.span_id == root.context.span_id for s in tasks)
assert all(s.attributes["run_id"] == result.run_id for s in tasks)
by_id = {s.context.span_id: s for s in spans}
for task in tasks:
    children = [
        s for s in spans if s.parent and s.parent.span_id == task.context.span_id
    ]
    assert {s.name for s in children} >= {
        "worker.retrieve",
        "worker.pack_evidence",
        "worker.derive_findings",
        "worker.validate_findings",
        "worker.finish_task",
    }
    assert all(s.context.trace_id == root.context.trace_id for s in children)
assert all(
    s.parent.span_id == root.context.span_id
    for s in spans
    if s.name in {"plan", "aggregate", "synthesize", "validate_final", "finalize"}
)
assert any(e.name == "dispatch" for e in root.events)
assert len(callbacks.tags) > 10 and all(
    "inherited-test-tag" in tags for tags in callbacks.tags
)
assert [e.sequence for e in events] == list(range(len(events)))
assert [e.kind for e in events].count("run_finished") == 1
assert all(e.result is None for e in events[:-1])
nested_stream_probe()
all_spans = exporter.get_finished_spans()
all_by_id = {s.context.span_id: s for s in all_spans}
for span in all_spans:
    if span.name == "researcher.task":
        parent = all_by_id[span.parent.span_id]
        assert parent.name == "atlas_localis.question"
        assert parent.attributes["run_id"] == span.attributes["run_id"]
        assert parent.context.trace_id == span.context.trace_id
provider.shutdown()
print(
    json.dumps(
        {
            "tasks": len(tasks),
            "spans": len(spans),
            "callbacks": len(callbacks.tags),
            "nested": "passed",
        }
    )
)
