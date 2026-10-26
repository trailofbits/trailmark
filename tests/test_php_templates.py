"""Mixed PHP/HTML and executable script bodies retain their actual call sites."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_directory, parse_file
from trailmark.query.api import QueryEngine


def test_mixed_template_retains_sql_calls_and_callable_boundaries(tmp_path: Path) -> None:
    file = tmp_path / "custom.phtml"
    file.write_text(
        "<h1>Records</h1>\n<?php\n"
        "$sql = 'SELECT * FROM records WHERE id=' . $_GET['id'];\n"
        "$connection->query($sql);\n"
        "function helper() { nested_only(); }\n"
        "helper();\n?>\n<p><?= escape($name) ?></p>\n"
    )
    graph = parse_file(str(file), strict=True)
    template = graph.nodes["custom:<template>"]
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS and e.source_id == template.id]
    assert {e.location.start_line for e in calls if e.location} == {4, 6, 8}
    assert any("query" in e.target_id for e in calls)
    assert not any("nested_only" in e.target_id for e in calls)
    assert (template.location.start_line, template.location.end_line) == (1, 9)
    assert parse_directory(str(tmp_path), "auto").source_file_counts == {"php": 1}


def test_top_level_script_owns_its_calls(tmp_path: Path) -> None:
    file = tmp_path / "custom.php"
    file.write_text(
        "<?php\nmysqli_query($db, 'SELECT 1');\nfunction declared() { inside_only(); }\n"
    )
    graph = parse_file(str(file), strict=True)
    calls = [
        e for e in graph.edges if e.source_id == "custom:<script>" and e.kind == EdgeKind.CALLS
    ]
    assert len(calls) == 1
    assert calls[0].target_id.endswith(":mysqli_query")


def test_anonymous_class_methods_do_not_execute_in_template_body(tmp_path: Path) -> None:
    file = tmp_path / "anon.phtml"
    file.write_text("<?php $a = new class(build()) { function f(){ hidden(); } }; shown(); ?>")
    graph = parse_file(str(file), strict=True)
    targets = {
        edge.target_id.rsplit(":", 1)[-1]
        for edge in graph.edges
        if edge.source_id == "anon:<template>" and edge.kind == EdgeKind.CALLS
    }
    assert targets == {"build", "shown"}


@pytest.mark.parametrize("kind", ["class", "interface", "trait", "enum"])
def test_declarations_do_not_become_script_bodies(kind: str, tmp_path: Path) -> None:
    file = tmp_path / "decl.php"
    body = (
        "public function call();" if kind == "interface" else "public function call() { inner(); }"
    )
    file.write_text(f"<?php {kind} Container {{ {body} }}")
    graph = parse_file(str(file), strict=True)
    assert "decl:<script>" not in graph.nodes


def test_declaration_directives_and_empty_statements_do_not_make_script_bodies(
    tmp_path: Path,
) -> None:
    file = tmp_path / "decl.php"
    file.write_text("<?php declare(strict_types=1); ; class Thing {}")
    assert "decl:<script>" not in parse_file(str(file), strict=True).nodes
    file.write_text("<?php declare(ticks=1) { work(); }")
    graph = parse_file(str(file), strict=True)
    assert any(
        edge.source_id == "decl:<script>" for edge in graph.edges if edge.kind == EdgeKind.CALLS
    )


def test_php_and_sql_in_arbitrary_custom_directory(tmp_path: Path) -> None:
    custom = tmp_path / "custom" / "templates"
    custom.mkdir(parents=True)
    (custom / "page.phtml").write_text("<?php $db->query('SELECT 1'); ?>")
    (custom / "setup.sql").write_text("CREATE TABLE records (id int);")
    (custom / "initialize.php").write_text("<?php initialize();")
    assert parse_directory(str(tmp_path), "auto").source_file_counts == {"php": 2, "sql": 1}


def test_include_executes_inspected_file_body_and_dynamic_paths_remain_unknown(
    tmp_path: Path,
) -> None:
    (tmp_path / "main.php").write_text("<?php require __DIR__ . '/page.phtml'; include $path;")
    (tmp_path / "page.phtml").write_text("<?php $db->query('SELECT 1'); ?>")
    graph = parse_directory(str(tmp_path), "php")
    engine = QueryEngine.from_graph(graph)
    targets = {node["id"] for node in engine.callees_of("main:<script>")}
    assert "page:<template>" in targets
    assert any("$include@" in target for target in targets)
    assert any("query" in node["id"] for node in engine.reachable_from("main:<script>"))
    assert any(
        edge.confidence.value == "uncertain" and "$include@" in edge.target_id
        for edge in graph.edges
    )


@pytest.mark.parametrize("source", ["<?php exit;", "<?php unset($name);", "<p>Only markup</p>"])
def test_executable_file_body_does_not_require_a_call(source: str, tmp_path: Path) -> None:
    file = tmp_path / "body.php"
    file.write_text(source)
    assert "body:<script>" in parse_file(str(file), strict=True).nodes


def test_script_calls_retain_lexical_namespaces(tmp_path: Path) -> None:
    file = tmp_path / "body.php"
    file.write_text("<?php namespace A; function f() {} f(); namespace B; function f() {} f();")
    graph = parse_file(str(file), strict=True)
    calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
    assert {edge.source_id for edge in calls} == {"body:<script>"}
    assert {edge.target_id for edge in calls} == {"body:A.f", "body:B.f"}
