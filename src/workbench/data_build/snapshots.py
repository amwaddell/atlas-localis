"""Immutable bundle verification, recoverable legacy conversion and explicit retention."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from workbench.data_build.lifecycle import atomic_json, fingerprint, read_json, sync_dir
from workbench.data_build.pipeline import inventory


def managed(indexes: Path, name: str) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", name):
        raise ValueError("Snapshot must be a single safe name")
    path = indexes / "snapshots" / name
    if (
        (indexes / "snapshots").is_symlink()
        or path.is_symlink()
        or path.resolve().parent != (indexes / "snapshots").resolve()
    ):
        raise ValueError("Snapshot escapes managed root")
    return path


def verify(path: Path) -> dict:
    manifest = read_json(path / "manifest.json")
    if manifest["format"] != 1 or manifest["files"] != inventory(path):
        raise ValueError(f"Bundle inventory mismatch: {path}")
    if manifest["provenance"] == "verified-build":
        if manifest["id"] != fingerprint(manifest["inputs"]):
            raise ValueError("Bundle input fingerprint mismatch")
        if (
            manifest["validation"]["chunks"] <= 0
            or manifest["canonical_smoke"].get("outcome") not in {"complete", "partial"}
            or not manifest["canonical_smoke"].get("citations")
        ):
            raise ValueError("Bundle missing validation/canonical smoke")
    elif manifest["provenance"] != "legacy-unknown":
        raise ValueError("Unknown bundle provenance")
    return manifest


def point(indexes: Path, destination: Path):
    active = indexes / "active"
    if active.exists() and not active.is_symlink():
        raise ValueError(
            "Active is a real directory. Stop app and run convert; nothing removed."
        )
    if destination.stat().st_dev != indexes.stat().st_dev:
        raise ValueError("Promotion requires same filesystem")
    temporary = indexes / ".active-next"
    if temporary.is_symlink():
        if temporary.resolve() != destination.resolve():
            raise ValueError("Unmatched .active-next target; preserved for inspection")
    elif temporary.exists():
        raise ValueError("Unowned .active-next path; preserved")
    else:
        temporary.symlink_to(
            os.path.relpath(destination, indexes), target_is_directory=True
        )
    os.replace(temporary, active)
    sync_dir(indexes)


def activate(indexes: Path, name: str, *, rollback=False):
    destination = managed(indexes, name)
    manifest = verify(destination)
    if manifest["provenance"] != "verified-build" and not rollback:
        raise ValueError("Legacy bundle allowed only for explicit rollback")
    active = indexes / "active"
    previous = active.resolve() if active.is_symlink() else None
    # Intent journal survives a kill between symlink replacement and receipt.
    atomic_json(
        indexes / "promotion.json",
        {
            "previous": str(previous) if previous else None,
            "target": name,
            "state": "pending",
        },
    )
    point(indexes, destination)
    atomic_json(
        indexes / "promotion.json",
        {
            "previous": str(previous) if previous else None,
            "target": name,
            "state": "complete",
        },
    )


def convert(indexes: Path, processed: Path, *, app_stopped=False):
    """Recover after each rename; preserve original index bytes and source parquets."""
    if not app_stopped:
        raise ValueError(
            "Conversion requires --app-stopped after stopping all old apps"
        )
    active = indexes / "active"
    journal = indexes / "conversion.json"
    if journal.exists():
        state = read_json(journal)
        destination = managed(indexes, state["target"])
        if active.is_symlink():
            if active.resolve() != destination.resolve():
                raise ValueError("Conversion journal conflicts with active pointer")
            verify(destination)
            return destination
    else:
        if active.is_symlink() or not (active / "lancedb").is_dir():
            raise ValueError("Expected legacy real active/lancedb directory")
        name = "legacy-preserved"
        destination = managed(indexes, name)
        if destination.exists():
            raise ValueError("Legacy destination exists without conversion journal")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Retain the Stage 2/3 cache identity used by already saved runs.
        pinned = active.resolve()
        identity = [
            (str(p.relative_to(pinned)), p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted((pinned / "lancedb").glob("**/_versions/*.manifest"))
        ]
        legacy_id = "local-" + fingerprint((str(pinned), identity))
        aliases = (
            read_json(indexes / "aliases.json")
            if (indexes / "aliases.json").exists()
            else {}
        )
        aliases[legacy_id] = name
        atomic_json(indexes / "aliases.json", aliases)
        atomic_json(journal, {"target": name, "state": "prepared"})
    if active.is_dir() and not active.is_symlink():
        if destination.exists():
            raise ValueError("Both legacy paths exist; inspect conversion journal")
        os.rename(active, destination)
        sync_dir(indexes)
    if not destination.is_dir():
        raise ValueError("Conversion source/destination missing; inspect journal")
    for source, target in [
        ("wikipedia_articles.parquet", "articles.parquet"),
        ("chunks.parquet", "chunks.parquet"),
    ]:
        out = destination / target
        if not out.exists():
            part = out.with_suffix(".part")
            shutil.copyfile(processed / source, part)
            os.replace(part, out)
    if not (destination / "manifest.json").exists():
        atomic_json(
            destination / "manifest.json",
            {
                "format": 1,
                "id": destination.name,
                "provenance": "legacy-unknown",
                "notes": "Observed bytes only. Dump date, build/model provenance and full compatibility unknown; no canonical validation asserted.",
                "files": inventory(destination),
            },
        )
    verify(destination)
    point(indexes, destination)
    atomic_json(journal, {"target": destination.name, "state": "complete"})
    return destination


def status(indexes: Path):
    active = indexes / "active"
    return {
        "active": str(active.resolve()) if active.exists() else None,
        "layout": "symlink"
        if active.is_symlink()
        else "legacy-directory"
        if active.is_dir()
        else "missing",
        "snapshots": [
            p.name for p in sorted((indexes / "snapshots").glob("*")) if p.is_dir()
        ],
        "builds": [
            {
                "name": p.name,
                "progress": read_json(p / "progress.json")
                if (p / "progress.json").exists()
                else None,
            }
            for p in sorted(indexes.glob(".build-*"))
            if p.is_dir()
        ],
        "active_manifest": read_json(active / "manifest.json")
        if (active / "manifest.json").is_file()
        else {"provenance": "legacy-unknown"}
        if active.is_dir()
        else None,
        "promotion": read_json(indexes / "promotion.json")
        if (indexes / "promotion.json").exists()
        else None,
        "conversion": read_json(indexes / "conversion.json")
        if (indexes / "conversion.json").exists()
        else None,
    }


def prune(indexes: Path, name: str, *, confirm=False):
    """Remove only a named inactive index, retaining verified citation parquets."""
    path = managed(indexes, name)
    if path == (indexes / "active").resolve():
        raise ValueError("Cannot prune active snapshot")
    if not confirm:
        raise ValueError("Explicit --confirm required; citation archive is retained")
    tombstone = indexes / (".prune-" + name)
    if tombstone.is_symlink():
        raise ValueError("Prune tombstone cannot be a symlink")
    archive = indexes / "citations" / name
    if archive.is_symlink() or archive.parent.is_symlink():
        raise ValueError("Citation archive cannot be a symlink")
    if tombstone.exists():
        if path.exists():
            raise ValueError("Both prune source and tombstone exist; preserved")
        manifest = read_json(archive / "manifest.json")
    else:
        manifest = verify(path)
    archive.mkdir(parents=True, exist_ok=True)
    for filename in ("articles.parquet", "chunks.parquet"):
        destination = archive / filename
        expected = manifest["files"][filename]
        if destination.is_symlink():
            raise ValueError("Citation archive file cannot be a symlink")
        if not destination.exists():
            os.link(path / filename, destination)
        from workbench.data_build.lifecycle import sha256

        if (
            destination.stat().st_size != expected["size"]
            or sha256(destination) != expected["sha256"]
        ):
            raise ValueError("Citation archive mismatch; snapshot retained")
    atomic_json(archive / "manifest.json", manifest)
    sync_dir(archive)
    if not tombstone.exists():
        os.rename(path, tombstone)
        sync_dir(path.parent)
        sync_dir(indexes)
    shutil.rmtree(tombstone)
    sync_dir(indexes)


def citation_store(indexes: Path, snapshot: str):
    """Resolve old-run evidence without following today's active pointer."""
    from workbench.stores.chunk_store import ChunkStore

    aliases = (
        read_json(indexes / "aliases.json")
        if (indexes / "aliases.json").exists()
        else {}
    )
    snapshot = aliases.get(snapshot, snapshot)
    path = managed(indexes, snapshot)
    if (path / "manifest.json").is_file():
        return ChunkStore(path / "lancedb")
    archive = indexes / "citations" / snapshot
    manifest = read_json(archive / "manifest.json")
    from workbench.data_build.lifecycle import sha256

    if (
        sha256(archive / "chunks.parquet")
        != manifest["files"]["chunks.parquet"]["sha256"]
    ):
        raise ValueError("Citation archive corrupt")

    class ArchiveStore:
        def get_many(self, ids):
            import pyarrow.dataset as ds

            table = ds.dataset(archive / "chunks.parquet").to_table(
                filter=ds.field("chunk_id").isin(ids)
            )
            rows = {r["chunk_id"]: r for r in table.to_pylist()}
            return [ChunkStore._row_to_chunk(rows[cid]) for cid in ids if cid in rows]

        def get(self, cid):
            rows = self.get_many([cid])
            return rows[0] if rows else None

    return ArchiveStore()
