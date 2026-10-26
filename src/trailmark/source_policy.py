"""Replayable source selection with explicit roots and file-level reasons."""

from __future__ import annotations

import fnmatch
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

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

    Exclusions are relative to each root. Auxiliary files can be consumed as
    registration evidence but never parsed as ordinary implementations.
    """

    roots: tuple[SourceRoot, ...]
    exclude: tuple[str, ...] = ("var/cache/**", "generated/**")
    include: tuple[str, ...] = ()
    auxiliary: tuple[str, ...] = ()
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
            root = Path(selected.path).resolve()
            if not root.is_dir():
                raise ValueError(f"Not a directory: {selected.path}")
            counts: dict[str, int] = {}
            roots.append({**asdict(selected), "path": str(root), "counts": counts})
            for directory, dirs, names in os.walk(root, followlinks=False):
                dirs.sort()
                # VCS object stores have no source or registration evidence.
                for name in list(dirs):
                    subdir = Path(directory) / name
                    if subdir.is_symlink() or name in {".git", ".hg", ".svn"}:
                        dirs.remove(name)
                        reason = "symlink_directory" if subdir.is_symlink() else "vcs_directory"
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
                    auxiliary = _matches(relative, self.auxiliary)
                    generated = _matches(relative, ("var/cache/**", "generated/**"))
                    category = "generated" if generated else selected.category
                    if any(
                        p in {"test", "tests", "fixtures", "__tests__", "e2e"}
                        for p in Path(relative).parts
                    ):
                        category = "test_fixture"
                    reason = "selected"
                    status = "inspected"
                    if canonical in seen:
                        status, reason = "skipped", "duplicate"
                    elif not canonical.is_relative_to(root):
                        status, reason = "skipped", "symlink_out_of_root"
                    elif auxiliary:
                        status, reason = "auxiliary", "explicit_auxiliary"
                    elif not included and _matches(relative, self.exclude):
                        status, reason = "skipped", "excluded_pattern"
                    elif (
                        not included
                        and self.default_exclusions
                        and any(should_skip_dir(part) for part in Path(relative).parts[:-1])
                    ):
                        status, reason = "skipped", "default_directory"
                    if status in {"inspected", "auxiliary"}:
                        seen.add(canonical)
                    files.append(
                        {
                            "path": str(path),
                            "root": selected.name,
                            "status": status,
                            "reason": reason,
                            "category": category,
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
            "auxiliary": list(self.auxiliary),
            "default_exclusions": self.default_exclusions,
        }


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)
