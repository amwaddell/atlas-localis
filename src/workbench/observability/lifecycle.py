"""Shared, application-owned telemetry; optional viewers cannot change answers."""

from contextlib import contextmanager
from pathlib import Path

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import NoOpTracer

from workbench.core.local_http import LocalSession
from workbench.core.offline import local_url
from workbench.observability import tracing
from workbench.observability.logging import get_logger
from workbench.observability.metrics import MetricsStore


class Telemetry:
    def __init__(self, config):
        self.settings = dict(config.observability)
        self.logs_dir = Path(config.runs.get("logs_dir", "runs/logs"))
        self.metrics_path = (
            Path(config.runs.get("output_dir", "runs")) / "metrics/metrics.sqlite"
        )
        self.provider = None
        self.tracer = NoOpTracer()
        if self.settings.get("enable_tracing", True):
            # Dedicated provider avoids changing the global SDK on cache reload.
            self.provider = TracerProvider(
                resource=Resource.create({"service.name": "agentic-workbench"})
            )
            self.tracer = self.provider.get_tracer("workbench.application")
            endpoint = self.settings.get("phoenix_export_endpoint")
            if endpoint:
                endpoint = local_url(
                    endpoint
                )  # invalid remote config is never optional
                try:
                    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                        OTLPSpanExporter,
                    )

                    exporter = OTLPSpanExporter(
                        endpoint=endpoint, session=LocalSession(), timeout=1
                    )
                    self.provider.add_span_processor(BatchSpanProcessor(exporter))
                except ImportError:
                    pass  # JSONL/metrics remain usable without the optional exporter.

    @contextmanager
    def run(self, run_id):
        token = tracing._scoped_tracer.set(self.tracer)
        try:
            yield
        finally:
            tracing._scoped_tracer.reset(token)

    def record(self, result):
        try:
            get_logger(run_id=result.run_id, logs_dir=self.logs_dir).log_event(
                "application_completed",
                {
                    "outcome": result.outcome,
                    "delivery": result.delivery,
                    "citations": len(result.citations),
                    "error_code": result.error.code if result.error else None,
                },
            )
            if self.settings.get("enable_metrics", True):
                MetricsStore(self.metrics_path).record_component_latency(
                    component_name="application.run",
                    component_type="application",
                    duration_ms=result.diagnostics.elapsed_ms,
                )
        except Exception:
            pass
        self.flush()

    def flush(self):
        if self.provider is not None:
            try:
                self.provider.force_flush(timeout_millis=1000)
            except Exception:
                pass

    def close(self):
        self.flush()
        if self.provider is not None:
            self.provider.shutdown()
            self.provider = None
