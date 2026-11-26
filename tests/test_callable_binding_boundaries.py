"""Callable discovery stays conservative across incomplete and ambiguous bindings."""

from pathlib import Path

import pytest

from tests.test_callable_references import _bindings, _discover, _evidence, _references, _write
from trailmark.analysis._callable_references import discover_callable_references
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parse import parse_directory, parse_roots
from trailmark.source_policy import SourcePolicy, SourceRoot


@pytest.mark.mutation_fast
def test_function_attributes_do_not_resolve_to_nested_declarations(tmp_path: Path) -> None:
    graph = _discover(tmp_path, {"app.py": "def owner():\n    def h(): pass\nconsume(owner.h)\n"})
    assert "app:owner.h" in graph.nodes
    assert _references(graph) == set()
    assert _evidence(graph) == []


@pytest.mark.mutation_fast
def test_ambiguous_module_is_not_resolved_by_its_only_known_member(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "a/lib.py": "def h(): pass\n",
            "b/lib.py": "unrelated = 1\n",
            "app.py": "import lib\nconsume(lib.h)\n",
        },
    )
    assert _references(graph) == set()
    assert _bindings(graph, "app") == {("a.lib:h", "ambiguous")}


@pytest.mark.mutation_fast
def test_imports_resolve_with_explicit_source_root_prefixes(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "src/lib.py": "def h(): pass\n",
            "src/app.py": "import lib\nconsume(lib.h)\n",
            "dependency/dep.py": "value = 1\n",
        },
    )
    graph = parse_roots(
        SourcePolicy(
            (
                SourceRoot(str(tmp_path / "src"), "selected"),
                SourceRoot(str(tmp_path / "dependency"), "dependency"),
            )
        ),
        "python",
    )
    discover_callable_references(graph)
    assert _references(graph) == {("selected::app", "selected::lib:h")}


@pytest.mark.mutation_fast
def test_global_reference_bypasses_an_enclosing_local(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "app.py": (
                "def h(): pass\ndef outer():\n    def h(): pass\n"
                "    def inner():\n        global h\n        consume(h)\n"
            )
        },
    )
    assert _references(graph) == {("app:outer.inner", "app:h")}


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "nested",
    [
        "    class Inner:\n        h = 1\n"
        "        def swap(self):\n            nonlocal h\n            h = None\n",
        "    def middle():\n        nonlocal h\n"
        "        def swap():\n            nonlocal h\n            h = None\n",
        "    def middle():\n        def swap():\n            nonlocal h\n            h = None\n",
    ],
)
def test_nonlocal_writes_find_the_enclosing_function_binding(tmp_path: Path, nested: str) -> None:
    graph = _discover(
        tmp_path, {"app.py": f"def outer():\n    def h(): pass\n{nested}    consume(h)\n"}
    )
    assert _references(graph) == set()
    assert _bindings(graph, "app:outer") == {("app:outer.h", "ambiguous")}


@pytest.mark.mutation_fast
def test_multiple_imports_select_the_requested_binding(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "a.py": "def h(): pass\n",
            "b.py": "def h(): pass\n",
            "app.py": (
                "import a, b\nimport a as aa, b as bb\nimport a, b as mixed\n"
                "from a import missing, h as ah\n"
                "consume(b.h, bb.h, mixed.h, ah)\n"
            ),
        },
    )
    assert _references(graph) == {("app", "a:h"), ("app", "b:h")}
    assert {(fact.symbol, fact.node_id) for fact in _evidence(graph)} == {
        ("b.h", "b:h"),
        ("bb.h", "b:h"),
        ("mixed.h", "b:h"),
        ("ah", "a:h"),
    }


@pytest.mark.mutation_fast
def test_relative_import_can_reach_the_selected_root(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "__init__.py": "",
            "lib.py": "def h(): pass\n",
            "pkg/__init__.py": "from ..lib import h\nconsume(h)\n",
            "pkg/app.py": "from ..lib import h\nconsume(h)\n",
        },
    )
    assert _references(graph) == {("pkg", "lib:h"), ("pkg.app", "lib:h")}


@pytest.mark.mutation_fast
@pytest.mark.parametrize(("links", "resolved"), [(7, True), (8, True), (9, False)])
def test_alias_expansion_stops_beyond_its_budget(
    tmp_path: Path, links: int, resolved: bool
) -> None:
    aliases = [f"a{i} = {'h' if i == 1 else f'a{i - 1}'}\n" for i in range(1, links + 1)]
    graph = _discover(tmp_path, {"app.py": f"def h(): pass\n{''.join(aliases)}consume(a{links})\n"})
    facts = [f for f in _evidence(graph) if dict(f.details)["consumer"] == "consume"]
    assert [(fact.node_id, fact.binding) for fact in facts] == (
        [("app:h", "resolved")] if resolved else []
    )


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    ("links", "attribute", "resolved"),
    [(6, True, True), (7, True, False), (7, False, True), (8, False, False)],
)
def test_import_expansion_counts_module_and_attribute_steps(
    tmp_path: Path, links: int, attribute: bool, resolved: bool
) -> None:
    files = {"lib.py": "def h(): pass\n"}
    for index in range(1, links + 1):
        previous = "lib" if index == 1 else f"m{index - 1}"
        files[f"m{index}.py"] = f"from {previous} import h\n"
    files["app.py"] = (
        f"import m{links}\nconsume(m{links}.h)\n"
        if attribute
        else f"from m{links} import h\nconsume(h)\n"
    )
    graph = _discover(tmp_path, files)
    assert _bindings(graph, "app") == ({("lib:h", "resolved")} if resolved else set())


@pytest.mark.mutation_fast
def test_import_outside_the_inspected_package_stays_opaque(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "__init__.py": "",
            "outside.py": "def h(): pass\n",
            "pkg/__init__.py": "",
            "pkg/app.py": "from ..outside import h\nconsume(h)\n",
        },
    )
    graph = parse_directory(str(tmp_path / "pkg"), "python")
    discover_callable_references(graph)
    assert _references(graph) == set()
    assert _evidence(graph) == []


@pytest.mark.mutation_fast
def test_tuple_assignment_does_not_guess_callable_unpacking(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path, {"app.py": "def h(): pass\nfirst, second = (h, None)\nconsume(first)\n"}
    )
    assert not any(fact.symbol == "first" for fact in _evidence(graph))
    (fact,) = _evidence(graph)
    assert (fact.symbol, fact.node_id) == ("h", "app:h")


@pytest.mark.mutation_fast
@pytest.mark.parametrize("missing", ["app.py", "lib.py"])
def test_discovery_tolerates_source_removed_after_parsing(tmp_path: Path, missing: str) -> None:
    _write(
        tmp_path,
        {"lib.py": "def h(): pass\n", "app.py": "from lib import h\nconsume(h)\n"},
    )
    graph = parse_directory(str(tmp_path), "python")
    (tmp_path / missing).unlink()
    discover_callable_references(graph)
    assert _references(graph) == set()
    assert _evidence(graph) == []


@pytest.mark.mutation_fast
def test_partial_graph_retains_unresolved_function_evidence(tmp_path: Path) -> None:
    _write(tmp_path, {"app.py": "def h(): pass\nclass C: pass\nconsume(h, C)\n"})
    parsed = parse_directory(str(tmp_path), "python")
    graph = CodeGraph(
        nodes={key: unit for key, unit in parsed.nodes.items() if unit.kind == NodeKind.MODULE},
        language=parsed.language,
        root_path=parsed.root_path,
    )
    discover_callable_references(graph)
    assert _references(graph) == set()
    (fact,) = _evidence(graph)
    assert (fact.symbol, fact.node_id, fact.binding) == ("h", None, "unresolved")


@pytest.mark.mutation_fast
def test_php_boolean_named_constant_keeps_its_callable_array_context(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "app.php": (
                "<?php\nclass C { public static function h() {}\n"
                "public const FALSE = [self::class, 'h']; }\n"
            )
        },
        "php",
    )
    assert _references(graph) == {("app:C", "app:C.h")}
    (fact,) = _evidence(graph)
    assert (fact.node_id, fact.binding) == ("app:C.h", "resolved")
    assert dict(fact.details)["consumer_kind"] == "const_element"
    assert dict(fact.details)["form"] == "array"


@pytest.mark.mutation_fast
def test_php_string_references_keep_value_paths_and_skip_keys_and_interpolation(
    tmp_path: Path,
) -> None:
    graph = _discover(
        tmp_path,
        {
            "app.php": (
                "<?php\nfunction h() {}\nconsume(\"$h\", \"h\\n\", ['h' => 1], ['key' => 'h']);\n"
            )
        },
        "php",
    )
    (fact,) = _evidence(graph)
    assert (fact.symbol, fact.node_id) == ("'h'", "app:h")
    assert dict(fact.details)["path"] == "args[3]['key']"


@pytest.mark.mutation_fast
def test_php_non_assignment_consumer_keeps_its_syntax_kind(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path, {"app.php": "<?php\nfunction h() {}\n$values = [] + ['h'];\n"}, "php"
    )
    (fact,) = _evidence(graph)
    assert dict(fact.details)["consumer_kind"] == "binary_expression"
    assert dict(fact.details)["path"] == "[0]"


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "value",
    [
        "[C::class, 1]",
        "[C::class, $method]",
        "[C::class, 'h' => 1]",
        "[C::class, 'h', 'extra']",
        "[$obj, 'unrelated']",
    ],
)
def test_php_data_arrays_do_not_become_callable_pairs(tmp_path: Path, value: str) -> None:
    graph = _discover(
        tmp_path,
        {"app.php": f"<?php\nclass C {{ public static function h() {{}} }}\nconsume({value});\n"},
        "php",
    )
    assert _references(graph) == set()
    assert _evidence(graph) == []


@pytest.mark.mutation_fast
@pytest.mark.parametrize("expression", ["$fn(...)", "C::missing(...)"])
def test_php_unknown_acquisition_retains_evidence_without_a_target(
    tmp_path: Path, expression: str
) -> None:
    graph = _discover(
        tmp_path, {"app.php": f"<?php\nclass C {{}}\n$callback = {expression};\n"}, "php"
    )
    assert _references(graph) == set()
    (fact,) = _evidence(graph)
    assert (fact.symbol, fact.node_id, fact.binding) == (expression, None, "unresolved")


@pytest.mark.mutation_fast
@pytest.mark.parametrize(("decorator", "resolved"), [("staticmethod", False), ("wrapped", True)])
def test_python_static_method_parameter_is_not_an_implicit_receiver(
    tmp_path: Path, decorator: str, resolved: bool
) -> None:
    graph = _discover(
        tmp_path,
        {
            "app.py": (
                "class C:\n    def h(self): pass\n"
                f"    @{decorator}\n    def setup(self): consume(self.h)\n"
            )
        },
    )
    assert _references(graph) == ({("app:C.setup", "app:C.h")} if resolved else set())


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    ("expression", "dispatch", "target"),
    [
        ("C::h(...)", False, "app:C.h"),
        ("self::h(...)", False, "app:C.h"),
        ("static::h(...)", True, "app:C.h"),
        ("['C', 'h']", False, "app:C.h"),
        ("[C::class, 'h']", False, "app:C.h"),
        ("[self::class, 'h']", False, "app:C.h"),
        ("[static::class, 'h']", True, "app:C.h"),
        ("[$obj, 'h']", True, None),
        ("$this->h(...)", True, "app:C.h"),
    ],
)
def test_php_receiver_dispatch_is_limited_to_dynamic_receivers(
    tmp_path: Path, expression: str, dispatch: bool, target: str | None
) -> None:
    graph = _discover(
        tmp_path,
        {
            "app.php": (
                "<?php\nclass C { public static function h() {}\n"
                f"public function setup($obj) {{ consume({expression}); }} }}\n"
            )
        },
        "php",
    )
    (fact,) = _evidence(graph)
    assert fact.node_id == target
    assert ("receiver_dispatch" in fact.unresolved) is dispatch


@pytest.mark.mutation_fast
def test_php_namespace_relative_first_class_callables_resolve_locally(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "app.php": (
                "<?php\nnamespace N;\nfunction h() {}\n"
                "class C { public static function run() {} }\n"
                "consume(namespace\\h(...), namespace\\C::run(...));\n"
            )
        },
        "php",
    )
    assert _references(graph) == {("app", "app:N.h"), ("app", "app:N.C.run")}
    assert all(fact.binding == "resolved" for fact in _evidence(graph))


@pytest.mark.mutation_fast
@pytest.mark.parametrize("literal", [r"Ns\nh", r"Ns\th", r"Ns\rh", r"Ns\$h"])
def test_php_escaped_data_is_not_decoded_as_a_namespace_separator(
    tmp_path: Path, literal: str
) -> None:
    graph = _discover(
        tmp_path,
        {
            "app.php": (
                f'<?php namespace Ns {{ function h() {{}} }} namespace {{ consume("{literal}"); }}'
            )
        },
        "php",
    )
    assert _references(graph) == set()
    assert _evidence(graph) == []


@pytest.mark.mutation_fast
def test_php_callable_pair_does_not_also_reference_its_component_strings(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "app.php": (
                "<?php function C() {} function h() {} "
                "class C { public static function h() {} } consume(['C', 'h']);"
            )
        },
        "php",
    )
    assert _references(graph) == {("app", "app:C.h")}
    (fact,) = _evidence(graph)
    assert (fact.symbol, dict(fact.details)["form"]) == ("['C', 'h']", "array")


@pytest.mark.mutation_fast
def test_php_closure_receiver_without_a_lexical_class_stays_unresolved(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {"app.php": "<?php $callback = function() { return $this->h(...); };"},
        "php",
    )
    assert _references(graph) == set()
    (fact,) = _evidence(graph)
    assert (fact.symbol, fact.node_id, fact.binding) == ("$this->h(...)", None, "unresolved")
    assert "receiver_dispatch" in fact.unresolved


@pytest.mark.mutation_fast
def test_php_relative_qualified_names_keep_their_namespace_segment(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "sub.php": (
                "<?php namespace N\\Sub; function h() {} "
                "class C { public static function run() {} }"
            ),
            "app.php": (
                "<?php namespace N; function h() {} "
                "class C { public static function run() {} } "
                "consume(Sub\\h(...), Sub\\C::run(...));"
            ),
        },
        "php",
    )
    assert _references(graph) == {("app", "sub:N.Sub.h"), ("app", "sub:N.Sub.C.run")}


@pytest.mark.mutation_fast
def test_php_quoted_static_method_has_no_dynamic_receiver(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {"app.php": "<?php class C { public static function h() {} } consume('C::h');"},
        "php",
    )
    assert _references(graph) == {("app", "app:C.h")}
    (fact,) = _evidence(graph)
    assert "receiver_dispatch" not in fact.unresolved
