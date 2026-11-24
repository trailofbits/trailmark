"""Bind shared mutation baseline checks to their exact execution inputs."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from importlib.metadata import distributions
from pathlib import Path
from typing import Any


def _input_files(root: Path) -> list[Path]:
    inventory = subprocess.run(  # noqa: S603
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],  # noqa: S607
        cwd=root,
        capture_output=True,
        check=False,
    )
    if inventory.returncode == 0:
        return sorted({root / os.fsdecode(name) for name in inventory.stdout.split(b"\0") if name})
    # Small standalone projects in the runner's subprocess controls need no Git repository.
    paths = []
    for directory, children, files in os.walk(root):
        children[:] = [
            name
            for name in children
            if not name.startswith(".") and name not in {"__pycache__", "coverage", "node_modules"}
        ]
        paths.extend(Path(directory) / name for name in files if not name.endswith(".pyc"))
    return sorted(paths)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _file_digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def input_fingerprint(root: Path) -> str:
    files = {
        path.relative_to(root).as_posix(): _file_digest(path)
        for path in _input_files(root)
        if path.is_file()
    }
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.startswith("TRAILMARK_")
        or key
        in {
            "PYTEST_ADDOPTS",
            "PYTEST_PLUGINS",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
            "PYTHONHASHSEED",
            "PYTHONPATH",
        }
    }
    packages = sorted((dist.metadata["Name"], dist.version) for dist in distributions())
    return digest(
        {
            "files": files,
            "python": sys.version,
            "platform": [sys.platform, platform.machine()],
            "packages": packages,
            "environment": environment,
            "native_tools": {
                key: _file_digest(Path(value)) if Path(value).is_file() else None
                for key, value in environment.items()
                if key.startswith(("TRAILMARK_LLVM_", "TRAILMARK_GNU_"))
            },
        }
    )


def baseline_receipt(
    context: dict[str, Any], orders: list[tuple[str, ...]], shard: tuple[int, int]
) -> dict[str, Any]:
    return _partition_receipt(context, [digest(order) for order in orders], shard)


def _partition_receipt(
    context: dict[str, Any], identifiers: list[str], shard: tuple[int, int]
) -> dict[str, Any]:
    index, count = shard
    return {
        "context": context,
        "orders": identifiers,
        "shard": list(shard),
        "checked": identifiers[index - 1 :: count],
    }


def validate_baselines(
    directory: Path,
    context: dict[str, Any],
    orders: list[tuple[str, ...]],
) -> None:
    paths = sorted(directory.glob("baseline-*.json"))
    if not paths:
        raise ValueError("Missing shared mutation baselines")
    receipts = [json.loads(path.read_text()) for path in paths]
    first_shard = receipts[0]["shard"]
    if not isinstance(first_shard, list) or len(first_shard) != 2:
        raise ValueError("Invalid shared mutation baseline partition")
    count = first_shard[1]
    if type(count) is not int or count != len(receipts):
        raise ValueError("Incomplete shared mutation baselines")
    by_partition = {tuple(receipt["shard"]): receipt for receipt in receipts}
    identifiers = [digest(order) for order in orders]
    expected = {
        (index, count): _partition_receipt(context, identifiers, (index, count))
        for index in range(1, count + 1)
    }
    if len(by_partition) != len(receipts) or by_partition != expected:
        raise ValueError("Stale, incomplete, or mismatched shared mutation baselines")
