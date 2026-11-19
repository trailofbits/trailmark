"""Artifact integrity and subprocess helpers shared by the evaluation runner."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def write_json(path: Path, value: Any) -> None:
    """Atomically publish one complete artifact; never expose partial JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = stream.name
    os.replace(temporary, path)


def command(args: list[str], *, cwd: Path | None = None, timeout: int = 300) -> str:
    return subprocess.check_output(  # noqa: S603
        args, cwd=cwd, text=True, stderr=subprocess.STDOUT, timeout=timeout
    ).strip()


def source_inventory(root: Path) -> list[dict[str, Any]]:
    """Census tracked source independently of Trailmark discovery and parsing."""
    names = (
        subprocess.check_output(  # noqa: S603
            ["git", "-C", str(root), "ls-files", "-z"]  # noqa: S607
        )
        .decode()
        .split("\0")
    )
    result = []
    for name in sorted(filter(None, names)):
        path = root / name
        if path.is_file() and not path.is_symlink():
            result.append({"path": name, "sha256": file_digest(path), "size": path.stat().st_size})
    return result


def case_partition(case_id: str, index: int, total: int) -> bool:
    if not 1 <= index <= total:
        raise ValueError("Shard must satisfy 1 <= index <= total")
    return int(hashlib.sha256(case_id.encode()).hexdigest(), 16) % total == index - 1
