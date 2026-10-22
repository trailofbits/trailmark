"""Overload identities retain every body and the references owned by that body."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from trailmark.analysis.proxies import ensure_proxy_nodes
from trailmark.models import (
    CodeGraph,
    CodeUnit,
    EdgeConfidence,
    EdgeKind,
    NodeKind,
    SourceLocation,
)
from trailmark.parsers._declarations import add_callable, callable_scope
from trailmark.parsers.base import LanguageParser
from trailmark.parsers.cpp import CppParser
from trailmark.parsers.csharp import CSharpParser
from trailmark.parsers.java import JavaParser
from trailmark.parsers.kotlin import KotlinParser
from trailmark.parsers.solidity import SolidityParser
from trailmark.parsers.swift import SwiftParser

_OVERLOADS = [
    pytest.param(
        CppParser,
        ".cpp",
        """class C { public:
int first(){return 1;}
int second(){return 2;}
int third(){return 3;}
int f(int x){return first();}
int f(double x){return second();}
int f(const char* x){return third();}
int invoke(){return f(1);}
};""",
        id="cpp",
    ),
    pytest.param(
        JavaParser,
        ".java",
        """class C {
int first(){return 1;}
int second(){return 2;}
int third(){return 3;}
int f(int x){return first();}
int f(double x){return second();}
int f(String x){return third();}
int invoke(){return f(1);}
}""",
        id="java",
    ),
    pytest.param(
        CSharpParser,
        ".cs",
        """class C {
int first(){return 1;}
int second(){return 2;}
int third(){return 3;}
int f(int x){return first();}
int f(double x){return second();}
int f(string x){return third();}
int invoke(){return f(1);}
}""",
        id="csharp",
    ),
    pytest.param(
        SolidityParser,
        ".sol",
        """contract C {uint a; uint b; uint c;
function first() internal pure returns(uint){return 1;}
function second() internal pure returns(uint){return 2;}
function third() internal pure returns(uint){return 3;}
function f(uint x) public view returns(uint){return first()+a;}
function f(address x) public view returns(uint){return second()+b;}
function f(bytes32 x) public view returns(uint){return third()+c;}
function invoke() public view returns(uint){return f(1);}
}""",
        id="solidity",
    ),
    pytest.param(
        KotlinParser,
        ".kt",
        """class C {
fun first(): Int {return 1};
fun second(): Int {return 2};
fun third(): Int {return 3};
fun f(x: Int): Int {return first()};
fun f(x: Double): Int {return second()};
fun f(x: String): Int {return third()};
fun invoke(): Int {return f(1)};
}""",
        id="kotlin",
    ),
    pytest.param(
        SwiftParser,
        ".swift",
        """class C {
func first() -> Int {return 1};
func second() -> Int {return 2};
func third() -> Int {return 3};
func f(_ x: Int) -> Int {return first()};
func f(_ x: Double) -> Int {return second()};
func f(_ x: String) -> Int {return third()};
func invoke() -> Int {return f(1)};
}""",
        id="swift",
    ),
]


def _parse(
    tmp_path: Path, parser_type: type[LanguageParser], extension: str, source: str
) -> CodeGraph:
    path = tmp_path / f"identity{extension}"
    path.write_text(source)
    return parser_type().parse_file(str(path))


@pytest.mark.parametrize(("parser_type", "extension", "source"), _OVERLOADS)
@pytest.mark.parametrize("same_line", [False, True], ids=["multiline", "same-line"])
def test_each_overload_keeps_its_body_and_container(
    tmp_path: Path,
    parser_type: type[LanguageParser],
    extension: str,
    source: str,
    same_line: bool,
) -> None:
    if same_line:
        source = source.replace("\n", " ")
    graph = _parse(tmp_path, parser_type, extension, source)

    overloads = sorted(
        (node for node in graph.nodes.values() if node.name == "f"),
        key=lambda node: (node.location.start_line, node.location.start_col or 0),
    )
    assert len(overloads) == 3
    assert "identity:C.f" not in graph.nodes
    assert all(node.kind == NodeKind.METHOD for node in overloads)
    assert len({node.id for node in overloads}) == 3
    assert len({(node.location.start_line, node.location.start_col) for node in overloads}) == 3
    assert all(len(node.parameters) == 1 for node in overloads)
    assert len({node.parameters[0].type_ref for node in overloads}) == 3

    for node, marker, state in zip(
        overloads, ("first", "second", "third"), ("a", "b", "c"), strict=True
    ):
        calls = [e for e in graph.edges if e.source_id == node.id and e.kind == EdgeKind.CALLS]
        assert len(calls) == 1
        assert calls[0].target_id.rsplit(":", 1)[-1].rsplit(".", 1)[-1] == marker
        assert calls[0].location is not None
        assert calls[0].location.start_line == node.location.start_line
        assert (
            sum(
                e.source_id == "identity:C" and e.target_id == node.id
                for e in graph.edges
                if e.kind == EdgeKind.CONTAINS
            )
            == 1
        )
        if extension == ".sol":
            accesses = [
                e for e in graph.edges if e.source_id == node.id and e.kind == EdgeKind.READS
            ]
            assert [e.target_id for e in accesses] == [f"identity:C.{state}"]

    assert "identity:C.invoke" in graph.nodes
    assert all(f"identity:C.{name}" in graph.nodes for name in ("first", "second", "third"))
    ambiguous_calls = [
        e for e in graph.edges if e.source_id == "identity:C.invoke" and e.kind == EdgeKind.CALLS
    ]
    assert len(ambiguous_calls) == 1
    assert ambiguous_calls[0].target_id not in {node.id for node in overloads}
    assert ambiguous_calls[0].confidence == EdgeConfidence.UNCERTAIN
    assert all(e.source_id in graph.nodes for e in graph.edges)


@pytest.mark.parametrize(
    ("parser_type", "extension", "source"),
    [
        pytest.param(
            KotlinParser,
            ".kt",
            """fun f(x: Int): Int {return x}
fun f(x: String): Int {return 0}
fun outside(): Int {return f(1)}
class C {
fun f(x: Int): Int {return x}
fun inside(): Int {return f(1)}
}
class D {fun inside(): Int {return f(1)}}
""",
            id="kotlin",
        ),
        pytest.param(
            SwiftParser,
            ".swift",
            """func f(_ x: Int) -> Int {return x}
func f(_ x: String) -> Int {return 0}
func outside() -> Int {return f(1)}
class C {
func f(_ x: Int) -> Int {return x}
func inside() -> Int {return f(1)}
}
class D {func inside() -> Int {return f(1)}}
""",
            id="swift",
        ),
    ],
)
def test_bare_overload_confidence_respects_the_callers_container(
    tmp_path: Path, parser_type: type[LanguageParser], extension: str, source: str
) -> None:
    graph = _parse(tmp_path, parser_type, extension, source)

    calls = {e.source_id: e for e in graph.edges if e.kind == EdgeKind.CALLS}
    assert calls["identity:outside"].confidence == EdgeConfidence.UNCERTAIN
    assert calls["identity:D.inside"].confidence == EdgeConfidence.UNCERTAIN
    assert calls["identity:C.inside"].confidence == EdgeConfidence.CERTAIN
    assert calls["identity:C.inside"].target_id == "identity:C.f"
    assert all(
        calls[source].target_id not in graph.nodes
        for source in ("identity:outside", "identity:D.inside")
    )


def test_solidity_structs_and_enums_belong_to_their_declaring_contract(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        SolidityParser,
        ".sol",
        """struct Data {uint globalValue;}
enum Status {Global}
contract A {
    struct Data {uint localValue;}
    enum Status {Local}
}
contract B {
    struct Data {address localValue;}
    enum Status {Other}
}
""",
    )

    expected = {
        "identity:Data": (NodeKind.STRUCT, "identity", 1),
        "identity:Status": (NodeKind.ENUM, "identity", 2),
        "identity:A.Data": (NodeKind.STRUCT, "identity:A", 4),
        "identity:A.Status": (NodeKind.ENUM, "identity:A", 5),
        "identity:B.Data": (NodeKind.STRUCT, "identity:B", 8),
        "identity:B.Status": (NodeKind.ENUM, "identity:B", 9),
    }
    for node_id, (kind, parent_id, line) in expected.items():
        assert graph.nodes[node_id].kind == kind
        assert graph.nodes[node_id].location.start_line == line
        parents = [
            e.source_id
            for e in graph.edges
            if e.kind == EdgeKind.CONTAINS and e.target_id == node_id
        ]
        assert parents == [parent_id]


def test_java_nested_classes_keep_distinct_owners_and_overload_bodies(tmp_path: Path) -> None:
    graph = _parse(
        tmp_path,
        JavaParser,
        ".java",
        """class Base {}
interface Contract {}
class A {
    static class Base {}
    interface Contract {}
    static class Inner extends Base implements Contract {
        int first(){return 1;}
        int second(){return 2;}
        int f(int x){return first();}
        int f(String x){return second();}
    }
}
class B {
    class Base {}
    interface Contract {}
    class Inner extends Base implements Contract {int f(){return 3;}}
}
""",
    )

    assert graph.nodes["identity:A.Inner"].kind == NodeKind.CLASS
    assert graph.nodes["identity:B.Inner"].kind == NodeKind.CLASS
    assert "identity:Inner" not in graph.nodes
    overloads = [
        node
        for node in graph.nodes.values()
        if node.name == "f" and node.id.startswith("identity:A.Inner.")
    ]
    assert len(overloads) == 2
    for node, marker in zip(overloads, ("first", "second"), strict=True):
        assert [
            e.source_id
            for e in graph.edges
            if e.kind == EdgeKind.CONTAINS and e.target_id == node.id
        ] == ["identity:A.Inner"]
        assert [
            e.target_id for e in graph.edges if e.kind == EdgeKind.CALLS and e.source_id == node.id
        ] == [f"identity:A.Inner.{marker}"]
    assert "identity:B.Inner.f" in graph.nodes
    for owner in ("A", "B"):
        assert any(
            e.kind == EdgeKind.CONTAINS
            and e.source_id == f"identity:{owner}"
            and e.target_id == f"identity:{owner}.Inner"
            for e in graph.edges
        )
        assert {
            (e.kind, e.target_id)
            for e in graph.edges
            if e.source_id == f"identity:{owner}.Inner"
            and e.kind in {EdgeKind.INHERITS, EdgeKind.IMPLEMENTS}
        } == {
            (EdgeKind.INHERITS, f"identity:{owner}.Base"),
            (EdgeKind.IMPLEMENTS, f"identity:{owner}.Contract"),
        }


@dataclass(frozen=True)
class _SharedName:
    parser_type: type[LanguageParser]
    extension: str
    type_id: str
    type_kind: NodeKind
    owner_id: str
    prefix: str
    type_source: str
    callable_sources: tuple[str, str]
    suffix: str


_SHARED_NAMES = [
    pytest.param(
        _SharedName(
            CppParser,
            ".cpp",
            "identity:stat",
            NodeKind.STRUCT,
            "identity",
            "int first(){return 1;}\nint second(){return 2;}",
            "struct stat {int size;};",
            (
                "int stat(const char* p, struct stat* s){return first();}",
                "int stat(int fd, struct stat* s){return second();}",
            ),
            'int use(struct stat* s){return stat("",s);}',
        ),
        id="cpp",
    ),
    pytest.param(
        _SharedName(
            JavaParser,
            ".java",
            "identity:Outer.Builder",
            NodeKind.CLASS,
            "identity:Outer",
            "class Outer {int first(){return 1;} int second(){return 2;}",
            "static class Builder {int size(){return 1;}}",
            (
                "Builder Builder(){first(); return new Builder();}",
                "Builder Builder(int count){second(); return new Builder();}",
            ),
            "Builder use(){return Builder();}}",
        ),
        id="java",
    ),
    pytest.param(
        _SharedName(
            KotlinParser,
            ".kt",
            "identity:Scope",
            NodeKind.INTERFACE,
            "identity",
            "fun first() {}\nfun second() {}",
            "interface Scope {}",
            (
                "fun Scope(c: Int): Scope {first(); return object : Scope {}}",
                "fun Scope(c: String): Scope {second(); return object : Scope {}}",
            ),
            "fun use(): Scope {return Scope(1)}",
        ),
        id="kotlin",
    ),
]


@pytest.mark.parametrize("case", _SHARED_NAMES)
@pytest.mark.parametrize("type_first", [True, False], ids=["type-first", "callable-first"])
@pytest.mark.parametrize("callable_count", [1, 2], ids=["unique", "overloaded"])
def test_types_and_callables_share_a_name_without_overwriting_each_other(
    tmp_path: Path, case: _SharedName, type_first: bool, callable_count: int
) -> None:
    declarations = list(case.callable_sources[:callable_count])
    declarations.insert(0 if type_first else len(declarations), case.type_source)
    source = "\n".join((case.prefix, *declarations, case.suffix))
    graph = _parse(tmp_path, case.parser_type, case.extension, source)
    factory_name = case.type_id.rsplit(":", 1)[-1].rsplit(".", 1)[-1]
    callables = sorted(
        (
            node
            for node in graph.nodes.values()
            if node.name == factory_name and node.kind in {NodeKind.FUNCTION, NodeKind.METHOD}
        ),
        key=lambda node: node.location.start_line,
    )

    assert graph.nodes[case.type_id].kind == case.type_kind
    assert len(callables) == callable_count
    assert all(node.id.startswith(f"{case.type_id}@") for node in callables)
    for node, marker in zip(callables, ("first", "second")[:callable_count], strict=True):
        calls = [
            e
            for e in graph.edges
            if e.source_id == node.id
            and e.kind == EdgeKind.CALLS
            and "constructor_type" not in dict(e.attributes)
        ]
        if case.extension == ".java":
            assert any(
                e.source_id == node.id and "constructor_type" in dict(e.attributes)
                for e in graph.edges
            )
        assert len(calls) == 1
        assert calls[0].target_id.rsplit(":", 1)[-1].rsplit(".", 1)[-1] == marker
    for node_id in (case.type_id, *(node.id for node in callables)):
        assert [
            e.source_id
            for e in graph.edges
            if e.kind == EdgeKind.CONTAINS and e.target_id == node_id
        ] == [case.owner_id]

    use_id = f"{case.owner_id}.use" if case.extension == ".java" else "identity:use"
    use_calls = [e for e in graph.edges if e.source_id == use_id and e.kind == EdgeKind.CALLS]
    assert len(use_calls) == 1
    if callable_count == 1:
        assert use_calls[0].target_id == callables[0].id
        assert use_calls[0].confidence == EdgeConfidence.CERTAIN
    else:
        assert use_calls[0].target_id not in graph.nodes
        assert use_calls[0].confidence == EdgeConfidence.UNCERTAIN

    if case.extension == ".java":
        assert [
            e.source_id
            for e in graph.edges
            if e.kind == EdgeKind.CONTAINS and e.target_id == "identity:Outer.Builder.size"
        ] == [case.type_id]
    if case.extension in {".java", ".kt"}:
        ensure_proxy_nodes(graph)
        assert {
            e.source_id
            for e in graph.edges
            if e.kind == EdgeKind.TYPE_USES and e.target_id == case.type_id
        } == {use_id, *(node.id for node in callables)}


def test_identical_callable_source_positions_still_raise() -> None:
    graph = CodeGraph(language="java")
    unit = CodeUnit(
        id="identity:C.f",
        name="f",
        kind=NodeKind.METHOD,
        location=SourceLocation(file_path="identity.java", start_line=1, end_line=1, start_col=0),
    )

    with pytest.raises(ValueError, match="Declaration ID collision"), callable_scope(graph):
        add_callable(graph, unit)
        add_callable(graph, unit)


@pytest.mark.parametrize("type_first", [True, False], ids=["type-first", "factory-first"])
def test_kotlin_class_constructor_is_not_mistaken_for_the_factory(
    tmp_path: Path, type_first: bool
) -> None:
    declarations = ["class Scope {}", "fun Scope(c: Int): Scope {return Scope()}"]
    if not type_first:
        declarations.reverse()
    graph = _parse(
        tmp_path,
        KotlinParser,
        ".kt",
        "\n".join((*declarations, "fun use(): Scope {return Scope(1)}")),
    )

    assert graph.nodes["identity:Scope"].kind == NodeKind.CLASS
    factory = next(
        node
        for node in graph.nodes.values()
        if node.name == "Scope" and node.kind == NodeKind.FUNCTION
    )
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert {e.source_id for e in calls} == {factory.id, "identity:use"}
    assert len(calls) == 2
    assert all(e.confidence == EdgeConfidence.UNCERTAIN for e in calls)
    assert all(e.target_id not in graph.nodes for e in calls)
    ensure_proxy_nodes(graph)
    assert all(
        graph.nodes[e.target_id].kind == NodeKind.PROXY
        for e in graph.edges
        if e.kind == EdgeKind.CALLS
    )
