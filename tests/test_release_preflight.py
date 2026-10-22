"""Release metadata must agree before anything reaches PyPI."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest

from tests.release_preflight import check_release


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "src/trailmark").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "trailmark"\nversion = "0.7.0"\n')
    (tmp_path / "src/trailmark/__init__.py").write_text('__version__ = "0.7.0"\n')
    (tmp_path / "CHANGELOG.md").write_text("## 0.7.0 (unreleased)\n")
    return tmp_path


def _wheel(project: Path, version: str) -> Path:
    dist = project / "dist"
    dist.mkdir()
    with ZipFile(dist / "trailmark.whl", "w") as wheel:
        wheel.writestr("trailmark.dist-info/METADATA", f"Name: trailmark\nVersion: {version}\n")
    return dist


def test_matching_release_and_wheel(project: Path) -> None:
    assert check_release(project, "v0.7.0", _wheel(project, "0.7.0")) == "0.7.0"


@pytest.mark.parametrize("tag", ["v0.6.0", "v0.7.0rc1"])
def test_tag_mismatch_rejected(project: Path, tag: str) -> None:
    with pytest.raises(ValueError, match="version mismatch"):
        check_release(project, tag)


def test_runtime_mismatch_rejected(project: Path) -> None:
    (project / "src/trailmark/__init__.py").write_text('__version__ = "0.6.0"\n')
    with pytest.raises(ValueError, match="version mismatch"):
        check_release(project, "v0.7.0")


def test_wheel_mismatch_rejected(project: Path) -> None:
    with pytest.raises(ValueError, match="wheel metadata"):
        check_release(project, "v0.7.0", _wheel(project, "0.6.0"))


def test_missing_changelog_rejected(project: Path) -> None:
    (project / "CHANGELOG.md").write_text("## 0.6.0\n")
    with pytest.raises(ValueError, match="changelog"):
        check_release(project, "v0.7.0")
