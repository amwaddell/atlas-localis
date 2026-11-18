"""Owned native process lifecycle shared with explicit build acceptance."""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path


def stop_owned(child):
    """Reap only the process group created by this owner."""
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()


@contextmanager
def native_smoke_server(gguf: Path, log_path: Path):
    """Start a provisioned binary/model for one build smoke; no acquisition."""
    from workbench.models.llamacpp_server import LlamaCppServerModel

    executable = shutil.which("llama-server")
    if not executable or not gguf.is_file():
        raise FileNotFoundError(
            "Provision pinned native llama-server and GGUF before smoke"
        )
    version = subprocess.run(
        [executable, "--version"], capture_output=True, text=True, check=True
    )
    if "ed4837891" not in version.stdout + version.stderr:
        raise ValueError("Native build must match validated llama.cpp commit ed4837891")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    with log_path.open("w") as log:
        child = subprocess.Popen(
            [
                executable,
                "--model",
                str(gguf),
                "--alias",
                "ministral-3-8b-instruct",
                "--parallel",
                "1",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--n-gpu-layers",
                "-1",
                "--ctx-size",
                "8192",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        model = LlamaCppServerModel(
            base_url=endpoint, model_name="ministral-3-8b-instruct", timeout_seconds=2
        )
        try:
            deadline = time.monotonic() + 120
            while True:
                if child.poll() is not None:
                    raise RuntimeError(
                        f"Owned native smoke server exited; inspect {log_path}"
                    )
                try:
                    model.check_identity()
                    break
                except Exception:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            f"Native smoke readiness deadline; inspect {log_path}"
                        ) from None
                    time.sleep(0.25)
            # Recheck ownership after readiness to avoid borrowing a racing listener.
            if child.poll() is not None:
                raise RuntimeError("Owned native server exited at readiness")
            yield endpoint
        finally:
            model.close()
            stop_owned(child)
