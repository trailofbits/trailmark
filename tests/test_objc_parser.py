"""Tests for the Objective-C parser."""

from __future__ import annotations

from pathlib import Path

from trailmark.models.graph import CodeGraph
from trailmark.parsers.objc import ObjCParser


def _parse(tmp_path: Path, body: str, name: str = "Foo.m") -> CodeGraph:
    (tmp_path / name).write_text(body)
    return ObjCParser().parse_directory(str(tmp_path))


class TestCFunctions:
    def test_main_extracted(self, tmp_path: Path) -> None:
        graph = _parse(tmp_path, "int main(int argc, char **argv) { return 0; }\n")
        assert "Foo:main" in graph.nodes
        func = graph.nodes["Foo:main"]
        assert func.kind.value == "function"
        assert [p.name for p in func.parameters] == ["argc", "argv"]

    def test_return_type_captured(self, tmp_path: Path) -> None:
        graph = _parse(tmp_path, "void hello(void) { }\n")
        func = graph.nodes["Foo:hello"]
        assert func.return_type is not None
        assert func.return_type.name == "void"


class TestClasses:
    def test_class_interface_creates_class_node(self, tmp_path: Path) -> None:
        graph = _parse(
            tmp_path,
            "@interface Thing : NSObject\n- (void)doStuff;\n@end\n",
        )
        assert "Foo:Thing" in graph.nodes
        assert graph.nodes["Foo:Thing"].kind.value == "class"

    def test_zero_arg_method_selector(self, tmp_path: Path) -> None:
        graph = _parse(
            tmp_path,
            "@interface T : NSObject\n- (void)reset;\n@end\n",
        )
        assert "Foo:T.reset" in graph.nodes
        assert graph.nodes["Foo:T.reset"].name == "reset"

    def test_multi_arg_method_selector_has_colons(self, tmp_path: Path) -> None:
        graph = _parse(
            tmp_path,
            "@interface T : NSObject\n"
            "- (BOOL)login:(NSString *)user password:(NSString *)pw;\n"
            "@end\n",
        )
        assert "Foo:T.login:password:" in graph.nodes
        method = graph.nodes["Foo:T.login:password:"]
        assert method.name == "login:password:"
        assert [p.name for p in method.parameters] == ["user", "pw"]

    def test_implementation_body_replaces_interface_stub(
        self,
        tmp_path: Path,
    ) -> None:
        code = (
            "@interface T : NSObject\n"
            "- (int)count;\n"
            "@end\n"
            "@implementation T\n"
            "- (int)count {\n"
            "    if (1) { return 1; }\n"
            "    return 0;\n"
            "}\n"
            "@end\n"
        )
        graph = _parse(tmp_path, code)
        method = graph.nodes["Foo:T.count"]
        # Body was observed; complexity > 1.
        assert method.cyclomatic_complexity is not None
        assert method.cyclomatic_complexity >= 2


class TestImports:
    def test_system_import_captured(self, tmp_path: Path) -> None:
        graph = _parse(
            tmp_path,
            "#import <Foundation/Foundation.h>\nvoid f(void) {}\n",
        )
        assert "Foundation" in graph.dependencies
