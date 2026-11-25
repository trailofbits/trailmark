"""Solidity receiver calls retain their own text, locations, and proxy identity."""

from collections import Counter
from pathlib import Path

import pytest

from trailmark.query.api import QueryEngine


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("self.flag(1)", [("self.flag", "self.flag(1)")]),
        ("a != self.flag(1)", [("self.flag", "self.flag(1)")]),
        ("/* λ */ a != self.flag(1)", [("self.flag", "self.flag(1)")]),
        (
            "a != self.flag(1) || b != self.flag(2)",
            [("self.flag", "self.flag(1)"), ("self.flag", "self.flag(2)")],
        ),
        (
            "a != self.flag(1)\n            || b != self.flag(2)",
            [("self.flag", "self.flag(1)"), ("self.flag", "self.flag(2)")],
        ),
        (
            "a != self.flag(1) || b != self.flag(1)",
            [("self.flag", "self.flag(1)"), ("self.flag", "self.flag(1)")],
        ),
        (
            "a != self.nested().flag(1)",
            [("self.nested", "self.nested()"), ("self.nested().flag", "self.nested().flag(1)")],
        ),
        (
            "(a ? self : self).flag(1)",
            [("(a ? self : self).flag", "(a ? self : self).flag(1)")],
        ),
    ],
)
def test_receiver_call_text_and_span(
    tmp_path: Path, expression: str, expected: list[tuple[str, str]]
) -> None:
    source = (
        "interface Receiver {\n"
        "    function flag(uint256 n) external returns (bool);\n"
        "    function nested() external returns (Receiver);\n}\n"
        "contract C {\n"
        "    function check(Receiver self, bool a, bool b) public returns (bool) {\n"
        f"        return {expression};\n    }}\n}}\n"
    )
    (tmp_path / "app.sol").write_text(source)
    engine = QueryEngine.from_directory(str(tmp_path), language="solidity")
    assert engine.parse_diagnostics() == []
    sites = engine.unresolved_calls("check")
    lines = source.encode().splitlines(keepends=True)
    observed = []
    positions = []
    for site in sites:
        location = site["location"]
        start = sum(map(len, lines[: location["start_line"] - 1])) + location["start_col"]
        end = sum(map(len, lines[: location["end_line"] - 1])) + location["end_col"]
        observed.append((site["raw_target"], source.encode()[start:end].decode()))
        positions.append((start, end))
    assert Counter(observed) == Counter(expected)
    assert len(set(positions)) == len(expected)
    assert len({site["target"] for site in sites}) == len({name for name, _ in expected})
