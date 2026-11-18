"""Real SDK lifecycle/disabled-tracing probe outside conftest tracing mocks."""

import json
import sqlite3
import tempfile
from pathlib import Path

from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from tests.test_supervisor_orchestration import graph

from workbench.core.config import load_config
from workbench.core.run_context import get_run_context
from workbench.graphs.research_models import ConversationInput
from workbench.observability import tracing
from workbench.observability.lifecycle import Telemetry

with tempfile.TemporaryDirectory(prefix="workbench-lifecycle-") as directory:
    config = load_config(
        overrides={"runs": {"output_dir": directory, "logs_dir": directory + "/logs"}}
    )
    telemetry = Telemetry(config)
    exporter = InMemorySpanExporter()
    provider = telemetry.provider
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = graph()
    app._telemetry = telemetry
    result = app.invoke(ConversationInput(question="Rome?"))
    assert get_run_context() is None and tracing._scoped_tracer.get() is None
    spans = exporter.get_finished_spans()
    root = next(s for s in spans if s.name == "atlas_localis.question")
    assert root.attributes["run_id"] == result.run_id
    assert all(s.attributes["run_id"] == result.run_id for s in spans)
    assert all(s.context.trace_id == root.context.trace_id for s in spans)
    with sqlite3.connect(telemetry.metrics_path) as connection:
        rows = connection.execute(
            "select run_id, component_name from component_latency"
        ).fetchall()
    assert rows == [(result.run_id, "application.run")]
    log = json.loads(
        (Path(directory) / "logs" / (result.run_id + ".jsonl"))
        .read_text()
        .splitlines()[0]
    )
    assert log["data"]["outcome"] == "complete"
    app.close()
    assert telemetry.provider is None

    disabled = config.model_copy(deep=True)
    disabled.observability.update(enable_tracing=False, enable_metrics=False)
    disabled.runs["output_dir"] = directory + "/disabled"
    disabled.runs["logs_dir"] = directory + "/disabled/logs"
    telemetry = Telemetry(disabled)
    app = graph()
    app._telemetry = telemetry
    assert app.invoke(ConversationInput(question="Rome?")).outcome == "complete"
    assert telemetry.provider is None
    assert not telemetry.metrics_path.exists()
    assert tracing._scoped_tracer.get() is None
    app.close()
print(
    json.dumps(
        {
            "lifecycle": "passed",
            "spans": len(spans),
            "metrics": len(rows),
            "disabled": "passed",
        }
    )
)
