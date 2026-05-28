"""Tests for the TypeScript language parser."""

from __future__ import annotations

import os
import tempfile

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers.typescript.parser import TypeScriptParser

SAMPLE_CODE = """\
import { Observable } from 'rxjs';

interface Serializable {
    serialize(): string;
    deserialize(data: string): void;
}

enum Status {
    Active,
    Inactive,
    Pending,
}

/** Base entity class. */
class Entity {
    constructor(public id: number) {}

    getId(): number {
        return this.id;
    }
}

class User extends Entity implements Serializable {
    constructor(id: number, public name: string) {
        super(id);
    }

    serialize(): string {
        return JSON.stringify({ id: this.getId(), name: this.name });
    }

    deserialize(data: string): void {
        const parsed = JSON.parse(data);
        this.name = parsed.name;
    }

    greet(): string {
        return "Hello, " + this.name;
    }
}

function filterActive(
    users: Array<User>,
    status: Status = Status.Active,
): User[] {
    const results: User[] = [];
    for (const user of users) {
        if (status === Status.Active) {
            results.push(user);
        } else if (status === Status.Pending) {
            continue;
        }
    }
    if (results.length === 0) {
        throw new Error("no active users");
    }
    return results;
}

function getName(user: User): string {
    return user.greet();
}

const transform = (x: number): number => {
    return x * 2;
};
"""


def _parse_sample() -> tuple[TypeScriptParser, CodeGraph]:
    parser = TypeScriptParser()
    with tempfile.NamedTemporaryFile(
        suffix=".ts",
        mode="w",
        delete=False,
    ) as f:
        f.write(SAMPLE_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return parser, graph


class TestTypeScriptParserNodes:
    def test_finds_module(self) -> None:
        _, graph = _parse_sample()
        modules = [n for n in graph.nodes.values() if n.kind == NodeKind.MODULE]
        assert len(modules) == 1

    def test_finds_classes(self) -> None:
        _, graph = _parse_sample()
        classes = [n for n in graph.nodes.values() if n.kind == NodeKind.CLASS]
        names = {c.name for c in classes}
        assert "Entity" in names
        assert "User" in names

    def test_finds_interface(self) -> None:
        _, graph = _parse_sample()
        ifaces = [n for n in graph.nodes.values() if n.kind == NodeKind.INTERFACE]
        names = {i.name for i in ifaces}
        assert "Serializable" in names

    def test_finds_enum(self) -> None:
        _, graph = _parse_sample()
        enums = [n for n in graph.nodes.values() if n.kind == NodeKind.ENUM]
        names = {e.name for e in enums}
        assert "Status" in names

    def test_finds_functions(self) -> None:
        _, graph = _parse_sample()
        funcs = [n for n in graph.nodes.values() if n.kind == NodeKind.FUNCTION]
        names = {f.name for f in funcs}
        assert "filterActive" in names
        assert "getName" in names

    def test_finds_arrow_function(self) -> None:
        _, graph = _parse_sample()
        funcs = [n for n in graph.nodes.values() if n.kind == NodeKind.FUNCTION]
        names = {f.name for f in funcs}
        assert "transform" in names

    def test_finds_methods(self) -> None:
        _, graph = _parse_sample()
        methods = [n for n in graph.nodes.values() if n.kind == NodeKind.METHOD]
        names = {m.name for m in methods}
        assert "serialize" in names
        assert "deserialize" in names
        assert "getId" in names

    def test_class_jsdoc(self) -> None:
        _, graph = _parse_sample()
        entity = next(n for n in graph.nodes.values() if n.name == "Entity")
        assert entity.docstring is not None
        assert "entity" in entity.docstring.lower()


class TestTypeScriptParserParameters:
    def test_typed_parameter(self) -> None:
        _, graph = _parse_sample()
        get_name = next(n for n in graph.nodes.values() if n.name == "getName")
        assert len(get_name.parameters) == 1
        assert get_name.parameters[0].name == "user"
        assert get_name.parameters[0].type_ref is not None
        assert get_name.parameters[0].type_ref.name == "User"

    def test_return_type(self) -> None:
        _, graph = _parse_sample()
        get_name = next(n for n in graph.nodes.values() if n.name == "getName")
        assert get_name.return_type is not None
        assert get_name.return_type.name == "string"

    def test_default_parameter(self) -> None:
        _, graph = _parse_sample()
        filter_fn = next(n for n in graph.nodes.values() if n.name == "filterActive")
        params = {p.name: p for p in filter_fn.parameters}
        assert "users" in params
        assert "status" in params


class TestTypeScriptParserComplexity:
    def test_simple_method_complexity(self) -> None:
        _, graph = _parse_sample()
        get_id = next(n for n in graph.nodes.values() if n.name == "getId")
        assert get_id.cyclomatic_complexity == 1

    def test_branching_function_complexity(self) -> None:
        _, graph = _parse_sample()
        filter_fn = next(n for n in graph.nodes.values() if n.name == "filterActive")
        assert filter_fn.cyclomatic_complexity is not None
        assert filter_fn.cyclomatic_complexity >= 4

    def test_branches_tracked(self) -> None:
        _, graph = _parse_sample()
        filter_fn = next(n for n in graph.nodes.values() if n.name == "filterActive")
        assert len(filter_fn.branches) > 0


class TestTypeScriptParserEdges:
    def test_contains_edges(self) -> None:
        _, graph = _parse_sample()
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        assert len(contains) > 0

    def test_inherits_edge(self) -> None:
        _, graph = _parse_sample()
        inherits = [e for e in graph.edges if e.kind == EdgeKind.INHERITS]
        assert len(inherits) >= 1
        targets = {e.target_id for e in inherits}
        has_entity = any("Entity" in t for t in targets)
        assert has_entity

    def test_implements_edge(self) -> None:
        _, graph = _parse_sample()
        implements = [e for e in graph.edges if e.kind == EdgeKind.IMPLEMENTS]
        assert len(implements) >= 1
        targets = {e.target_id for e in implements}
        has_serializable = any("Serializable" in t for t in targets)
        assert has_serializable

    def test_call_edges(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert len(calls) > 0

    def test_throw_exception_type(self) -> None:
        _, graph = _parse_sample()
        filter_fn = next(n for n in graph.nodes.values() if n.name == "filterActive")
        assert len(filter_fn.exception_types) == 1
        assert filter_fn.exception_types[0].name == "Error"

    def test_edge_confidence(self) -> None:
        _, graph = _parse_sample()
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        certain = [e for e in calls if e.confidence == EdgeConfidence.CERTAIN]
        inferred = [e for e in calls if e.confidence == EdgeConfidence.INFERRED]
        assert len(certain) > 0 or len(inferred) > 0


class TestTypeScriptParserDependencies:
    def test_imports_tracked(self) -> None:
        _, graph = _parse_sample()
        assert "rxjs" in graph.dependencies


EXTRA_TS_CODE = """\
export function exported(): void {
    console.log("hello");
}

export class ExportedClass {
    run(): void {}
}

interface Orderable<T> {
    compare(other: T): number;
}

class Sortable implements Orderable<Sortable> {
    compare(other: Sortable): number {
        return 0;
    }
}

function withOptional(x?: number, y?: string): void {}

"""

ASSIGN_TS_CODE = """\
var handler: any;
handler = function(): number { return 1; };
"""


def _parse_extra() -> CodeGraph:
    parser = TypeScriptParser()
    with tempfile.NamedTemporaryFile(
        suffix=".ts",
        mode="w",
        delete=False,
    ) as f:
        f.write(EXTRA_TS_CODE)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return graph


def _parse_ts_snippet(code: str) -> CodeGraph:
    parser = TypeScriptParser()
    with tempfile.NamedTemporaryFile(
        suffix=".ts",
        mode="w",
        delete=False,
    ) as f:
        f.write(code)
        f.flush()
        graph = parser.parse_file(f.name)
    os.unlink(f.name)
    return graph


class TestTypeScriptExtraFeatures:
    def test_export_function_extracted(self) -> None:
        graph = _parse_extra()
        names = {n.name for n in graph.nodes.values()}
        assert "exported" in names

    def test_export_class_extracted(self) -> None:
        graph = _parse_extra()
        names = {n.name for n in graph.nodes.values()}
        assert "ExportedClass" in names

    def test_implements_generic_interface(self) -> None:
        graph = _parse_extra()
        impl_edges = [e for e in graph.edges if e.kind == EdgeKind.IMPLEMENTS]
        targets = {e.target_id for e in impl_edges}
        assert any("Orderable" in t for t in targets)

    def test_generic_parameters_extracted(self) -> None:
        graph = _parse_ts_snippet(
            "interface Box<T extends Entity> {}\n"
            "function identity<U>(value: U): U { return value; }\n",
        )
        box = next(n for n in graph.nodes.values() if n.name == "Box")
        identity = next(n for n in graph.nodes.values() if n.name == "identity")

        assert box.type_parameters[0].name == "T"
        assert box.type_parameters[0].constraints[0].name == "Entity"
        assert identity.type_parameters[0].name == "U"

    def test_optional_parameter(self) -> None:
        graph = _parse_extra()
        fn = next(n for n in graph.nodes.values() if n.name == "withOptional")
        assert len(fn.parameters) == 2
        assert fn.parameters[0].name == "x"
        assert fn.parameters[1].name == "y"

    def test_expression_assignment_function(self) -> None:
        graph = _parse_ts_snippet(ASSIGN_TS_CODE)
        names = {n.name for n in graph.nodes.values()}
        assert "handler" in names


class TestTypeScriptParseDirectory:
    def test_parses_multiple_files(self) -> None:
        parser = TypeScriptParser()
        code_a = "function fromA(): void {}\n"
        code_b = "function fromB(): void {}\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, code in [("a.ts", code_a), ("b.ts", code_b)]:
                path = os.path.join(tmpdir, name)
                with open(path, "w") as f:
                    f.write(code)
            graph = parser.parse_directory(tmpdir)
        assert graph.language == "typescript"
        assert graph.root_path == tmpdir
        names = {n.name for n in graph.nodes.values()}
        assert "fromA" in names
        assert "fromB" in names

    def test_ignores_wrong_extensions(self) -> None:
        parser = TypeScriptParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "skip.txt")
            with open(path, "w") as f:
                f.write("not source code")
            graph = parser.parse_directory(tmpdir)
        assert len(graph.nodes) == 0
