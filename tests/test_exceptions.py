"""Cross-frontend exception evidence and ownership regression corpus."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests.exception_cases import EXCEPTION_CASES
from trailmark.analysis.diff import compute_diff
from trailmark.models import CodeGraph, EdgeKind, ExceptionEventKind, ExceptionSupportStatus
from trailmark.models.exception_support import _PROFILES
from trailmark.parse import parse_file, supported_languages
from trailmark.query.api import QueryEngine

_FIXTURES = Path(__file__).parent / "fixtures" / "exceptions"
_CASES = [
    ("python", "python.py", ["Bad"], ["Bad", "Other"]),
    ("javascript", "javascript.js", ["Bad"], []),
    ("typescript", "typescript.ts", ["Bad"], []),
    ("cpp", "cpp.cpp", ["Bad"], ["Bad"]),
    ("java", "java.java", ["Bad"], ["Bad", "Other"]),
    ("c_sharp", "csharp.cs", ["Bad"], ["Bad"]),
    ("objc", "objc.m", [], ["Bad"]),
    ("kotlin", "kotlin.kt", ["Bad"], ["Bad"]),
    ("swift", "swift.swift", ["Bad"], []),
    ("dart", "dart.dart", ["Bad"], ["Bad"]),
    ("php", "php.php", ["\\App\\Bad"], ["\\App\\Bad", "Other"]),
]


def test_audit_corpus_covers_every_registered_frontend() -> None:
    assert {case[0] for case in EXCEPTION_CASES} == set(supported_languages())
    assert len(EXCEPTION_CASES) == len(supported_languages())
    assert set(_PROFILES) == set(supported_languages())


@pytest.mark.parametrize("language,filename,kinds,diagnostic", EXCEPTION_CASES)
def test_every_frontend_has_an_explicit_exception_audit_disposition(
    language: str,
    filename: str,
    kinds: frozenset[str],
    diagnostic: bool,
) -> None:
    path = _FIXTURES / filename
    graph = parse_file(str(path), language)
    support = graph.exception_support()
    assert len(support) == 1
    assert support[0].language == language
    assert support[0].events != ExceptionSupportStatus.UNVERIFIED
    assert bool(graph.parse_diagnostics) == diagnostic
    recorded = {
        event.kind.value for unit in graph.nodes.values() for event in unit.exception_events
    }
    assert recorded == kinds
    if kinds:
        assert support[0].events == ExceptionSupportStatus.PARTIAL
    else:
        assert support[0].events in {
            ExceptionSupportStatus.UNSUPPORTED,
            ExceptionSupportStatus.NOT_APPLICABLE,
        }
    engine = QueryEngine.from_graph(graph)
    assert json.loads(engine.to_json())["exception_support"] == engine.exception_support()
    if diagnostic:
        with pytest.raises(ValueError):
            parse_file(str(path), language, strict=True)
    else:
        parse_file(str(path), language, strict=True)
    for unit in graph.nodes.values():
        for event in unit.exception_events:
            assert event.location.file_path == str(path)
            assert unit.location.start_line <= event.location.start_line
            assert event.location.end_line <= unit.location.end_line


def test_call_based_error_candidates_do_not_claim_builtin_dispatch() -> None:
    for language, filename in [("go", "go.go"), ("r", "r.r"), ("lua", "lua.lua")]:
        graph = parse_file(str(_FIXTURES / filename), language, strict=True)
        event = next(
            event
            for unit in graph.nodes.values()
            for event in unit.exception_events
            if event.kind == ExceptionEventKind.RAISE
        )
        assert event.confidence.value == "uncertain"
        assert event.types == ()
        assert event.expression


@pytest.mark.parametrize(
    "source",
    [
        'package main\nfunc panic(s string) {}\nfunc f() { panic("ordinary call") }',
        'package main\nfunc f(panic func(string)) { panic("callback") }',
    ],
)
def test_known_shadowed_failure_names_are_not_exception_events(tmp_path: Path, source: str) -> None:
    path = tmp_path / "app.go"
    path.write_text(source)
    graph = parse_file(str(path), "go", strict=True)
    assert not any(unit.exception_events for unit in graph.nodes.values())


def test_embedded_verus_body_is_an_explicit_exception_coverage_gap(tmp_path: Path) -> None:
    path = tmp_path / "app.rs"
    path.write_text('verus! { fn f() { panic!("failed"); } }')
    graph = parse_file(str(path), "rust", strict=True)
    unit = next(unit for unit in graph.nodes.values() if unit.name == "f")
    assert unit.exception_events == ()
    assert "Verus macro bodies and unmodeled closures" in graph.exception_support()[0].notes


@pytest.mark.parametrize(
    "language,suffix,source",
    [
        ("go", ".go", 'package main\nfunc outer() { register(func() { panic("bad") }) }'),
        ("rust", ".rs", 'fn outer() { register(|| { panic!("bad"); }); }'),
        ("rust", ".rs", 'fn outer() { let future = async { panic!("bad"); }; }'),
        ("ruby", ".rb", "def outer\nregister { raise Bad }\nend"),
        ("ruby", ".rb", "def outer\nregister do\nraise Bad\nend\nend"),
    ],
)
def test_unmodeled_closure_events_are_not_assigned_to_the_creator(
    tmp_path: Path, language: str, suffix: str, source: str
) -> None:
    path = tmp_path / f"app{suffix}"
    path.write_text(source)
    graph = parse_file(str(path), language, strict=True)
    outer = next(unit for unit in graph.nodes.values() if unit.name == "outer")
    assert outer.exception_events == ()
    assert "omitted from events" in graph.exception_support()[0].notes


def test_support_profiles_merge_languages_and_keep_unknown_languages_unverified() -> None:
    graph = CodeGraph(language="auto", source_file_counts={"go": 1, "thrift": 1, "sql": 1})
    engine = QueryEngine.from_graph(graph)
    support = {row["language"]: row for row in engine.exception_support()}
    assert support["go"]["interprocedural_propagation"] == "unsupported"
    assert support["thrift"]["declarations"] == "partial"
    assert support["thrift"]["implicit_execution"] == "not_applicable"
    assert support["sql"]["events"] == "unsupported"
    support["go"]["notes"] = "changed"
    assert engine.exception_support()[0]["notes"] != "changed"
    unknown = QueryEngine.from_graph(CodeGraph(language="external"))
    assert unknown.exception_support()[0]["events"] == "unverified"
    assert QueryEngine.from_graph(CodeGraph()).exception_support() == []


def test_dart_on_only_handler_keeps_type_body_and_protected_region(tmp_path: Path) -> None:
    path = tmp_path / "app.dart"
    path.write_text("void f() { try { work(); } on Bad { observe(); } }")
    graph = parse_file(str(path), strict=True)
    unit = next(unit for unit in graph.nodes.values() if unit.name == "f")
    (handler,) = unit.exception_events
    assert handler.kind == ExceptionEventKind.HANDLER
    assert handler.expression == "on Bad"
    assert [ref.name for ref in handler.types] == ["Bad"]
    assert handler.protected_region is not None
    assert handler.location.start_col is not None and handler.location.end_col is not None
    assert (
        path.read_text()[handler.location.start_col : handler.location.end_col]
        == "on Bad { observe(); }"
    )


def test_swift_try_variants_do_not_conflate_propagation_suppression_and_trap(
    tmp_path: Path,
) -> None:
    path = tmp_path / "app.swift"
    path.write_text("func f() throws { try work(); let x = try? work(); let y = try! work() }")
    graph = parse_file(str(path), strict=True)
    unit = next(unit for unit in graph.nodes.values() if unit.name == "f")
    variants = [event for event in unit.exception_events if event.syntax == "try_expression"]
    assert [event.kind.value for event in variants] == ["propagate", "handler", "terminate"]
    assert variants[1].protected_region is not None


def test_cpp_exception_specifications_are_distinct_from_direct_throws(tmp_path: Path) -> None:
    path = tmp_path / "app.cpp"
    path.write_text("void f() noexcept(false) { throw 1; } void g() throw(Bad) { work(); }")
    graph = parse_file(str(path), strict=True)
    f = next(unit for unit in graph.nodes.values() if unit.name == "f")
    g = next(unit for unit in graph.nodes.values() if unit.name == "g")
    assert f.exception_events[0].expression == "noexcept(false)"
    assert f.exception_events[0].kind == ExceptionEventKind.DECLARES
    assert f.exception_events[0].types == ()
    assert g.exception_events[0].kind == ExceptionEventKind.DECLARES
    assert [ref.name for ref in g.exception_events[0].types] == ["Bad"]
    assert g.exception_types == ()


def test_csharp_using_declarations_do_not_mark_ordinary_locals_as_cleanup(tmp_path: Path) -> None:
    path = tmp_path / "app.cs"
    path.write_text(
        "class A { void f() { var x = 1; using var y = acquire(); "
        "await using var z = acquire(); } }"
    )
    graph = parse_file(str(path), strict=True)
    unit = next(unit for unit in graph.nodes.values() if unit.name == "f")
    assert len(unit.exception_events) == 2
    assert all(event.kind == ExceptionEventKind.CLEANUP for event in unit.exception_events)


def test_func_handler_has_a_lexical_region_and_events_follow_source_order() -> None:
    graph = parse_file(str(_FIXTURES / "func.fc"), strict=True)
    unit = next(unit for unit in graph.nodes.values() if unit.name == "f")
    raised, handler = unit.exception_events
    assert raised.kind == ExceptionEventKind.RAISE
    assert handler.kind == ExceptionEventKind.HANDLER
    assert handler.expression == "catch (arg, code)"
    assert handler.protected_region is not None
    assert raised.location.start_line < handler.location.start_line


def test_solidity_catch_protects_attempt_not_success_block() -> None:
    graph = parse_file(str(_FIXTURES / "solidity.sol"), strict=True)
    unit = next(unit for unit in graph.nodes.values() if unit.name == "f")
    handler = unit.exception_events[0]
    assert [ref.name for ref in handler.types] == ["Error"]
    assert handler.protected_region is not None
    source = (_FIXTURES / "solidity.sol").read_text().splitlines()[3]
    location = handler.protected_region
    assert location.start_col is not None and location.end_col is not None
    assert source[location.start_col : location.end_col] == "this.helper()"


def test_ruby_bare_raise_and_rescue_header_are_preserved() -> None:
    graph = parse_file(str(_FIXTURES / "ruby.rb"), strict=True)
    unit = next(unit for unit in graph.nodes.values() if unit.name == "f")
    assert sum(event.kind == ExceptionEventKind.RAISE for event in unit.exception_events) == 2
    handler = next(
        event for event in unit.exception_events if event.kind == ExceptionEventKind.HANDLER
    )
    assert "observe" not in (handler.expression or "")


def test_php_interruption_research_cases_keep_separate_owners_and_failure_branches() -> None:
    graph = parse_file(str(_FIXTURES / "php_interruptions.php"), strict=True)
    methods = {unit.name: unit for unit in graph.nodes.values() if unit.kind.value == "method"}
    for method, raised in {
        "__wakeup": "\\RuntimeException",
        "__unserialize": "\\UnexpectedValueException",
        "__toString": "\\Error",
    }.items():
        assert [ref.name for ref in methods[method].exception_types] == [raised]
        assert methods[method].exception_events[0].kind == ExceptionEventKind.RAISE
    decode = next(unit for unit in graph.nodes.values() if unit.name == "decode")
    assert [ref.name for ref in decode.exception_types] == ["\\LogicException"]
    handler = next(
        event for event in decode.exception_events if event.kind == ExceptionEventKind.HANDLER
    )
    assert [ref.name for ref in handler.types] == ["\\Exception"]
    assert any(event.kind == ExceptionEventKind.CLEANUP for event in decode.exception_events)
    callback = next(
        unit
        for unit in graph.nodes.values()
        if any(ref.name == "\\ErrorException" for ref in unit.exception_types)
    )
    assert callback.id != decode.id
    assert any(
        edge.source_id == methods["__destruct"].id and "sensitive" in edge.target_id
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS
    )
    assert not any(
        edge.source_id == decode.id and edge.target_id == methods["__destruct"].id
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS
    )
    support = QueryEngine.from_graph(graph).exception_support()[0]
    assert support["implicit_execution"] == "unsupported"


@pytest.mark.parametrize("language,filename,raised,caught", _CASES)
def test_exception_corpus(
    language: str,
    filename: str,
    raised: list[str],
    caught: list[str],
) -> None:
    graph = parse_file(str(_FIXTURES / filename), language, strict=True)
    unit = next(unit for unit in graph.nodes.values() if unit.name == "f")
    assert [ref.name for ref in unit.exception_types] == raised
    events = unit.exception_events
    assert any(event.kind == ExceptionEventKind.RAISE for event in events)
    handler = next(event for event in events if event.kind == ExceptionEventKind.HANDLER)
    assert [ref.name for ref in handler.types] == caught
    assert handler.protected_region is not None
    references = QueryEngine.from_graph(graph).type_references(unit.id)
    assert set(caught) <= {ref["name"] for ref in references}
    if language != "cpp":
        assert any(event.kind == ExceptionEventKind.CLEANUP for event in events)
    calls = [
        edge for edge in graph.edges if edge.source_id == unit.id and edge.kind == EdgeKind.CALLS
    ]
    # A handler body remains an ordinary possible call path; recording exception
    # syntax must not discard calls or pretend that catching a type proves execution.
    assert any("observe" in edge.target_id for edge in calls)
    engine = QueryEngine.from_graph(graph)
    encoded = json.loads(engine.to_json())
    queried = json.loads(json.dumps(engine.exception_events(unit.id)))
    assert encoded["nodes"][unit.id]["exception_events"] == queried
    assert all(event["location"]["file_path"].endswith(filename) for event in queried)


@pytest.mark.parametrize(
    ("language", "suffix", "source"),
    [
        ("kotlin", ".kt", "fun outer() { fun inner() { throw Bad() }; return helper() }"),
        (
            "swift",
            ".swift",
            "func outer() { func inner() throws { throw Bad() }; return helper() }",
        ),
        ("dart", ".dart", "void outer() { void inner() { throw Bad(); } return helper(); }"),
        ("php", ".php", "<?php function outer() { $inner = function() { throw new Bad(); }; }"),
    ],
)
def test_nested_exception_ownership(
    tmp_path: Path,
    language: str,
    suffix: str,
    source: str,
) -> None:
    path = tmp_path / f"app{suffix}"
    path.write_text(source)
    graph = parse_file(str(path), language, strict=True)
    outer = next(unit for unit in graph.nodes.values() if unit.name == "outer")
    assert outer.exception_events == ()
    assert outer.exception_types == ()
    inner = next(unit for unit in graph.nodes.values() if unit.exception_types)
    assert [ref.name for ref in inner.exception_types] == ["Bad"]
    assert inner.id != outer.id


def test_unknown_thrown_value_is_evidence_without_a_type(tmp_path: Path) -> None:
    path = tmp_path / "app.php"
    path.write_text("<?php function f($value) { throw $value; }")
    graph = parse_file(str(path), strict=True)
    unit = next(unit for unit in graph.nodes.values() if unit.name == "f")
    assert unit.exception_types == ()
    assert unit.exception_events[0].expression == "$value"
    assert unit.exception_events[0].kind == ExceptionEventKind.RAISE


def test_exception_diff_ignores_movement_but_detects_changed_handlers() -> None:
    graph = parse_file(str(_FIXTURES / "python.py"))
    unit = next(unit for unit in graph.nodes.values() if unit.name == "f")
    moved = replace(
        unit,
        exception_events=tuple(
            replace(
                event, location=replace(event.location, start_line=event.location.start_line + 10)
            )
            for event in unit.exception_events
        ),
    )
    before = CodeGraph(nodes={unit.id: unit})
    after = CodeGraph(nodes={unit.id: moved})
    assert compute_diff(before, after)["nodes"]["modified"] == []
    changed = replace(unit.exception_events[1], expression="except Other as error:")
    after.nodes[unit.id] = replace(unit, exception_events=(unit.exception_events[0], changed))
    changes = compute_diff(before, after)["nodes"]["modified"][0]["changes"]
    assert "exception_events" in changes
