"""General language facts improve discovery without framework adapters."""

from pathlib import Path

import pytest

from trailmark.analysis.entrypoint_candidates import discover_entrypoint_candidates
from trailmark.models.edges import EdgeKind
from trailmark.parse import _LANGUAGE_EXTENSIONS, parse_directory
from trailmark.query.api import QueryEngine
from trailmark.source_policy import SourcePolicy, SourceRoot


def test_bare_decorators_keep_evidence_without_gaining_http_tags(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "from external import patch, get\n"
        '@patch(target="pkg.fn")\ndef patched(): pass\n'
        '@patch(f"{name}.fn")\ndef dynamic(): pass\n'
        '@get("/path")\ndef bare(): pass\n'
        'alias = receiver.get\n@alias("/path")\ndef member(): pass\n'
    )
    engine = QueryEngine.from_directory(str(tmp_path))
    assert {item["node_id"] for item in engine.attack_surface()} == {"app:member"}
    assert {item["node_id"] for item in engine.entrypoint_candidates()} == {
        "app:patched",
        "app:dynamic",
        "app:bare",
    }


def test_callable_references_survive_namespaced_roots(tmp_path: Path) -> None:
    app, lib = tmp_path / "app", tmp_path / "lib"
    app.mkdir()
    lib.mkdir()
    (app / "use.py").write_text("from handlers import handler\nconsume(handler)")
    (lib / "handlers.py").write_text("def handler(): pass")
    (app / "use.php").write_text("<?php consume('Library\\\\handler');")
    (lib / "handlers.php").write_text("<?php namespace Library; function handler() {}")
    policy = SourcePolicy((SourceRoot(str(app), "app"), SourceRoot(str(lib), "lib")))
    engine = QueryEngine.from_directory(str(app), "auto", source_policy=policy)
    facts = engine.entrypoint_candidates()
    targets = {
        fact["node_id"]
        for fact in facts
        if fact["binding"] == "resolved" and fact["basis"] == "reference"
    }
    assert targets == {"lib::handlers\\.py:handler", "lib::handlers\\.php:Library.handler"}


def test_decorator_false_positives_aliases_factories_and_renamed_receivers(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("""
from unittest import mock as altered
from external import Factory as F
@altered.patch('package.symbol')
def patched(): pass
class Store:
    def get(self, key): return lambda f: f
store = Store()
@store.get('/')
def identity(): pass
def factory():
    instance = F()
    register = instance.get
    @register('/one')
    @instance.post('/two')
    def handler(): pass
    return instance
""")
    engine = QueryEngine.from_directory(str(tmp_path))
    assert {item["node_id"] for item in engine.attack_surface()} == {"app:factory.handler"}
    facts = [
        fact
        for fact in engine.entrypoint_evidence("factory.handler")
        if fact["rule"] == "python.decorator"
    ]
    assert {fact["details"]["path"] for fact in facts} == {"/one", "/two"}
    assert {fact["details"]["consumer_path"] for fact in facts} == {
        "external.Factory().get",
        "external.Factory().post",
    }
    assert all("registrar_semantics" in fact["unresolved"] for fact in facts)


def test_unknown_decorator_bindings_preserve_legacy_tags_and_uncertainty(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("""
from external import Factory
app = Factory()
if condition:
    app = replacement
@app.get('/maybe')
def ambiguous(): pass
@app.get(dynamic())
def dynamic_path(): pass
""")
    engine = QueryEngine.from_directory(str(tmp_path))
    assert {entry["node_id"] for entry in engine.attack_surface()} == {
        "app:ambiguous",
        "app:dynamic_path",
    }
    facts = engine.entrypoint_evidence()
    assert {fact["node_id"] for fact in facts if fact["rule"] == "python.decorator"} == {
        "app:ambiguous",
        "app:dynamic_path",
    }
    assert all(
        fact["details"]["consumer_path"] is None
        for fact in facts
        if fact["rule"] == "python.decorator"
    )


@pytest.mark.parametrize(
    "source",
    [
        "function GET() {}",
        "export function outer() { function GET() {} }",
        "export class Handler { GET() {} }",
    ],
)
def test_route_name_requires_exported_callable(source: str, tmp_path: Path) -> None:
    (tmp_path / "route.ts").write_text(source)
    engine = QueryEngine.from_directory(str(tmp_path), "typescript")
    assert not engine.attack_surface()


def test_indirect_export_keeps_the_file_route_convention(tmp_path: Path) -> None:
    (tmp_path / "route.ts").write_text("function GET() {}\nexport { GET };\n")
    engine = QueryEngine.from_directory(str(tmp_path), "typescript")
    assert {item["node_id"] for item in engine.attack_surface()} == {"route:GET"}


def test_route_convention_uses_exported_alias_and_records_each_occurrence(tmp_path: Path) -> None:
    (tmp_path / "route.ts").write_text(
        "function internal() {}\nfunction GET() {}\n"
        "export { internal as GET, internal as POST, GET as hidden };\n"
    )
    engine = QueryEngine.from_directory(str(tmp_path), "typescript")
    assert {item["node_id"] for item in engine.attack_surface()} == {"route:internal"}
    facts = [fact for fact in engine.entrypoint_evidence() if fact["rule"] == "module_export"]
    assert {(fact["node_id"], fact["symbol"]) for fact in facts} == {
        ("route:internal", "GET"),
        ("route:internal", "POST"),
        ("route:GET", "hidden"),
    }
    assert len({fact["id"] for fact in facts}) == 3


@pytest.mark.parametrize("export", ["export default wrap(handler);", "module.exports = handler;"])
def test_default_export_keeps_existing_handler_with_export_evidence(
    tmp_path: Path, export: str
) -> None:
    root = tmp_path / "pages/api"
    root.mkdir(parents=True)
    (root / "action.js").write_text("function handler() {}\n" + export)
    engine = QueryEngine.from_directory(str(tmp_path), "javascript")
    assert {item["node_id"] for item in engine.attack_surface()} == {"pages.api.action:handler"}
    fact = next(f for f in engine.entrypoint_evidence() if f["rule"] == "module_export")
    assert fact["symbol"] == "default"
    assert fact["details"]["indirect"] == ("wrap" in export)
    assert ("export_value" in fact["unresolved"]) == ("wrap" in export)
    assert fact["location"]["start_line"] == 2


def test_graphql_extension_and_directive_references_preserve_execution_uncertainty(
    tmp_path: Path,
) -> None:
    (tmp_path / "handler.php").write_text(
        "<?php namespace App; class Handler { public function run() {} }"
    )
    (tmp_path / "schema.graphqls").write_text(
        'type Query { item: String @arbitrary(target: "App\\\\Handler") }'
    )
    engine = QueryEngine.from_directory(str(tmp_path), "auto")
    facts = [
        fact for fact in engine.entrypoint_candidates() if fact["rule"] == "declarative_reference"
    ]
    assert len(facts) == 1
    assert facts[0]["node_id"] == "handler:App.Handler"
    assert facts[0]["binding"] == "resolved"
    assert "consumer_semantics" in facts[0]["unresolved"]
    assert any(item["node_id"].endswith(":Query.item") for item in engine.attack_surface())
    assert not engine.entrypoint_paths_to("Handler.run")


def test_data_references_do_not_guess_between_qualified_matches(tmp_path: Path) -> None:
    for name in ("a", "b"):
        (tmp_path / f"{name}.php").write_text("<?php namespace App; class Handler {}")
    (tmp_path / "config.json").write_text(
        '{"arbitrary": "App\\\\Handler", "missing": "Elsewhere.Handler"}'
    )
    graph = parse_directory(str(tmp_path), "php")
    discover_entrypoint_candidates(graph)
    first = len(graph.entrypoint_evidence), len(graph.edges), len(graph.nodes)
    discover_entrypoint_candidates(graph)
    assert first == (len(graph.entrypoint_evidence), len(graph.edges), len(graph.nodes))
    facts = QueryEngine.from_graph(graph).entrypoint_candidates()
    assert {fact["node_id"] for fact in facts if fact["binding"] == "ambiguous"} == {
        "a:App.Handler",
        "b:App.Handler",
    }
    missing = next(fact for fact in facts if fact["symbol"] == "Elsewhere.Handler")
    assert missing["node_id"] is None
    assert missing["binding"] == "unresolved"
    assert not any(edge.kind == EdgeKind.CALLS for edge in graph.edges)


def test_selected_roots_preserve_local_overrides(tmp_path: Path) -> None:
    for name in ("one", "two"):
        root = tmp_path / name
        (root / ".trailmark").mkdir(parents=True)
        (root / "app.py").write_text("def selected(): pass\n")
        (root / ".trailmark/entrypoints.toml").write_text(
            '[[entrypoint]]\nnode="app:selected"\nkind="api"\n'
        )
    policy = SourcePolicy(tuple(SourceRoot(str(tmp_path / name), name) for name in ("one", "two")))
    engine = QueryEngine.from_directory(str(tmp_path), source_policy=policy)
    assert {entry["node_id"] for entry in engine.attack_surface()} == {
        "one::app:selected",
        "two::app:selected",
    }


def test_relative_file_references_do_not_bind_to_other_directories(tmp_path: Path) -> None:
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
    (tmp_path / "a/config.json").write_text(
        '{"explicit": "./index.js", "suffix": "index.js", "glob": "src/**/*.js"}'
    )
    (tmp_path / "b/index.js").write_text("export function run() {}")
    engine = QueryEngine.from_directory(str(tmp_path), "javascript")
    facts = {fact["symbol"]: fact for fact in engine.entrypoint_candidates()}
    assert facts["./index.js"]["binding"] == "unresolved"
    assert facts["index.js"]["binding"] == "ambiguous"
    assert "target_selection" in facts["index.js"]["unresolved"]
    assert "src/**/*.js" not in facts


def test_unresolved_data_does_not_add_provenance_nodes(tmp_path: Path) -> None:
    (tmp_path / "source.py").write_text("def work(): pass")
    (tmp_path / "config.json").write_text('{"value":"unknown.Handler"}')
    graph = parse_directory(str(tmp_path))
    nodes = set(graph.nodes)
    discover_entrypoint_candidates(graph)
    assert set(graph.nodes) == nodes
    assert next(iter(graph.entrypoint_evidence.values())).source_id is None


def test_shared_handler_script_entries_have_distinct_provenance(tmp_path: Path) -> None:
    (tmp_path / "cli.py").write_text("def run(): pass")
    (tmp_path / "pyproject.toml").write_text('[project.scripts]\none="cli:run"\ntwo="cli:run"\n')
    engine = QueryEngine.from_directory(str(tmp_path))
    facts = [fact for fact in engine.entrypoint_evidence() if fact["rule"] == "pyproject_script"]
    assert {fact["details"]["script_name"] for fact in facts} == {"one", "two"}
    assert len({fact["id"] for fact in facts}) == 2
    assert {fact["node_id"] for fact in facts} == {"cli:run"}


def test_merged_graph_retains_configuration_for_unmanifested_nodes(tmp_path: Path) -> None:
    from trailmark.analysis.entrypoints import detect_entrypoints
    from trailmark.parse import parse_file

    for name in ("a", "b"):
        root = tmp_path / name
        (root / ".trailmark").mkdir(parents=True)
        (root / f"{name}.py").write_text("def entry(): pass")
        (root / ".trailmark/entrypoints.toml").write_text(f'[[entrypoint]]\nnode="{name}:entry"\n')
    graph = parse_directory(str(tmp_path / "a"))
    graph.merge(parse_file(str(tmp_path / "b/b.py")))
    assert set(detect_entrypoints(graph, str(tmp_path / "b"))) == {"a:entry", "b:entry"}


def test_shared_configuration_does_not_choose_between_selected_roots(tmp_path: Path) -> None:
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "app.py").write_text("def entry(): pass")
    (tmp_path / "pyproject.toml").write_text('[project.scripts]\nrun="app:entry"\n')
    policy = SourcePolicy(tuple(SourceRoot(str(tmp_path / name), name) for name in ("a", "b")))
    engine = QueryEngine.from_directory(str(tmp_path), source_policy=policy)
    assert not engine.attack_surface()
    fact = next(fact for fact in engine.entrypoint_evidence() if fact["rule"] == "pyproject_script")
    assert fact["binding"] == "unresolved"


@pytest.mark.parametrize(
    "language,extension",
    [(lang, ext) for lang, exts in _LANGUAGE_EXTENSIONS.items() for ext in exts],
)
def test_every_registered_extension_reaches_its_frontend(
    language: str, extension: str, tmp_path: Path
) -> None:
    from tests.kat_runner import fixture_pair
    from tests.test_kat_parsers import PARSER_FIXTURES

    fixture, _ = fixture_pair(language, dict(reversed(PARSER_FIXTURES))[language])
    (tmp_path / (fixture.stem + extension)).write_bytes(fixture.read_bytes())
    graph = parse_directory(str(tmp_path), language)
    assert graph.source_file_counts == {language: 1}
    assert graph.nodes
