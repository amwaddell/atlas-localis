"""Record the installed dependency closure; run only in a deliberately validated env.

pyproject.toml is authoritative. This never treats unrelated installed packages
as application dependencies. Lock updates are explicit, never part of refresh.
"""

import importlib.metadata as metadata
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]


def closure(requirements):
    found = {}
    pending = list(requirements)
    visited = set()
    while pending:
        requirement = Requirement(pending.pop())
        extras = requirement.extras | {""}
        if requirement.marker and not any(
            requirement.marker.evaluate({"extra": x}) for x in extras
        ):
            continue
        name = canonicalize_name(requirement.name)
        dist = metadata.distribution(name)
        if dist.version not in requirement.specifier:
            raise ValueError(f"Installed {name}=={dist.version} violates {requirement}")
        found[name] = dist.version
        for extra in extras:
            if (name, extra) in visited:
                continue
            visited.add((name, extra))
            for dep in dist.requires or []:
                req = Requirement(dep)
                if not req.marker or req.marker.evaluate({"extra": extra}):
                    req.marker = None
                    pending.append(str(req))
    return found


def main():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    base = project["dependencies"]
    for kind, extra in [
        ("runtime", []),
        ("dev", project["optional-dependencies"]["dev"]),
        ("phoenix", project["optional-dependencies"]["phoenix"]),
    ]:
        packages = closure(base + extra + ["setuptools==82.0.0", "wheel==0.46.3"])
        path = ROOT / "locks" / f"{kind}-macos-arm64-py312.txt"
        path.write_text(
            "# Generated from pyproject + installed closure; CPython 3.12 / macOS arm64.\n"
            + "".join(f"{k}=={v}\n" for k, v in sorted(packages.items()))
        )
        print(path.name, len(packages))


if __name__ == "__main__":
    main()
