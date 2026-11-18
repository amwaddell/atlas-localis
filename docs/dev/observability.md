# Observability

The shared application owns telemetry across UI, CLI and evaluation. `observability/lifecycle.py` creates a scoped OpenTelemetry provider, records terminal results and flushes/closes resources. Disabling telemetry does not change research answers.

- JSONL terminal events: `runs/logs/<run_id>.jsonl`, including outcome, delivery, citation count and error code.
- SQLite metrics: `runs/metrics/metrics.sqlite`, including application elapsed time.
- SDK spans: orchestration, worker and model phases, using the shared run context.

Tracing and metrics honor `observability.enable_tracing` and `enable_metrics`. Local logs remain available independently. A viewer URL alone does not enable trace export.

## Optional Phoenix

Install optional dependencies into the selected environment during explicit setup:

```bash
python -m pip install -r requirements-phoenix.txt
bash scripts/run_phoenix.sh
```

The launcher binds a read-only viewer to loopback at http://127.0.0.1:6006 and disables telemetry/external resources. Live viewer ingestion is not established by this launcher. If separately running a compatible local receiver, explicitly configure:

```yaml
observability:
  enable_tracing: true
  enable_metrics: true
  phoenix_endpoint: "http://127.0.0.1:6006"
  phoenix_export_endpoint: "http://127.0.0.1:6006/v1/traces"
```

Remote export endpoints and cloud tracing overrides are rejected. Export uses a proxy-free local session; missing optional viewer services should not determine answer success. End-to-end Phoenix viewer/export acceptance remains unverified; use local logs and evaluation reports for inspection today.
