"""Callable references retain consumers without becoming calls or entrypoints."""

from __future__ import annotations

from pathlib import Path

import pytest

from trailmark.analysis._callable_references import discover_callable_references
from trailmark.analysis.entrypoints import detect_entrypoints
from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.entrypoints import EntrypointEvidence
from trailmark.models.graph import CodeGraph
from trailmark.parse import parse_directory
from trailmark.query.api import QueryEngine

_UNRESOLVED = ("consumer_semantics", "invocation_context")


def _write(root: Path, files: dict[str, str]) -> None:
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)


def _discover(root: Path, files: dict[str, str], language: str = "python") -> CodeGraph:
    _write(root, files)
    graph = parse_directory(str(root), language)
    discover_callable_references(graph)
    return graph


def _references(graph: CodeGraph) -> set[tuple[str, str]]:
    return {(e.source_id, e.target_id) for e in graph.edges if e.kind == EdgeKind.REFERENCES}


def _reference_edges(graph: CodeGraph, target: str) -> list[CodeEdge]:
    return [e for e in graph.edges if e.kind == EdgeKind.REFERENCES and e.target_id == target]


def _evidence(graph: CodeGraph, symbol: str | None = None) -> list[EntrypointEvidence]:
    return sorted(
        (f for f in graph.entrypoint_evidence.values() if symbol is None or f.symbol == symbol),
        key=lambda f: (f.location.start_line, f.location.start_col or 0, f.node_id or ""),
    )


def _referenced(graph: CodeGraph, source: str) -> set[str]:
    return {target for owner, target in _references(graph) if owner == source}


# --------------------------------------------------------------------------- Python


def test_python_argument_reference_is_not_a_call(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "app.py": (
                "def h():\n    pass\n\n"
                "def setup():\n    register('/x', h)\n\n"
                "register('/y', handler=h)\n"
            )
        },
    )
    assert _references(graph) == {("app:setup", "app:h"), ("app", "app:h")}
    assert not any(e.kind == EdgeKind.CALLS and e.target_id == "app:h" for e in graph.edges), (
        "a reference must not be recorded as a call"
    )
    inside, module_level = _evidence(graph, "h")
    assert (inside.source_id, module_level.source_id) == ("app:setup", "app")
    assert inside.basis == "reference"
    assert inside.rule == "python.callable_reference"
    assert inside.binding == "resolved"
    assert inside.unresolved == _UNRESOLVED
    assert dict(inside.details) == {
        "consumer_kind": "call",
        "consumer": "register",
        "path": "args[1]",
        "expression": "h",
        "form": "name",
    }
    assert dict(module_level.details)["path"] == "kwargs[handler]"
    edge = _reference_edges(graph, "app:h")[0]
    assert edge.confidence == EdgeConfidence.CERTAIN


def test_references_do_not_change_reachability_or_entrypoints(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "app.py": (
                "def leaf():\n    pass\n\ndef h():\n    leaf()\n\ndef setup():\n    register(h)\n"
            )
        },
    )
    graph = parse_directory(str(tmp_path), "python")
    graph.entrypoints.update(detect_entrypoints(graph, str(tmp_path)))
    before = QueryEngine.from_graph(graph)
    surface = before.attack_surface()
    callees = before.callees_of("app:setup")
    reachable = before.reachable_from("app:setup")
    entrypoints = dict(graph.entrypoints)

    discover_callable_references(graph)
    after = QueryEngine.from_graph(graph)

    assert ("app:setup", "app:h") in _references(graph)
    assert after.callees_of("app:setup") == callees
    assert after.reachable_from("app:setup") == reachable
    assert "app:h" not in {unit["id"] for unit in after.reachable_from("app:setup")}
    assert after.attack_surface() == surface
    assert graph.entrypoints == entrypoints
    assert "app:h" not in graph.entrypoints
    assert all(e.kind != EdgeKind.CALLS for e in graph.edges if e.target_id == "app:h"), (
        "discovery must not synthesize calls"
    )


@pytest.mark.parametrize("consumer", ["register", "zz_unrelated.attach", "make_thing"])
def test_python_consumer_name_does_not_matter(tmp_path: Path, consumer: str) -> None:
    graph = _discover(
        tmp_path,
        {"app.py": f"def h():\n    pass\n\ndef setup():\n    {consumer}(1, [h])\n"},
    )
    assert _references(graph) == {("app:setup", "app:h")}
    (fact,) = _evidence(graph, "h")
    assert dict(fact.details)["consumer"] == consumer
    assert dict(fact.details)["path"] == "args[1][0]"


def test_python_aliases_and_collections(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "app.py": (
                "def h():\n    pass\n\n"
                "def g():\n    pass\n\n"
                "target = h\n"
                "first = second = g\n"
                "routes = [h, {'k': g}]\n\n"
                "def setup():\n"
                "    consume(target, second)\n"
                "    consume(routes)\n"
            )
        },
    )
    assert _referenced(graph, "app:setup") == {"app:h", "app:g"}
    assert _referenced(graph, "app") == {"app:h", "app:g"}
    stored = {dict(f.details)["path"]: f for f in _evidence(graph) if f.source_id == "app"}
    assert set(stored) == {"[0]", "[1]['k']"}
    assert dict(stored["[1]['k']"].details)["consumer_kind"] == "assignment"
    assert dict(stored["[1]['k']"].details)["consumer"] == "routes"
    assert stored["[1]['k']"].node_id == "app:g"
    # A variable holding a collection is not itself a callable reference.
    assert not any(f.symbol == "routes" for f in _evidence(graph))


def test_python_imports_from_local_modules(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/handlers.py": "def h():\n    pass\n",
            "pkg/app.py": (
                "from .handlers import h as hh\n"
                "from pkg import handlers\n"
                "import pkg.handlers as hm\n"
                "import pkg.handlers\n\n"
                "def setup():\n"
                "    attach(hh, handlers.h, hm.h, pkg.handlers.h)\n"
            ),
        },
    )
    edges = _reference_edges(graph, "pkg.handlers:h")
    assert {(e.source_id, dict(e.attributes)["path"]) for e in edges} == {
        ("pkg.app:setup", "args[0]"),
        ("pkg.app:setup", "args[1]"),
        ("pkg.app:setup", "args[2]"),
        ("pkg.app:setup", "args[3]"),
    }
    assert {e.confidence for e in edges} == {EdgeConfidence.INFERRED}


def test_python_duplicate_names_bind_by_import_not_terminal_name(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "a.py": "def h():\n    pass\n",
            "b.py": "def h():\n    pass\n",
            "unbound.py": "def setup():\n    attach(h)\n",
            "imported.py": "from b import h\n\ndef setup():\n    attach(h)\n",
            "either.py": (
                "try:\n    from a import h\nexcept ImportError:\n    from b import h\n\n"
                "def setup():\n    attach(h)\n"
            ),
        },
    )
    assert _referenced(graph, "unbound:setup") == set()
    assert not any(f.source_id == "unbound:setup" for f in _evidence(graph))
    assert _referenced(graph, "imported:setup") == {"b:h"}
    assert _referenced(graph, "either:setup") == set()
    ambiguous = [f for f in _evidence(graph) if f.source_id == "either:setup"]
    assert {(f.node_id, f.binding) for f in ambiguous} == {
        ("a:h", "ambiguous"),
        ("b:h", "ambiguous"),
    }


def test_python_parameters_and_locals_shadow_globals(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "app.py": (
                "def h():\n    pass\n\n"
                "def param(h):\n    attach(h)\n\n"
                "def reassigned():\n    h = make()\n    attach(h)\n\n"
                "def later():\n    attach(h)\n    h = 1\n\n"
                "def local_import():\n    from external import h\n    attach(h)\n\n"
                "def loop(xs):\n    for h in xs:\n        attach(h)\n\n"
                "def handled():\n    with open() as h:\n        attach(h)\n\n"
                "def comprehension(xs):\n    return [attach(h) for h in xs]\n\n"
                "def lam():\n    return lambda h: attach(h)\n\n"
                "def nested():\n    def h():\n        pass\n    attach(h)\n\n"
                "def global_use():\n    attach(h)\n"
            )
        },
    )
    references = _references(graph)
    for owner in (
        "param",
        "reassigned",
        "later",
        "local_import",
        "loop",
        "handled",
        "comprehension",
        "lam",
    ):
        assert not any(source == f"app:{owner}" for source, _ in references), owner
    assert ("app:nested", "app:nested.h") in references
    assert ("app:nested", "app:h") not in references
    assert ("app:global_use", "app:h") in references
    assert {f.source_id for f in _evidence(graph)} == {"app:nested", "app:global_use"}


def test_python_conditional_rebinding_is_ambiguous(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "app.py": (
                "def h():\n    pass\n\nif FLAG:\n    h = wrap(h)\n\ndef setup():\n    attach(h)\n"
            )
        },
    )
    assert _referenced(graph, "app:setup") == set()
    (fact,) = [f for f in _evidence(graph) if f.source_id == "app:setup"]
    assert (fact.node_id, fact.binding) == ("app:h", "ambiguous")


_H = "def h():\n    pass\n\n"


def _bindings(graph: CodeGraph, source: str) -> set[tuple[str | None, str]]:
    return {(f.node_id, f.binding) for f in _evidence(graph) if f.source_id == source}


def test_python_del_targets_never_fall_back_to_globals(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "app.py": (
                _H + "def local_del():\n    del h\n    attach(h)\n\n"
                "def maybe_del():\n    if FLAG:\n        del h\n    attach(h)\n\n"
                "def drop():\n    global h\n    del h\n\n"
                "def later():\n    attach(h)\n"
            ),
            "deleted.py": _H + "del h\n\ndef setup():\n    attach(h)\n",
            "nested.py": (
                "def outer():\n"
                "    def h():\n        pass\n"
                "    def drop():\n        nonlocal h\n        del h\n"
                "    drop()\n    attach(h)\n"
            ),
        },
    )
    assert _references(graph) == set()
    # ``del`` makes ``h`` local: the global is never consulted, conditionally or not.
    assert _bindings(graph, "app:local_del") == set()
    assert _bindings(graph, "app:maybe_del") == set()
    assert _bindings(graph, "deleted:setup") == set()
    assert _bindings(graph, "app:later") == {("app:h", "ambiguous")}
    assert _bindings(graph, "nested:outer") == {("nested:outer.h", "ambiguous")}


def test_python_comprehension_walrus_binds_the_enclosing_scope(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "app.py": (
                _H + "def local(xs):\n    [h := x for x in xs]\n    attach(h)\n\n"
                "def nested(xs):\n    return [[(h := y) for y in x] for x in xs] and attach(h)\n\n"
                "def other():\n    attach(h)\n"
            ),
            "module.py": _H + "[h := x for x in xs]\nattach(h)\n",
            "clause.py": _H + "[attach(h) for x in xs if (h := x)]\n",
        },
    )
    # Inside ``local`` and ``nested``, ``h`` is a local that may never be assigned.
    assert _bindings(graph, "app:local") == set()
    assert _bindings(graph, "app:nested") == set()
    assert _referenced(graph, "app:other") == {"app:h"}
    # At module level a comprehension may run zero times, leaving the function bound.
    assert _bindings(graph, "module") == {("module:h", "ambiguous")}
    # Clauses run before the element, so the element may read the walrus value.
    assert _bindings(graph, "clause") == {("clause:h", "ambiguous")}
    assert _referenced(graph, "module") == _referenced(graph, "clause") == set()


def test_python_star_imports_never_fabricate_or_keep_resolution(tmp_path: Path) -> None:
    star = "from elsewhere import *\n\n"
    graph = _discover(
        tmp_path,
        {
            "after.py": _H + star + "attach(h)\n\ndef setup():\n    attach(h)\n",
            "before.py": star + _H + "attach(h)\n",
            "only.py": star + "def setup():\n    attach(h)\n",
            # Without the star import this conditional self-alias resolves to ``h``.
            "maybe.py": star + _H + "if FLAG:\n    h = h\n\nattach(h)\n",
            "lib.py": _H + star,
            "user.py": "import lib\nfrom lib import h\n\ndef setup():\n    attach(h, lib.h)\n",
        },
    )
    assert _bindings(graph, "after") == {("after:h", "ambiguous")}
    assert _bindings(graph, "after:setup") == {("after:h", "ambiguous")}
    assert _referenced(graph, "before") == {"before:h"}
    assert _bindings(graph, "only:setup") == set()
    assert _bindings(graph, "maybe") == {("maybe:h", "ambiguous")}
    assert _bindings(graph, "user:setup") == {("lib:h", "ambiguous")}
    assert {source for source, _ in _references(graph)} == {"before"}


def test_python_scope_writes_and_type_aliases_block_resolution(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "app.py": (
                _H + "def swap():\n    global h\n    h = other\n\ndef setup():\n    attach(h)\n"
            ),
            "alias.py": _H + "type h = int\nattach(h)\n",
        },
    )
    assert _references(graph) == set()
    assert _bindings(graph, "app:setup") == {("app:h", "ambiguous")}
    assert _bindings(graph, "alias") == set()


def test_python_nested_owners_and_methods(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "app.py": (
                "def h():\n    pass\n\n"
                "def outer():\n"
                "    def inner():\n        attach(h)\n"
                "    attach(inner)\n\n"
                "class Service:\n"
                "    def h(self):\n        pass\n\n"
                "    def setup(self):\n"
                "        attach(h)\n"
                "        attach(self.h, Service.h)\n"
            )
        },
    )
    references = _references(graph)
    assert ("app:outer.inner", "app:h") in references
    assert ("app:outer", "app:outer.inner") in references
    assert ("app:outer", "app:h") not in references
    # Class scope is not visible from methods, so bare ``h`` is the module function.
    assert _referenced(graph, "app:Service.setup") == {"app:h", "app:Service.h"}
    (receiver,) = _evidence(graph, "self.h")
    assert receiver.unresolved == (*_UNRESOLVED, "receiver_dispatch")
    by_symbol = {
        dict(e.attributes)["expression"]: e for e in _reference_edges(graph, "app:Service.h")
    }
    assert by_symbol["self.h"].confidence == EdgeConfidence.INFERRED
    assert by_symbol["Service.h"].confidence == EdgeConfidence.CERTAIN


def test_python_non_callable_values_are_ignored(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "app.py": (
                "import os\n"
                "class Widget:\n    pass\n\n"
                "LIMIT = 3\n\n"
                "def setup(value):\n"
                "    attach(value, LIMIT, Widget, os.path, print, missing)\n"
            )
        },
    )
    assert _references(graph) == set()
    assert graph.entrypoint_evidence == {}


# --------------------------------------------------------------------------- PHP

_PHP_APP = (
    "<?php\n"
    "namespace App;\n"
    "\n"
    "function f() {}\n"
    "class Type {\n"
    "    public static function method() {}\n"
    "    public function inst() { return [$this, 'inst']; }\n"
    "    public function acquire() { return $this->inst(...); }\n"
    "}\n"
    "function setup($obj) {\n"
    "    any_api('App\\f', ['App\\Type', 'method'], [Type::class, 'method']);\n"
    "    any_api('f', 'App\\Type::method', ['k' => 'App\\f']);\n"
    "    $c = Type::method(...);\n"
    "    $d = $obj->method(...);\n"
    "    $e = f(...);\n"
    "}\n"
    "any_api('App\\f');\n"
)


def _php(tmp_path: Path, files: dict[str, str] | None = None) -> CodeGraph:
    return _discover(tmp_path, files or {"app.php": _PHP_APP}, language="php")


def test_php_string_and_array_callables(tmp_path: Path) -> None:
    graph = _php(tmp_path)
    paths = {
        (dict(f.details)["path"], f.node_id)
        for f in _evidence(graph)
        if f.source_id == "app:App.setup" and dict(f.details)["consumer_kind"] == "call"
    }
    assert paths == {
        ("args[0]", "app:App.f"),
        ("args[1]", "app:App.Type.method"),
        ("args[2]", "app:App.Type.method"),
        ("args[2]['k']", "app:App.f"),
    }
    # Quoted names are fully qualified: 'f' is the global function, which is absent.
    assert not any(f.symbol == "'f'" for f in _evidence(graph))
    assert all(f.rule == "php.callable_reference" for f in _evidence(graph))


def test_php_first_class_acquisition_is_not_execution(tmp_path: Path) -> None:
    graph = _php(tmp_path)
    assert ("app:App.setup", "app:App.Type.method") in _references(graph)
    assert ("app:App.setup", "app:App.f") in _references(graph)
    assert not any(
        e.kind == EdgeKind.CALLS and e.target_id in {"app:App.Type.method", "app:App.f"}
        for e in graph.edges
    )
    acquired = {
        f.symbol: f for f in _evidence(graph) if dict(f.details)["form"] == "first_class_callable"
    }
    assignment = acquired["Type::method(...)"]
    assert dict(assignment.details)["consumer_kind"] == "assignment"
    assert dict(assignment.details)["consumer"] == "$c"
    engine = QueryEngine.from_graph(graph)
    assert "app:App.Type.method" not in {u["id"] for u in engine.callees_of("app:App.setup")}
    assert "app:App.Type.method" not in {u["id"] for u in engine.reachable_from("app:App.setup")}


def test_php_dynamic_receivers_stay_unresolved(tmp_path: Path) -> None:
    graph = _php(tmp_path)
    (dynamic,) = _evidence(graph, "$obj->method(...)")
    assert (dynamic.node_id, dynamic.binding) == (None, "unresolved")
    assert dynamic.unresolved == (*_UNRESOLVED, "receiver_dispatch")
    assert not any(dict(e.attributes).get("expression") == "$obj->method(...)" for e in graph.edges)
    this_refs = {
        f.symbol: f for f in _evidence(graph) if (f.source_id or "").startswith("app:App.Type.")
    }
    assert this_refs["$this->inst(...)"].node_id == "app:App.Type.inst"
    assert this_refs["[$this, 'inst']"].node_id == "app:App.Type.inst"
    assert "receiver_dispatch" in this_refs["[$this, 'inst']"].unresolved


def test_php_module_level_reference_uses_module_source(tmp_path: Path) -> None:
    graph = _php(tmp_path)
    module_level = [f for f in _evidence(graph) if f.location.start_line == 17]
    assert [(f.source_id, f.node_id) for f in module_level] == [("app", "app:App.f")]
    assert ("app", "app:App.f") in _references(graph)
    assert not any(source.endswith("<script>") for source, _ in _references(graph))


@pytest.mark.parametrize("consumer", ["register_callback", "call_user_func", "zz_custom"])
def test_php_consumer_name_does_not_matter(tmp_path: Path, consumer: str) -> None:
    graph = _php(tmp_path, {"app.php": f"<?php\nfunction f() {{}}\n{consumer}('f');\n"})
    assert _references(graph) == {("app", "app:f")}


def test_php_duplicate_declarations_are_ambiguous(tmp_path: Path) -> None:
    graph = _php(
        tmp_path,
        {
            "one.php": "<?php\nfunction dup() {}\n",
            "two.php": "<?php\nfunction dup() {}\n",
            "use.php": "<?php\nfunction setup() { attach('dup', 'not_declared'); }\n",
        },
    )
    facts = [f for f in _evidence(graph) if f.source_id == "use:setup"]
    assert {(f.node_id, f.binding) for f in facts} == {
        ("one:dup", "ambiguous"),
        ("two:dup", "ambiguous"),
    }
    assert _referenced(graph, "use:setup") == set()


def test_php_namespace_imports_resolve_class_constants(tmp_path: Path) -> None:
    graph = _php(
        tmp_path,
        {
            "handlers.php": (
                "<?php\nnamespace Lib\\Handlers;\nclass Type { public function run() {} }\n"
            ),
            "app.php": (
                "<?php\nnamespace App;\nuse Lib\\Handlers\\Type as Alias;\n"
                "function setup() { attach([Alias::class, 'run'], Alias::run(...)); }\n"
            ),
        },
    )
    edges = _reference_edges(graph, "handlers:Lib.Handlers.Type.run")
    assert {dict(e.attributes)["form"] for e in edges} == {"array", "first_class_callable"}
    assert {e.confidence for e in edges} == {EdgeConfidence.INFERRED}


def test_python_method_references_are_case_sensitive(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "app.py": (
                "class Handler:\n"
                "    def Run(self): pass\n"
                "    def run(self): pass\n"
                "def setup():\n    attach(Handler.Run, Handler.run)\n"
            )
        },
    )
    facts = _evidence(graph)
    assert [(f.node_id, f.binding, dict(f.details)["path"]) for f in facts] == [
        ("app:Handler.Run", "resolved", "args[0]"),
        ("app:Handler.run", "resolved", "args[1]"),
    ]


def test_python_package_initializer_resolves_sibling_imports(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "pkg/__init__.py": (
                "from .handlers import h as handler\ndef setup():\n    attach(handler)\n"
            ),
            "pkg/handlers.py": "def h(): pass\n",
        },
    )
    assert _references(graph) == {("pkg:setup", "pkg.handlers:h")}
    (fact,) = _evidence(graph, "handler")
    assert (fact.binding, fact.source_id) == ("resolved", "pkg:setup")


def test_python_duplicate_module_suffixes_keep_all_candidates_ambiguous(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "left/pkg/handlers.py": "def h(): pass\n",
            "right/pkg/handlers.py": "def h(): pass\n",
            "app.py": "from pkg import handlers\ndef setup():\n    attach(handlers.h)\n",
        },
    )
    assert _references(graph) == set()
    assert {(f.node_id, f.binding) for f in _evidence(graph, "handlers.h")} == {
        ("left.pkg.handlers:h", "ambiguous"),
        ("right.pkg.handlers:h", "ambiguous"),
    }


def test_php_grouped_imports_and_absolute_callable_names(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "lib.php": (
                r"<?php namespace Lib\Handlers; function f() {} "
                "class Type { static function Run() {} }"
            ),
            "app.php": r"""<?php namespace App;
use Lib\Handlers\{Type as Alias};
use function Lib\Handlers\{f as handle};
function setup() {
    attach(Alias::RUN(...), \Lib\Handlers\Type::Run(...),
           handle(...), \Lib\Handlers\f(...));
    attach(["Lib\\Handlers\\Type", 'RUN']);
}
""",
        },
        "php",
    )
    facts = _evidence(graph)
    assert [(f.node_id, dict(f.details)["form"], dict(f.details)["path"]) for f in facts] == [
        ("lib:Lib.Handlers.Type.Run", "first_class_callable", "args[0]"),
        ("lib:Lib.Handlers.Type.Run", "first_class_callable", "args[1]"),
        ("lib:Lib.Handlers.f", "first_class_callable", "args[2]"),
        ("lib:Lib.Handlers.f", "first_class_callable", "args[3]"),
        ("lib:Lib.Handlers.Type.Run", "array", "args[0]"),
    ]
    assert {f.binding for f in facts} == {"resolved"}
    assert {f.source_id for f in facts} == {"app:App.setup"}
    assert {edge.confidence for edge in graph.edges if edge.kind == EdgeKind.REFERENCES} == {
        EdgeConfidence.INFERRED
    }


def test_php_relative_class_scopes_preserve_dispatch_uncertainty(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "app.php": """<?php namespace App;
class Base { static function Run() {} }
class Child extends Base {
    static function Run() {}
    function setup() {
        attach(self::RUN(...), static::run(...), parent::RUN(...));
    }
}
"""
        },
        "php",
    )
    assert [(f.node_id, f.unresolved) for f in _evidence(graph)] == [
        ("app:App.Child.Run", _UNRESOLVED),
        ("app:App.Child.Run", (*_UNRESOLVED, "receiver_dispatch")),
        ("app:App.Base.Run", _UNRESOLVED),
    ]
    assert [edge.confidence for edge in graph.edges if edge.kind == EdgeKind.REFERENCES] == [
        EdgeConfidence.CERTAIN,
        EdgeConfidence.INFERRED,
        EdgeConfidence.CERTAIN,
    ]


def test_php_nested_consumers_and_data_arrays_preserve_reference_context(tmp_path: Path) -> None:
    graph = _discover(
        tmp_path,
        {
            "app.php": """<?php function h() {} class Type { static function run() {} }
function setup() {
    Type::attach(h(...));
    new Registry(h(...));
    $r?->attach(h(...));
    attach(["Missing", "h"], [0 => "h", 1 => "h"]);
}
"""
        },
        "php",
    )
    facts = _evidence(graph)
    assert {f.node_id for f in facts} == {"app:h"}
    assert {f.binding for f in facts} == {"resolved"}
    assert [(dict(f.details)["consumer"], dict(f.details)["path"]) for f in facts] == [
        ("Type::attach", "args[0]"),
        ("new Registry", "args[0]"),
        ("$r->attach", "args[0]"),
        ("attach", "args[0][1]"),
        ("attach", "args[1][0]"),
        ("attach", "args[1][1]"),
    ]


# --------------------------------------------------------------------------- Stability


@pytest.mark.parametrize(
    ("language", "files"),
    [
        ("python", {"app.py": "def h():\n    pass\n\ndef s():\n    attach(h, [h])\n"}),
        ("php", {"app.php": _PHP_APP}),
    ],
)
def test_repeated_discovery_is_stable(tmp_path: Path, language: str, files: dict[str, str]) -> None:
    graph = _discover(tmp_path, files, language)
    edges = list(graph.edges)
    evidence = dict(graph.entrypoint_evidence)
    assert evidence
    discover_callable_references(graph)
    assert graph.edges == edges
    assert graph.entrypoint_evidence == evidence
