"""Cross-frontend exception evidence and ownership regression corpus."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests.exception_cases import EXCEPTION_CASES
from trailmark.analysis.diff import compute_diff
from trailmark.models import (
    CodeGraph,
    EdgeKind,
    ExceptionEventKind,
    ExceptionSupportStatus,
    NodeKind,
)
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
    kinds = [event.kind for event in unit.exception_events]
    assert kinds.count(ExceptionEventKind.RAISE) == 1
    assert kinds.count(ExceptionEventKind.RETHROW) == 1
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


def _events(tmp_path: Path, name: str, source: str) -> dict[str, list[str]]:
    """Parse ``source`` and map each unit name to its event kinds."""
    path = tmp_path / name
    path.write_text(source)
    graph = parse_file(str(path), strict=True)
    return {
        unit.name: [event.kind.value for event in unit.exception_events]
        for unit in graph.nodes.values()
        if unit.exception_events
    }


@pytest.mark.parametrize(
    ("name", "source", "expected"),
    [
        (
            "app.swift",
            "func run(_ body: () throws -> Void) { }\n"
            "func make() throws -> () throws -> Int {\n"
            "    let h: (Int) throws -> Void = { _ in }\n    return { 1 }\n}\n"
            "func pass(_ f: () throws -> Void) rethrows { }\n"
            "protocol P { func p() throws -> () throws -> Int }\n",
            {"make": ["declares"], "pass": ["declares"], "p": ["declares"]},
        ),
        (
            "app.java",
            "class C { @A public <T> void f() throws Bad {} C() throws Worse {} }",
            {"f": ["declares"], "C": ["declares"]},
        ),
        (
            "app.thrift",
            "exception Bad {1: string reason} service S {void f() throws (1: Bad err)}",
            {"f": ["declares"]},
        ),
        (
            "app.swift",
            "@discardableResult func f<T>() throws -> T { fatalError() }\n",
            {"f": ["declares"]},
        ),
        (
            "app.cpp",
            "void f() { std::function<void() noexcept> cb; }\n"
            "void g(void (*cb)() noexcept) noexcept { }\n"
            "auto h() -> void (*)() noexcept { return nullptr; }\n"
            "struct S { void m() const noexcept {} void d() noexcept; };\n"
            "int* p() noexcept { return 0; }\n"
            "template <class T> void t() noexcept { }\n"
            "void q() throw(Bad) { void (*fp)() throw(X); }\n",
            {"g": ["declares"], "m": ["declares"], "p": ["declares"], "t": ["declares"]}
            | {"q": ["declares"]},
        ),
    ],
)
def test_declarations_come_only_from_the_owners_signature(
    tmp_path: Path, name: str, source: str, expected: dict[str, list[str]]
) -> None:
    assert _events(tmp_path, name, source) == expected


def test_raises_outside_callables_are_evidence_without_raising_functions(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    path.write_text(
        "import sys\nif sys.version_info < (3, 8):\n    raise RuntimeError('old')\n"
        "class C:\n    raise TypeError('body')\n"
        "def f():\n    raise ValueError('call')\n"
    )
    engine = QueryEngine.from_directory(str(tmp_path), language="python")
    module = engine.exception_events("app")
    assert [event["kind"] for event in module] == ["raise", "raise"]
    assert engine.functions_that_raise("RuntimeError") == []
    assert engine.functions_that_raise("TypeError") == []
    assert [unit["name"] for unit in engine.functions_that_raise("ValueError")] == ["f"]


def test_python_bare_class_raises_keep_type_hints(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    path.write_text(
        "def plain():\n    raise ValueError\n"
        "def chained():\n    raise ValueError from None\n"
        "def rebound():\n    try:\n        work()\n    except Bad as err:\n        raise err\n"
    )
    graph = parse_file(str(path), strict=True)
    types = {unit.name: [ref.name for ref in unit.exception_types] for unit in graph.nodes.values()}
    assert types["plain"] == ["ValueError"]
    assert types["chained"] == ["ValueError"]
    assert types["rebound"] == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("def f():\n    raise ValueError\n", "ValueError"),
        ("def f():\n    raise PythonFinalizationError\n", "PythonFinalizationError"),
        ("class bad(Exception): pass\ndef f():\n    raise bad\n", "bad"),
        ("def f():\n    class Bad(Exception): pass\n    raise Bad\n", "Bad"),
        (
            "class Outer:\n    class Bad(Exception): pass\ndef f():\n    raise Outer.Bad\n",
            "Outer.Bad",
        ),
        ("from builtins import ValueError as Bad\ndef f():\n    raise Bad\n", "Bad"),
        ("import builtins as b\ndef f():\n    raise b.ValueError\n", "b.ValueError"),
        ("def f(ValueError):\n    raise ValueError\n", None),
        ("def f(ValueError=ValueError):\n    raise ValueError\n", None),
        ("ValueError = object()\ndef f():\n    raise ValueError\n", None),
        ("def f():\n    raise ValueError\n    ValueError = object()\n", None),
        ("def f():\n    ValueError: object\n    raise ValueError\n", None),
        (
            "def f():\n    try: pass\n    except Exception as ValueError:\n"
            "        raise ValueError\n",
            None,
        ),
        ("def f():\n    with manager() as ValueError:\n        raise ValueError\n", None),
        ("def f():\n    for ValueError in values:\n        raise ValueError\n", None),
        ("def f(x):\n    match x:\n        case ValueError:\n            raise ValueError\n", None),
        ("def f():\n    del ValueError\n    raise ValueError\n", None),
        ("from other import ValueError\ndef f():\n    raise ValueError\n", None),
        ("import other as b\ndef f():\n    raise b.ValueError\n", None),
        ("import builtins.ValueError as Bad\ndef f():\n    raise Bad\n", None),
        ("from other import *\ndef f():\n    raise ValueError\n", None),
        ("if flag:\n    class Bad(Exception): pass\ndef f():\n    raise Bad\n", None),
        ("@decorate\nclass Bad(Exception): pass\ndef f():\n    raise Bad\n", None),
        ("def Bad(): pass\ndef f():\n    raise Bad\n", None),
        ("class Bad(Exception): pass\nerr = Bad()\ndef f():\n    raise err\n", None),
        ("def f():\n    raise UnknownError\n", None),
        ("class Bad(Exception): pass\ndef f():\n    raise Bad\n    Bad = object()\n", None),
        (
            "class Bad(Exception): pass\ndef mutate():\n    global Bad\n    Bad = object()\n"
            "def f():\n    raise Bad\n",
            None,
        ),
        (
            "class Outer:\n    class Bad(Exception): pass\nOuter.Bad = object()\n"
            "def f():\n    raise Outer.Bad\n",
            None,
        ),
        ("import builtins\nbuiltins.ValueError = object()\ndef f():\n    raise ValueError\n", None),
        ("import builtins as b\nb.ValueError = object()\ndef f():\n    raise b.ValueError\n", None),
        ("globals()['ValueError'] = object()\ndef f():\n    raise ValueError\n", None),
        (
            "class C:\n    ValueError = object()\n    def f(self):\n        raise ValueError\n",
            "ValueError",
        ),
        ("def f():\n    ValueError = object()\n    def g():\n        raise ValueError\n", None),
    ],
)
def test_python_bare_raise_types_follow_lexical_class_bindings(
    tmp_path: Path, source: str, expected: str | None
) -> None:
    path = tmp_path / "app.py"
    path.write_text(source)
    graph = parse_file(str(path), strict=True)
    (event,) = (
        event
        for unit in graph.nodes.values()
        for event in unit.exception_events
        if event.kind == ExceptionEventKind.RAISE
    )
    assert [ref.name for ref in event.types] == ([] if expected is None else [expected])
    assert event.expression is not None


@pytest.mark.parametrize(
    ("source", "column"),
    [
        ("void (*f() noexcept)() noexcept { return nullptr; }", 11),
        ("void (*f())() noexcept { return nullptr; }", None),
        ("void (&f() noexcept)() { return target; }", 11),
        ("void f() noexcept try { work(); } catch (...) {}", 9),
        ("void (C::*f())() noexcept { return nullptr; }", None),
    ],
)
def test_cpp_declarations_follow_the_callable_through_returned_function_types(
    tmp_path: Path, source: str, column: int | None
) -> None:
    path = tmp_path / "app.cpp"
    path.write_text(source)
    graph = parse_file(str(path), strict=True)
    unit = next(unit for unit in graph.nodes.values() if unit.kind == NodeKind.FUNCTION)
    declarations = [
        event for event in unit.exception_events if event.kind == ExceptionEventKind.DECLARES
    ]
    assert [event.location.start_col for event in declarations] == (
        [] if column is None else [column]
    )


@pytest.mark.parametrize(
    ("filename", "source", "synthetic", "kind"),
    [
        ("app.lua", 'function f() error("bad") end', "<chunk>", ExceptionEventKind.RAISE),
        ("app.sh", "f() { exit 1; }", "<script>", ExceptionEventKind.TERMINATE),
    ],
)
def test_callable_owns_events_when_its_span_equals_the_file_body(
    tmp_path: Path, filename: str, source: str, synthetic: str, kind: ExceptionEventKind
) -> None:
    path = tmp_path / filename
    path.write_text(source)
    graph = parse_file(str(path), strict=True)
    units = {unit.name: unit for unit in graph.nodes.values()}
    assert [event.kind for event in units["f"].exception_events] == [kind]
    assert units[synthetic].exception_events == ()


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("throw Err.bad", ["Err"]),
        ("throw Err.bad(code: 1)", ["Err"]),
        ("throw Err.make(reason)", ["Err"]),
        ("throw Module.Failure(code: 1)", ["Module.Failure"]),
        ("throw Failure(code: 1)", ["Failure"]),
    ],
)
def test_swift_enum_payload_cases_name_their_containing_type(
    tmp_path: Path, statement: str, expected: list[str]
) -> None:
    path = tmp_path / "app.swift"
    path.write_text(f"func f() throws {{ {statement} }}")
    graph = parse_file(str(path), strict=True)
    unit = next(unit for unit in graph.nodes.values() if unit.name == "f")
    assert [ref.name for ref in unit.exception_types] == expected


def test_solidity_revert_reasons_are_not_error_types(tmp_path: Path) -> None:
    path = tmp_path / "app.sol"
    path.write_text(
        "contract C { error Bad(uint x);\n"
        "function f() public { revert Bad(1); }\n"
        "function g() public { revert('reason'); }\n"
        "function h() public { revert(); } }"
    )
    graph = parse_file(str(path), strict=True)
    types = {unit.name: [ref.name for ref in unit.exception_types] for unit in graph.nodes.values()}
    assert (types["f"], types["g"], types["h"]) == (["Bad"], [], [])


@pytest.mark.parametrize(
    ("name", "source", "expression"),
    [
        ("app.r", "f <- function() {\n  tryCatch({\n    heavy()\n  }, error = h)\n}\n", "tryCatch"),
        ("app.lua", "function f()\n  pcall(function()\n    heavy()\n  end)\nend\n", "pcall"),
        ("app.hs", "module App where\nf = catch (heavy 1) handler\n", "catch"),
        ("app.rb", "def f\n  catch(:done) do\n    heavy\n  end\nend\n", "catch"),
        ("app.sh", "f() {\n  trap cleanup ERR\n}\n", "trap cleanup ERR"),
        ("app.r", "f <- function() {\n  on.exit(cleanup())\n}\n", "on.exit(cleanup())"),
    ],
)
def test_call_based_wrappers_omit_their_protected_body(
    tmp_path: Path, name: str, source: str, expression: str
) -> None:
    path = tmp_path / name
    path.write_text(source)
    graph = parse_file(str(path), strict=True)
    (event,) = (
        event
        for unit in graph.nodes.values()
        for event in unit.exception_events
        if event.kind in {ExceptionEventKind.HANDLER, ExceptionEventKind.CLEANUP}
    )
    assert event.expression == expression


def test_ruby_bare_raises_are_rethrows_in_every_statement_position(tmp_path: Path) -> None:
    source = (
        "def f\n  begin\n    work\n  rescue Bad\n    raise\n  else\n    raise\n"
        "  ensure\n    fail\n  end\nend\n"
        "def g(x)\n  case x\n  when 1\n    raise\n  end\n  while x do raise end\n"
        "  raise if x\nend\n"
        "def h(obj)\n  obj.raise\n  raise unless obj\n  (raise)\nend\n"
        "def k\n  raise Bad, 'message'\nend\n"
    )
    events = _events(tmp_path, "app.rb", source)
    assert events["f"] == ["handler", "rethrow", "rethrow", "cleanup", "rethrow"]
    assert events["g"] == ["rethrow"] * 3
    assert events["h"] == ["rethrow"] * 2
    assert events["k"] == ["raise"]
