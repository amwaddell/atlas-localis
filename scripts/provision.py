#!/usr/bin/env python3
"""Dependency-free setup bootstrap. It never changes the invoking environment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate_host(root, minimum_gib):
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise RuntimeError("Supported setup requires macOS Apple Silicon (arm64)")
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("Use CPython 3.12; no global/Conda interpreter is modified")
    if minimum_gib < 20:
        raise ValueError("Free-space safety reserve must be at least 20 GiB")
    if shutil.disk_usage(root).free < minimum_gib * 1024**3:
        raise RuntimeError(
            f"At least {minimum_gib} GiB free is required (conservative reserve, not measured full-build peak)"
        )
    subprocess.run(["xcode-select", "-p"], check=True, capture_output=True)
    subprocess.run(["xcrun", "clang", "--version"], check=True, capture_output=True)
    executable = shutil.which("llama-server")
    if not executable:
        raise RuntimeError(
            "Provision native llama.cpp build 8130 / ed4837891 with Metal first; no brew/global install is performed"
        )
    result = subprocess.run(
        [executable, "--version"], check=True, text=True, capture_output=True
    )
    if "ed4837891" not in result.stdout + result.stderr:
        raise RuntimeError(
            "Native llama-server must match validated commit ed4837891 (build 8130)"
        )
    return {
        "platform": platform.platform(),
        "python": sys.version,
        "free_bytes": shutil.disk_usage(root).free,
        "llama_server": executable,
        "llama_sha256": hashlib.sha256(Path(executable).read_bytes()).hexdigest(),
    }


def provision_environment(path, lock):
    path = path.absolute()
    if path.resolve() == Path(sys.prefix).resolve() or path.is_symlink():
        raise ValueError("Refusing invoking environment or symlink venv")
    receipt = path / "atlas-setup.json"
    expected = {
        "root": str(ROOT),
        "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
        "python": "3.12",
    }
    if path.exists():
        if not receipt.is_file() or json.loads(receipt.read_text()) != expected:
            raise ValueError(
                f"Existing venv is unowned or lock changed; preserved: {path}. Choose a new --venv path."
            )
    else:
        venv.create(path, with_pip=True)
        # Ownership written before install allows interrupted matching setup to resume.
        receipt.write_text(json.dumps(expected))
    python = path / "bin/python"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PIP_", "PYTHON"))}
    env["PIP_CONFIG_FILE"] = os.devnull
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-r",
            str(lock),
        ],
        check=True,
        env=env,
    )
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-build-isolation",
            "-e",
            str(ROOT),
        ],
        check=True,
        env=env,
    )
    subprocess.run([str(python), "-m", "pip", "check"], check=True, env=env)
    return python


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv", type=Path, default=ROOT / ".atlas-venv")
    parser.add_argument("--minimum-free-gib", type=int, default=40)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--dump-manifest", type=Path)
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    args.data = args.data.absolute()
    start = time.monotonic()
    stage = "host validation"
    try:
        print(
            json.dumps(validate_host(ROOT, args.minimum_free_gib), indent=2), flush=True
        )
        # Check the actual target filesystem too, before installing anything.
        data_parent = args.data
        while not data_parent.exists():
            data_parent = data_parent.parent
        if not os.access(data_parent, os.W_OK):
            raise RuntimeError(f"Data target is not writable: {data_parent}")
        if shutil.disk_usage(data_parent).free < args.minimum_free_gib * 1024**3:
            raise RuntimeError(
                f"Data filesystem requires {args.minimum_free_gib} GiB free"
            )
        if args.check_only:
            return
        stage = "dedicated locked environment"
        python = provision_environment(
            args.venv, ROOT / "locks/runtime-macos-arm64-py312.txt"
        )
        stage = "local assets and staged snapshot"
        command = [
            str(python),
            "-m",
            "workbench.data_build.provisioning",
            "setup",
            "--data",
            str(args.data),
        ]
        if args.dump_manifest:
            command += ["--dump-manifest", str(args.dump_manifest.absolute())]
        subprocess.run(command, check=True, cwd=ROOT)
        print(
            f"Setup complete in {time.monotonic() - start:.1f}s. Runtime Python: {python}"
        )
    except (Exception, KeyboardInterrupt) as exc:
        parser.exit(
            1,
            f"Setup failed at {stage} after {time.monotonic() - start:.1f}s: {exc}\nExisting assets/environments retained.\n",
        )


if __name__ == "__main__":
    main()
