"""Narrow installed-API evidence, not application/cancellation acceptance."""

import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROBE = Path(__file__).parent / "fixtures/langgraph_contract_probe.py"
ROOT = Path(__file__).parents[1]


def run_probe(capacity, mode, timeout=12):
    return subprocess.run(
        [sys.executable, "-B", str(PROBE), str(capacity), mode],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        },
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )


def test_supported_installed_framework_versions():
    for package, version in [
        ("langgraph", "1.0.9"),
        ("langchain-core", "1.2.15"),
        ("langchain", "1.2.10"),
    ]:
        assert importlib.metadata.version(package) == version
        assert f"{package}=={version}" in (ROOT / "pyproject.toml").read_text()


@pytest.mark.parametrize("mode,capacity", [("invoke", 1), ("stream", 4)])
def test_send_nested_variable_paths_one_barrier_and_terminal(mode, capacity):
    result = json.loads(run_probe(capacity, mode).stdout)
    assert result["terminal"] == ["t1", "t2", "t3"] and result["barriers"] == 1
    assert result["peak_workers"] == result["peak_model"] == 1
    if mode == "stream":
        assert result["nested"] > 0
        assert result["completion"] == ["t3", "t2", "t1"]


def test_one_scheduler_slot_stream_regression_is_bounded():
    # Pin documents a known limitation. Parent kills/reaps the child; no private
    # scheduler patches or daemon threads survive a failed/stalled probe.
    with pytest.raises(subprocess.TimeoutExpired):
        run_probe(1, "stream", timeout=3)


pytestmark = pytest.mark.fixture_integration
