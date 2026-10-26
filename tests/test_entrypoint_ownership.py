"""Entrypoint markers must belong to the declaration being tagged."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from trailmark.models.annotations import AssetValue, TrustLevel
from trailmark.query.api import QueryEngine


@pytest.mark.parametrize(
    ("language", "extension", "source", "expected"),
    [
        (
            "python",
            "py",
            'def before(): pass\n@app.get("/")\ndef route(): pass\ndef helper(): pass\n',
            "app:route",
        ),
        (
            "rust",
            "rs",
            '#[get("/")]\nfn route() {}\nfn helper() {}',
            "app:route",
        ),
        (
            "javascript",
            "js",
            'class X {\n before() {}\n @Get("/")\n route() {}\n helper() {}\n}',
            "app:X.route",
        ),
        (
            "typescript",
            "tsx",
            'class X {\n before() {}\n @Get("/")\n route() {}\n helper() {}\n}',
            "app:X.route",
        ),
        (
            "java",
            "java",
            'class X {\n void before() {}\n @GetMapping("/")\n'
            " void route() {}\n void helper() {}\n}",
            "app:X.route",
        ),
        (
            "c_sharp",
            "cs",
            "class X {\n void Before() {}\n [HttpGet]\n void Route() {}\n void Helper() {}\n}",
            "app:X.Route",
        ),
        (
            "php",
            "php",
            '<?php\nclass X {\n function before() {}\n #[Route("/")]\n'
            " function route() {}\n function helper() {}\n}",
            "app:X.route",
        ),
        (
            "cairo",
            "cairo",
            "fn before() {}\n#[external(v0)]\nfn route() {}\nfn helper() {}",
            "app:route",
        ),
        (
            "kotlin",
            "kt",
            'class X {\n fun before() {}\n @GetMapping("/")\n fun route() {}\n fun helper() {}\n}',
            "app:X.route",
        ),
        (
            "dart",
            "dart",
            "void before() {}\n@pragma('vm:entry-point')\nvoid route() {}\nvoid helper() {}",
            "app:route",
        ),
    ],
)
def test_attributes_do_not_tag_neighbouring_functions(
    tmp_path: Path, language: str, extension: str, source: str, expected: str
) -> None:
    if language == "python":
        source = "from flask import Flask\napp = Flask(__name__)\n" + source
    (tmp_path / f"app.{extension}").write_text(source)
    engine = QueryEngine.from_directory(str(tmp_path), language=language)
    assert {entry["node_id"] for entry in engine.attack_surface()} == {expected}


@pytest.mark.parametrize(
    "source",
    [
        '@app.get("/")\nclass Routes:\n    def helper(self): pass\n',
        'def outer():\n    @app.get("/")\n    def nested(): pass\n',
        'def helper():\n    text = """\n@app.get("/")\n"""\n',
    ],
)
def test_python_does_not_inherit_markers_from_class_or_body(tmp_path: Path, source: str) -> None:
    source = "from flask import Flask\napp = Flask(__name__)\n" + source
    (tmp_path / "app.py").write_text(source)
    engine = QueryEngine.from_directory(str(tmp_path), language="python")
    # Nested decorators belong only to the nested declaration.
    expected = {"app:outer.nested"} if "def nested" in source else set()
    assert {entry["node_id"] for entry in engine.attack_surface()} == expected


def test_python_multiline_decorator_and_long_stack(tmp_path: Path) -> None:
    source = '@app.get(\n    "/",\n)\n' + "@wrap\n" * 15 + "def route(): pass\n"
    source = "from flask import Flask\napp = Flask(__name__)\n" + source
    (tmp_path / "app.py").write_text(source)
    engine = QueryEngine.from_directory(str(tmp_path), language="python")
    assert {entry["node_id"] for entry in engine.attack_surface()} == {"app:route"}


@pytest.mark.parametrize(
    ("language", "extension", "source", "expected"),
    [
        ("java", "java", '@GetMapping("/") void route() {} void helper() {}', "app:X.route"),
        ("c_sharp", "cs", "[Authorize, HttpGet] void Route() {} void Helper() {}", "app:X.Route"),
        (
            "php",
            "php",
            '#[Audit, Route("/")] function route() {} function helper() {}',
            "app:X.route",
        ),
    ],
)
def test_inline_attributes_and_attribute_groups(
    tmp_path: Path, language: str, extension: str, source: str, expected: str
) -> None:
    prefix = "<?php\n" if language == "php" else ""
    (tmp_path / f"app.{extension}").write_text(f"{prefix}class X {{ {source} }}")
    engine = QueryEngine.from_directory(str(tmp_path), language=language)
    assert {entry["node_id"] for entry in engine.attack_surface()} == {expected}


@pytest.mark.parametrize("comment", ['/**\n * @Route("/")\n */', '/** @Route("/") */'])
def test_php_legacy_route_doccomment_belongs_to_next_method(tmp_path: Path, comment: str) -> None:
    (tmp_path / "app.php").write_text(
        f"<?php\nclass X {{\n{comment}\nfunction route() {{}}\nfunction helper() {{}}\n}}"
    )
    engine = QueryEngine.from_directory(str(tmp_path), language="php")
    assert {entry["node_id"] for entry in engine.attack_surface()} == {"app:X.route"}


@pytest.mark.parametrize(("kind", "modifier"), [("struct", "static"), ("class", "class")])
def test_swift_main_uses_owning_type_not_line_distance(
    tmp_path: Path, kind: str, modifier: str
) -> None:
    (tmp_path / "app.swift").write_text(
        f"@main\n{kind} App {{\n    func helper() {{}}\n"
        + "\n" * 20
        + f"    {modifier} func main() {{}}\n}}\nstruct Other {{ static func main() {{}} }}\n"
    )
    engine = QueryEngine.from_directory(str(tmp_path), language="swift")
    surface = {entry["node_id"]: entry for entry in engine.attack_surface()}
    assert set(surface) == {"app:App.main", "app:Other.main"}
    assert surface["app:App.main"]["trust_level"] == TrustLevel.UNTRUSTED_EXTERNAL.value
    assert surface["app:Other.main"]["trust_level"] == TrustLevel.TRUSTED_INTERNAL.value


def test_circom_main_is_an_untrusted_taint_source(tmp_path: Path) -> None:
    (tmp_path / "circuit.circom").write_text(
        dedent("""\
        pragma circom 2.0.0;
        template Check() {
            signal input value;
            signal output result;
            result <== value;
        }
        component main {public [value]} = Check();
        """)
    )
    engine = QueryEngine.from_directory(str(tmp_path), language="circom")
    surface = engine.attack_surface()
    assert len(surface) == 1
    assert surface[0]["node_id"] == "circuit:main"
    assert surface[0]["trust_level"] == TrustLevel.UNTRUSTED_EXTERNAL.value
    assert surface[0]["asset_value"] == AssetValue.HIGH.value
    engine.preanalysis()
    assert {node["id"] for node in engine.subgraph("tainted")} == {"circuit:main", "circuit:Check"}


def test_circom_plain_main_function_is_not_a_circuit(tmp_path: Path) -> None:
    (tmp_path / "helper.circom").write_text("function main() { return 1; }\n")
    engine = QueryEngine.from_directory(str(tmp_path), language="circom")
    assert [entry["trust_level"] for entry in engine.attack_surface()] == [
        TrustLevel.TRUSTED_INTERNAL.value
    ]


@pytest.mark.parametrize(
    ("language", "extension", "source"),
    [
        ("rust", "rs", 'fn helper() {} pub extern "C" fn exposed() {}'),
        ("rust", "rs", 'pub extern\n"C" fn exposed() {}\nfn helper() {}'),
        ("rust", "rs", 'pub unsafe extern "C" fn exposed() {}\nfn helper() {}'),
        ("cpp", "cpp", 'void helper() {} extern "C" void exposed() {}'),
        ("cpp", "cpp", 'extern "C" void exposed() {}\nvoid helper() {}'),
        ("cpp", "cpp", 'extern "C" { void exposed() {} }\nvoid helper() {}'),
        ("c", "h", '__attribute__((visibility("default")))\nvoid exposed() {}\nvoid helper() {}'),
        ("c", "c", "__declspec(dllexport)\nvoid exposed() {}\nvoid helper() {}"),
        (
            "c",
            "c",
            'void helper() { const char *s = "__declspec(dllexport)"; }\n'
            "__declspec(dllexport) void exposed() {}",
        ),
        (
            "cpp",
            "cpp",
            'void helper(const char *text = R"(extern "C")") {}\nextern "C" void exposed() {}',
        ),
        (
            "cpp",
            "cpp",
            'extern "C" { struct Box { void method() {} }; void exposed() {} }',
        ),
        (
            "cpp",
            "cpp",
            "struct Box { void method(); };\n"
            'extern "C" { void Box::method() {} void exposed() {} }',
        ),
        (
            "cpp",
            "cpp",
            'void exposed() __attribute__((visibility("default"))) {}\nvoid helper() {}',
        ),
        (
            "cpp",
            "cpp",
            'extern "C" { static void helper() {} void exposed() { helper(); } }',
        ),
    ],
)
def test_export_markers_belong_to_the_declaration(
    tmp_path: Path, language: str, extension: str, source: str
) -> None:
    (tmp_path / f"app.{extension}").write_text(source)
    engine = QueryEngine.from_directory(str(tmp_path), language=language)
    assert {entry["node_id"] for entry in engine.attack_surface()} == {"app:exposed"}


@pytest.mark.parametrize("macro", ["verus", "verus::verus"])
@pytest.mark.parametrize("marker", ["#[no_mangle]", '#[get("/")]'])
def test_rust_attributes_inside_verus_blocks(tmp_path: Path, macro: str, marker: str) -> None:
    (tmp_path / "app.rs").write_text(
        f"{macro}! {{\n{marker}\nfn exposed() {{}}\nfn helper() {{}}\n}}\n"
    )
    engine = QueryEngine.from_directory(str(tmp_path), language="rust")
    assert {entry["node_id"] for entry in engine.attack_surface()} == {"app:exposed"}


def test_rust_ffi_signature_inside_verus_block(tmp_path: Path) -> None:
    (tmp_path / "app.rs").write_text(
        'verus! {\npub extern "C" fn exposed() {}\nfn helper() {}\n}\n'
    )
    engine = QueryEngine.from_directory(str(tmp_path), language="rust")
    assert {entry["node_id"] for entry in engine.attack_surface()} == {"app:exposed"}


@pytest.mark.parametrize(
    "field",
    [
        "kind",
        "trust",
        "asset_value",
        "node",
        "file_glob",
        "param_type",
        "name_regex",
        "description",
    ],
)
@pytest.mark.parametrize("value", ["[]", "{}", "123", "true"])
def test_mistyped_overrides_skip_the_whole_entry(tmp_path: Path, field: str, value: str) -> None:
    (tmp_path / "app.py").write_text("def route(): pass\ndef helper(): pass\n")
    config = tmp_path / ".trailmark"
    config.mkdir()
    selector = 'file_glob = "*.py"' if field == "name_regex" else 'name_regex = ".*"'
    (config / "entrypoints.toml").write_text(
        f"[[entrypoint]]\n{selector}\n{field} = {value}\n"
        '[[entrypoint]]\nnode = "app:helper"\ndescription = "valid entry"\n'
    )
    engine = QueryEngine.from_directory(str(tmp_path), language="python")
    assert [(entry["node_id"], entry["description"]) for entry in engine.attack_surface()] == [
        ("app:helper", "valid entry")
    ]
