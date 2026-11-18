"""Runtime network boundary. Acquisition belongs in explicit setup/update tools."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def local_url(value: str) -> str:
    """Validate without DNS; normalize the exact localhost alias to IPv4 loopback."""
    try:
        if not isinstance(value, str):
            raise ValueError
        url = urlsplit(value)
        host = url.hostname
        if host == "localhost":
            host = "127.0.0.1"
        address = ipaddress.ip_address(host or "")
        if (
            url.scheme not in {"http", "https"}
            or not address.is_loopback
            or address.is_unspecified
            or (
                isinstance(address, ipaddress.IPv6Address)
                and address.ipv4_mapped is not None
            )
            or "%" in (host or "")
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or any(c.isspace() for c in value)
        ):
            raise ValueError
        port = url.port
        authority = f"[{address}]" if address.version == 6 else str(address)
        if port is not None:
            if not 1 <= port <= 65535:
                raise ValueError
            authority += f":{port}"
        return urlunsplit((url.scheme, authority, url.path, "", "")).rstrip("/")
    except (ValueError, TypeError) as exc:
        raise ValueError(
            "Runtime endpoints must use loopback HTTP(S), without credentials/query/fragment"
        ) from exc


def local_path(value: str | Path) -> Path:
    """Reject cloud/URI paths before passing them to storage/model libraries."""
    if ":" in str(value) or str(value).startswith("//"):
        raise ValueError("Runtime assets must use local filesystem paths")
    return Path(value).expanduser()


def validate_environment() -> None:
    """Reject cloud telemetry overrides even when tracing is currently disabled."""
    for key, value in os.environ.items():
        if not value:
            continue
        if (
            (key.startswith("OTEL_EXPORTER_") and key.endswith("ENDPOINT"))
            or (key.startswith("PHOENIX_") and key.endswith("ENDPOINT"))
            or key
            in {
                "PHOENIX_ENDPOINT",
                "PHOENIX_COLLECTOR_ENDPOINT",
                "LANGCHAIN_ENDPOINT",
                "LANGSMITH_ENDPOINT",
            }
        ):
            local_url(value)
        if key == "PHOENIX_SQL_DATABASE_URL" and not value.startswith(
            ("sqlite:///", "sqlite+aiosqlite:///")
        ):
            raise ValueError("Phoenix storage must be local SQLite")
        if (
            key.startswith(("PHOENIX_POSTGRES_", "PHOENIX_OAUTH2_", "PHOENIX_LDAP_"))
            or key == "PHOENIX_SMTP_HOSTNAME"
        ):
            raise ValueError(f"{key} enables external viewer services; unset it")
        if key in {"PHOENIX_API_KEY", "LANGCHAIN_API_KEY", "LANGSMITH_API_KEY"}:
            raise ValueError(f"{key} is unsupported in offline runtime; unset it")
        if key in {
            "LANGCHAIN_TRACING",
            "LANGCHAIN_TRACING_V2",
            "LANGSMITH_TRACING",
        } and value.lower() not in {"false", "0", "no"}:
            raise ValueError(f"{key} enables external tracing; unset it")
        if key in {
            "OTEL_EXPERIMENTAL_RESOURCE_DETECTORS",
            "OTEL_PYTHON_TRACER_PROVIDER",
            "OTEL_PYTHON_METER_PROVIDER",
            "OTEL_PYTHON_LOGGER_PROVIDER",
        }:
            raise ValueError(f"{key} can load external providers; unset it")
        if key in {
            "OTEL_TRACES_EXPORTER",
            "OTEL_METRICS_EXPORTER",
            "OTEL_LOGS_EXPORTER",
        } and value not in {"none", "console"}:
            raise ValueError(
                f"{key} auto-export is unsupported; use explicit local Phoenix"
            )


def cached_model_path(model_name: str) -> str:
    """Resolve an existing snapshot only. Missing/incomplete assets fail offline."""
    validate_environment()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    candidate = local_path(model_name)
    if candidate.is_dir():
        return str(candidate.resolve())
    if candidate.is_absolute() or model_name.startswith((".", "~")):
        raise FileNotFoundError(
            f"Model assets missing: {model_name}. Provision during explicit online setup/update."
        )
    # Explicit setup materializes complete pinned snapshots outside ambient caches.
    import json
    root = Path(__file__).resolve().parents[3]
    receipt = root / "data/models/provisioned.json"
    if receipt.is_file():
        for model in json.loads(receipt.read_text())["models"]:
            if model["id"] == model_name:
                provisioned = root / "data/models" / model_name.replace("/", "--") / model["revision"]
                if not provisioned.is_dir():
                    raise FileNotFoundError(f"Provisioned pinned model missing: {provisioned}")
                return str(provisioned)
    from huggingface_hub import snapshot_download

    try:
        return snapshot_download(repo_id=model_name, local_files_only=True)
    except Exception as exc:
        raise FileNotFoundError(
            f"Model cache missing: {model_name}. Provision during explicit online setup/update."
        ) from exc
