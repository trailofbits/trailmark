from pathlib import Path

from trailmark.models.edges import EdgeKind
from trailmark.models.nodes import NodeKind
from trailmark.parse import parse_directory


def test_receivers_resolve_within_the_directory_and_package(tmp_path: Path) -> None:
    (tmp_path / "types.go").write_text(
        "package demo\ntype Server struct{}\ntype Counter int\n"
        "func (Counter) Value() int { return 1 }\nfunc (s *Server) Gate() {}\n"
    )
    (tmp_path / "handler.go").write_text("package demo\nfunc (s *Server) Handle() { s.Gate() }\n")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "types.go").write_text("package demo\ntype Server struct{}\n")
    graph = parse_directory(str(tmp_path), "go")
    assert graph.nodes["types:Counter"].kind == NodeKind.TYPE
    ownership = {(e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.CONTAINS}
    assert ("types:Server", "handler:Server.Handle") in ownership
    assert ("types:Counter", "types:Counter.Value") in ownership
    assert all(e.source_id in graph.nodes and e.target_id in graph.nodes for e in graph.edges)


def test_duplicate_package_types_remain_ambiguous(tmp_path: Path) -> None:
    for filename in ("one.go", "two.go"):
        (tmp_path / filename).write_text("package demo\ntype Server struct{}\n")
    (tmp_path / "handler.go").write_text("package demo\nfunc (s *Server) Handle() {}\n")
    graph = parse_directory(str(tmp_path), "go")
    edge = next(e for e in graph.edges if e.target_id == "handler:Server.Handle")
    assert edge.source_id not in graph.nodes
    assert dict(edge.attributes)["unresolved_reason"] == "ambiguous_go_receiver"
