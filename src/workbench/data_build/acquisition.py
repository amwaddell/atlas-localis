"""Explicit-only, content-verified acquisition; imports never perform I/O."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from workbench.data_build.lifecycle import atomic_json, read_json, sha256, sync_dir


def validate_artifact(spec):
    if not re.fullmatch(r"[0-9a-f]{64}", spec["sha256"]):
        raise ValueError("Artifact requires exact SHA-256")
    if spec["size"] <= 0:
        raise ValueError("Artifact requires positive byte size")
    url = spec["url"]
    if urlparse(url).scheme != "https" or any(
        x in url.lower() for x in ("/latest/", "/resolve/main/", "/resolve/master/")
    ):
        raise ValueError(
            "Use immutable HTTPS revision/date URL, never latest/main/master"
        )


def download(spec: dict, target: Path, *, opener=urlopen) -> Path:
    """Resume only a matching spec with an unchanged strong ETag and valid range.

    Without a strong server validator, restart the owned partial file. Final
    size and SHA-256 are always authoritative, including after a restart.
    """
    validate_artifact(spec)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise ValueError("Artifact target cannot be a symlink; preserved")
    if target.exists():
        if target.stat().st_size != spec["size"] or sha256(target) != spec["sha256"]:
            raise ValueError(f"Existing artifact differs; preserved: {target}")
        return target
    part = target.with_name(target.name + ".part")
    meta = target.with_name(target.name + ".download.json")
    if part.is_symlink() or target.is_symlink() or meta.is_symlink():
        raise ValueError("Artifact paths cannot be symlinks; preserved")
    state = read_json(meta) if meta.exists() else None
    if part.exists() and (state is None or state["spec"] != spec):
        raise ValueError(f"Unmatched partial source; preserved: {part}")
    offset = part.stat().st_size if part.exists() else 0
    etag = state.get("etag") if state else None
    resume = 0 < offset < spec["size"] and etag and not etag.startswith("W/")
    headers = {"Range": f"bytes={offset}-", "If-Range": etag} if resume else {}
    headers["User-Agent"] = "AtlasLocalis-Setup/0.0 (explicit artifact provisioning)"
    with opener(Request(spec["url"], headers=headers), timeout=60) as response:
        if urlparse(response.geturl()).scheme != "https":
            raise ValueError("Refusing non-HTTPS redirect")
        status = response.status
        if resume and status == 206:
            expected = f"bytes {offset}-{spec['size'] - 1}/{spec['size']}"
            if (
                response.headers.get("Content-Range") != expected
                or response.headers.get("ETag") != etag
            ):
                raise ValueError("Range/ETag mismatch; partial preserved")
            mode = "ab"
        elif status == 200:
            mode = "wb"
        else:
            raise ValueError(f"Unexpected download status {status}; partial preserved")
        atomic_json(meta, {"spec": spec, "etag": response.headers.get("ETag")})
        with part.open(mode) as stream:
            while block := response.read(1024 * 1024):
                stream.write(block)
                if stream.tell() > spec["size"]:
                    raise ValueError("Download exceeds pinned size")
            stream.flush()
            os.fsync(stream.fileno())
    if part.stat().st_size != spec["size"] or sha256(part) != spec["sha256"]:
        raise ValueError(
            f"Download checksum/size failure; retained for inspection: {part}"
        )
    os.replace(part, target)
    sync_dir(target.parent)
    return target
