"""Own one native llama-server; verify identity and reap only the owned process group."""

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from workbench.core.offline import local_url  # noqa: E402
from workbench.models.llamacpp_server import LlamaCppServerModel  # noqa: E402
from workbench.models.native_process import stop_owned  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Verify an existing native server and exit",
    )
    args = parser.parse_args()
    port = os.environ.get("LLM_PORT", "8080")
    endpoint = local_url(f"http://127.0.0.1:{port}")
    model = LlamaCppServerModel(
        base_url=endpoint, model_name="ministral-3-8b-instruct", timeout_seconds=2
    )
    if args.no_llm:
        try:
            model.check_identity()
        finally:
            model.close()
        return 0
    stopped = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    child = subprocess.Popen(
        ["bash", str(Path(__file__).with_name("start_model_server.sh"))],
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 120
        while not stopped.is_set():
            if child.poll() is not None:
                raise RuntimeError("Owned native llama-server exited during startup")
            try:
                model.check_identity()
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Native llama-server readiness/identity deadline exceeded"
                    ) from None
                stopped.wait(0.25)
        if stopped.is_set():
            return 130
        print(
            f"Native llama-server ready at {endpoint}; E5/BGE load once in the application.",
            flush=True,
        )
        while not stopped.wait(0.25):
            if child.poll() is not None:
                raise RuntimeError("Owned native llama-server exited unexpectedly")
        return 0
    finally:
        model.close()
        stop_owned(child)


if __name__ == "__main__":
    raise SystemExit(main())
