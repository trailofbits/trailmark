"""KAT (known-answer test) suite for trailmark parsers.

Each test parses a fixture and asserts the canonical CodeGraph JSON output
matches a stored snapshot byte-for-byte. Snapshots cover the full
input-to-output surface, so a single change in any structural field
(node kind, edge kind, parameter, branch info, complexity, docstring,
import resolution, etc.) is caught by the test.

To regenerate snapshots after an intentional output change:

    TRAILMARK_UPDATE_SNAPSHOTS=1 uv run pytest tests/test_kat_parsers.py

Review the resulting diff before committing.
"""

from __future__ import annotations

import pytest

from tests.kat_runner import assert_snapshot_matches, fixture_pair

# (language, fixture_basename) pairs. Each pair maps to
# tests/fixtures/kat/<language>/<basename>.<ext> and a sibling
# <basename>.expected.json snapshot.
PARSER_FIXTURES: list[tuple[str, str]] = [
    ("c", "taxonomy"),
    ("cairo", "taxonomy"),
    ("circom", "taxonomy"),
    ("cpp", "taxonomy"),
    ("c_sharp", "taxonomy"),
    ("dart", "taxonomy"),
    ("erlang", "taxonomy"),
    ("func", "taxonomy"),
    ("go", "taxonomy"),
    ("graphql", "taxonomy"),
    ("haskell", "taxonomy"),
    ("java", "Taxonomy"),
    ("javascript", "taxonomy"),
    ("kotlin", "taxonomy"),
    ("masm", "taxonomy"),
    ("move", "taxonomy"),
    ("objc", "Taxonomy"),
    ("php", "taxonomy"),
    ("proto", "taxonomy"),
    ("python", "taxonomy"),
    ("rego", "taxonomy"),
    ("ruby", "taxonomy"),
    ("rust", "taxonomy"),
    ("solidity", "taxonomy"),
    ("swift", "taxonomy"),
    ("sway", "taxonomy"),
    ("tact", "taxonomy"),
    ("thrift", "taxonomy"),
    ("sql", "taxonomy"),
    ("typescript", "taxonomy"),
]


@pytest.mark.parametrize(("language", "basename"), PARSER_FIXTURES)
def test_parser_snapshot(language: str, basename: str) -> None:
    fixture, snapshot = fixture_pair(language, basename)
    assert_snapshot_matches(fixture, snapshot, language=language)
