"""Registration identity, provenance and conservative framework bindings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from trailmark.models.endpoints import EndpointRegistration, HandlerBinding, RegistrationEvidence
from trailmark.models.graph import CodeGraph
from trailmark.query.api import QueryEngine
from trailmark.storage.graph_store import GraphStore


def _write(root: Path, files: dict[str, str]) -> None:
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)


def _inventory(engine: QueryEngine, framework: str | None = None) -> list[dict]:
    return [
        r
        for r in engine.endpoint_inventory()["registrations"]
        if framework is None or r["framework"] == framework
    ]


def test_registration_model_roundtrip_and_missing_queries() -> None:
    record = EndpointRegistration(
        "r",
        "http",
        "/source",
        (RegistrationEvidence("static"),),
        (HandlerBinding("external.handler", status="out_of_root"),),
    )
    assert EndpointRegistration.from_dict(json.loads(json.dumps(record.to_dict()))) == record
    assert record.to_dict()["enabled"] is None
    graph = CodeGraph()
    graph.add_registration(record)
    other = CodeGraph()
    other.merge(graph)
    assert other.registrations == graph.registrations
    assert GraphStore(graph).endpoint_registrations("missing") == []
    assert QueryEngine.from_graph(graph).endpoint_inventory("missing")["registrations"] == []
    with pytest.raises(ValueError, match="native"):
        EndpointRegistration(
            "r", "http", "/source", (RegistrationEvidence("static"),), enabled=True
        )
    with pytest.raises(ValueError, match="Unknown registration handler"):
        GraphStore(graph).add_registration(
            EndpointRegistration(
                "missing",
                "http",
                "/source",
                (RegistrationEvidence("static"),),
                (HandlerBinding(node_id="missing", status="resolved"),),
            )
        )


def test_python_multiple_registrations_and_explicit_unknowns(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "routes.py": "from fastapi import FastAPI\napp = FastAPI()\n"
            "@app.get('/a')\n@app.post('/b')\ndef shared(): pass\n"
        },
    )
    engine = QueryEngine.from_directory(str(tmp_path))
    records = _inventory(engine)
    assert {(r["method"], r["path"]) for r in records} == {("GET", "/a"), ("POST", "/b")}
    assert len({r["id"] for r in records}) == 2
    assert all(r["enabled"] is None for r in records)
    assert len(engine.attack_surface()) == 1
    assert records == _inventory(QueryEngine.from_directory(str(tmp_path)))


def test_medusa_and_unknown_file_conventions(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "src/api/store/products/[id]/route.ts": 'import { MedusaRequest } from "@medusajs'
            '/framework/http";\n'
            "export const GET = async (req: MedusaRequest) => {};\n",
            "fixtures/route.ts": "export function GET() {}\n",
        },
    )
    engine = QueryEngine.from_directory(str(tmp_path), language="typescript")
    medusa = _inventory(engine, "medusa")
    assert [(r["method"], r["path"]) for r in medusa] == [("GET", "/store/products/:id")]
    assert medusa[0]["enabled"] is None
    descriptions = {r["description"] for r in engine.attack_surface()}
    assert descriptions == {"Medusa API handler", "File route handler (framework unknown)"}


def test_next_framework_requires_import_or_package_evidence(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "app/route.ts": "// import { NextRequest } from 'next/server';\n"
            "export function GET() {}\n",
            "pages/api/items.ts": "export default function handler() {}\n",
        },
    )
    engine = QueryEngine.from_directory(str(tmp_path), "auto")
    assert all(r["framework"] is None for r in _inventory(engine))
    assert {r["description"] for r in engine.attack_surface()} == {
        "File route handler (framework unknown)"
    }
    _write(tmp_path, {"package.json": '{"dependencies": {"next": "15"}}'})
    engine = QueryEngine.from_directory(str(tmp_path), "auto")
    record = _inventory(engine, "nextjs")[0]
    assert record["framework"] == "nextjs"
    assert record["path"] == "/"
    assert record["enabled"] is None
    assert {r["description"] for r in engine.attack_surface()} == {
        "Next.js App Router handler",
        "Next.js Pages API handler",
    }


def test_nested_django_namespaces_cbv_and_shared_handler(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "urls.py": "from django.urls import path, include\nurlpatterns = ["
            "path('shop/', include('shop.urls', namespace='shop')),"
            "path('other/', include('shop.urls', namespace='other'))]\n",
            "shop/__init__.py": "",
            "shop/urls.py": "from django.urls import path\nfrom .views import shared, Login\n"
            "urlpatterns = [path('one/', shared, name='one'), path('two/', shared),"
            "path('login/', Login.as_view())]\n",
            "shop/views.py": "def shared(): pass\nclass Login:\n def dis"
            "patch(self, request): pass\n",
        },
    )
    engine = QueryEngine.from_directory(str(tmp_path))
    records = _inventory(engine, "django")
    assert len(records) == 6
    assert {r["namespace"] for r in records} == {"shop", "other"}
    assert {r["path"] for r in records} == {
        "shop/one/",
        "shop/two/",
        "shop/login/",
        "other/one/",
        "other/two/",
        "other/login/",
    }
    assert all(r["bindings"][0]["status"] == "resolved" for r in records)
    assert len(engine.attack_surface()) == 2


def test_native_django_keeps_out_of_root_and_shared_rows(tmp_path: Path) -> None:
    _write(tmp_path, {"views.py": "def shared(): pass\n"})
    engine = QueryEngine.from_directory(str(tmp_path))
    native = tmp_path / "native.json"
    rows: list[dict[str, Any]] = [
        {
            "path_pattern": path,
            "handler": "views.shared",
            "file": str(tmp_path / "views.py"),
            "line": 1,
            "methods": None,
            "name": None,
        }
        for path in ("one/", "two/")
    ]
    rows.append(
        {
            "path_pattern": "external/",
            "handler": "outside.view",
            "file": "/outside/views.py",
            "line": 5,
            "methods": ["POST"],
        }
    )
    native.write_text(json.dumps({"routes": rows}))
    assert engine.import_native_registrations(str(native), framework="django") == 3
    records = _inventory(engine)
    assert len(records) == 3
    assert sum(r["bindings"][0]["status"] == "resolved" for r in records) == 2
    assert sum(r["bindings"][0]["status"] == "out_of_root" for r in records) == 1
    assert all(r["enabled"] is True for r in records)
    assert engine.import_native_registrations(str(native), framework="django") == 3
    assert len(_inventory(engine)) == 3


@pytest.mark.parametrize(
    ("framework", "payload"),
    [
        ("django", {"routes": [{"path_pattern": "/ok"}, {"name": ["bad"]}]}),
        ("symfony", {"ok": {"path": "/ok"}, "bad": {"defaults": {"_controller": ["bad"]}}}),
        ("magento", [{"path": "/ok"}, {"service": {"class": "MissingMethod"}}]),
        ("medusa", {"routes": [{"matcher": "/ok"}, {"handler": ["bad"]}]}),
    ],
)
def test_invalid_native_snapshot_does_not_partially_import(
    tmp_path: Path, framework: str, payload: object
) -> None:
    engine = QueryEngine.from_graph(CodeGraph())
    native = tmp_path / "native.json"
    native.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="Native"):
        engine.import_native_registrations(str(native), framework=framework)
    assert _inventory(engine) == []


def test_symfony_yaml_xml_imports_and_service_binding(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "config/routes.yaml": "shop:\n  resource: child.xml\n  prefix: /shop\n",
            "config/child.xml": '<routes xmlns="http://symfony.com/schema/routing">'
            '<route id="login" path="/login" methods="POST"><default key="_controller">'
            "login_controller::login</default></route></routes>",
            "config/services.yaml": "services:\n  login_controller:\n    class:"
            " App\\Controller\\Account\n",
            "src/Account.php": "<?php namespace App\\Controller; class Ac"
            "count { public function login() {} }",
        },
    )
    records = _inventory(QueryEngine.from_directory(str(tmp_path), "php"), "symfony")
    assert len(records) == 1
    assert records[0]["path"] == "/shop/login"
    assert records[0]["methods"] == ["POST"]
    assert records[0]["bindings"][0]["status"] == "resolved"
    assert "routes.yaml" in records[0]["evidence"][0]["detail"]


def test_api_platform_explicit_operations_keep_unknown_dispatch(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "config/api.yaml": "resources:\n  App\\Entity\\Product:\n    operations:\n"
            "      ApiPlatform\\Metadata\\GetCollection:\n        uriTemplate: /products\n"
            "      ApiPlatform\\Metadata\\Post: ~\n"
        },
    )
    engine = QueryEngine.from_directory(str(tmp_path), "php", allow_empty=True)
    records = _inventory(engine, "api-platform")
    assert len(records) == 1
    assert records[0]["bindings"][0]["status"] == "unresolved"
    assert (
        engine.endpoint_inventory()["diagnostics"][0]["reason"] == "unsupported_generated_operation"
    )


def test_magento_rest_preferences_controller_and_acl(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "app/etc/config.php": "<?php return ['modules' => ['Demo_Shop' => 1]];",
            "app/code/Demo/Shop/etc/module.xml": '<config><module name="Demo_Shop"/></config>',
            "app/code/Demo/Shop/etc/webapi.xml": '<routes><route url="/V1/orders" method="POST">'
            '<service class="Demo\\Shop\\Api\\Orders" method="save"/>'
            '<resources><resource ref="Demo_Shop::orders"/></resources></route></routes>',
            "app/code/Demo/Shop/etc/di.xml": '<config><preference for="Demo\\Shop\\Api\\Orders" '
            'type="Demo\\Shop\\Model\\Orders"/></config>',
            "app/code/Demo/Shop/etc/frontend/routes.xml": '<config><router id="standard">'
            '<route id="shop" frontName="shop"><modul'
            'e name="Demo_Shop"/></route></router></c'
            "onfig>",
            "app/code/Demo/Shop/Model/Orders.php": "<?php namespace Demo\\Shop\\Model; "
            "class Orders { public function save() {} }",
            "app/code/Demo/Shop/Controller/Order/Crea"
            "te.php": "<?php namespace Demo\\Shop\\Controller\\Ord"
            "er; "
            "class Create implements \\Magento\\Framework\\App\\Action\\HttpPostActionInterface "
            "{ public function execute() {} }",
        },
    )
    records = _inventory(QueryEngine.from_directory(str(tmp_path), "php"), "magento")
    assert {(r["path"], r["method"]) for r in records} == {
        ("/V1/orders", "POST"),
        ("/shop/order/create", "POST"),
    }
    assert all(r["bindings"][0]["status"] == "resolved" for r in records)
    assert any(r["acl"] == ["Demo_Shop::orders"] for r in records)
    assert all(r["enabled"] is None for r in records)
    assert all(r["declared_enabled"] is True for r in records)


def test_api_platform_xml_and_unsafe_xml_diagnostics(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "api.xml": '<resources xmlns="https://api-platform.com/schema/metadata/resources-3.0">'
            '<resource class="App\\Product"><operations><operation '
            'class="ApiPlatform\\Metadata\\Get" uriTemplate="/products/{id}" '
            'controller="App\\Show"/>'
            "</operations></resource></resources>",
            "Show.php": "<?php namespace App; class Show { function __invoke() {} }",
            "evil.xml": '<!DOCTYPE routes [<!ENTITY x "oops">]><routes>&x;</routes>',
        },
    )
    engine = QueryEngine.from_directory(str(tmp_path), "php")
    record = _inventory(engine, "api-platform")[0]
    assert (record["path"], record["method"]) == ("/products/{id}", "GET")
    assert record["bindings"][0]["status"] == "resolved"
    assert any(
        d["reason"] == "invalid_registration_input"
        for d in engine.endpoint_inventory()["diagnostics"]
    )


def test_native_ids_survive_snapshot_copy_and_reordering(tmp_path: Path) -> None:
    _write(tmp_path, {"views.py": "def one(): pass\ndef two(): pass\n"})
    engine = QueryEngine.from_directory(str(tmp_path))
    rows = [{"handler": f"views.{name}", "path_pattern": name + "/"} for name in ("one", "two")]
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    first.write_text(json.dumps({"routes": rows}))
    second.write_text(json.dumps({"routes": rows[::-1]}))
    engine.import_native_registrations(str(first), framework="django")
    engine.import_native_registrations(str(second), framework="django")
    assert len(_inventory(engine)) == 2
    second.write_text(json.dumps({"routes": [*rows, {"methods": "invalid"}]}))
    before = engine.endpoint_inventory()
    with pytest.raises(ValueError, match="methods"):
        engine.import_native_registrations(str(second), framework="django")
    assert engine.endpoint_inventory() == before


def test_native_python_decorator_line_crosswalk_is_exact(tmp_path: Path) -> None:
    _write(tmp_path, {"views.py": "@decorate\ndef view(): pass\n"})
    snapshot = tmp_path / "routes.json"
    snapshot.write_text(
        json.dumps(
            {
                "routes": [
                    {
                        "handler": "views.view",
                        "file": str(tmp_path / "views.py"),
                        "line": 1,
                    }
                ]
            }
        )
    )
    engine = QueryEngine.from_directory(str(tmp_path))
    engine.import_native_registrations(str(snapshot), framework="django")
    binding = _inventory(engine)[0]["bindings"][0]
    assert binding["status"] == "resolved"
    assert binding["location"]["start_line"] == 2


def test_symfony_php_configurator_aliases_and_native_reconciliation(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "controller.php": "<?php namespace App; class Contact { function __invoke() {} }",
            "services.php": "<?php namespace Symfony\\Component\\DependencyInjection"
            "\\Loader\\Configurator; use App\\Contact as Handler; "
            "return static function (ContainerConfigurator $container) { "
            "$services=$container->services(); $services->set('app.contact', Handler::class); };",
            "routes.yaml": "contact:\n  path: /contact\n  controller: app.contact\n",
        },
    )
    engine = QueryEngine.from_directory(str(tmp_path), "php")
    assert _inventory(engine)[0]["bindings"][0]["status"] == "resolved"
    snapshot = tmp_path / "routes.json"
    snapshot.write_text(
        json.dumps(
            {
                "contact": {
                    "path": "/contact",
                    "defaults": {"_controller": "app.contact"},
                }
            }
        )
    )
    engine.import_native_registrations(str(snapshot), framework="symfony")
    assert all(r["bindings"][0]["status"] == "resolved" for r in _inventory(engine))
    # The same fluent API without a typed Symfony container is insufficient.
    services = tmp_path / "services.php"
    services.write_text(
        services.read_text().replace("ContainerConfigurator $container", "$container")
    )
    assert (
        _inventory(QueryEngine.from_directory(str(tmp_path), "php"))[0]["bindings"][0]["status"]
        == "unresolved"
    )
