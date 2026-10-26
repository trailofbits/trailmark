"""Schema identity is distinct from proven, namespaced resolver wiring."""

from pathlib import Path

from trailmark.query.api import QueryEngine


def _write(root: Path, name: str, text: str) -> None:
    file = root / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text)


def test_graphqls_merges_fragments_and_binds_magento_resolver(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "one/schema.graphqls",
        'type Query { customer: String @resolver(class: "Demo\\\\Model\\\\Customer") }\n',
    )
    _write(tmp_path, "two/schema.graphqls", "extend type Query { health: String }\n")
    _write(
        tmp_path,
        "Model/Customer.php",
        "<?php namespace Demo\\Model; class Customer { public function resolve() {} }",
    )
    engine = QueryEngine.from_directory(str(tmp_path), "auto")
    records = engine.endpoint_inventory()["registrations"]
    operations = {r["operation_name"]: r for r in records if r["kind"] == "graphql"}
    assert set(operations) == {"customer", "health"}
    assert operations["customer"]["bindings"][0]["status"] == "resolved"
    assert operations["customer"]["transport"] is None
    assert operations["customer"]["enabled"] is None


def test_nest_provider_wiring_preserves_service_namespaces(tmp_path: Path) -> None:
    for namespace in ("shop", "admin"):
        _write(
            tmp_path, f"schema/{namespace}-api/schema.graphql", "type Query { shared: String }\n"
        )
        _write(
            tmp_path,
            f"{namespace}.resolver.ts",
            "import { Query, Resolver } from '@nestjs/graphql';\n"
            f"@Resolver() export class {namespace.title()}Resolver {{\n"
            " @Query()\n shared() { return 'ok'; }\n}\n",
        )
    _write(
        tmp_path,
        "api.ts",
        "import { Module } from '@nestjs/common';\n"
        "import { ShopResolver } from './shop.resolver';\n"
        "import { AdminResolver } from './admin.resolver';\n"
        "@Module({providers:[ShopResolver]}) export class ShopModule {}\n"
        "@Module({providers:[AdminResolver]}) export class AdminModule {}\n"
        "configureGraphQLModule(x => ({apiType:'shop',resolverModule:ShopModule}));\n"
        "configureGraphQLModule(x => ({apiType:'admin',resolverModule:AdminModule}));\n",
    )
    engine = QueryEngine.from_directory(str(tmp_path), "auto")
    records = [r for r in engine.endpoint_inventory()["registrations"] if r["kind"] == "graphql"]
    assert len(records) == 2
    assert {r["namespace"] for r in records} == {"shop-api", "admin-api"}
    for record in records:
        assert len(record["bindings"]) == 1
        binding = record["bindings"][0]
        assert binding["status"] == "resolved"
        assert record["namespace"].split("-")[0] in binding["location"]["file_path"]
        assert len(record["transport_registrations"]) == 1


def test_unregistered_or_unimported_query_method_is_not_execution(tmp_path: Path) -> None:
    _write(tmp_path, "schema.graphql", "type Query { publicProduct: String }\n")
    _write(tmp_path, "product.ts", "class ProductResolver {\n @Query()\n publicProduct() {}\n}\n")
    engine = QueryEngine.from_directory(str(tmp_path), "auto")
    record = next(r for r in engine.endpoint_inventory()["registrations"] if r["kind"] == "graphql")
    assert not any(b["status"] == "resolved" for b in record["bindings"])


def test_graphene_registered_schema_and_field_owner(tmp_path: Path) -> None:
    _write(tmp_path, "schema.graphql", "type Query { product: String }\n")
    _write(
        tmp_path,
        "schema.py",
        "import graphene\nclass Products:\n"
        " product = graphene.String()\n def resolve_product(root, info): return 'p'\n"
        "class Query(Products, graphene.ObjectType): pass\n"
        "schema = graphene.Schema(query=Query)\n",
    )
    engine = QueryEngine.from_directory(str(tmp_path), "auto")
    record = next(r for r in engine.endpoint_inventory()["registrations"] if r["kind"] == "graphql")
    assert record["bindings"][0]["status"] == "resolved"
    assert record["bindings"][0]["symbol"].endswith("Products.resolve_product")


def test_graphene_transport_is_wired_to_schema_and_never_assumes_subscription(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "schema.graphql",
        "type Query { health: String }\ntype Subscription { health: String }\n",
    )
    _write(
        tmp_path,
        "schema.py",
        "import graphene\nclass Query(graphene.ObjectType):\n"
        " health = graphene.String()\n def resolve_health(root, info): pass\n"
        "schema = graphene.Schema(query=Query, subscription=Query)\n",
    )
    _write(
        tmp_path,
        "urls.py",
        "from django.urls import path\n"
        "from graphene_django.views import GraphQLView\nfrom schema import schema\n"
        "urlpatterns = [path('graphql/', GraphQLView.as_view(schema=schema))]\n",
    )
    records = QueryEngine.from_directory(str(tmp_path), "auto").endpoint_inventory()[
        "registrations"
    ]
    query = next(r for r in records if r["operation_type"] == "Query")
    subscription = next(r for r in records if r["operation_type"] == "Subscription")
    assert len(query["transport_registrations"]) == 1
    assert subscription["transport_registrations"] == []
    assert query["enabled"] is None


def test_disabled_magento_schema_and_area_transport_are_separate(tmp_path: Path) -> None:
    _write(tmp_path, "app/etc/config.php", "<?php return ['modules' => ['Demo_GraphQl' => 0]];")
    _write(
        tmp_path,
        "app/code/Demo/GraphQl/etc/module.xml",
        '<config><module name="Demo_GraphQl"/></config>',
    )
    _write(tmp_path, "app/code/Demo/GraphQl/etc/schema.graphqls", "type Query { health: String }\n")
    _write(
        tmp_path,
        "app/code/Demo/GraphQl/etc/di.xml",
        "<config>"
        '<type name="Magento\\Framework\\App\\AreaList"><arguments><argument name="areas">'
        '<item name="graphql"><item name="frontName">graphql</item></item></argument>'
        "</arguments></type></config>",
    )
    records = QueryEngine.from_directory(str(tmp_path), "auto").endpoint_inventory()[
        "registrations"
    ]
    operation = next(r for r in records if r["kind"] == "graphql")
    transport = next(r for r in records if r["kind"] == "http")
    assert operation["declared_enabled"] is False
    assert transport["declared_enabled"] is False
    assert operation["enabled"] is None
    assert operation["transport_registrations"] == [transport["id"]]


def test_vendure_selected_plugin_literal_schema_and_js_imports(tmp_path: Path) -> None:
    from trailmark.source_policy import SourcePolicy, SourceRoot

    plugin = tmp_path / "node_modules" / "dashboard"
    _write(tmp_path, "main.ts", "export function boot() {}")
    _write(
        plugin,
        "schema.ts",
        "import gql from 'graphql-tag';\n"
        "export const extension = gql`extend type Query { dashboardMetricSummary: Int }`;\n",
    )
    _write(
        plugin,
        "resolver.ts",
        "import { Query, Resolver } from '@nestjs/graphql';\n"
        "@Resolver() export class Metrics { @Query() dashboardMetricSummary() { return 1; } }",
    )
    _write(
        plugin,
        "plugin.ts",
        "import { VendurePlugin } from '@vendure/core';\n"
        "import { Metrics } from './resolver.js';\nimport { extension } from './schema.js';\n"
        "@VendurePlugin({adminApiExtensions: {schema: extension, resolvers: [Metrics]}})\n"
        "export class DashboardPlugin {}",
    )
    assert (
        QueryEngine.from_directory(str(tmp_path), "auto").endpoint_inventory()["registrations"]
        == []
    )
    policy = SourcePolicy(
        (SourceRoot(str(tmp_path), "app"), SourceRoot(str(plugin), "dashboard", "dependency"))
    )
    engine = QueryEngine.from_directory(str(tmp_path), "auto", source_policy=policy)
    record = engine.endpoint_inventory()["registrations"][0]
    assert record["operation_name"] == "dashboardMetricSummary"
    assert record["namespace"] == "admin-api"
    assert record["owning_root"] == str(plugin)
    assert record["bindings"][0]["status"] == "resolved"
    assert record["bindings"][0]["node_id"].startswith("dashboard::")
    assert record["enabled"] is None
    _write(
        plugin, "admin-api/schema.graphql", "extend type Query { dashboardMetricSummary: Int }\n"
    )
    merged = QueryEngine.from_directory(str(tmp_path), "auto", source_policy=policy)
    operations = [r for r in merged.endpoint_inventory()["registrations"] if r["kind"] == "graphql"]
    assert len(operations) == 1
    assert operations[0]["declaration_nodes"]
    assert operations[0]["bindings"][0]["status"] == "resolved"
