"""Check sdist document selection in an isolated Hatchling environment.

Run with: uv --no-config run --no-project --with hatchling python tests/sdist_smoke.py
The small fixture disables the native grammar build hook.
"""

from __future__ import annotations

import copy
import shutil
import tarfile
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

PUBLIC_GUIDES = {
    "docs/augmenting.md",
    "docs/call-resolution.md",
    "docs/cli.md",
    "docs/entrypoint-patterns.md",
    "docs/entrypoints.md",
    "docs/graph-model.md",
    "docs/installation.md",
    "docs/languages.md",
    "docs/links.md",
    "docs/python-api.md",
    "docs/parser-coverage.md",
    "docs/skills-compatibility.md",
}


def _archive_files(root: Path, config: dict[str, Any], output: Path) -> set[str]:
    # Hatchling is supplied by the isolated build environment, as in hatch_build.py.
    from hatchling.builders.sdist import SdistBuilder  # ty: ignore[unresolved-import]

    (archive,) = SdistBuilder(str(root), config=config).build(directory=str(output))
    with tarfile.open(archive) as source:
        return {member.name.partition("/")[2] for member in source if member.isfile()}


def check_sdist_documents(project: Path) -> None:
    config = tomllib.loads((project / "pyproject.toml").read_text())
    build = config["tool"]["hatch"]["build"]
    build.pop("hooks", None)
    target = build["targets"]["sdist"]

    with TemporaryDirectory(prefix="trailmark-sdist-docs-") as temporary:
        fixture = Path(temporary)
        for name in target["only-include"]:
            source = project / name
            if source.is_file():
                destination = fixture / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
        shutil.copyfile(project / ".gitignore", fixture / ".gitignore")

        # Worktree-local Git exclusions are not a packaging boundary.
        excludes = fixture / ".git" / "info" / "exclude"
        excludes.parent.mkdir(parents=True)
        excludes.write_text("/docs/audits/\n")
        scratch = {
            "docs/private-notes.md",
            "docs/private-notes.json",
            "docs/audits/private-notes.md",
            "docs/audits/v0.7.0/nested/private-notes.md",
        }
        for name in scratch:
            path = fixture / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("local-only scratch marker\n")

        files = _archive_files(fixture, config, fixture / "dist")
        documents = {name for name in files if name.startswith("docs/")}
        assert documents == PUBLIC_GUIDES, documents
        assert not files.intersection(scratch)

        # Exercise the exclusion independently of the public-guide allowlist.
        broad_config = copy.deepcopy(config)
        broad_config["tool"]["hatch"]["build"]["targets"]["sdist"]["only-include"] = ["docs"]
        broad_files = _archive_files(fixture, broad_config, fixture / "broad-dist")
        assert broad_files >= PUBLIC_GUIDES
        assert not any(name.startswith("docs/audits/") for name in broad_files)


if __name__ == "__main__":
    check_sdist_documents(Path(__file__).resolve().parents[1])
    print("sdist public guides and audit scratch exclusions verified")
