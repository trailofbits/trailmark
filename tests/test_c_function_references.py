from pathlib import Path

from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_directory
from trailmark.query.api import QueryEngine


def test_literal_callback_dependencies_are_references_not_calls(tmp_path: Path) -> None:
    (tmp_path / "callbacks.c").write_text(
        "void protected_work(void) {}\n"
        "void (*global_handler)(void) = protected_work;\n"
        "void consume(void (*callback)(void)) {}\n"
        "void install(void) { global_handler = &protected_work; consume(protected_work); }\n"
        "void shadow(void (*protected_work)(void)) { consume(protected_work); }\n"
    )
    graph = parse_directory(str(tmp_path), "c")
    references = [e for e in graph.edges if e.kind == EdgeKind.REFERENCES]
    assert [
        (e.source_id, e.target_id, e.location.start_line if e.location else None)
        for e in references
    ] == [
        ("callbacks", "callbacks:protected_work", 2),
        ("callbacks:install", "callbacks:protected_work", 4),
        ("callbacks:install", "callbacks:protected_work", 4),
    ]
    engine = QueryEngine.from_graph(graph)
    assert {n["id"] for n in engine.reachable_from("callbacks:install")} == {"callbacks:consume"}
    assert "callbacks:protected_work" in {
        n["id"]
        for n in engine.reachable_from("callbacks:install", edge_kinds=("calls", "references"))
    }


def test_references_respect_cross_file_visibility_and_variable_shadowing(tmp_path: Path) -> None:
    (tmp_path / "target.c").write_text("void exported(void) {}\nstatic void hidden(void) {}\n")
    (tmp_path / "main.c").write_text(
        "void exported(void);\nvoid register_it(void (*callback)(void));\n"
        "void install(void) { register_it(exported); register_it(hidden); }\n"
        "void shadow(void) { int exported = 0; register_it(exported); }\n"
    )
    graph = parse_directory(str(tmp_path), "c")
    references = [e for e in graph.edges if e.kind == EdgeKind.REFERENCES]
    assert [(e.source_id, e.target_id) for e in references] == [("main:install", "target:exported")]


def test_declared_external_callback_is_retained_without_its_implementation(tmp_path: Path) -> None:
    (tmp_path / "main.c").write_text(
        "void external(void);\nvoid consume(void (*callback)(void));\n"
        "void install(void) { consume(external); }\n"
    )
    graph = parse_directory(str(tmp_path), "c")
    (reference,) = [edge for edge in graph.edges if edge.kind == EdgeKind.REFERENCES]
    assert reference.source_id == "main:install"
    assert reference.target_id == "main:external"
    assert reference.target_id not in graph.nodes
    assert dict(reference.attributes)["literal_callable_declared"] is True
    assert reference in graph.unresolved_relationships()
