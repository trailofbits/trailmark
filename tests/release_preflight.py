"""Validate release versions before publishing source and wheel distributions."""

from __future__ import annotations

import argparse
import ast
import email
import re
import tarfile
import tomllib
from pathlib import Path
from zipfile import ZipFile


def check_release(project: Path, tag: str, dist: Path | None = None) -> str:
    """Reject unpublished changelogs and mismatched tag, runtime, or artifact versions."""
    metadata = tomllib.loads((project / "pyproject.toml").read_text())["project"]
    version = metadata["version"]
    tree = ast.parse((project / "src/trailmark/__init__.py").read_text())
    runtime = next(
        (
            ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets)
        ),
        None,
    )
    if tag.removeprefix("v") != version or runtime != version:
        raise ValueError(
            f"release version mismatch: tag={tag}, project={version}, runtime={runtime}"
        )
    _check_changelog(project, version)
    if dist is not None:
        _check_artifacts(dist, metadata["name"], version)
    return version


def _check_changelog(project: Path, version: str) -> None:
    headings = (project / "CHANGELOG.md").read_text().splitlines()
    pattern = rf"## {re.escape(version)} \(\d{{4}}-\d{{2}}-\d{{2}}\)"
    if not any(re.fullmatch(pattern, line) for line in headings):
        raise ValueError(f"changelog has no dated release entry for {version}")


def _check_metadata(content: bytes, name: str, version: str, artifact: str) -> None:
    built = email.message_from_bytes(content)
    if built["Name"] != name or built["Version"] != version:
        raise ValueError(f"{artifact} metadata does not match {name} {version}")


def _check_artifacts(dist: Path, name: str, version: str) -> None:
    wheels, sdists = list(dist.glob("*.whl")), list(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("release must contain exactly one wheel and one sdist")
    with ZipFile(wheels[0]) as wheel:
        names = [name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")]
        if len(names) != 1:
            raise ValueError("wheel must contain exactly one distribution metadata record")
        _check_metadata(wheel.read(names[0]), name, version, "wheel")
    with tarfile.open(sdists[0]) as sdist:
        records = [
            member
            for member in sdist
            if len(Path(member.name).parts) == 2
            and Path(member.name).name == "PKG-INFO"
            and member.isfile()
        ]
        if len(records) != 1:
            raise ValueError("sdist must contain exactly one root PKG-INFO record")
        content = sdist.extractfile(records[0])
        if content is None:
            raise ValueError("sdist PKG-INFO cannot be read")
        _check_metadata(content.read(), name, version, "sdist")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--dist", type=Path)
    args = parser.parse_args()
    try:
        version = check_release(Path(__file__).resolve().parents[1], args.tag, args.dist)
    except (ValueError, OSError) as error:
        parser.exit(1, f"release preflight: {error}\n")
    print(f"release versions verified: {version}")


if __name__ == "__main__":
    main()
