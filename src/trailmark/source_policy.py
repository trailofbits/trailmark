"""Replayable source selection with explicit roots and file-level reasons."""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from trailmark._path_glob import compile_path_glob
from trailmark.parsers._common import should_skip_dir

SourceCategory = Literal["authored", "dependency", "generated", "test_fixture", "unknown"]


@dataclass(frozen=True)
class SourceRoot:
    """A deliberately selected root; its parent exclusions do not apply."""

    path: str
    name: str
    category: SourceCategory = "authored"


@dataclass(frozen=True)
class SourcePolicy:
    """Ordered roots, relative glob exclusions and deliberate include overrides.

    Exclusions are relative to each root. Defaults retain the parser's existing
    directory exclusions; additional generated/cache exclusions require explicit globs.
    """

    roots: tuple[SourceRoot, ...]
    exclude: tuple[str, ...] = ()
    include: tuple[str, ...] = ()
    default_exclusions: bool = True

    def __post_init__(self) -> None:
        if not self.roots:
            raise ValueError("At least one source root is required")
        names = [r.name for r in self.roots]
        if len(set(names)) != len(names) or any(not n or ":" in n for n in names):
            raise ValueError("Source root names must be unique, nonempty and contain no colon")

    def discover(self) -> dict[str, object]:
        """Return a deterministic manifest; canonical files belong to the first root."""
        seen: set[Path] = set()
        roots: list[dict[str, object]] = []
        files: list[dict[str, str]] = []
        for selected in self.roots:
            root = Path(os.path.abspath(selected.path))
            canonical_root = root.resolve()
            if not root.is_dir():
                raise ValueError(f"Not a directory: {selected.path}")
            counts: dict[str, int] = {}
            roots.append({**asdict(selected), "path": str(root), "counts": counts})
            for directory, dirs, names in os.walk(root, followlinks=False):
                dirs.sort()
                for name in list(dirs):
                    subdir = Path(directory) / name
                    relative_dir = subdir.relative_to(root).as_posix()
                    reason = ""
                    if subdir.is_symlink():
                        reason = "symlink_directory"
                    elif name in {".git", ".hg", ".svn"}:
                        reason = "vcs_directory"
                    elif not _may_select_below(relative_dir, self.include):
                        if _excluded(relative_dir, self.exclude):
                            reason = "excluded_pattern"
                        elif self.default_exclusions and should_skip_dir(name):
                            reason = "default_directory"
                    if reason:
                        dirs.remove(name)
                        files.append(
                            {
                                "path": str(subdir),
                                "root": selected.name,
                                "status": "skipped",
                                "reason": reason,
                                "category": "unknown",
                                "unit": "directory",
                            }
                        )
                        counts[reason] = counts.get(reason, 0) + 1
                for name in sorted(names):
                    path = Path(directory) / name
                    relative = path.relative_to(root).as_posix()
                    canonical = path.resolve()
                    included = _matches(relative, self.include)
                    reason = "selected"
                    status = "inspected"
                    if canonical in seen:
                        status, reason = "skipped", "duplicate"
                    elif not canonical.is_relative_to(canonical_root):
                        status, reason = "skipped", "symlink_out_of_root"
                    elif not included and _excluded(relative, self.exclude):
                        status, reason = "skipped", "excluded_pattern"
                    elif (
                        not included
                        and self.default_exclusions
                        and any(should_skip_dir(part) for part in Path(relative).parts[:-1])
                    ):
                        status, reason = "skipped", "default_directory"
                    if status == "inspected":
                        seen.add(canonical)
                    files.append(
                        {
                            "path": str(path),
                            "root": selected.name,
                            "status": status,
                            "reason": reason,
                            "category": selected.category,
                            "unit": "file",
                        }
                    )
                    counts[reason] = counts.get(reason, 0) + 1
        return {
            "schema_version": 1,
            "roots": roots,
            "files": files,
            "exclude": list(self.exclude),
            "include": list(self.include),
            "default_exclusions": self.default_exclusions,
        }


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(compile_path_glob(pattern).fullmatch(path) is not None for pattern in patterns)


def _excluded(path: str, patterns: tuple[str, ...]) -> bool:
    """A matched directory excludes descendants even if an include keeps it open."""
    if not patterns:
        return False
    normalized = tuple(pattern.rstrip("/").removesuffix("/**") for pattern in patterns)
    parts = path.split("/")
    return _matches(path, patterns) or any(
        _matches("/".join(parts[:end]), normalized) for end in range(1, len(parts) + 1)
    )


def _may_select_below(directory: str, patterns: tuple[str, ...]) -> bool:
    """Keep excluded directories open only when an include could apply inside."""
    for pattern in patterns:
        if "**" not in pattern and len(pattern.split("/")) <= len(directory.split("/")):
            continue
        prefix = re.split(r"[*?\[]", pattern, maxsplit=1)[0]
        if prefix.startswith(directory + "/") or (directory + "/").startswith(prefix):
            return True
    return False
