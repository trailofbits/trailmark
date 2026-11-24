"""Policy rule dependencies remain distinct from runtime call relationships."""

from pathlib import Path

from tests.parse_work import parse_work
from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_file
from trailmark.query.api import QueryEngine


def test_negated_and_indexed_rule_references_retain_clause_sites(tmp_path: Path) -> None:
    source = tmp_path / "admission.rego"
    source.write_text(
        "package admission\n"
        "admit { not invalid }\n"
        "invalid { not allowed_keys[key] }\n"
        'allowed_keys = {"hostname"}\n'
    )

    graph = parse_file(str(source), language="rego", strict=True)
    refs = [edge for edge in graph.edges if edge.kind == EdgeKind.REFERENCES]

    assert {(edge.target_id, edge.location.start_line) for edge in refs if edge.location} == {
        ("admission:invalid", 2),
        ("admission:allowed_keys", 3),
    }
    assert all(graph.nodes[edge.source_id].name == "$clause" for edge in refs)
    engine = QueryEngine.from_graph(graph)
    assert engine.paths_between(
        "admission:admit", "admission:allowed_keys", edge_kinds=("calls", "references")
    )


def test_rule_parameters_some_and_assignments_shadow_rule_names(tmp_path: Path) -> None:
    source = tmp_path / "shadow.rego"
    source.write_text(
        "package shadow\n"
        "invalid = true\n"
        "parameter(invalid) { invalid == true }\n"
        "local { invalid := true; invalid }\n"
        "quantified { some invalid; invalid == 1 }\n"
    )

    graph, _ = parse_work(source, language="rego", max_work=100_000)

    assert not [edge for edge in graph.edges if edge.kind == EdgeKind.REFERENCES]


def test_rule_reference_arguments_do_not_turn_callees_into_references(tmp_path: Path) -> None:
    source = tmp_path / "arguments.rego"
    source.write_text(
        "package arguments\nresult { helper(allowed) }\nhelper(value) { value }\nallowed = true\n"
    )

    graph = parse_file(str(source), language="rego", strict=True)

    assert {edge.target_id for edge in graph.edges if edge.kind == EdgeKind.REFERENCES} == {
        "arguments:allowed"
    }


def test_some_domain_rule_is_not_a_local_binder(tmp_path: Path) -> None:
    source = tmp_path / "domain.rego"
    source.write_text("package p\nallowed = [true]\nallow { some x in allowed; x }\n")

    graph = parse_file(str(source), language="rego", strict=True)

    assert [edge.target_id for edge in graph.edges if edge.kind == EdgeKind.REFERENCES] == [
        "p:allowed"
    ]


def test_comprehension_local_binders_do_not_hide_outer_rule_references(tmp_path: Path) -> None:
    source = tmp_path / "comprehension.rego"
    source.write_text(
        "package p\nallowed = true\n"
        "allow { xs := [x | some allowed; allowed := true; x := allowed]; allowed }\n"
    )

    graph = parse_file(str(source), language="rego", strict=True)
    refs = [edge for edge in graph.edges if edge.kind == EdgeKind.REFERENCES]

    assert [edge.target_id for edge in refs] == ["p:allowed"]
    assert refs[0].location is not None
    assert refs[0].location.start_col == source.read_text().splitlines()[2].rindex("allowed")
