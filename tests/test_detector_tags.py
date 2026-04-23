"""Precise EntrypointTag assertions for framework detectors.

Existing tests check that a decorator fires detection; these lock in
every field of the resulting EntrypointTag (kind, trust_level,
asset_value, description). This kills a broad class of mutation-testing
survivors that change enum values or rewrite description strings.

Organized one class per language to make the audit trail readable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trailmark.query.api import QueryEngine


def _single_surface(tmp_path: Path, language: str) -> dict[str, object]:
    engine = QueryEngine.from_directory(str(tmp_path), language=language)
    surface = engine.attack_surface()
    assert len(surface) == 1, f"expected exactly one entrypoint, got {surface}"
    return surface[0]


def _by_name(tmp_path: Path, language: str, name: str) -> dict[str, object]:
    engine = QueryEngine.from_directory(str(tmp_path), language=language)
    matches = [ep for ep in engine.attack_surface() if name in ep["node_id"]]
    assert matches, f"no entrypoint for {name}: {engine.attack_surface()}"
    return matches[0]


class TestPythonDetectorTags:
    @pytest.mark.parametrize("verb", ["route", "get", "post", "put", "patch", "delete"])
    def test_flask_http_decorator_tag_fields(self, tmp_path: Path, verb: str) -> None:
        (tmp_path / "app.py").write_text(
            "from flask import Flask\n"
            "app = Flask(__name__)\n"
            "\n"
            f"@app.{verb}('/')\n"
            "def handler():\n"
            "    return 'ok'\n",
        )
        ep = _single_surface(tmp_path, "python")
        assert ep["kind"] == "api"
        assert ep["trust_level"] == "untrusted_external"
        assert ep["asset_value"] == "high"
        assert ep["description"] == "Python HTTP route decorator"

    def test_click_command_tag(self, tmp_path: Path) -> None:
        (tmp_path / "cli.py").write_text(
            "import click\n@click.command()\ndef run(): pass\n",
        )
        ep = _single_surface(tmp_path, "python")
        assert ep["kind"] == "user_input"
        assert ep["trust_level"] == "untrusted_external"
        assert ep["asset_value"] == "medium"
        assert ep["description"] == "Python CLI command (Click/Typer)"

    def test_celery_task_tag(self, tmp_path: Path) -> None:
        (tmp_path / "tasks.py").write_text(
            "from celery import Celery\napp = Celery()\n@app.task\ndef send(): pass\n",
        )
        ep = _single_surface(tmp_path, "python")
        assert ep["kind"] == "third_party"
        assert ep["trust_level"] == "semi_trusted_external"
        assert ep["asset_value"] == "medium"
        assert ep["description"] == "Python task queue handler (Celery)"


class TestJavaDetectorTags:
    @pytest.mark.parametrize(
        "annotation",
        [
            "GetMapping",
            "PostMapping",
            "PutMapping",
            "DeleteMapping",
            "PatchMapping",
            "RequestMapping",
        ],
    )
    def test_spring_mapping_tags(self, tmp_path: Path, annotation: str) -> None:
        (tmp_path / "C.java").write_text(
            f'class C {{\n    @{annotation}("/x")\n    public void f() {{}}\n}}\n',
        )
        ep = _by_name(tmp_path, "java", ".f")
        assert ep["kind"] == "api"
        assert ep["trust_level"] == "untrusted_external"
        assert ep["asset_value"] == "high"
        assert ep["description"] == "Spring MVC/WebFlux handler"

    @pytest.mark.parametrize("verb", ["GET", "POST", "PUT", "DELETE", "PATCH"])
    def test_jaxrs_verb_tags(self, tmp_path: Path, verb: str) -> None:
        (tmp_path / "C.java").write_text(
            f"class C {{\n    @{verb}\n    public void f() {{}}\n}}\n",
        )
        ep = _by_name(tmp_path, "java", ".f")
        assert ep["kind"] == "api"
        assert ep["description"] == "JAX-RS handler"

    def test_kafka_listener_tag(self, tmp_path: Path) -> None:
        (tmp_path / "C.java").write_text(
            'class C {\n    @KafkaListener(topics = "x")\n    public void consume() {}\n}\n',
        )
        ep = _by_name(tmp_path, "java", ".consume")
        assert ep["kind"] == "third_party"
        assert ep["trust_level"] == "semi_trusted_external"
        assert ep["asset_value"] == "medium"
        assert ep["description"] == "Kafka listener"

    @pytest.mark.parametrize(
        "method",
        ["doGet", "doPost", "doPut", "doDelete", "doHead", "doOptions", "doTrace"],
    )
    def test_servlet_method_tag(self, tmp_path: Path, method: str) -> None:
        (tmp_path / "C.java").write_text(
            f"class C extends HttpServlet {{\n    public void {method}() {{}}\n}}\n",
        )
        ep = _by_name(tmp_path, "java", f".{method}")
        assert ep["kind"] == "api"
        assert ep["trust_level"] == "untrusted_external"
        assert ep["asset_value"] == "high"
        assert ep["description"] == "HttpServlet method"


class TestRustDetectorTags:
    @pytest.mark.parametrize("verb", ["get", "post", "put", "patch", "delete", "head", "options"])
    def test_actix_web_handler_tags(self, tmp_path: Path, verb: str) -> None:
        (tmp_path / "h.rs").write_text(
            f'#[{verb}("/x")]\nasync fn handler() {{}}\n',
        )
        ep = _by_name(tmp_path, "rust", "handler")
        assert ep["kind"] == "api"
        assert ep["trust_level"] == "untrusted_external"
        assert ep["asset_value"] == "high"
        assert ep["description"] == "Rust HTTP handler attribute"

    def test_no_mangle_ffi_tag(self, tmp_path: Path) -> None:
        (tmp_path / "ffi.rs").write_text(
            '#[no_mangle]\npub extern "C" fn expose() {}\n',
        )
        ep = _by_name(tmp_path, "rust", "expose")
        assert ep["kind"] == "api"
        assert ep["trust_level"] == "untrusted_external"
        assert ep["asset_value"] == "high"
        # Either the #[no_mangle] line or the pub extern "C" fn match may
        # describe this; both indicate an FFI export.
        description = ep.get("description") or ""
        assert isinstance(description, str)
        assert "FFI export" in description


class TestCSharpDetectorTags:
    @pytest.mark.parametrize("attr", ["HttpGet", "HttpPost", "HttpPut", "HttpDelete", "HttpPatch"])
    def test_aspnet_attribute_tags(self, tmp_path: Path, attr: str) -> None:
        (tmp_path / "Ctl.cs").write_text(
            f'public class Ctl {{\n    [{attr}("/x")]\n    public object F() => null;\n}}\n',
        )
        ep = _by_name(tmp_path, "c_sharp", ".F")
        assert ep["kind"] == "api"
        assert ep["trust_level"] == "untrusted_external"
        assert ep["asset_value"] == "high"
        assert ep["description"] == "ASP.NET Core HTTP handler"

    def test_azure_function_tag(self, tmp_path: Path) -> None:
        (tmp_path / "Fn.cs").write_text(
            'public class Fn {\n    [Function("run")]\n    public object Run() => null;\n}\n',
        )
        ep = _by_name(tmp_path, "c_sharp", ".Run")
        assert ep["kind"] == "api"
        assert ep["description"] == "Azure Function"


class TestSolidityDetectorTags:
    @pytest.mark.parametrize("visibility", ["external", "public"])
    def test_function_visibility_tags(self, tmp_path: Path, visibility: str) -> None:
        (tmp_path / "C.sol").write_text(
            "// SPDX-License-Identifier: MIT\n"
            "pragma solidity ^0.8.0;\n"
            "contract C {\n"
            f"    function act() {visibility} {{}}\n"
            "}\n",
        )
        ep = _by_name(tmp_path, "solidity", ".act")
        assert ep["kind"] == "api"
        assert ep["trust_level"] == "untrusted_external"
        assert ep["asset_value"] == "high"
        assert ep["description"] == "Solidity external/public function"


class TestNextJsDetectorTags:
    @pytest.mark.parametrize("verb", ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
    def test_app_router_verb_tags(self, tmp_path: Path, verb: str) -> None:
        (tmp_path / "route.ts").write_text(
            f"export async function {verb}(req: Request) {{ return new Response(); }}\n",
        )
        ep = _by_name(tmp_path, "typescript", verb)
        assert ep["kind"] == "api"
        assert ep["trust_level"] == "untrusted_external"
        assert ep["asset_value"] == "high"
        assert ep["description"] == "Next.js App Router handler"


class TestSwiftDetectorTags:
    def test_at_main_tag(self, tmp_path: Path) -> None:
        (tmp_path / "app.swift").write_text(
            "@main\nstruct App { static func main() {} }\n",
        )
        ep = _by_name(tmp_path, "swift", ".main")
        assert ep["kind"] == "user_input"
        assert ep["trust_level"] == "untrusted_external"
        assert ep["asset_value"] == "high"
        assert ep["description"] == "Swift @main app entrypoint"


class TestObjCDetectorTags:
    def test_url_handler_tag(self, tmp_path: Path) -> None:
        (tmp_path / "AppDelegate.m").write_text(
            "@interface AppDelegate : NSObject\n"
            "- (BOOL)application:(UIApplication *)app "
            "openURL:(NSURL *)url options:(NSDictionary *)opts;\n"
            "@end\n"
            "@implementation AppDelegate\n"
            "- (BOOL)application:(UIApplication *)app "
            "openURL:(NSURL *)url options:(NSDictionary *)opts { return YES; }\n"
            "@end\n",
        )
        ep = _by_name(tmp_path, "objc", "application:openURL:options:")
        assert ep["kind"] == "api"
        assert ep["trust_level"] == "untrusted_external"
        assert ep["asset_value"] == "high"
        assert ep["description"] == "Objective-C UIApplicationDelegate lifecycle method"


class TestKotlinDetectorTags:
    def test_spring_tag(self, tmp_path: Path) -> None:
        (tmp_path / "C.kt").write_text(
            'class C {\n    @GetMapping("/x")\n    fun f(): String = "ok"\n}\n',
        )
        ep = _by_name(tmp_path, "kotlin", ".f")
        assert ep["kind"] == "api"
        assert ep["asset_value"] == "high"
        assert ep["description"] == "Spring MVC/WebFlux handler (Kotlin)"

    @pytest.mark.parametrize(
        "method",
        [
            "onCreate",
            "onStart",
            "onResume",
            "onNewIntent",
            "onActivityResult",
            "onReceive",
            "onBind",
            "onHandleIntent",
        ],
    )
    def test_android_lifecycle_tags(self, tmp_path: Path, method: str) -> None:
        (tmp_path / "A.kt").write_text(
            f"class Activity {{\n    fun {method}() {{}}\n}}\n",
        )
        ep = _by_name(tmp_path, "kotlin", f".{method}")
        assert ep["kind"] == "api"
        assert ep["trust_level"] == "untrusted_external"
        assert ep["asset_value"] == "high"
        assert ep["description"] == "Android component lifecycle method"


class TestDartDetectorTags:
    def test_vm_entry_point_tag(self, tmp_path: Path) -> None:
        (tmp_path / "callbacks.dart").write_text(
            "@pragma('vm:entry-point')\nvoid fn() {}\n",
        )
        ep = _by_name(tmp_path, "dart", ":fn")
        assert ep["kind"] == "api"
        assert ep["trust_level"] == "untrusted_external"
        assert ep["asset_value"] == "high"
        assert ep["description"] == "Dart @pragma('vm:entry-point') native callback"


class TestCCppDetectorTags:
    def test_extern_c_tag(self, tmp_path: Path) -> None:
        (tmp_path / "api.cpp").write_text(
            'extern "C" int expose(int x) { return x; }\n',
        )
        ep = _by_name(tmp_path, "cpp", "expose")
        assert ep["kind"] == "api"
        assert ep["trust_level"] == "untrusted_external"
        assert ep["asset_value"] == "high"
        # Description mentions at least one of the three markers.
        description = ep.get("description") or ""
        assert isinstance(description, str)
        assert "exported symbol" in description
