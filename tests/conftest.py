"""Model-free tests use real scoped telemetry; acceptance is explicit and fails closed."""

import socket

import pytest
from opentelemetry.sdk.trace import TracerProvider


@pytest.fixture(autouse=True)
def isolated_tracing(monkeypatch):
    import workbench.observability.tracing as tracing

    provider = TracerProvider()
    monkeypatch.setattr(tracing, "_tracer_provider", provider)
    monkeypatch.setattr(tracing, "_tracer", provider.get_tracer("atlas-tests"))
    yield
    provider.shutdown()
    # Tests may install their own provider; close it before monkeypatch restores.
    if tracing._tracer_provider is not provider and tracing._tracer_provider:
        tracing._tracer_provider.shutdown()


@pytest.fixture(autouse=True)
def no_external_network(request, monkeypatch):
    """No model-free test may fetch assets or send telemetry outside loopback."""
    if request.node.get_closest_marker("acceptance"):
        return
    original = socket.socket.connect

    def connect(sock, address):
        if isinstance(address, tuple) and address[0] not in {
            "127.0.0.1",
            "::1",
            "localhost",
        }:
            raise AssertionError(
                f"Model-free test attempted external connection: {address[0]}"
            )
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    monkeypatch.setenv("HF_HUB_DISABLE_TELEMETRY", "1")


@pytest.fixture(autouse=True)
def isolated_resource_lease(request, monkeypatch, tmp_path):
    """Keep fixture ownership tests independent of a resident real application."""
    if request.node.get_closest_marker("acceptance"):
        return
    from types import SimpleNamespace

    import workbench.systems.runner as runner

    monkeypatch.setattr(
        runner, "tempfile", SimpleNamespace(gettempdir=lambda: str(tmp_path))
    )
