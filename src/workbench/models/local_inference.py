"""Explicit cached-model device policy; no implicit CUDA or online recovery."""

import os
import threading
from contextlib import contextmanager

# Also protect direct adapter use outside the application admission gates.
_LOCAL_INFERENCE = threading.RLock()


def local_device(requested: str | None) -> str:
    import torch

    if os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK", "0") != "0":
        raise ValueError(
            "Unset PYTORCH_ENABLE_MPS_FALLBACK; inference failures must be explicit"
        )
    if requested not in {None, "auto", "mps", "cpu"}:
        raise ValueError("Local inference device must be auto, mps or cpu")
    if requested == "cpu":
        return "cpu"
    # CPU fallback is only for unavailable Metal, never for corrupt assets,
    # allocation failure or unsupported operations during inference.
    return "mps" if torch.backends.mps.is_available() else "cpu"


@contextmanager
def inference():
    import torch

    with _LOCAL_INFERENCE, torch.inference_mode():
        yield
