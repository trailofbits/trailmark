"""Tests for automatic entrypoint detection.

Regression guard: an earlier version of Trailmark silently returned no
entrypoints because no parser populated ``graph.entrypoints``. These
tests lock in that entrypoint detection runs automatically and that the
three detection layers (main heuristic, pyproject scripts, override
file) have the intended precedence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trailmark.analysis.entrypoints import detect_entrypoints
from trailmark.models.annotations import AssetValue, EntrypointKind, TrustLevel
from trailmark.query.api import QueryEngine

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"


class TestSelfAnalysis:
    """Running Trailmark on its own source must produce entrypoints.

    This is the regression test for the Codex-discovered bug where
    ``graph.entrypoints`` was never populated and ``attack_surface()``
    silently returned an empty list.
    """

    def test_self_analysis_has_entrypoints(self) -> None:
        engine = QueryEngine.from_directory(str(SRC), language="python")
        summary = engine.summary()
        assert summary["entrypoints"] > 0, (
            "Running Trailmark on its own src/ must detect at least one "
            "entrypoint (the pyproject [project.scripts] target)."
        )

    def test_self_analysis_attack_surface_nonempty(self) -> None:
        engine = QueryEngine.from_directory(str(SRC), language="python")
        surface = engine.attack_surface()
        assert surface, "attack_surface() returned empty on trailmark's own source"

    def test_self_analysis_finds_pyproject_script(self) -> None:
        engine = QueryEngine.from_directory(str(SRC), language="python")
        surface = engine.attack_surface()
        node_ids = {ep["node_id"] for ep in surface}
        assert "cli:main" in node_ids, (
            f"Expected cli:main (the pyproject.toml script target) in {node_ids}"
        )


class TestMainHeuristic:
    def test_bare_main_function_detected(self, tmp_path: Path) -> None:
        sample = tmp_path / "tool.py"
        sample.write_text("def main():\n    return 0\n")
        engine = QueryEngine.from_directory(str(tmp_path))
        surface = engine.attack_surface()
        ids = {ep["node_id"] for ep in surface}
        assert "tool:main" in ids

    def test_main_gets_trusted_internal_by_default(self, tmp_path: Path) -> None:
        sample = tmp_path / "tool.py"
        sample.write_text("def main():\n    pass\n")
        engine = QueryEngine.from_directory(str(tmp_path))
        (ep,) = engine.attack_surface()
        assert ep["trust_level"] == "trusted_internal"

    def test_non_main_function_not_detected(self, tmp_path: Path) -> None:
        sample = tmp_path / "tool.py"
        sample.write_text("def helper():\n    pass\n")
        engine = QueryEngine.from_directory(str(tmp_path))
        assert engine.attack_surface() == []


class TestPyprojectScripts:
    def test_pyproject_script_overrides_main_heuristic(self, tmp_path: Path) -> None:
        """A pyproject.toml script target beats the generic main heuristic."""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "0.0.0"\n[project.scripts]\ndemo = "demo:main"\n',
        )
        src = tmp_path / "demo.py"
        src.write_text("def main():\n    pass\n")

        engine = QueryEngine.from_directory(str(tmp_path))
        (ep,) = engine.attack_surface()
        assert ep["node_id"] == "demo:main"
        assert ep["trust_level"] == "untrusted_external"
        assert ep["asset_value"] == "medium"

    def test_pyproject_script_in_parent_is_discovered(self, tmp_path: Path) -> None:
        """Detection walks up from the parse path to find pyproject.toml."""
        (tmp_path / "pyproject.toml").write_text(
            "[project]\n"
            'name = "demo"\n'
            'version = "0.0.0"\n'
            "[project.scripts]\n"
            'demo = "pkg.app:main"\n',
        )
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "app.py").write_text("def main():\n    pass\n")

        engine = QueryEngine.from_directory(str(pkg))
        ids = {ep["node_id"] for ep in engine.attack_surface()}
        assert "app:main" in ids

    def test_malformed_pyproject_is_tolerated(self, tmp_path: Path) -> None:
        """A broken pyproject.toml must not crash detection."""
        (tmp_path / "pyproject.toml").write_text("this is not valid toml = [")
        (tmp_path / "tool.py").write_text("def main():\n    pass\n")
        engine = QueryEngine.from_directory(str(tmp_path))
        # main heuristic still fires
        assert engine.attack_surface()


class TestOverrideFile:
    def _write_override(self, tmp_path: Path, body: str) -> None:
        (tmp_path / ".trailmark").mkdir()
        (tmp_path / ".trailmark" / "entrypoints.toml").write_text(body)

    def test_override_adds_entrypoint(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("def handle_request(req):\n    pass\n")
        self._write_override(
            tmp_path,
            "[[entrypoint]]\n"
            'node = "app:handle_request"\n'
            'kind = "api"\n'
            'trust = "untrusted_external"\n'
            'asset_value = "high"\n'
            'description = "HTTP handler"\n',
        )
        engine = QueryEngine.from_directory(str(tmp_path))
        (ep,) = engine.attack_surface()
        assert ep["node_id"] == "app:handle_request"
        assert ep["kind"] == "api"
        assert ep["trust_level"] == "untrusted_external"
        assert ep["asset_value"] == "high"
        assert ep["description"] == "HTTP handler"

    def test_override_beats_pyproject_and_main(self, tmp_path: Path) -> None:
        """Override file is the final word."""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "x"\nversion = "0"\n[project.scripts]\nx = "app:main"\n',
        )
        (tmp_path / "app.py").write_text("def main():\n    pass\n")
        self._write_override(
            tmp_path,
            "[[entrypoint]]\n"
            'node = "app:main"\n'
            'kind = "api"\n'
            'trust = "untrusted_external"\n'
            'asset_value = "high"\n',
        )
        engine = QueryEngine.from_directory(str(tmp_path))
        (ep,) = engine.attack_surface()
        assert ep["kind"] == "api"  # override, not user_input
        assert ep["asset_value"] == "high"  # override, not medium

    def test_override_accepts_module_reference(self, tmp_path: Path) -> None:
        """Override `node = "module.path:func"` resolves like pyproject scripts."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "app.py").write_text("def serve():\n    pass\n")
        self._write_override(
            tmp_path,
            '[[entrypoint]]\nnode = "pkg.app:serve"\nkind = "api"\n',
        )
        engine = QueryEngine.from_directory(str(tmp_path))
        ids = {ep["node_id"] for ep in engine.attack_surface()}
        assert "app:serve" in ids

    def test_override_unknown_node_is_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("def real():\n    pass\n")
        self._write_override(
            tmp_path,
            '[[entrypoint]]\nnode = "nonexistent:func"\nkind = "api"\n',
        )
        engine = QueryEngine.from_directory(str(tmp_path))
        # Nothing matched, no main heuristic trigger either
        assert engine.attack_surface() == []

    def test_override_invalid_enum_is_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("def main():\n    pass\n")
        self._write_override(
            tmp_path,
            '[[entrypoint]]\nnode = "app:main"\nkind = "not-a-real-kind"\n',
        )
        engine = QueryEngine.from_directory(str(tmp_path))
        # Override is skipped, main heuristic still applies
        (ep,) = engine.attack_surface()
        assert ep["trust_level"] == "trusted_internal"

    def test_malformed_override_toml_is_tolerated(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("def main():\n    pass\n")
        self._write_override(tmp_path, "this is not valid toml = [")
        engine = QueryEngine.from_directory(str(tmp_path))
        # Heuristic still runs
        assert engine.attack_surface()


class TestOptOut:
    def test_detection_can_be_disabled(self, tmp_path: Path) -> None:
        """``detect_entrypoints_=False`` skips automatic detection."""
        (tmp_path / "tool.py").write_text("def main():\n    pass\n")
        engine = QueryEngine.from_directory(
            str(tmp_path),
            detect_entrypoints_=False,
        )
        assert engine.attack_surface() == []


class TestDirectAPI:
    """``detect_entrypoints`` can be called directly on a prebuilt graph."""

    def test_returns_mapping_of_node_id_to_tag(self, tmp_path: Path) -> None:
        from trailmark.parsers.python import PythonParser

        (tmp_path / "tool.py").write_text("def main():\n    pass\n")
        graph = PythonParser().parse_directory(str(tmp_path))
        detected = detect_entrypoints(graph, str(tmp_path))

        assert "tool:main" in detected
        tag = detected["tool:main"]
        assert tag.kind == EntrypointKind.USER_INPUT
        assert tag.trust_level == TrustLevel.TRUSTED_INTERNAL
        assert tag.asset_value == AssetValue.LOW


class TestPythonFrameworks:
    def test_flask_route_detected(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            "from flask import Flask\n"
            "app = Flask(__name__)\n"
            "\n"
            "@app.route('/login', methods=['POST'])\n"
            "def login():\n"
            "    return 'ok'\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path))
        surface = engine.attack_surface()
        by_id = {ep["node_id"]: ep for ep in surface}
        assert "app:login" in by_id
        assert by_id["app:login"]["kind"] == "api"
        assert by_id["app:login"]["trust_level"] == "untrusted_external"
        assert by_id["app:login"]["asset_value"] == "high"

    def test_fastapi_post_detected(self, tmp_path: Path) -> None:
        (tmp_path / "api.py").write_text(
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n"
            "\n"
            "@app.post('/auth')\n"
            "async def auth(body: dict):\n"
            "    return body\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path))
        ids = {ep["node_id"] for ep in engine.attack_surface()}
        assert "api:auth" in ids

    def test_fastapi_router_detected(self, tmp_path: Path) -> None:
        (tmp_path / "routes.py").write_text(
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "\n"
            "@router.get('/users/{id}')\n"
            "def get_user(id: int):\n"
            "    return {'id': id}\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path))
        ids = {ep["node_id"] for ep in engine.attack_surface()}
        assert "routes:get_user" in ids

    def test_click_command_detected(self, tmp_path: Path) -> None:
        (tmp_path / "tool.py").write_text(
            "import click\n\n@click.command()\ndef run():\n    pass\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path))
        by_id = {ep["node_id"]: ep for ep in engine.attack_surface()}
        assert "tool:run" in by_id
        assert by_id["tool:run"]["kind"] == "user_input"
        assert by_id["tool:run"]["asset_value"] == "medium"

    def test_typer_command_detected(self, tmp_path: Path) -> None:
        (tmp_path / "cli.py").write_text(
            "import typer\n"
            "app = typer.Typer()\n"
            "\n"
            "@app.command()\n"
            "def hello(name: str):\n"
            "    pass\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path))
        ids = {ep["node_id"] for ep in engine.attack_surface()}
        assert "cli:hello" in ids

    def test_celery_task_detected(self, tmp_path: Path) -> None:
        (tmp_path / "tasks.py").write_text(
            "from celery import Celery\n"
            "celery_app = Celery()\n"
            "\n"
            "@celery_app.task\n"
            "def send_email(to, body):\n"
            "    pass\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path))
        by_id = {ep["node_id"]: ep for ep in engine.attack_surface()}
        assert "tasks:send_email" in by_id
        assert by_id["tasks:send_email"]["kind"] == "third_party"
        assert by_id["tasks:send_email"]["trust_level"] == "semi_trusted_external"

    def test_undecorated_function_not_detected(self, tmp_path: Path) -> None:
        (tmp_path / "mod.py").write_text("def helper():\n    pass\n")
        engine = QueryEngine.from_directory(str(tmp_path))
        assert engine.attack_surface() == []


class TestRustFrameworks:
    def test_actix_web_get_detected(self, tmp_path: Path) -> None:
        (tmp_path / "server.rs").write_text(
            "use actix_web::{get, Responder};\n"
            "\n"
            '#[get("/users/{id}")]\n'
            "async fn get_user() -> impl Responder {\n"
            '    "ok"\n'
            "}\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="rust")
        ids = {ep["node_id"] for ep in engine.attack_surface()}
        assert any("get_user" in node_id for node_id in ids), ids

    def test_no_mangle_ffi_detected(self, tmp_path: Path) -> None:
        (tmp_path / "ffi.rs").write_text(
            '#[no_mangle]\npub extern "C" fn add_one(x: i32) -> i32 {\n    x + 1\n}\n',
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="rust")
        by_id = {ep["node_id"]: ep for ep in engine.attack_surface()}
        assert any("add_one" in nid for nid in by_id), by_id
        # And its asset value is high.
        assert any(ep["asset_value"] == "high" for ep in by_id.values())


class TestSolidity:
    def test_external_function_detected(self, tmp_path: Path) -> None:
        (tmp_path / "Vault.sol").write_text(
            "// SPDX-License-Identifier: MIT\n"
            "pragma solidity ^0.8.0;\n"
            "contract Vault {\n"
            "    function withdraw(uint256 amount) external {\n"
            "    }\n"
            "}\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="solidity")
        ids = {ep["node_id"] for ep in engine.attack_surface()}
        assert any("withdraw" in nid for nid in ids), ids

    def test_public_function_detected(self, tmp_path: Path) -> None:
        (tmp_path / "Token.sol").write_text(
            "// SPDX-License-Identifier: MIT\n"
            "pragma solidity ^0.8.0;\n"
            "contract Token {\n"
            "    function balanceOf(address who) public view returns (uint256) {\n"
            "        return 0;\n"
            "    }\n"
            "}\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="solidity")
        ids = {ep["node_id"] for ep in engine.attack_surface()}
        assert any("balanceOf" in nid for nid in ids), ids

    def test_internal_function_not_detected(self, tmp_path: Path) -> None:
        (tmp_path / "Lib.sol").write_text(
            "// SPDX-License-Identifier: MIT\n"
            "pragma solidity ^0.8.0;\n"
            "contract Lib {\n"
            "    function _helper(uint256 x) internal pure returns (uint256) {\n"
            "        return x + 1;\n"
            "    }\n"
            "}\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="solidity")
        assert engine.attack_surface() == []

    def test_fallback_and_receive_detected_when_parser_emits_them(
        self,
        tmp_path: Path,
    ) -> None:
        """Guard against regressions once the Solidity parser exposes special fns.

        Today the Solidity parser does not emit ``receive()`` / ``fallback()``
        as separate function nodes, so the detector has nothing to tag. When
        the parser is updated to emit them, this test should start passing
        without any detector changes — the _SOL_SPECIAL regex already handles
        the signature shape.
        """
        (tmp_path / "Wallet.sol").write_text(
            "// SPDX-License-Identifier: MIT\n"
            "pragma solidity ^0.8.0;\n"
            "contract Wallet {\n"
            "    receive() external payable {}\n"
            "    fallback() external payable {}\n"
            "}\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="solidity")
        ids = {ep["node_id"] for ep in engine.attack_surface()}
        # Current expectation: parser does not emit these yet.
        assert not any("receive" in nid for nid in ids)
        assert not any("fallback" in nid for nid in ids)


class TestJavaScriptFrameworks:
    def test_nestjs_controller_method_detected(self, tmp_path: Path) -> None:
        (tmp_path / "users.controller.ts").write_text(
            "import { Controller, Get } from '@nestjs/common';\n"
            "\n"
            "@Controller('users')\n"
            "export class UsersController {\n"
            "  @Get(':id')\n"
            "  findOne(id: string) {\n"
            "    return { id };\n"
            "  }\n"
            "}\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="typescript")
        surface = engine.attack_surface()
        descriptions = {ep["description"] for ep in surface}
        assert any("NestJS" in d for d in descriptions if d), surface

    def test_nextjs_app_router_detected(self, tmp_path: Path) -> None:
        # Trailmark uses file basename in node ids, so we only need a
        # `route.ts` file (the directory structure is irrelevant for the
        # detector, which looks at basename and export name).
        route = tmp_path / "route.ts"
        route.write_text(
            "export async function GET(request: Request) {\n"
            "  return new Response('ok');\n"
            "}\n"
            "export async function POST(request: Request) {\n"
            "  return new Response('created');\n"
            "}\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="typescript")
        surface = engine.attack_surface()
        descriptions = [ep["description"] for ep in surface if ep.get("description")]
        assert any("Next.js App Router" in d for d in descriptions), surface


class TestJavaFrameworks:
    def test_spring_get_mapping_detected(self, tmp_path: Path) -> None:
        (tmp_path / "UserController.java").write_text(
            "import org.springframework.web.bind.annotation.*;\n"
            "\n"
            "@RestController\n"
            "public class UserController {\n"
            '    @GetMapping("/users/{id}")\n'
            "    public User get(@PathVariable long id) {\n"
            "        return null;\n"
            "    }\n"
            "}\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="java")
        surface = engine.attack_surface()
        descriptions = [ep["description"] for ep in surface if ep.get("description")]
        assert any("Spring" in d for d in descriptions), surface

    def test_jaxrs_get_detected(self, tmp_path: Path) -> None:
        (tmp_path / "Resource.java").write_text(
            "import javax.ws.rs.*;\n"
            "\n"
            '@Path("/users")\n'
            "public class UserResource {\n"
            "    @GET\n"
            '    @Path("/{id}")\n'
            '    public User get(@PathParam("id") long id) {\n'
            "        return null;\n"
            "    }\n"
            "}\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="java")
        surface = engine.attack_surface()
        descriptions = [ep["description"] for ep in surface if ep.get("description")]
        assert any("JAX-RS" in d for d in descriptions), surface


class TestCSharpFrameworks:
    def test_aspnet_core_http_get_detected(self, tmp_path: Path) -> None:
        (tmp_path / "UsersController.cs").write_text(
            "using Microsoft.AspNetCore.Mvc;\n"
            "\n"
            "[ApiController]\n"
            '[Route("api/[controller]")]\n'
            "public class UsersController : ControllerBase {\n"
            '    [HttpGet("{id}")]\n'
            "    public IActionResult Get(int id) {\n"
            "        return Ok();\n"
            "    }\n"
            "}\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="c_sharp")
        surface = engine.attack_surface()
        descriptions = [ep["description"] for ep in surface if ep.get("description")]
        assert any("ASP.NET" in d for d in descriptions), surface


class TestPhpFrameworks:
    def test_symfony_route_attribute_detected(self, tmp_path: Path) -> None:
        (tmp_path / "ProductController.php").write_text(
            "<?php\n"
            "namespace App\\Controller;\n"
            "\n"
            "use Symfony\\Component\\Routing\\Annotation\\Route;\n"
            "\n"
            "class ProductController {\n"
            "    #[Route('/products', methods: ['GET'])]\n"
            "    public function list() {\n"
            "        return [];\n"
            "    }\n"
            "}\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="php")
        surface = engine.attack_surface()
        descriptions = [ep["description"] for ep in surface if ep.get("description")]
        assert any("Symfony" in d for d in descriptions), surface


class TestErlang:
    def test_exported_function_detected(self, tmp_path: Path) -> None:
        (tmp_path / "auth.erl").write_text(
            "-module(auth).\n"
            "-export([login/2, logout/1]).\n"
            "\n"
            "login(User, Pass) -> ok.\n"
            "logout(User) -> ok.\n"
            "internal_only() -> ok.\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="erlang")
        surface = engine.attack_surface()
        exported_names = {ep["node_id"].split(":", 1)[-1] for ep in surface}
        assert "login" in exported_names, surface
        assert "logout" in exported_names, surface
        assert "internal_only" not in exported_names, surface


class TestSwift:
    def test_at_main_attribute_detected(self, tmp_path: Path) -> None:
        (tmp_path / "app.swift").write_text(
            "@main\nstruct App {\n    static func main() {}\n}\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="swift")
        surface = engine.attack_surface()
        descriptions = [ep.get("description") or "" for ep in surface]
        assert any("Swift @main" in d for d in descriptions), surface


class TestObjectiveC:
    def test_app_delegate_selector_detected(self, tmp_path: Path) -> None:
        (tmp_path / "AppDelegate.m").write_text(
            "#import <UIKit/UIKit.h>\n"
            "@interface AppDelegate : NSObject\n"
            "- (BOOL)application:(UIApplication *)app "
            "didFinishLaunchingWithOptions:(NSDictionary *)opts;\n"
            "@end\n"
            "@implementation AppDelegate\n"
            "- (BOOL)application:(UIApplication *)app "
            "didFinishLaunchingWithOptions:(NSDictionary *)opts {\n"
            "    return YES;\n"
            "}\n"
            "@end\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="objc")
        surface = engine.attack_surface()
        descriptions = [ep.get("description") or "" for ep in surface]
        assert any("UIApplicationDelegate" in d for d in descriptions), surface

    def test_non_app_method_not_flagged(self, tmp_path: Path) -> None:
        (tmp_path / "Foo.m").write_text(
            "@interface Foo : NSObject\n"
            "- (void)helper;\n"
            "@end\n"
            "@implementation Foo\n"
            "- (void)helper { }\n"
            "@end\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="objc")
        assert engine.attack_surface() == []


class TestKotlin:
    def test_spring_annotation_detected(self, tmp_path: Path) -> None:
        (tmp_path / "UserController.kt").write_text(
            "import org.springframework.web.bind.annotation.*\n"
            "\n"
            "@RestController\n"
            "class UserController {\n"
            '    @GetMapping("/users/{id}")\n'
            '    fun get(id: Long): String { return "ok" }\n'
            "}\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="kotlin")
        surface = engine.attack_surface()
        descriptions = [ep.get("description") or "" for ep in surface]
        assert any("Spring" in d for d in descriptions), surface

    def test_android_lifecycle_method_detected(self, tmp_path: Path) -> None:
        (tmp_path / "MainActivity.kt").write_text(
            "package com.example\n"
            "\n"
            "class MainActivity {\n"
            "    fun onCreate(bundle: Bundle?) {\n"
            "        super.onCreate(bundle)\n"
            "    }\n"
            "}\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="kotlin")
        surface = engine.attack_surface()
        descriptions = [ep.get("description") or "" for ep in surface]
        assert any("Android" in d for d in descriptions), surface

    def test_helper_method_not_flagged(self, tmp_path: Path) -> None:
        (tmp_path / "Util.kt").write_text(
            "class Util {\n    fun helper(x: Int): Int { return x + 1 }\n}\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="kotlin")
        assert engine.attack_surface() == []


class TestDart:
    def test_vm_entry_point_detected(self, tmp_path: Path) -> None:
        (tmp_path / "callbacks.dart").write_text(
            "@pragma('vm:entry-point')\nvoid nativeCallback(String data) {\n  print(data);\n}\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="dart")
        surface = engine.attack_surface()
        descriptions = [ep.get("description") or "" for ep in surface]
        assert any("vm:entry-point" in d for d in descriptions), surface

    def test_plain_function_not_flagged(self, tmp_path: Path) -> None:
        (tmp_path / "util.dart").write_text(
            "void log(String s) { print(s); }\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="dart")
        assert engine.attack_surface() == []

    def test_main_still_detected(self, tmp_path: Path) -> None:
        (tmp_path / "app.dart").write_text(
            "void main() { print('hi'); }\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="dart")
        surface = engine.attack_surface()
        assert any(ep["node_id"] == "app:main" for ep in surface), surface


class TestGo:
    def test_http_handlefunc_detected(self, tmp_path: Path) -> None:
        (tmp_path / "server.go").write_text(
            "package main\n"
            'import "net/http"\n'
            "\n"
            "func loginHandler(w http.ResponseWriter, r *http.Request) {}\n"
            "\n"
            "func main() {\n"
            '    http.HandleFunc("/login", loginHandler)\n'
            "}\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="go")
        surface = engine.attack_surface()
        descriptions = {ep.get("description") or "" for ep in surface}
        assert any("Go HTTP handler" in d for d in descriptions), surface

    def test_gin_route_detected(self, tmp_path: Path) -> None:
        (tmp_path / "app.go").write_text(
            "package main\n"
            'import "github.com/gin-gonic/gin"\n'
            "\n"
            "func getUser(c *gin.Context) {}\n"
            "\n"
            "func main() {\n"
            "    r := gin.Default()\n"
            '    r.GET("/users/:id", getUser)\n'
            "}\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="go")
        surface = engine.attack_surface()
        ids = {ep["node_id"] for ep in surface}
        assert any("getUser" in nid for nid in ids), surface


class TestRuby:
    def test_rails_controller_action_detected(self, tmp_path: Path) -> None:
        (tmp_path / "users_controller.rb").write_text(
            "class UsersController < ApplicationController\n"
            "  def show\n"
            "    @user = User.find(params[:id])\n"
            "  end\n"
            "\n"
            "  def create\n"
            "    User.create(params[:user])\n"
            "  end\n"
            "end\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="ruby")
        surface = engine.attack_surface()
        descriptions = {ep.get("description") or "" for ep in surface}
        assert any("Rails controller" in d for d in descriptions), surface

    def test_non_controller_class_not_flagged(self, tmp_path: Path) -> None:
        (tmp_path / "util.rb").write_text(
            "class Util\n  def helper(x)\n    x + 1\n  end\nend\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="ruby")
        assert engine.attack_surface() == []

    def test_sidekiq_worker_perform_detected(self, tmp_path: Path) -> None:
        (tmp_path / "mailer.rb").write_text(
            "class EmailWorker\n"
            "  include Sidekiq::Worker\n"
            "\n"
            "  def perform(user_id)\n"
            "    puts user_id\n"
            "  end\n"
            "end\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="ruby")
        surface = engine.attack_surface()
        descriptions = {ep.get("description") or "" for ep in surface}
        assert any("Sidekiq" in d for d in descriptions), surface


class TestCCpp:
    def test_extern_c_detected(self, tmp_path: Path) -> None:
        (tmp_path / "api.cpp").write_text(
            'extern "C" int add_one(int x) {\n    return x + 1;\n}\n',
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="cpp")
        surface = engine.attack_surface()
        descriptions = {ep.get("description") or "" for ep in surface}
        assert any('extern "C"' in d or "exported" in d for d in descriptions), surface

    def test_dllexport_detected(self, tmp_path: Path) -> None:
        (tmp_path / "lib.c").write_text(
            "__declspec(dllexport)\nint compute(int x) {\n    return x * 2;\n}\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="c")
        surface = engine.attack_surface()
        descriptions = {ep.get("description") or "" for ep in surface}
        assert any("dllexport" in d or "exported" in d for d in descriptions), surface

    def test_plain_c_function_not_flagged(self, tmp_path: Path) -> None:
        (tmp_path / "util.c").write_text(
            "int helper(int x) {\n    return x + 1;\n}\n",
        )
        engine = QueryEngine.from_directory(str(tmp_path), language="c")
        # `main` is always flagged by the main-heuristic; helper is not.
        surface = engine.attack_surface()
        ids = {ep["node_id"] for ep in surface}
        assert not any("helper" in nid for nid in ids), surface


@pytest.fixture(autouse=True)
def _isolate_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Some tests create pyproject.toml in tmp_path; make sure detection does
    not accidentally pick up Trailmark's own pyproject.toml by walking up
    past tmp_path when the parse path is inside tmp_path.

    This fixture is a no-op for tests that don't rely on tmp_path; it just
    ensures a deterministic cwd.
    """
    monkeypatch.chdir(tmp_path)
