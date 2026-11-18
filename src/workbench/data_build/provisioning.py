"""Explicit setup/update CLI. Runtime imports do not acquire anything."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
from pathlib import Path

from workbench.data_build.acquisition import download, validate_artifact
from workbench.data_build.lifecycle import Lease, atomic_json, read_json, sha256
from workbench.data_build.snapshots import activate, convert, prune, status, verify

ROOT = Path(__file__).resolve().parents[3]


def artifact_manifest():
    return read_json(ROOT / "configs/artifacts/models.json")


def model_path(data: Path, model: dict):
    if "GGUF" in model["id"]:
        return data / "models"
    return data / "models" / model["id"].replace("/", "--") / model["revision"]


def provision_models(data: Path):
    manifest = artifact_manifest()
    for model in manifest["models"]:
        root = model_path(data, model)
        for filename, spec in model["files"].items():
            destination = root / filename
            # Adopt verified cached bytes via a new hard link, without cache mutation.
            if not destination.exists() and "GGUF" not in model["id"]:
                cached = (
                    Path.home()
                    / ".cache/huggingface/hub"
                    / ("models--" + model["id"].replace("/", "--"))
                    / "snapshots"
                    / model["revision"]
                    / filename
                )
                if (
                    cached.is_file()
                    and cached.stat().st_size == spec["size"]
                    and sha256(cached) == spec["sha256"]
                ):
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    # Copy avoids writable aliases to an owner's cache.
                    temporary = destination.with_name(destination.name + ".adopt.part")
                    shutil.copyfile(cached, temporary)
                    os.replace(temporary, destination)
            download(spec, destination)
    atomic_json(data / "models/provisioned.json", manifest)


def verified_models(data: Path):
    manifest = artifact_manifest()
    for model in manifest["models"]:
        for filename, spec in model["files"].items():
            path = model_path(data, model) / filename
            if (
                not path.is_file()
                or path.stat().st_size != spec["size"]
                or sha256(path) != spec["sha256"]
            ):
                raise ValueError(
                    f"Missing/corrupt pinned model: {path}; run explicit setup. Update never downloads/upgrades models."
                )
    return manifest


def staged_config(data: Path, bundle: Path, manifest):
    from workbench.core.config import load_config

    config = load_config()
    config.paths["active_index"] = str(bundle)
    config.paths["indexes_dir"] = str(data / "indexes")
    for section, model in zip(
        (config.embeddings, config.reranking), manifest["models"][:2], strict=True
    ):
        section["model_path"] = str(model_path(data, model))
        section["model_name"] = model["id"]
    # Setup/build smoke is local; no trace/viewer service is required.
    config.observability["enable_tracing"] = False
    config.observability["enable_metrics"] = False
    return config


def canonical_smoke(data, bundle, manifest):
    from workbench.graphs.research_models import ConversationInput
    from workbench.models.native_process import native_smoke_server
    from workbench.systems.runner import create_application

    gguf = data / "models" / next(iter(manifest["models"][2]["files"]))
    with native_smoke_server(gguf, bundle / "native-smoke.log") as endpoint:
        config = staged_config(data, bundle, manifest)
        config.model["base_url"] = endpoint
        with create_application(config=config) as app:
            result = app.invoke(
                ConversationInput(question="When did the Roman Republic end?")
            )
            # Retain failed canonical outcomes as inspectable stage evidence too.
            atomic_json(
                bundle / "canonical-result.json", result.model_dump(mode="json")
            )
            import platform
            import subprocess

            binary = shutil.which("llama-server")
            version = subprocess.run(
                [binary, "--version"], text=True, capture_output=True, check=True
            )
            return {
                "native_binary_sha256": sha256(Path(binary)),
                "native_version": version.stdout + version.stderr,
                "platform": platform.platform(),
                "question": "When did the Roman Republic end?",
                "outcome": result.outcome,
                "citations": len(result.citations),
                "models": [m["revision"] for m in manifest["models"]],
            }


def build(data: Path, dump_manifest: Path):
    from workbench.data_build.embeddings import SentenceTransformerEmbedder
    from workbench.data_build.pipeline import build_bundle

    manifest = verified_models(data)
    if dump_manifest is None:
        dump_manifest = ROOT / "configs/artifacts/wikipedia.json"
    if not dump_manifest.is_file():
        raise ValueError(
            "No dated dump pin is verified. Supply --dump-manifest with immutable dated URL, identity, size, SHA-256 and license_reference; legacy latest date remains unknown."
        )
    source = read_json(dump_manifest)
    validate_artifact(source)
    date = re.search(r"/simplewiki/(\d{8})/", source["url"])
    if (
        not date
        or source["identity"] != "simplewiki-" + date.group(1)
        or not source.get("license_reference")
    ):
        raise ValueError(
            "Dump requires matching simplewiki-YYYYMMDD identity, dated source URL and license_reference"
        )
    dump = download(
        source,
        data / "raw/wikipedia" / (source["identity"] + "-pages-articles.xml.bz2"),
    )
    embedding = manifest["embedding"]
    embedder = SentenceTransformerEmbedder(
        model_name=embedding["id"],
        model_path=str(model_path(data, manifest["models"][0])),
        device="mps",
        batch_size=4,
    )
    with Lease(data / "indexes/.build.lock"):
        result = build_bundle(
            data / "indexes",
            dump,
            source,
            embedding,
            embedder,
            lambda bundle: canonical_smoke(data, bundle, manifest),
            batch_size=32,
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "setup",
            "update",
            "status",
            "activate",
            "rollback",
            "convert",
            "prune",
            "verify",
        ],
    )
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--dump-manifest", type=Path)
    parser.add_argument("--snapshot")
    parser.add_argument("--app-stopped", action="store_true")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    data = args.data.absolute()
    indexes = data / "indexes"
    start = time.monotonic()
    stage = args.command
    try:
        if args.command == "status":
            print(json.dumps(status(indexes), indent=2))
            return
        if args.command in {"setup", "update"}:
            data.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(data).free < 20 * 1024**3:
                raise ValueError(
                    "At least 20 GiB free required alongside retained data"
                )
            if args.command == "setup":
                stage = "model provisioning"
                with Lease(data / "models/.provision.lock"):
                    provision_models(data)
                if (indexes / "active").is_symlink() and args.dump_manifest is None:
                    verify((indexes / "active").resolve())
                    print(
                        "Setup rerun: pinned models and active inventory verified; unchanged."
                    )
                    return
            stage = "snapshot acquisition/build/validation"
            result = build(data, args.dump_manifest)
            stage = "activation"
            with Lease(indexes / ".lifecycle.lock"):
                activate(indexes, result.name)
            print(f"Activated {result.name}")
        else:
            if not args.snapshot and args.command not in {"convert"}:
                raise ValueError("--snapshot NAME required")
            with Lease(indexes / ".lifecycle.lock"):
                if args.command == "convert":
                    print(
                        convert(
                            indexes, data / "processed", app_stopped=args.app_stopped
                        )
                    )
                elif args.command == "prune":
                    prune(indexes, args.snapshot, confirm=args.confirm)
                elif args.command == "verify":
                    from workbench.data_build.snapshots import managed

                    print(json.dumps(verify(managed(indexes, args.snapshot)), indent=2))
                else:
                    activate(
                        indexes, args.snapshot, rollback=args.command == "rollback"
                    )
        print(f"{args.command} finished in {time.monotonic() - start:.2f}s")
    except (Exception, KeyboardInterrupt) as exc:
        parser.exit(
            1,
            f"{stage} failed after {time.monotonic() - start:.2f}s: {exc}\nActive data and checkpoints retained; use status before retry.\n",
        )


if __name__ == "__main__":
    main()
