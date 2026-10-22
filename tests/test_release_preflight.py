"""Release metadata must agree before anything reaches PyPI."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from zipfile import ZipFile

import pytest

from tests.release_preflight import check_release


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "src/trailmark").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "trailmark"\nversion = "0.7.0"\n')
    (tmp_path / "src/trailmark/__init__.py").write_text('__version__ = "0.7.0"\n')
    (tmp_path / "CHANGELOG.md").write_text("## 0.7.0 (2026-10-22)\n")
    return tmp_path


def _wheel(project: Path, version: str) -> Path:
    dist = project / "dist"
    dist.mkdir()
    with ZipFile(dist / "trailmark.whl", "w") as wheel:
        wheel.writestr("trailmark.dist-info/METADATA", f"Name: trailmark\nVersion: {version}\n")
    _sdist(dist, "0.7.0")
    return dist


def _sdist(dist: Path, version: str, *, record: str = "trailmark/PKG-INFO") -> None:
    content = f"Name: trailmark\nVersion: {version}\n".encode()
    with tarfile.open(dist / "trailmark.tar.gz", "w:gz") as archive:
        member = tarfile.TarInfo(record)
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))


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


@pytest.mark.parametrize("heading", ["unreleased", "", "2026/10/22"])
def test_unpublished_changelog_rejected(project: Path, heading: str) -> None:
    (project / "CHANGELOG.md").write_text(f"## 0.7.0 ({heading})\n")
    with pytest.raises(ValueError, match="dated release"):
        check_release(project, "v0.7.0")


def test_sdist_mismatch_rejected(project: Path) -> None:
    dist = _wheel(project, "0.7.0")
    _sdist(dist, "0.6.0")
    with pytest.raises(ValueError, match="sdist metadata"):
        check_release(project, "v0.7.0", dist)


def test_sdist_requires_root_metadata(project: Path) -> None:
    dist = _wheel(project, "0.7.0")
    _sdist(dist, "0.7.0", record="trailmark/nested/PKG-INFO")
    with pytest.raises(ValueError, match="root PKG-INFO"):
        check_release(project, "v0.7.0", dist)


def test_sdist_required(project: Path) -> None:
    dist = _wheel(project, "0.7.0")
    (dist / "trailmark.tar.gz").unlink()
    with pytest.raises(ValueError, match="one sdist"):
        check_release(project, "v0.7.0", dist)
