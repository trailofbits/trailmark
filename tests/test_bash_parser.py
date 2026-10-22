"""Shell execution ownership and conservative command binding."""

from pathlib import Path

import pytest
from tree_sitter import Parser
from tree_sitter_language_pack import get_language

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.nodes import NodeKind
from trailmark.parse import detect_languages, parse_directory, parse_file
from trailmark.query.api import QueryEngine


def _file(tmp_path: Path, source: str) -> Path:
    assert not Parser(get_language("bash")).parse(source.encode()).root_node.has_error
    path = tmp_path / "sample.sh"
    path.write_text(source)
    return path


def test_script_functions_substitutions_and_ownership(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        "helper() { if true; then echo ok; fi; }\n"
        'function main { helper | cat; x=$(helper); (helper); }\nmain "$@"\n',
    )
    graph = parse_file(str(path))
    assert graph.nodes["sample:helper"].parameters == ()
    assert graph.nodes["sample:helper"].cyclomatic_complexity == 2
    assert graph.nodes["sample:<script>"].cyclomatic_complexity == 1
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    helpers = [e for e in calls if e.target_id == "sample:helper"]
    assert len(helpers) == 3 and all(e.source_id == "sample:main" for e in helpers)
    assert all(e.confidence == EdgeConfidence.INFERRED for e in helpers)
    assert any(e.source_id == "sample:<script>" and e.target_id == "sample:main" for e in calls)


@pytest.mark.parametrize(
    "source",
    [
        "helper; helper() { :; }",
        "helper() { :; }; helper() { :; }; helper",
        "if true; then helper() { :; }; fi; helper",
        "outer() { helper() { :; }; }; helper",
        "helper() { :; }; alias helper=other; helper",
        "helper() { :; }; unset -f helper; helper",
        'helper() { :; }; eval "$CODE"; helper',
        "helper() { :; }; source ./other.sh; helper",
        'helper() { :; }; builtin eval "$CODE"; helper',
        "helper() { :; }; command -p source ./other.sh; helper",
        "helper() { :; }; command source ./other.sh -v; helper",
        "helper() { :; }; builtin unset -f helper; helper",
    ],
)
def test_runtime_bindings_stay_uncertain(tmp_path: Path, source: str) -> None:
    path = _file(tmp_path, source)
    graph = parse_file(str(path))
    assert all(
        e.confidence == EdgeConfidence.UNCERTAIN for e in graph.edges if e.kind == EdgeKind.CALLS
    )


def test_dynamic_and_external_commands_do_not_bind_across_files(tmp_path: Path) -> None:
    _file(
        tmp_path,
        "helper() { :; }; main() { command helper; builtin helper; "
        '"$CMD"; ./helper; receiver.helper; }\n',
    )
    (tmp_path / "other.bash").write_text("helper() { :; }; receiver.helper() { :; }")
    graph = parse_directory(str(tmp_path), language="auto")
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS and e.source_id == "sample:main"]
    assert len(calls) == 5
    assert all(graph.nodes[e.target_id].kind == NodeKind.PROXY for e in calls)
    assert graph.source_file_counts == {"bash": 2}
    assert detect_languages(str(tmp_path)) == ["bash"]


def test_literal_source_dependencies_and_query_paths(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        "source ./one.sh\n. 'two.sh'\n. \"$DIR/three.sh\"\nsource *.sh\nsource ~/four.sh\n",
    )
    assert parse_file(str(path)).dependencies == ["./one.sh", "two.sh"]
    path.write_text("helper() { :; }\nmain() { helper; }\nmain\n")
    engine = QueryEngine.from_directory(str(tmp_path), language="bash")
    assert {e["node_id"] for e in engine.attack_surface()} == {"sample:main"}
    assert engine.entrypoint_paths_to("helper")


def test_function_body_can_be_a_conditional_and_own_redirects(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        "helper() { :; }\nmain() if helper; then helper; fi >$(helper)\nmain\n",
    )
    graph = parse_file(str(path))
    assert graph.nodes["sample:main"].cyclomatic_complexity == 2
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len([e for e in calls if e.source_id == "sample:main"]) == 3
    assert all(e.target_id == "sample:helper" for e in calls if e.source_id == "sample:main")
    assert [e.target_id for e in calls if e.source_id == "sample:<script>"] == ["sample:main"]


def test_builtin_inspection_does_not_execute_and_shadowed_source_is_not_import(
    tmp_path: Path,
) -> None:
    path = _file(
        tmp_path,
        "source() { :; }\nsource fake.sh\nbuiltin source real.sh\n",
    )
    assert parse_file(str(path)).dependencies == ["real.sh"]
    path.write_text("helper() { :; }\ncommand -v eval\nmain() { helper; }\n")
    graph = parse_file(str(path))
    assert any(
        e.kind == EdgeKind.CALLS
        and e.source_id == "sample:main"
        and e.target_id == "sample:helper"
        and e.confidence == EdgeConfidence.INFERRED
        for e in graph.edges
    )


def test_repeated_and_nested_definitions_keep_separate_bodies(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        "helper() { first; }\nhelper() { second; }\n"
        "outer() { inner() { if true; then third; fi; }; fourth; }\n",
    )
    graph = parse_file(str(path))
    helpers = [u for u in graph.nodes.values() if u.name == "helper"]
    assert len(helpers) == 2 and len({u.id for u in helpers}) == 2
    assert graph.nodes["sample:outer.inner"].cyclomatic_complexity == 2
    assert graph.nodes["sample:outer"].cyclomatic_complexity == 1
    outer_calls = [
        e for e in graph.edges if e.kind == EdgeKind.CALLS and e.source_id == "sample:outer"
    ]
    assert len(outer_calls) == 2
    assert any("fourth" in e.target_id for e in outer_calls)
    assert any(
        e.target_id == "sample:outer.inner" and dict(e.attributes).get("deferred")
        for e in outer_calls
    )


def test_script_entrypoint_override_reaches_helper(tmp_path: Path) -> None:
    _file(tmp_path, "helper() { :; }\nhelper\n")
    config = tmp_path / ".trailmark"
    config.mkdir()
    (config / "entrypoints.toml").write_text(
        '[[entrypoint]]\nnode = "sample:<script>"\nkind = "user_input"\n'
        'trust = "untrusted_external"\n'
    )
    engine = QueryEngine.from_directory(str(tmp_path), language="auto")
    assert {e["node_id"] for e in engine.attack_surface()} == {"sample:<script>"}
    assert engine.entrypoint_paths_to("helper")


def test_parse_errors_warn(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "broken.sh"
    path.write_text("function broken { if")
    parse_file(str(path))
    assert "Bash syntax errors" in caplog.text
