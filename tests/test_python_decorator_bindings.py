"""Lexical decorator binding evidence for Python handlers."""

import re
import textwrap
from pathlib import Path

import pytest

from trailmark.analysis._python_decorators import DecoratorFact, python_decorators

_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)", re.MULTILINE)


def _facts(tmp_path: Path, source: str) -> dict[str, list[DecoratorFact]]:
    """Group facts by handler name using the ``def`` line of each handler."""
    text = textwrap.dedent(source)
    path = tmp_path / "subject.py"
    path.write_text(text)
    names = {
        text.count("\n", 0, match.start(1)) + 1: match.group(1) for match in _DEF.finditer(text)
    }
    grouped: dict[str, list[DecoratorFact]] = {}
    for fact in python_decorators(str(path)):
        grouped.setdefault(names[fact.handler_line], []).append(fact)
    return grouped


def _only(facts: dict[str, list[DecoratorFact]], name: str) -> DecoratorFact:
    assert len(facts[name]) == 1, facts[name]
    return facts[name][0]


def _summary(fact: DecoratorFact) -> tuple[str | None, str, str | None, str | None, str, str]:
    return (
        fact.consumer_path,
        fact.receiver_binding,
        fact.method,
        fact.path,
        fact.disposition,
        fact.reason,
    )


def test_patch_style_aliases_are_non_path_decorators(tmp_path: Path) -> None:
    facts = _facts(
        tmp_path,
        """
        import unittest.mock as um
        from unittest import mock
        from unittest.mock import patch as p
        from toolkit import substitute as swap
        patched = mock.patch

        @mock.patch("pkg.module.name")
        def module_attr(): ...

        @um.patch("pkg.module.name")
        def module_alias(): ...

        @p("pkg.module.name")
        def name_alias(): ...

        @patched("pkg.module.name")
        def assigned_alias(): ...

        @swap("pkg.module.name")
        def neutral(): ...
        """,
    )
    patch_path = "unittest.mock.patch"
    expected = {
        "module_attr": (patch_path, "import_name"),
        "module_alias": (patch_path, "import_module"),
        "name_alias": (patch_path, "import_name"),
        "assigned_alias": (patch_path, "import_name"),
        "neutral": ("toolkit.substitute", "import_name"),
    }
    for name, (consumer, binding) in expected.items():
        method = consumer.rsplit(".", 1)[-1]
        assert _summary(_only(facts, name)) == (
            consumer,
            binding,
            method,
            "pkg.module.name",
            "non_path_decorator",
            "non_path_argument",
        )


def test_unrelated_get_with_key_is_non_path(tmp_path: Path) -> None:
    facts = _facts(
        tmp_path,
        """
        from settings_store import Store
        import memo
        config = Store()

        @config.get("feature.flag")
        def flagged(): ...

        @memo.get("cache-key", ttl=5)
        def cached(): ...
        """,
    )
    assert _summary(_only(facts, "flagged")) == (
        "settings_store.Store().get",
        "call_result",
        "get",
        "feature.flag",
        "non_path_decorator",
        "non_path_argument",
    )
    cached = _only(facts, "cached")
    assert cached.disposition == "non_path_decorator"
    assert cached.arguments == ('"cache-key"', "ttl=5")


def test_constructed_external_receivers_and_aliases_are_path_decorators(tmp_path: Path) -> None:
    facts = _facts(
        tmp_path,
        """
        from flask import Flask as F
        import fastapi
        from vendor_http import Server
        app = F(__name__)
        api = fastapi.APIRouter(prefix="/v1")
        server = Server()
        alias = app

        @app.route("/")
        def index(): ...

        @api.get("/items")
        def items(): ...

        @server.get("")
        def neutral(): ...

        @alias.post("/alias")
        def aliased(): ...

        @Server().get("/inline")
        def inline(): ...
        """,
    )
    expected = {
        "index": ("flask.Flask().route", "route", "/"),
        "items": ("fastapi.APIRouter().get", "get", "/items"),
        "neutral": ("vendor_http.Server().get", "get", ""),
        "aliased": ("flask.Flask().post", "post", "/alias"),
        "inline": ("vendor_http.Server().get", "get", "/inline"),
    }
    for name, (consumer, method, path) in expected.items():
        assert _summary(_only(facts, name)) == (
            consumer,
            "call_result",
            method,
            path,
            "path_decorator",
            "literal_path",
        )


def test_module_receivers_can_be_registrars(tmp_path: Path) -> None:
    facts = _facts(
        tmp_path,
        """
        import bottle
        from bottle import get as fetch
        import neutral_routes as nr

        @bottle.get("/")
        def home(): ...

        @fetch("/fetched")
        def fetched(): ...

        @nr.get("/")
        def neutral(): ...
        """,
    )
    assert _summary(_only(facts, "home"))[:2] == ("bottle.get", "import_module")
    assert _summary(_only(facts, "fetched"))[:2] == ("bottle.get", "import_name")
    assert _summary(_only(facts, "neutral"))[:2] == ("neutral_routes.get", "import_module")
    for name in ("home", "fetched", "neutral"):
        assert _only(facts, name).disposition == "path_decorator"


def test_factories_keep_nested_function_scope(tmp_path: Path) -> None:
    facts = _facts(
        tmp_path,
        """
        from aiohttp import web
        from neutral.server import Hub
        routes = Hub()

        def create_app():
            routes = web.RouteTableDef()

            @routes.get("/")
            def index(): ...

            def install():
                @routes.post("/nested")
                def nested(): ...

            return routes

        @routes.get("/module")
        def module_level(): ...
        """,
    )
    assert _only(facts, "index").consumer_path == "aiohttp.web.RouteTableDef().get"
    assert _only(facts, "nested").consumer_path == "aiohttp.web.RouteTableDef().post"
    assert _only(facts, "module_level").consumer_path == "neutral.server.Hub().get"


def test_parameters_and_shadowing_do_not_inherit_global_provenance(tmp_path: Path) -> None:
    facts = _facts(
        tmp_path,
        """
        from flask import Flask
        app = Flask(__name__)

        def register(app):
            @app.get("/param")
            def from_param(): ...

        def maybe(app):
            if app is None:
                app = Flask(__name__)
            @app.get("/maybe")
            def conditional(): ...

        def shadowed():
            app = make_router()
            @app.get("/shadow")
            def shadow(): ...

        def defaulted(app=app):
            @app.get("/default")
            def default(): ...
        """,
    )
    assert _summary(_only(facts, "from_param")) == (
        None,
        "parameter",
        "get",
        "/param",
        "path_decorator",
        "literal_path",
    )
    assert _summary(_only(facts, "conditional")) == (
        None,
        "ambiguous",
        "get",
        "/maybe",
        "unknown",
        "ambiguous_binding",
    )
    assert _summary(_only(facts, "shadow"))[:3] == (None, "unbound", "get")
    assert _summary(_only(facts, "default"))[:2] == (None, "parameter")


def test_conditional_and_external_rebinding_are_ambiguous(tmp_path: Path) -> None:
    facts = _facts(
        tmp_path,
        """
        from flask import Flask
        from neutral import Hub
        app = Flask(__name__)
        if DEBUG:
            app = Hub()

        @app.get("/")
        def conditional(): ...

        try:
            from fast import Router
        except ImportError:
            from slow import Router
        router = Router()

        @router.get("/r")
        def fallback(): ...

        hub = Hub()
        def reset():
            global hub
            hub = Flask(__name__)

        @hub.get("/g")
        def global_write(): ...

        from elsewhere import *

        @route("/star")
        def starred(): ...
        """,
    )
    for name in ("conditional", "fallback", "global_write", "starred"):
        fact = _only(facts, name)
        assert (fact.consumer_path, fact.receiver_binding) == (None, "ambiguous"), name
        assert (fact.disposition, fact.reason) == ("unknown", "ambiguous_binding"), name


def test_sequential_unconditional_rebinding_follows_source_order(tmp_path: Path) -> None:
    facts = _facts(
        tmp_path,
        """
        from flask import Flask
        from neutral import Hub
        app = Flask(__name__)

        @app.get("/first")
        def first(): ...

        app = Hub()

        @app.get("/second")
        def second(): ...

        def later():
            @app.get("/later")
            def deferred(): ...
        """,
    )
    assert _only(facts, "first").consumer_path == "flask.Flask().get"
    assert _only(facts, "second").consumer_path == "neutral.Hub().get"
    # Closures read the global at call time, after any rebinding.
    assert _only(facts, "deferred").receiver_binding == "ambiguous"


def test_decorator_method_aliases_resolve_to_the_bound_method(tmp_path: Path) -> None:
    facts = _facts(
        tmp_path,
        """
        from flask import Flask
        from vendor import Server
        app = Flask(__name__)
        get = app.get
        route = app.route
        post = Server().post

        @get("/a")
        def a(): ...

        @route("/b")
        def b(): ...

        @post("/c")
        def c(): ...

        def factory(router):
            put = router.put

            @put("/d")
            def d(): ...
        """,
    )
    assert _summary(_only(facts, "a"))[:3] == ("flask.Flask().get", "call_result", "get")
    assert _summary(_only(facts, "b"))[:3] == ("flask.Flask().route", "call_result", "route")
    assert _summary(_only(facts, "c"))[:3] == ("vendor.Server().post", "call_result", "post")
    assert _summary(_only(facts, "d"))[:3] == (None, "parameter", "put")


def test_constant_and_dynamic_paths(tmp_path: Path) -> None:
    facts = _facts(
        tmp_path,
        """
        from vendor import Server
        app = Server()
        ROOT = "/"
        ALIAS = ROOT
        KEY = "cache-key"

        def factory(prefix):
            LOCAL = "/local"

            @app.get(LOCAL)
            def local(): ...

            @app.get(prefix)
            def param(): ...

            @app.get(f"{prefix}/x")
            def formatted(): ...

        @app.get(ALIAS)
        def aliased(): ...

        @app.get(KEY)
        def key(): ...

        @app.get(PREFIX + "/x")
        def concatenated(): ...

        @app.get(path="/kw")
        def keyword(): ...

        @app.get(*paths)
        def splat(): ...
        """,
    )
    expected = {
        "local": ("/local", "path_decorator", "constant_path"),
        "aliased": ("/", "path_decorator", "constant_path"),
        "key": ("cache-key", "non_path_decorator", "non_path_argument"),
        "param": (None, "unknown", "nonliteral_argument"),
        "formatted": (None, "unknown", "nonliteral_argument"),
        "concatenated": (None, "unknown", "nonliteral_argument"),
        "keyword": (None, "unknown", "no_positional_argument"),
        "splat": (None, "unknown", "nonliteral_argument"),
    }
    for name, outcome in expected.items():
        assert _summary(_only(facts, name))[3:] == outcome, name
    assert _only(facts, "formatted").arguments == ('f"{prefix}/x"',)
    assert _only(facts, "keyword").arguments == ('path="/kw"',)


def test_local_identity_decorators_are_suppressed_even_for_slash(tmp_path: Path) -> None:
    facts = _facts(
        tmp_path,
        """
        class Cache:
            def get(self, path):
                \"\"\"Return an identity decorator.\"\"\"
                return lambda fn: fn

            def put(self, path):
                def decorate(fn):
                    return fn
                return decorate

            def post(self, path):
                def decorate(fn):
                    self.routes[path] = fn
                    return fn
                return decorate

            @staticmethod
            def head(path):
                return lambda fn: fn

        def lookup(path):
            return lambda handler: handler

        cache = Cache()

        @cache.get("/")
        def via_instance(): ...

        @Cache().put("/put")
        def via_inline(): ...

        @lookup("/")
        def via_function(): ...

        @cache.post("/registered")
        def registering(): ...

        @Cache.head("/")
        def decorated_member(): ...
        """,
    )
    for name, path in (("via_instance", "/"), ("via_inline", "/put"), ("via_function", "/")):
        fact = _only(facts, name)
        assert fact.receiver_binding == "enclosing_local"
        assert (fact.path, fact.disposition, fact.reason) == (
            path,
            "non_path_decorator",
            "local_nonregistering_decorator",
        )
    assert _summary(_only(facts, "registering")) == (
        "Cache().post",
        "enclosing_local",
        "post",
        "/registered",
        "path_decorator",
        "literal_path",
    )
    # Decorated members are not proven identities.
    assert _only(facts, "decorated_member").disposition == "path_decorator"


_BOX = """
class Box:
    def attach(self, path):
        return lambda fn: fn
"""


@pytest.mark.parametrize(
    ("setup", "decorator"),
    [
        pytest.param(
            """
            class App:
                def __init__(self, real):
                    self.route = real.route

                def route(self, path):
                    return lambda f: f

            app = App(x)
            """,
            "app.route",
            id="instance_init",
        ),
        pytest.param(
            """
            class Hub:
                def __init__(self, inner):
                    self.bind, self.size = inner.bind, 0

                def bind(self, path):
                    return lambda f: f
            """,
            "Hub(x).bind",
            id="renamed_tuple_store_in_init",
        ),
        pytest.param(_BOX + "box = Box()\nbox.attach = real.attach\n", "box.attach", id="module"),
        pytest.param(_BOX + "Box.attach = real.attach\n", "Box().attach", id="class_patch"),
        pytest.param(
            _BOX + "box = Box()\nwith opener() as box.attach:\n    pass\n", "box.attach", id="with"
        ),
        pytest.param(_BOX + "box = Box()\nsetattr(box, NAME, real)\n", "box.attach", id="setattr"),
        pytest.param(_BOX + "box = Box()\nvars(box)[NAME] = real\n", "box.attach", id="vars"),
        pytest.param(_BOX + "box = Box()\nbox.__class__ = Real\n", "box.attach", id="class_swap"),
        pytest.param(
            _BOX + "    def __new__(cls):\n        return real\n", "Box().attach", id="new"
        ),
        pytest.param(
            _BOX + "    def __getattribute__(self, name):\n        return real\n",
            "Box().attach",
            id="getattribute",
        ),
        pytest.param(
            _BOX.replace("class Box:", "class Box(Base):"), "Box().attach", id="unknown_base"
        ),
        pytest.param(_BOX + "from elsewhere import *\n", "Box().attach", id="star_class"),
        pytest.param(
            "def lookup(path):\n    return lambda f: f\nfrom elsewhere import *\n",
            "lookup",
            id="star_function",
        ),
        pytest.param(
            "def lookup(path):\n    return lambda f: f\n"
            "import sys\nsys.modules[__name__].lookup = real\n",
            "lookup",
            id="module_object_store",
        ),
    ],
)
def test_replaceable_identity_decorators_are_not_suppressed(
    tmp_path: Path, setup: str, decorator: str
) -> None:
    source = textwrap.dedent(setup) + f"\n@{decorator}('/x')\ndef handler(): ...\n"
    fact = _only(_facts(tmp_path, source), "handler")
    assert _summary(fact) == (
        None,
        "ambiguous",
        decorator.rsplit(".", 1)[-1],
        "/x",
        "unknown",
        "ambiguous_binding",
    )


def test_identity_proofs_survive_unrelated_writes_and_earlier_star_imports(
    tmp_path: Path,
) -> None:
    facts = _facts(
        tmp_path,
        """
        from elsewhere import *

        class Box(object):
            def __init__(self):
                self.size = 0
                self.routes = {}

            def attach(self, path):
                return lambda fn: fn

            def __repr__(self):
                return self.__class__.__name__

        box = Box()
        box.size = 3
        box.routes["/x"] = None

        @box.attach("/x")
        def handler(): ...
        """,
    )
    assert _summary(_only(facts, "handler")) == (
        "Box().attach",
        "enclosing_local",
        "attach",
        "/x",
        "non_path_decorator",
        "local_nonregistering_decorator",
    )


def test_nested_classes_and_functions_own_spans_and_scopes(tmp_path: Path) -> None:
    source = """
        from vendor import Server
        app = Server()

        class Identity:
            def get(self, path):
                return lambda fn: fn

        class Views:
            app = Identity()

            @app.get("/")
            def in_class(self): ...

            def method(self):
                @app.get("/nested")
                async def in_method(): ...

            class Inner:
                @property
                @app.get("/inner")
                def inner(self): ...
        """
    facts = _facts(tmp_path, source)
    assert _only(facts, "in_class").reason == "local_nonregistering_decorator"
    nested = _only(facts, "in_method")
    assert nested.consumer_path == "vendor.Server().get"
    lines = textwrap.dedent(source).splitlines()
    def_line = next(i for i, line in enumerate(lines, 1) if "async def in_method" in line)
    assert (nested.handler_line, nested.handler_col) == (def_line, 8)
    assert (nested.location.start_line, nested.location.start_col) == (def_line - 1, 8)
    assert nested.location.file_path == str(tmp_path / "subject.py")
    prop, inner = facts["inner"]
    assert (prop.disposition, prop.reason, prop.method) == (
        "non_path_decorator",
        "not_called",
        "property",
    )
    assert inner.consumer_path == "vendor.Server().get"
    assert prop.handler_line == inner.handler_line == inner.location.start_line + 1


def test_argument_shapes_without_paths(tmp_path: Path) -> None:
    facts = _facts(
        tmp_path,
        """
        import functools
        from vendor import Server
        app = Server()

        @functools.lru_cache(128)
        def sized(): ...

        @functools.cache()
        def empty(): ...

        @app.get
        def bare(): ...
        """,
    )
    assert _summary(_only(facts, "sized"))[3:] == (None, "non_path_decorator", "non_path_argument")
    assert _summary(_only(facts, "empty"))[3:] == (None, "non_path_decorator", "no_arguments")
    assert _summary(_only(facts, "bare"))[:3] == ("vendor.Server().get", "call_result", "get")
    assert _only(facts, "bare").reason == "not_called"


def test_missing_and_malformed_files_are_safe(tmp_path: Path) -> None:
    assert python_decorators(str(tmp_path / "missing.py")) == []
    assert python_decorators(str(tmp_path)) == []
    broken = tmp_path / "broken.py"
    broken.write_text('@app.get("/"\ndef broken(:\n')
    assert isinstance(python_decorators(str(broken)), list)
    binary = tmp_path / "binary.py"
    binary.write_bytes(b"\xff\xfe@app.get('/\xff')\ndef f(): pass\n")
    assert isinstance(python_decorators(str(binary)), list)


def test_deep_and_cyclic_aliases_stay_bounded(tmp_path: Path) -> None:
    chain = "a." * 5000
    facts = _facts(
        tmp_path,
        f"""
        a = b
        b = a

        @a.get("/")
        def cyclic(): ...

        @{chain}get("/")
        def deep(): ...
        """,
    )
    assert _only(facts, "cyclic").receiver_binding == "ambiguous"
    assert _only(facts, "cyclic").disposition == "unknown"
    assert _only(facts, "deep").consumer_path is None


def test_only_function_decorators_are_reported_in_source_order(tmp_path: Path) -> None:
    path = tmp_path / "subject.py"
    path.write_text(
        "@class_marker\nclass Views:\n"
        "    @outer('/a')\n    @inner('/b')\n    async def method(self):\n"
        "        @nested('/c')\n        def handler(): pass\n"
        "@last('/d')\ndef final(): pass\n"
    )
    facts = python_decorators(str(path))
    assert [(fact.method, fact.handler_line, fact.handler_col) for fact in facts] == [
        ("outer", 5, 4),
        ("inner", 5, 4),
        ("nested", 7, 8),
        ("last", 9, 0),
    ]
    assert [fact.location.start_line for fact in facts] == [3, 4, 6, 8]
    assert [fact.path for fact in facts] == ["/a", "/b", "/c", "/d"]

    # Reusing the syntax query must not reuse facts from an earlier file version.
    path.write_text("@class_marker\nclass Views:\n    def method(self): pass\n")
    assert python_decorators(str(path)) == []
    path.write_text("@replacement('/new')\ndef handler(): pass\n")
    assert [(f.method, f.path) for f in python_decorators(str(path))] == [("replacement", "/new")]


def test_parenthesized_and_compound_receivers_keep_binding_precision(tmp_path: Path) -> None:
    facts = _facts(
        tmp_path,
        """
        from vendor import Router
        app = Router()

        @((app)).get('/known')
        def known(): ...

        @(app or fallback).get('/unknown')
        def unknown(): ...
        """,
    )
    assert _summary(_only(facts, "known")) == (
        "vendor.Router().get",
        "call_result",
        "get",
        "/known",
        "path_decorator",
        "literal_path",
    )
    assert _summary(_only(facts, "unknown")) == (
        None,
        "ambiguous",
        "get",
        "/unknown",
        "unknown",
        "ambiguous_binding",
    )


@pytest.mark.parametrize("returned", ["lambda fn: other", "lambda fn, extra: fn"])
def test_nonidentity_factories_are_not_suppressed(tmp_path: Path, returned: str) -> None:
    facts = _facts(
        tmp_path,
        f"def lookup(path):\n    return {returned}\n@lookup('/x')\ndef handler(): pass\n",
    )
    assert _summary(_only(facts, "handler")) == (
        "lookup",
        "enclosing_local",
        "lookup",
        "/x",
        "path_decorator",
        "literal_path",
    )


def test_nonlocal_writes_invalidate_the_enclosing_receiver(tmp_path: Path) -> None:
    facts = _facts(
        tmp_path,
        """
        from vendor import Router
        def setup():
            app = Router()
            def replace():
                nonlocal app
                app = other
            @app.get('/x')
            def handler(): ...
        """,
    )
    assert _summary(_only(facts, "handler")) == (
        None,
        "ambiguous",
        "get",
        "/x",
        "unknown",
        "ambiguous_binding",
    )


def test_pattern_capture_shadows_the_global_receiver(tmp_path: Path) -> None:
    facts = _facts(
        tmp_path,
        """
        from vendor import Router
        app = Router()
        def setup(value):
            match value:
                case app:
                    pass
            @app.get('/x')
            def handler(): ...
        """,
    )
    assert _summary(_only(facts, "handler")) == (
        None,
        "ambiguous",
        "get",
        "/x",
        "unknown",
        "ambiguous_binding",
    )


@pytest.mark.parametrize("length", [118, 120, 121, 200])
def test_long_argument_evidence_is_bounded_without_truncating_paths(
    tmp_path: Path, length: int
) -> None:
    path = "/" + "x" * (length - 3)
    argument = repr(path)
    assert len(argument) == length
    facts = _facts(tmp_path, f"@app.get({argument})\ndef handler(): pass\n")
    fact = _only(facts, "handler")
    assert fact.path == path
    assert fact.arguments == (argument if length <= 120 else argument[:117] + "...",)
