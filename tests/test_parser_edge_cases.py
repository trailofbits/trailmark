"""Tests for Python parser edge cases and uncovered paths."""

from __future__ import annotations

import os
import tempfile

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers.python.parser import PythonParser


def _parse_code(code: str) -> tuple[str, CodeGraph]:
    """Write code to a temp file, parse it, return (path, graph)."""
    parser = PythonParser()
    with tempfile.NamedTemporaryFile(
        suffix=".py",
        mode="w",
        delete=False,
    ) as f:
        f.write(code)
        f.flush()
        path = f.name
    graph = parser.parse_file(path)
    os.unlink(path)
    return path, graph


class TestLanguageProperty:
    def test_language_returns_python(self) -> None:
        parser = PythonParser()
        assert parser.language == "python"


class TestDecoratedMethods:
    def test_decorated_method_in_class(self) -> None:
        code = "class Svc:\n    @staticmethod\n    def create():\n        pass\n"
        _, graph = _parse_code(code)
        methods = [n for n in graph.nodes.values() if n.kind == NodeKind.METHOD]
        assert any(m.name == "create" for m in methods)

    def test_decorated_toplevel_function(self) -> None:
        code = "def decorator(f): return f\n\n@decorator\ndef f():\n    pass\n"
        _, graph = _parse_code(code)
        funcs = [n for n in graph.nodes.values() if n.kind == NodeKind.FUNCTION]
        names = {f.name for f in funcs}
        assert "f" in names
        assert "decorator" in names


class TestDefaultParameterWithoutType:
    def test_plain_default_param(self) -> None:
        code = "def f(x=5):\n    return x\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "f")
        assert len(func.parameters) == 1
        assert func.parameters[0].name == "x"
        assert func.parameters[0].default == "5"
        assert func.parameters[0].type_ref is None

    def test_string_default(self) -> None:
        code = "def f(x='hello'):\n    return x\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "f")
        assert func.parameters[0].name == "x"
        assert func.parameters[0].default == "'hello'"

    def test_none_default(self) -> None:
        code = "def f(x=None):\n    return x\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "f")
        assert func.parameters[0].name == "x"
        assert func.parameters[0].default == "None"


class TestTypedDefaultParameter:
    def test_typed_default(self) -> None:
        code = "def g(x: int = 10):\n    return x\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "g")
        assert len(func.parameters) == 1
        p = func.parameters[0]
        assert p.name == "x"
        assert p.type_ref is not None
        assert p.type_ref.name == "int"
        assert p.default == "10"

    def test_typed_default_str(self) -> None:
        code = "def g(name: str = 'default'):\n    return name\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "g")
        p = func.parameters[0]
        assert p.name == "name"
        assert p.type_ref is not None
        assert p.type_ref.name == "str"
        assert p.default == "'default'"

    def test_typed_default_bool(self) -> None:
        code = "def g(flag: bool = True):\n    return flag\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "g")
        p = func.parameters[0]
        assert p.name == "flag"
        assert p.type_ref is not None
        assert p.type_ref.name == "bool"
        assert p.default == "True"


class TestUnionTypeAnnotation:
    def test_union_type_param(self) -> None:
        code = "def h(x: int | str):\n    pass\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "h")
        assert len(func.parameters) == 1
        assert func.parameters[0].type_ref is not None
        assert "int" in func.parameters[0].type_ref.name
        assert "str" in func.parameters[0].type_ref.name


class TestRaiseBareIdentifier:
    def test_raise_identifier(self) -> None:
        code = "def fail():\n    err = ValueError('bad')\n    raise err\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "fail")
        assert len(func.exception_types) == 1
        assert func.exception_types[0].name == "err"


class TestAttributeCallEdge:
    def test_attribute_call_creates_edge(self) -> None:
        code = "def run():\n    os.path.join('a', 'b')\n"
        _, graph = _parse_code(code)
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert len(calls) >= 1
        inferred = [e for e in calls if e.confidence == EdgeConfidence.INFERRED]
        assert len(inferred) >= 1


class TestSelfMethodCall:
    def test_self_call_is_certain(self) -> None:
        code = "class C:\n    def a(self):\n        self.b()\n    def b(self):\n        pass\n"
        _, graph = _parse_code(code)
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        self_calls = [e for e in calls if e.confidence == EdgeConfidence.CERTAIN]
        assert len(self_calls) >= 1
        # Verify self.b() resolves to C.b
        self_b = [e for e in self_calls if e.target_id.endswith(".b")]
        assert len(self_b) == 1

    def test_self_call_target_resolution(self) -> None:
        """self.method() should resolve to ClassName.method."""
        code = (
            "class MyClass:\n"
            "    def caller(self):\n"
            "        self.callee()\n"
            "    def callee(self):\n"
            "        pass\n"
        )
        _, graph = _parse_code(code)
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert len(calls) == 1
        assert calls[0].target_id.endswith(".callee")
        assert "MyClass" in calls[0].target_id


class TestBooleanOperatorBranch:
    def test_boolean_operator_branch(self) -> None:
        code = "def check(a, b):\n    if a and b:\n        return True\n    return False\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "check")
        conditions = [b.condition for b in func.branches]
        assert "boolean_operator" in conditions

    def test_or_operator_branch(self) -> None:
        code = "def check(a, b):\n    if a or b:\n        return True\n    return False\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "check")
        conditions = [b.condition for b in func.branches]
        assert "boolean_operator" in conditions


class TestNoReturnType:
    def test_function_without_return_type(self) -> None:
        code = "def noop():\n    pass\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "noop")
        assert func.return_type is None


class TestDocstrings:
    def test_triple_double_docstring(self) -> None:
        code = 'def f():\n    """A triple double."""\n    pass\n'
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "f")
        assert func.docstring == "A triple double."

    def test_triple_single_docstring(self) -> None:
        code = "def f():\n    '''A triple single.'''\n    pass\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "f")
        assert func.docstring == "A triple single."

    def test_single_quote_docstring(self) -> None:
        code = "def f():\n    'A short doc.'\n    pass\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "f")
        assert func.docstring == "A short doc."

    def test_double_quote_docstring(self) -> None:
        code = 'def f():\n    "A double quote."\n    pass\n'
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "f")
        assert func.docstring == "A double quote."

    def test_multiline_docstring_stripped(self) -> None:
        code = 'def f():\n    """\n    Multi\n    line\n    """\n    pass\n'
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "f")
        assert func.docstring is not None
        assert "Multi" in func.docstring
        assert "line" in func.docstring

    def test_no_docstring(self) -> None:
        code = "def f():\n    x = 1\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "f")
        assert func.docstring is None

    def test_class_docstring(self) -> None:
        code = 'class C:\n    """Class doc."""\n    pass\n'
        _, graph = _parse_code(code)
        cls = next(n for n in graph.nodes.values() if n.kind == NodeKind.CLASS)
        assert cls.docstring == "Class doc."


class TestLocationFields:
    def test_location_line_numbers(self) -> None:
        code = "def f():\n    pass\n"
        path, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "f")
        loc = func.location
        assert loc.file_path == path
        assert loc.start_line == 1
        assert loc.end_line == 2
        assert loc.start_col is not None
        assert loc.start_col == 0
        assert loc.end_col is not None

    def test_location_multiline_function(self) -> None:
        code = "x = 1\ndef f():\n    pass\n"
        path, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "f")
        loc = func.location
        assert loc.start_line == 2
        assert loc.end_line == 3

    def test_branch_locations(self) -> None:
        code = "def f(x):\n    if x > 0:\n        pass\n"
        path, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "f")
        assert len(func.branches) == 1
        branch_loc = func.branches[0].location
        assert branch_loc.file_path == path
        assert branch_loc.start_line == 2
        assert branch_loc.start_col is not None


class TestTypeCapture:
    def test_return_type_captured(self) -> None:
        code = "def f(x) -> int:\n    return x\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "f")
        assert func.return_type is not None
        assert func.return_type.name == "int"

    def test_typed_parameters_captured(self) -> None:
        code = "def f(x: int, y: str) -> bool:\n    return True\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "f")
        assert func.return_type is not None
        assert func.return_type.name == "bool"
        assert len(func.parameters) == 2

    def test_untyped_function_has_no_return_type(self) -> None:
        code = "def f(x):\n    return x\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "f")
        assert func.return_type is None


class TestGenericReturnType:
    def test_generic_return(self) -> None:
        code = "def f() -> list[int]:\n    return []\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "f")
        assert func.return_type is not None
        assert func.return_type.name == "list"
        assert len(func.return_type.generic_args) == 1
        assert func.return_type.generic_args[0].name == "int"


class TestNoneReturnType:
    def test_none_return(self) -> None:
        code = "def f() -> None:\n    pass\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "f")
        assert func.return_type is not None
        assert func.return_type.name == "None"


class TestImportParsing:
    def test_import_statement(self) -> None:
        code = "import os\n"
        _, graph = _parse_code(code)
        assert "os" in graph.dependencies

    def test_from_import_statement(self) -> None:
        code = "from pathlib import Path\n"
        _, graph = _parse_code(code)
        assert "pathlib" in graph.dependencies

    def test_dotted_import(self) -> None:
        code = "import os.path\n"
        _, graph = _parse_code(code)
        assert "os" in graph.dependencies

    def test_no_duplicate_imports(self) -> None:
        code = "import os\nimport os.path\n"
        _, graph = _parse_code(code)
        assert graph.dependencies.count("os") == 1

    def test_import_count_exact(self) -> None:
        code = "import os\nfrom pathlib import Path\n"
        _, graph = _parse_code(code)
        assert len(graph.dependencies) == 2


class TestInheritance:
    def test_inherits_edge(self) -> None:
        code = "class Base:\n    pass\n\nclass Child(Base):\n    pass\n"
        _, graph = _parse_code(code)
        inherits = [e for e in graph.edges if e.kind == EdgeKind.INHERITS]
        assert len(inherits) == 1
        assert inherits[0].source_id.endswith(":Child")
        assert inherits[0].target_id.endswith(":Base")
        assert inherits[0].confidence == EdgeConfidence.INFERRED

    def test_multiple_bases(self) -> None:
        code = "class A:\n    pass\nclass B:\n    pass\nclass C(A, B):\n    pass\n"
        _, graph = _parse_code(code)
        inherits = [e for e in graph.edges if e.kind == EdgeKind.INHERITS]
        assert len(inherits) == 2
        targets = {e.target_id for e in inherits}
        assert any(t.endswith(":A") for t in targets)
        assert any(t.endswith(":B") for t in targets)


class TestCallEdgeConfidence:
    def test_plain_call_is_certain(self) -> None:
        """Non-dotted calls should have CERTAIN confidence."""
        code = "def a():\n    pass\ndef b():\n    a()\n"
        _, graph = _parse_code(code)
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert len(calls) == 1
        assert calls[0].confidence == EdgeConfidence.CERTAIN

    def test_dotted_call_is_inferred(self) -> None:
        """Dotted calls (not self.) should be INFERRED."""
        code = "def f():\n    os.path.join('a', 'b')\n"
        _, graph = _parse_code(code)
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert len(calls) == 1
        assert calls[0].confidence == EdgeConfidence.INFERRED


class TestCallTargetResolution:
    def test_plain_call_gets_module_prefix(self) -> None:
        """Plain calls should resolve to module_id:name."""
        code = "def helper():\n    pass\ndef f():\n    helper()\n"
        _, graph = _parse_code(code)
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert len(calls) == 1
        assert ":" in calls[0].target_id
        assert calls[0].target_id.endswith(":helper")

    def test_dotted_call_kept_as_is(self) -> None:
        """Dotted calls (not self.) should keep original name."""
        code = "def f():\n    os.path.join('a')\n"
        _, graph = _parse_code(code)
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert len(calls) == 1
        assert calls[0].target_id == "os.path.join"


class TestConditionText:
    def test_if_condition_extracted(self) -> None:
        code = "def f(x):\n    if x > 0:\n        pass\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "f")
        assert len(func.branches) == 1
        assert func.branches[0].condition == "x > 0"

    def test_for_loop_condition(self) -> None:
        code = "def f(items):\n    for i in items:\n        pass\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "f")
        assert len(func.branches) == 1
        # For loops don't have a "condition" field, uses node type
        assert func.branches[0].condition == "for_statement"

    def test_while_condition(self) -> None:
        code = "def f():\n    while True:\n        break\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "f")
        assert len(func.branches) == 1
        assert func.branches[0].condition == "True"

    def test_elif_condition(self) -> None:
        code = "def f(x):\n    if x > 0:\n        pass\n    elif x < 0:\n        pass\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "f")
        assert len(func.branches) == 2
        conditions = [b.condition for b in func.branches]
        assert "x > 0" in conditions
        assert "x < 0" in conditions


class TestModuleIdFromPath:
    def test_module_node_created(self) -> None:
        code = "x = 1\n"
        _, graph = _parse_code(code)
        modules = [n for n in graph.nodes.values() if n.kind == NodeKind.MODULE]
        assert len(modules) == 1

    def test_module_id_is_stem(self) -> None:
        code = "x = 1\n"
        _, graph = _parse_code(code)
        modules = [n for n in graph.nodes.values() if n.kind == NodeKind.MODULE]
        # Module ID should be the file stem (without extension)
        assert modules[0].id == modules[0].name


class TestContainsEdges:
    def test_module_contains_class(self) -> None:
        code = "class C:\n    pass\n"
        _, graph = _parse_code(code)
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        # module -> C
        assert len(contains) == 1

    def test_module_contains_function(self) -> None:
        code = "def f():\n    pass\n"
        _, graph = _parse_code(code)
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        assert len(contains) == 1

    def test_class_contains_method(self) -> None:
        code = "class C:\n    def m(self):\n        pass\n"
        _, graph = _parse_code(code)
        contains = [e for e in graph.edges if e.kind == EdgeKind.CONTAINS]
        # module -> C, C -> m
        assert len(contains) == 2


class TestExceptionFromCall:
    def test_raise_call_exception_type(self) -> None:
        """raise ValueError('x') should extract ValueError."""
        code = "def f():\n    raise ValueError('bad')\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "f")
        assert len(func.exception_types) == 1
        assert func.exception_types[0].name == "ValueError"

    def test_raise_no_arg(self) -> None:
        """Bare raise should not crash."""
        code = "def f():\n    try:\n        pass\n    except:\n        raise\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "f")
        # Bare raise has no exception type
        assert len(func.exception_types) == 0


class TestClsParameter:
    def test_cls_excluded(self) -> None:
        """cls parameter should be excluded like self."""
        code = "class C:\n    @classmethod\n    def create(cls, x):\n        pass\n"
        _, graph = _parse_code(code)
        method = next(n for n in graph.nodes.values() if n.name == "create")
        param_names = [p.name for p in method.parameters]
        assert "cls" not in param_names
        assert "x" in param_names


class TestCallEdgeLocation:
    def test_call_edge_has_location(self) -> None:
        """Call edges should have location information."""
        code = "def a():\n    pass\ndef b():\n    a()\n"
        path, graph = _parse_code(code)
        calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
        assert len(calls) == 1
        assert calls[0].location is not None
        assert calls[0].location.file_path == path
        assert calls[0].location.start_line > 0


class TestNodeIdFormat:
    def test_function_id_has_module_prefix(self) -> None:
        code = "def my_func():\n    pass\n"
        _, graph = _parse_code(code)
        func = next(n for n in graph.nodes.values() if n.name == "my_func")
        assert ":" in func.id
        assert func.id.endswith(":my_func")

    def test_method_id_has_dot_separator(self) -> None:
        code = "class C:\n    def my_method(self):\n        pass\n"
        _, graph = _parse_code(code)
        method = next(n for n in graph.nodes.values() if n.name == "my_method")
        assert ".my_method" in method.id
        # Should also contain class name
        assert "C" in method.id

    def test_class_id_has_module_prefix(self) -> None:
        code = "class MyClass:\n    pass\n"
        _, graph = _parse_code(code)
        cls = next(n for n in graph.nodes.values() if n.kind == NodeKind.CLASS)
        assert ":" in cls.id
        assert cls.id.endswith(":MyClass")
