"""Body walkers retain source sites without depending on Python recursion depth."""

from pathlib import Path

from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_directory


def test_deep_solidity_state_expression_does_not_abort_directory(tmp_path: Path) -> None:
    terms = 1200
    (tmp_path / "deep.sol").write_text(
        "contract C {uint x; function f() public view returns(uint) {return "
        + "+".join(["x"] * terms)
        + ";}}"
    )
    (tmp_path / "other.sol").write_text("contract Other {}")

    graph = parse_directory(str(tmp_path), language="solidity")

    reads = [e for e in graph.edges if e.kind == EdgeKind.READS]
    assert len(reads) == terms
    assert {e.target_id for e in reads} == {"deep:C.x"}
    assert len({e.location.start_col for e in reads if e.location}) == terms
    assert "other:Other" in graph.nodes


def test_deep_csharp_expression_retains_call_order(tmp_path: Path) -> None:
    (tmp_path / "deep.cs").write_text(
        "class C { int first() {return 0;} int last() {return 0;}"
        "int f(int x) {return first()+" + "+".join(["x"] * 1200) + "+last();}}"
    )

    graph = parse_directory(str(tmp_path), language="c_sharp")

    calls = [e for e in graph.edges if e.source_id == "deep:C.f" and e.kind == EdgeKind.CALLS]
    assert [e.target_id for e in calls] == ["deep:C.first", "deep:C.last"]
