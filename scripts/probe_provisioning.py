#!/usr/bin/env python3
"""Explicit cached-model lifecycle acceptance in a NEW isolated directory.

No acquisition. Owns only a native server started here on the requested port.
Never targets or converts the owner's active index.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

from native_runtime import stop_owned
from workbench.core.config import load_config
from workbench.data_build.embeddings import SentenceTransformerEmbedder
from workbench.data_build.lifecycle import Lease, sha256
from workbench.data_build.pipeline import build_bundle
from workbench.data_build.provisioning import artifact_manifest
from workbench.data_build.snapshots import activate, citation_store, verify
from workbench.graphs.research_models import ConversationInput
from workbench.models.llamacpp_server import LlamaCppServerModel
from workbench.systems.runner import create_application


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--port", default=18080, type=int)
    args = parser.parse_args()
    root = args.root.absolute()
    root.mkdir(exist_ok=args.resume, parents=True)
    text = (
        "The Roman Republic was a period of ancient Roman civilization. "
        "The Roman Republic ended in 27 BC when Augustus became the first Roman emperor. "
        "Octavian defeated Mark Antony at the Battle of Actium in 31 BC. "
        "In 27 BC the Roman Senate gave Octavian the title Augustus. "
    )
    dump = root / "tiny.xml"
    dump.write_text(
        '<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/"><page><title>Roman Republic</title><ns>0</ns><id>1</id><revision><text>'
        + text
        + "</text></revision></page></mediawiki>"
    )
    manifest = artifact_manifest()
    embedding = manifest["embedding"]
    start = time.monotonic()
    child = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("native_runtime.py"))],
        env={**os.environ, "LLM_PORT": str(args.port)},
        start_new_session=True,
        stdout=(root / "native.log").open("w"),
        stderr=subprocess.STDOUT,
    )
    model = LlamaCppServerModel(
        base_url=f"http://127.0.0.1:{args.port}",
        model_name="ministral-3-8b-instruct",
        timeout_seconds=2,
    )
    try:
        deadline = time.monotonic() + 120
        while True:
            if child.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Owned native startup failed; see native.log")
            try:
                model.check_identity()
                break
            except Exception:
                time.sleep(0.25)
        embedder = SentenceTransformerEmbedder(device="mps", batch_size=4)
        reports = []

        def smoke(bundle):
            config = load_config()
            config.paths["active_index"] = str(bundle)
            config.paths["indexes_dir"] = str(root / "indexes")
            config.model["base_url"] = f"http://127.0.0.1:{args.port}"
            config.observability["enable_tracing"] = False
            config.observability["enable_metrics"] = False
            with create_application(config=config) as app:
                result = app.invoke(
                    ConversationInput(question="When did the Roman Republic end?")
                )
                reports.append(result.model_dump(mode="json"))
                (root / "canonical-result.json").write_text(json.dumps(reports[-1], indent=2))
                return {
                    "outcome": result.outcome,
                    "citations": len(result.citations),
                    "models": [m["revision"] for m in manifest["models"]],
                }

        with Lease(root / "indexes/.build.lock"):
            first = build_bundle(
                root / "indexes",
                dump,
                {"identity": "synthetic-rome-fixture", "sha256": sha256(dump)},
                embedding,
                embedder,
                smoke,
                batch_size=2,
            )
            rerun = build_bundle(
                root / "indexes",
                dump,
                {"identity": "synthetic-rome-fixture", "sha256": sha256(dump)},
                embedding,
                embedder,
                smoke,
                batch_size=2,
            )
            assert rerun == first
        with Lease(root / "indexes/.lifecycle.lock"):
            activate(root / "indexes", first.name)
        cid = reports[0]["citations"][0]["excerpts"][0]["key"]["chunk_id"]
        assert citation_store(root / "indexes", first.name).get(cid)
        report = {
            "elapsed_s": time.monotonic() - start,
            "peak_process_rss_bytes": resource.getrusage(
                resource.RUSAGE_SELF
            ).ru_maxrss,
            "bundle": str(first),
            "manifest": verify(first),
            "runs": reports,
            "idempotent_rerun": True,
        }
        import torch

        report["metal_available"] = torch.backends.mps.is_available()
        if torch.backends.mps.is_available():
            report["metal_driver_bytes_at_end"] = torch.mps.driver_allocated_memory()
        (root / "report.json").write_text(json.dumps(report, indent=2))
        print(
            json.dumps(
                {k: v for k, v in report.items() if k not in {"manifest", "runs"}},
                indent=2,
            )
        )
    finally:
        model.close()
        stop_owned(child)


if __name__ == "__main__":
    main()
