"""Hard parent deadlines around nested streaming and unmocked trace evidence."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_real_application_nested_stream_trace_and_callback_parentage():
    root = Path(__file__).parents[1]
    result = subprocess.run(
        [sys.executable, "-B", str(root / "tests/fixtures/orchestration_probe.py")],
        cwd=root,
        env={
            **os.environ,
            "PYTHONPATH": str(root / "src") + os.pathsep + str(root),
            "HF_HUB_OFFLINE": "1",
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"nested": "passed"' in result.stdout


def test_real_application_telemetry_lifecycle_and_disabled_settings():
    root = Path(__file__).parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(root / "tests/fixtures/application_lifecycle_probe.py"),
        ],
        cwd=root,
        env={
            **os.environ,
            "PYTHONPATH": str(root / "src") + os.pathsep + str(root),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"lifecycle": "passed"' in result.stdout
    assert '"disabled": "passed"' in result.stdout


pytestmark = pytest.mark.fixture_integration
