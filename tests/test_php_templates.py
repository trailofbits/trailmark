"""Theme templates retain executable PHP, database calls and layout provenance."""

from pathlib import Path

from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_file
from trailmark.query.api import QueryEngine


def _write(root: Path, relative: str, source: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    return path


def test_mixed_template_body_owns_direct_sql_calls_and_preserves_nested_functions(
    tmp_path: Path,
) -> None:
    file = _write(
        tmp_path,
        "custom.phtml",
        "<h1>Products</h1>\n<?php\n"
        "$sql = 'SELECT * FROM products WHERE id=' . $_GET['id'];\n"
        "$connection->query($sql);\n"
        "function helper() { nested_only(); }\n"
        "helper();\n?>\n<p><?= escape($name) ?></p>\n",
    )
    graph = parse_file(str(file), strict=True)
    template = next(u for u in graph.nodes.values() if u.name == "<template>")
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS and e.source_id == template.id]
    assert {e.location.start_line for e in calls if e.location} == {4, 6, 8}
    assert any("query" in e.target_id for e in calls)
    assert not any("nested_only" in e.target_id for e in calls)
    assert template.location.start_line == 1
    assert template.location.end_line == 9


def test_custom_theme_php_sql_and_layout_with_competing_overrides(tmp_path: Path) -> None:
    template_path = "app/design/frontend/Acme/custom/Demo_Shop/templates/products.phtml"
    _write(tmp_path, template_path, "<?php $connection->query('SELECT * FROM products'); ?>")
    _write(tmp_path, "app/design/frontend/Acme/custom/registration.php", "<?php register_theme();")
    _write(
        tmp_path, "app/design/frontend/Acme/custom/setup.sql", "CREATE TABLE theme_data (id int);"
    )
    _write(
        tmp_path,
        "app/design/frontend/Acme/custom/Demo_Shop/layout/default.xml",
        '<page><body><block class="Demo\\Shop\\Block\\Products" '
        'template="Demo_Shop::products.phtml"/></body></page>',
    )
    engine = QueryEngine.from_directory(str(tmp_path), "auto")
    assert engine.summary()["source_file_counts"] == {"php": 2, "sql": 1}
    records = engine.endpoint_inventory()["registrations"]
    reference = next(r for r in records if r["registration_key"] == "Demo_Shop::products.phtml")
    assert reference["kind"] == "template"
    assert reference["bindings"][0]["status"] == "resolved"
    assert reference["enabled"] is None
    assert reference["path"] is None
    assert engine.attack_surface() == []
    _write(
        tmp_path, "app/code/Demo/Shop/view/frontend/templates/products.phtml", "<?php base_theme();"
    )
    records = QueryEngine.from_directory(str(tmp_path), "auto").endpoint_inventory()[
        "registrations"
    ]
    reference = next(r for r in records if r["registration_key"] == "Demo_Shop::products.phtml")
    assert len(reference["bindings"]) == 2
    assert all(b["status"] == "ambiguous" for b in reference["bindings"])


def test_php_top_level_calls_have_script_owner(tmp_path: Path) -> None:
    file = _write(
        tmp_path,
        "custom.php",
        "<?php\nmysqli_query($db, 'SELECT 1');\nfunction declared() { inside_only(); }\n",
    )
    graph = parse_file(str(file))
    script = next(u for u in graph.nodes.values() if u.name == "<script>")
    calls = [e for e in graph.edges if e.source_id == script.id and e.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert calls[0].target_id.endswith(":mysqli_query")
