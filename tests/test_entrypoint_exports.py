"""Export binding facts derived from ECMAScript and CommonJS module syntax."""

from pathlib import Path

import pytest

from trailmark.analysis._entrypoint_declarations import DeclarationCache
from trailmark.models.nodes import NodeKind
from trailmark.parse import parse_file

_SUFFIXES = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")


def _exports(tmp_path: Path, filename: str, source: str) -> dict[str, list[tuple[str, bool]]]:
    """Map each parsed callable (ID without module prefix) to (name, indirect) facts."""
    path = tmp_path / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    cache = DeclarationCache()
    return {
        unit.id.split(":", 1)[1]: [
            (fact.name, fact.indirect) for fact in cache.module_exports(unit)
        ]
        for unit in parse_file(str(path)).nodes.values()
        if unit.kind in {NodeKind.FUNCTION, NodeKind.METHOD}
    }


def _lambda(exports: dict[str, list[tuple[str, bool]]]) -> list[tuple[str, bool]]:
    (facts,) = [facts for unit_id, facts in exports.items() if "$lambda" in unit_id]
    return facts


@pytest.mark.parametrize("suffix", _SUFFIXES)
def test_alias_exports_the_alias_not_the_local_name(tmp_path: Path, suffix: str) -> None:
    exports = _exports(
        tmp_path,
        f"route{suffix}",
        "function internal() {}\nfunction GET() {}\nexport { internal as GET, GET as other };\n",
    )
    assert exports["internal"] == [("GET", False)]
    assert exports["GET"] == [("other", False)]


@pytest.mark.parametrize("suffix", _SUFFIXES)
def test_direct_export_declarations(tmp_path: Path, suffix: str) -> None:
    exports = _exports(
        tmp_path,
        f"route{suffix}",
        "export function GET() {}\n"
        "export const POST = async () => {};\n"
        "export let PUT = function () {}, unused = 1;\n"
        "export async function* stream() {}\n"
        "function hidden() {}\n",
    )
    assert exports["GET"] == [("GET", False)]
    assert exports["POST"] == [("POST", False)]
    assert exports["PUT"] == [("PUT", False)]
    assert exports["stream"] == [("stream", False)]
    assert exports["hidden"] == []


@pytest.mark.parametrize("suffix", _SUFFIXES)
def test_default_export_of_identifier_restores_legacy_api_handler(
    tmp_path: Path, suffix: str
) -> None:
    exports = _exports(
        tmp_path,
        f"pages/api/user{suffix}",
        "async function handler(req, res) {}\nexport default handler;\n",
    )
    assert exports["handler"] == [("default", False)]


@pytest.mark.parametrize(
    ("source", "unit_id"),
    [
        ("export default function GET() {}\n", "GET"),
        ("export default function () {}\n", "default"),
        ("function internal() {}\nexport { internal as default };\n", "internal"),
    ],
)
def test_default_exported_functions(tmp_path: Path, source: str, unit_id: str) -> None:
    assert _exports(tmp_path, "mod.ts", source)[unit_id] == [("default", False)]


@pytest.mark.parametrize(
    "source", ["export default async () => {};\n", "export default (async (req) => req);\n"]
)
def test_default_exported_arrow(tmp_path: Path, source: str) -> None:
    assert _exports(tmp_path, "mod.js", source) == {"default": [("default", False)]}


def test_string_export_name(tmp_path: Path) -> None:
    exports = _exports(
        tmp_path, "mod.mjs", 'function internal() {}\nexport { internal as "GET" };\n'
    )
    assert exports["internal"] == [("GET", False)]


def test_nested_functions_and_methods_do_not_inherit_exports(tmp_path: Path) -> None:
    exports = _exports(
        tmp_path,
        "route.ts",
        "export function GET() {\n"
        "  function POST() {}\n"
        "  const PUT = () => {};\n"
        "  function internal() {}\n"
        "}\n"
        "export class Api { DELETE() {} }\n"
        "export default { PATCH() {} };\n"
        "function internal() {}\n"
        "export { internal as HEAD };\n",
    )
    assert exports["GET"] == [("GET", False)]
    assert exports["GET.POST"] == []
    assert exports["GET.PUT"] == []
    assert exports["GET.internal"] == []
    assert exports["internal"] == [("HEAD", False)]
    assert exports["Api.DELETE"] == []
    assert [facts for unit_id, facts in exports.items() if unit_id.endswith("PATCH")] == [[]]


def test_comments_strings_and_property_names_are_not_exports(tmp_path: Path) -> None:
    exports = _exports(
        tmp_path,
        "route.js",
        "function GET() {}\n"
        "// export { GET };\n"
        "/* module.exports = GET; */\n"
        'const text = "export default GET";\n'
        "const api = { exports: {} };\n"
        "api.exports.GET = GET;\n"
        "api.module.exports = GET;\n"
        "function setup() { module.exports = GET; exports.GET = GET; }\n",
    )
    assert exports["GET"] == []
    assert exports["setup"] == []


@pytest.mark.parametrize(
    "source",
    [
        "async function handler() {}\nexport default withAuth(handler);\n",
        "async function handler() {}\nexport default outer(inner(handler, options));\n",
        "async function handler() {}\nmodule.exports = withAuth(handler);\n",
    ],
)
def test_wrapped_reference_is_indirect(tmp_path: Path, source: str) -> None:
    exports = _exports(tmp_path, "pages/api/user.js", source)
    assert exports["handler"] == [("default", True)]


@pytest.mark.parametrize(
    "source",
    [
        "export default withAuth(async (req) => req);\n",
        "const handler = withAuth(async (req) => req);\nexport default handler;\n",
        "export const handler = withAuth(async (req) => req);\n",
    ],
)
def test_wrapped_callable_expression_is_indirect(tmp_path: Path, source: str) -> None:
    name = "handler" if source.startswith("export const") else "default"
    assert _lambda(_exports(tmp_path, "pages/api/user.ts", source)) == [(name, True)]


def test_commonjs_direct_bindings(tmp_path: Path) -> None:
    exports = _exports(
        tmp_path,
        "api.cjs",
        "function handler() {}\n"
        "function internal() {}\n"
        "function POST() {}\n"
        "module.exports = handler;\n"
        "exports.GET = internal;\n"
        "module.exports.POST = POST;\n"
        "exports.PUT = function named() {};\n",
    )
    assert exports["handler"] == [("default", False)]
    assert exports["internal"] == [("GET", False)]
    assert exports["POST"] == [("POST", False)]
    assert exports["PUT"] == [("PUT", False)]


def test_commonjs_object_literal(tmp_path: Path) -> None:
    exports = _exports(
        tmp_path,
        "api.js",
        "function GET() {}\n"
        "function internal() {}\n"
        "module.exports = { GET, POST: internal, DELETE() {}, PUT: () => {} };\n",
    )
    assert exports["GET"] == [("GET", False)]
    assert exports["internal"] == [("POST", False)]
    delete, put = (
        facts for unit_id, facts in sorted(exports.items()) if unit_id.startswith("module.exports")
    )
    assert delete == [("DELETE", False)]
    assert put == [("PUT", False)]


def test_typescript_export_assignment(tmp_path: Path) -> None:
    exports = _exports(tmp_path, "api.ts", "function handler() {}\nexport = handler;\n")
    assert exports["handler"] == [("default", False)]


def test_reexports_do_not_export_unrelated_local_function(tmp_path: Path) -> None:
    exports = _exports(
        tmp_path,
        "route.ts",
        "function GET() {}\n"
        "function handler() {}\n"
        'export { GET } from "./other";\n'
        'export { handler as default } from "./other";\n'
        'export * from "./all";\n'
        'export * as POST from "./ns";\n',
    )
    assert exports["GET"] == []
    assert exports["handler"] == []


def test_type_only_exports_do_not_expose_runtime_function(tmp_path: Path) -> None:
    exports = _exports(
        tmp_path,
        "route.ts",
        "function GET() {}\n"
        "function POST() {}\n"
        "function PUT() {}\n"
        "export type { GET };\n"
        "export { type POST as DELETE };\n"
        "export { type PUT, PUT as PATCH };\n",
    )
    assert exports["GET"] == []
    assert exports["POST"] == []
    assert exports["PUT"] == [("PATCH", False)]


def test_tsx_jsx_does_not_disturb_export_lookup(tmp_path: Path) -> None:
    exports = _exports(
        tmp_path,
        "route.tsx",
        'const view = () => <div className="x">{"y"}</div>;\n'
        "function internal() {}\n"
        "export { internal as GET };\n",
    )
    assert exports["internal"] == [("GET", False)]
    assert exports["view"] == []


def test_fact_location_is_export_site_and_bool_wrapper(tmp_path: Path) -> None:
    path = tmp_path / "route.js"
    path.write_text("function internal() {}\n\nexport { internal as GET };\n")
    cache = DeclarationCache()
    unit = parse_file(str(path)).nodes["route:internal"]
    (fact,) = cache.module_exports(unit)
    assert (fact.location.file_path, fact.location.start_line) == (str(path), 3)
    assert (fact.location.start_col, fact.location.end_col) == (9, 24)
    assert cache.is_module_export(unit)
