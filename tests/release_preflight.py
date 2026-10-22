"""Validate release versions before publishing source and wheel distributions."""

from __future__ import annotations

import argparse
import ast
import email
import tomllib
from pathlib import Path
from zipfile import ZipFile


def check_release(project: Path, tag: str, dist: Path | None = None) -> str:
    """Reject mismatched tag, project, runtime, changelog, or wheel versions."""
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
    if not any(
        line.startswith(f"## {version} (")
        for line in (project / "CHANGELOG.md").read_text().splitlines()
    ):
        raise ValueError(f"changelog has no release entry for {version}")
    if dist is not None:
        wheels = list(dist.glob("*.whl"))
        if len(wheels) != 1:
            raise ValueError("release must contain exactly one wheel")
        with ZipFile(wheels[0]) as wheel:
            names = [name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")]
            if len(names) != 1:
                raise ValueError("wheel must contain exactly one distribution metadata record")
            built = email.message_from_bytes(wheel.read(names[0]))
        if built["Name"] != metadata["name"] or built["Version"] != version:
            raise ValueError(f"wheel metadata does not match {metadata['name']} {version}")
    return version


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--dist", type=Path)
    args = parser.parse_args()
    version = check_release(Path(__file__).resolve().parents[1], args.tag, args.dist)
    print(f"release versions verified: {version}")


if __name__ == "__main__":
    main()
