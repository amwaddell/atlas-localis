"""Validate launcher environment/assets without acquiring anything."""

import argparse
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from workbench.core.offline import (  # noqa: E402
    cached_model_path,
    local_url,
    validate_environment,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model")
    parser.add_argument("--free-port", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default="8080")
    args = parser.parse_args()
    validate_environment()
    if (
        not args.port.isascii()
        or not args.port.isdecimal()
        or not 1 <= int(args.port) <= 65535
    ):
        parser.error("port must be an integer from 1 to 65535")
    # Launchers accept only the canonical numeric bind addresses.
    if args.host not in {"127.0.0.1", "::1"}:
        parser.error("bind host must be 127.0.0.1 or ::1")
    host = f"[{args.host}]" if ":" in args.host else args.host
    local_url(f"http://{host}:{args.port}")
    if args.free_port:
        family = socket.AF_INET6 if ":" in args.host else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((args.host, int(args.port)))
            except OSError:
                parser.error(
                    "native server port is already occupied; preserve the existing process"
                )
    if args.model:
        print(cached_model_path(args.model))


if __name__ == "__main__":
    main()
