"""Elysia route registration binds actual handlers, not similarly named methods."""

from pathlib import Path

import pytest

from trailmark.query.api import QueryEngine


def _engine(tmp_path: Path, source: str, suffix: str = ".ts") -> QueryEngine:
    (tmp_path / f"app{suffix}").write_text(source)
    return QueryEngine.from_directory(str(tmp_path), language="auto")


@pytest.mark.parametrize("suffix", [".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"])
def test_named_inline_and_chained_routes(tmp_path: Path, suffix: str) -> None:
    engine = _engine(
        tmp_path,
        """
import { Elysia } from 'elysia';
function work() { return 'ok'; }
function named() { return work(); }
const app = new Elysia().get('/a', named).post('/b', () => work());
app.put('/c', function inline() { return work(); });
""",
        suffix,
    )
    surface = engine.attack_surface()
    assert len(surface) == 3
    assert {entry["kind"] for entry in surface} == {"api"}
    assert {entry["trust_level"] for entry in surface} == {"untrusted_external"}
    assert {entry["asset_value"] for entry in surface} == {"high"}
    paths = engine.entrypoint_paths_to("work")
    assert {path[0] for path in paths} == {entry["node_id"] for entry in surface}
    assert all(len(path) == 2 for path in paths)


@pytest.mark.parametrize(
    ("imports", "constructor"),
    [
        ("import Elysia from 'elysia';", "Elysia"),
        ("import { Elysia as Web } from 'elysia';", "Web"),
        ("import * as web from 'elysia';", "web.Elysia"),
    ],
)
def test_constructor_imports_and_const_aliases(
    tmp_path: Path, imports: str, constructor: str
) -> None:
    engine = _engine(
        tmp_path,
        f"""
{imports}
const Factory = {constructor};
const app = new Factory();
const router = app;
const handler = () => 'ok';
const alias = handler;
router.get('/', alias);
""",
    )
    assert [entry["node_id"] for entry in engine.attack_surface()] == ["app:handler"]


@pytest.mark.parametrize(
    "method", ["get", "post", "put", "patch", "delete", "head", "options", "all"]
)
def test_http_methods(tmp_path: Path, method: str) -> None:
    engine = _engine(
        tmp_path,
        f"""
import {{ Elysia }} from 'elysia';
new Elysia().{method}('/', () => 'ok');
""",
    )
    assert len(engine.attack_surface()) == 1


def test_route_method_and_each_registration_evidence(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        """
import { Elysia } from 'elysia';
function handler() {}
new Elysia().get('/one', handler).route('POST', '/two', handler);
""",
    )
    assert len(engine.attack_surface()) == 1
    facts = [e for e in engine.entrypoint_evidence() if e["rule"] == "elysia.route"]
    assert len(facts) == 2
    assert {e["node_id"] for e in facts} == {"app:handler"}
    assert {e["details"]["method"] for e in facts} == {"GET", "POST"}
    assert {e["details"]["path"] for e in facts} == {"/one", "/two"}
    assert all("runtime_activation" in e["unresolved"] for e in facts)


@pytest.mark.parametrize(
    "source",
    [
        "const app = { get(path, handler) {} }; app.get('/', () => 'no');",
        "import { Elysia } from 'other'; new Elysia().get('/', () => 'no');",
        "class Elysia {} new Elysia().get('/', () => 'no');",
        "import type { Elysia } from 'elysia'; new Elysia().get('/', () => 'no');",
        "import { type Elysia } from 'elysia'; new Elysia().get('/', () => 'no');",
        "import { Elysia } from 'elysia'; "
        "function register(Elysia) { new Elysia().get('/', () => 'no'); }",
        "import { Elysia } from 'elysia'; "
        "const app = new Elysia(); function register(app) { app.get('/', () => 'no'); }",
        "import { Elysia } from 'elysia'; "
        "let app = new Elysia(); app = other; app.get('/', () => 'no');",
        "import { Elysia } from 'elysia'; new Elysia().unknown().get('/', () => 'no');",
        "import { Elysia } from 'elysia'; new Elysia().get('/', 'static response');",
        "import { Elysia } from 'elysia'; new Elysia().use(() => {}).onBeforeHandle(() => {});",
        "import { Elysia } from 'elysia'; // new Elysia().get('/', handler)\nfunction handler() {}",
        "import { Elysia } from 'elysia'; "
        "function handler() {} function register(handler) { new Elysia().get('/', handler); }",
    ],
)
def test_non_routes_and_shadowed_bindings(tmp_path: Path, source: str) -> None:
    assert _engine(tmp_path, source).attack_surface() == []


def test_factory_routes_fluent_plugins_and_nested_callback_ownership(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        """
import { Elysia } from 'elysia';
function factory() {
  return new Elysia().use(plugin).onError(() => 'error').get('/', () => {
    function nested() { return 'private'; }
    return nested();
  });
}
""",
    )
    surface = engine.attack_surface()
    assert len(surface) == 1
    assert "$lambda" in surface[0]["node_id"]
    assert not surface[0]["node_id"].endswith("nested")


def test_unresolved_imported_handler_is_a_candidate(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        """
import { Elysia } from 'elysia';
import { handler } from './handlers';
new Elysia().get('/', handler);
""",
    )
    assert engine.attack_surface() == []
    facts = [e for e in engine.entrypoint_candidates() if e["rule"] == "elysia.route"]
    assert len(facts) == 1
    assert facts[0]["binding"] == "unresolved"
    assert facts[0]["symbol"] == "handler"


def test_override_wins(tmp_path: Path) -> None:
    config = tmp_path / ".trailmark"
    config.mkdir()
    (config / "entrypoints.toml").write_text("""
[[entrypoint]]
node = "app:handler"
kind = "api"
trust = "trusted_internal"
asset_value = "low"
""")
    engine = _engine(
        tmp_path,
        """
import { Elysia } from 'elysia';
function handler() {}
new Elysia().get('/', handler);
""",
    )
    assert engine.attack_surface()[0]["trust_level"] == "trusted_internal"


def test_dynamic_path_retains_handler_and_uncertainty(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        """
import { Elysia } from 'elysia';
new Elysia().route(methods, path, () => 'ok');
""",
    )
    assert len(engine.attack_surface()) == 1
    (fact,) = [e for e in engine.entrypoint_evidence() if e["rule"] == "elysia.route"]
    assert fact["details"]["path"] is None
    assert {"route_path", "route_method"} <= set(fact["unresolved"])


def test_registration_does_not_call_handler_or_tag_nested_functions(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        """
import { Elysia } from 'elysia';
function handler() { function inner() {} }
function register() { return new Elysia().get('/', handler); }
""",
    )
    assert [entry["node_id"] for entry in engine.attack_surface()] == ["app:handler"]
    assert engine.paths_between("register", "handler") == []


@pytest.mark.parametrize(
    "body",
    [
        "if (condition) handler = other; new Elysia().get('/', handler);",
        "new Elysia().get('/', wrapper(handler));",
        "new Elysia().get('/', handlers.handler);",
        "new Elysia().get(...args, handler);",
        "new Elysia().group('/v1', app => app.get('/', handler));",
        "new Elysia().guard({}, app => app.get('/', handler));",
    ],
)
def test_unsupported_bindings_do_not_promote_unrelated_handler(tmp_path: Path, body: str) -> None:
    engine = _engine(tmp_path, "import { Elysia } from 'elysia'; function handler() {}\n" + body)
    assert engine.attack_surface() == []


def test_cycle_is_bounded(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        """
import { Elysia } from 'elysia';
const a = b; const b = a;
new Elysia().get('/', a);
""",
    )
    assert engine.attack_surface() == []
    assert any(f["rule"] == "elysia.route" for f in engine.entrypoint_candidates())


def test_deep_chain_is_bounded(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        "import { Elysia } from 'elysia'; new Elysia()"
        + ".use(plugin)" * 150
        + ".get('/', () => 'ok');",
    )
    assert engine.attack_surface() == []


def test_type_wrappers_and_forward_function_declaration(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        """
import { Elysia } from 'elysia';
(new Elysia() as Elysia).get('/', (handler));
function handler() {}
""",
    )
    assert [entry["node_id"] for entry in engine.attack_surface()] == ["app:handler"]


@pytest.mark.parametrize(
    "body",
    [
        "function register() { if (condition) { var Elysia = Other; } "
        "new Elysia().get('/', () => 0); }",
        "const Other = class Elysia { register() { new Elysia().get('/', () => 0); } };",
        "const app = new Elysia(); function register() { if (condition) { var app = other; } "
        "app.get('/', () => 0); }",
    ],
)
def test_function_and_class_expression_shadowing(tmp_path: Path, body: str) -> None:
    engine = _engine(tmp_path, "import { Elysia } from 'elysia';\n" + body)
    assert engine.attack_surface() == []


@pytest.mark.parametrize(
    "block",
    [
        "try {} catch (app) { app.get('/hidden', () => 0); }",
        "for (const app of values) { app.get('/hidden', () => 0); }",
        "for (let app in values) { app.get('/hidden', () => 0); }",
    ],
)
def test_block_bindings_shadow_only_inside_the_block(tmp_path: Path, block: str) -> None:
    engine = _engine(
        tmp_path,
        "import { Elysia } from 'elysia'; const app = new Elysia();\n"
        + block
        + "\napp.get('/real', () => 0);",
    )
    assert len(engine.attack_surface()) == 1
    (fact,) = [e for e in engine.entrypoint_evidence() if e["rule"] == "elysia.route"]
    assert fact["details"]["path"] == "/real"


def test_switch_bindings_do_not_escape_the_switch(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        """
import { Elysia } from 'elysia';
const app = new Elysia();
switch (value) { case 0: const app = other; app.get('/hidden', () => 0); }
app.get('/real', () => 0);
switch (value) { case 0: const inner = new Elysia(); }
inner.get('/outside', () => 0);
""",
    )
    assert len(engine.attack_surface()) == 1
    (fact,) = [e for e in engine.entrypoint_evidence() if e["rule"] == "elysia.route"]
    assert fact["details"]["path"] == "/real"


@pytest.mark.parametrize("operator", ["of", "in"])
def test_loop_assignment_invalidates_handler_binding(tmp_path: Path, operator: str) -> None:
    engine = _engine(
        tmp_path,
        f"""
import {{ Elysia }} from 'elysia';
function handler() {{}}
for (handler {operator} otherHandlers) {{}}
new Elysia().get('/', handler);
""",
    )
    assert engine.attack_surface() == []
    (fact,) = [e for e in engine.entrypoint_candidates() if e["rule"] == "elysia.route"]
    assert fact["binding"] == "unresolved"
