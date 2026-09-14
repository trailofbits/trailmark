"""Smoke-test an installed distribution against every registered parser."""

from __future__ import annotations

import sys
from importlib.metadata import files
from pathlib import Path
from tempfile import TemporaryDirectory

import tree_sitter_sql

from trailmark.parse import parse_directory, supported_languages
from trailmark.parsers.rust.parser import RustParser


def _check_installed_verus_parser() -> None:
    """Exercise the vendored grammar from the installed distribution."""
    source = """\
fn host() {}
verus! {
    proof fn verified() {
        host();
    }
}
"""
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "verified.rs"
        path.write_text(source)
        graph = RustParser().parse_file(str(path))

    names = {node.name for node in graph.nodes.values()}
    if not {"host", "verified"} <= names:
        msg = "installed Rust parser did not extract functions from a verus! block"
        raise RuntimeError(msg)


def main() -> None:
    fixture_root = Path(sys.argv[1]).resolve()
    missing: list[str] = []
    for language in supported_languages():
        fixture = fixture_root / language
        if not fixture.is_dir():
            missing.append(language)
            continue
        graph = parse_directory(str(fixture), language)
        if not graph.nodes:
            msg = f"installed parser produced no nodes for {language}"
            raise RuntimeError(msg)
    if missing:
        msg = f"missing installed-package fixtures: {', '.join(missing)}"
        raise RuntimeError(msg)
    if not callable(tree_sitter_sql.language):
        msg = "tree-sitter-sql grammar binding is unavailable"
        raise RuntimeError(msg)
    sql_files = files("tree-sitter-sql") or ()
    if not any("license" in str(path).lower() for path in sql_files):
        msg = "tree-sitter-sql distribution does not include its license metadata"
        raise RuntimeError(msg)
    trailmark_files = files("trailmark") or ()
    if not any(str(path).endswith("tree_sitter_custom/verus/LICENSE") for path in trailmark_files):
        msg = "trailmark distribution does not include the vendored Verus grammar license"
        raise RuntimeError(msg)
    _check_installed_verus_parser()


if __name__ == "__main__":
    main()
