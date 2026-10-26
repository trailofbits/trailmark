"""Automatic entrypoint detection.

Returns tags for ``CodeGraph.entrypoints`` so that ``attack_surface()``, taint
propagation, entrypoint enumeration, and privilege-boundary crossing
produce meaningful results.

Detection layers (later layers override earlier ones):

1. **Generic ``main`` heuristic** — any function named ``main`` in any
   language. Tagged ``user_input`` / ``trusted_internal`` / ``low``.
2. **Framework-aware scan** — decorator/attribute patterns per language:

   - Python: Flask / FastAPI / aiohttp (``@app.route``, ``@router.get``);
     Click / Typer (``@click.command``, ``@app.command``); Celery
     (``@app.task``, ``@shared_task``).
   - JavaScript / TypeScript: NestJS decorators (``@Get``, ``@Post``,
     etc.), Next.js route handlers (App Router ``route.ts`` + HTTP-verb
     exports, Pages API ``pages/api/**``), AWS Lambda handlers.
   - Java: Spring (``@GetMapping``, ``@PostMapping``, ...), JAX-RS
     (``@GET``, ``@POST``), Kafka ``@KafkaListener``, servlet
     ``doGet``/``doPost``/... methods.
   - C#: ASP.NET Core ``[HttpGet]`` / ``[Route]`` attributes, Azure
     Functions ``[Function]`` / ``[FunctionName]``.
   - PHP: Symfony ``#[Route(...)]`` attributes and old-style
     ``@Route(...)`` annotations.
   - Rust: actix-web / rocket handler attributes (``#[get("/")]``, etc.),
     FFI exports (``#[no_mangle]``, ``pub extern "C"``),
     ``#[tokio::main]`` / ``#[actix_web::main]`` on ``main``.
   - Solidity: ``external``/``public`` function visibility, special
     ``fallback()`` / ``receive()``.
   - Cairo / StarkNet: ``#[external]``, ``#[view]``, ``#[l1_handler]``,
     ``#[constructor]`` attributes.
   - Circom: files declaring ``component main = ...``.
   - Miden Assembly: ``export.<name>`` directives.
   - Haskell: top-level ``main ::`` / ``main =`` bindings.
   - Erlang: functions listed in ``-export([...])``.
3. **pyproject.toml [project.scripts]** — explicitly declared CLI targets.
4. **Repo-local override file** — ``.trailmark/entrypoints.toml`` at the
   repository root. Always the authoritative source.

See ``docs/entrypoint-patterns.md`` for the full reference, including
frameworks not yet implemented (Express / Koa / Fastify, Laravel,
WordPress, Rails, Cobra, axum, warp, clap, etc.).
"""

from __future__ import annotations

import dataclasses
import logging
import re
import tomllib
from pathlib import Path
from typing import Any

from trailmark._path_glob import compile_path_glob
from trailmark.analysis._entrypoint_declarations import DeclarationCache
from trailmark.analysis._python_decorators import DecoratorFact, python_decorators
from trailmark.models.annotations import (
    AssetValue,
    EntrypointKind,
    EntrypointTag,
    TrustLevel,
)
from trailmark.models.edges import CodeEdge, EdgeKind
from trailmark.models.entrypoints import EntrypointEvidence
from trailmark.models.graph import CodeGraph, source_manifests
from trailmark.models.nodes import CodeUnit, NodeKind, NodeOrigin, SourceLocation

OVERRIDE_FILE = ".trailmark/entrypoints.toml"

# Existing HTTP method conventions are applied to lexical binding evidence.
_PY_HTTP_METHODS = frozenset(
    {"route", "get", "post", "put", "patch", "delete", "head", "options", "websocket", "api_route"}
)

# @click.command / @click.group / @typer_app.command
_PY_CLI_DECORATOR = re.compile(
    r"^\s*@\s*(click\.(command|group)|[A-Za-z_][\w.]*\.command)\s*\(",
)

# @celery_app.task, @shared_task, @app.task
_PY_TASK_DECORATOR = re.compile(
    r"^\s*@\s*([A-Za-z_][\w.]*\.task|shared_task)\b",
)

# Rust proc-macro handler attributes: #[get("/")], #[post("/")], etc.
_RS_HTTP_ATTR = re.compile(
    r"^\s*#\[\s*(get|post|put|delete|patch|head|options|connect|trace)\s*\(",
)

# Rust #[tokio::main] / #[async_std::main] / #[actix_web::main]
_RS_ASYNC_MAIN_ATTR = re.compile(r"^\s*#\[\s*\w+::main\s*\]\s*$")

# Rust FFI export: #[no_mangle] or `pub extern "C" fn`
_RS_NO_MANGLE = re.compile(
    r"^\s*#\[\s*(?:unsafe\s*\(\s*)?(?:no_mangle|export_name\s*=\s*"
    r'"[^"\\]*(?:\\.[^"\\]*)*")\s*\)?\s*\]\s*$'
)
_RS_EXTERN_C_FN = re.compile(r"\bpub\s+(?:unsafe\s+)?extern\s+\"C\"\s+fn\b")

# JS/TS — NestJS method decorators: @Get(), @Post(), @Put(), @Delete(), @Patch(),
# @Options(), @Head(), @All(). Capital first letter distinguishes from free
# functions named `get`/`post`/etc.
_JS_NEST_DECORATOR = re.compile(
    r"^\s*@\s*(Get|Post|Put|Delete|Patch|Options|Head|All)\s*\(",
)

# Java annotations — Spring MVC / WebFlux
_JAVA_SPRING_HANDLER = re.compile(
    r"^\s*@\s*(Get|Post|Put|Delete|Patch|Request)Mapping\b",
)

# Java annotations — JAX-RS
_JAVA_JAXRS_HANDLER = re.compile(
    r"^\s*@\s*(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s*(?:\(|$)",
)

# Java — Kafka listener
_JAVA_KAFKA_LISTENER = re.compile(r"^\s*@\s*KafkaListener\b")

# C# attributes — ASP.NET Core controllers / Minimal APIs / Azure Functions
_CS_HTTP_ATTR = re.compile(
    r"^\s*\[\s*(HttpGet|HttpPost|HttpPut|HttpDelete|HttpPatch|HttpHead|HttpOptions|Route)\b",
)
_CS_AZURE_FUNC_ATTR = re.compile(r"^\s*\[\s*Function(Name)?\s*\(")

# PHP — Symfony Route attribute (PHP 8+)
_PHP_ROUTE_ATTR = re.compile(r"^\s*#\[\s*Route\s*\(")

# PHP — Old-style annotation comments: @Route("/path")
_PHP_ROUTE_ANNOTATION = re.compile(r"^\s*\*\s*@Route\s*\(")

# Cairo/StarkNet attributes
_CAIRO_CONTRACT_ATTR = re.compile(
    r"^\s*#\[\s*(external|view|l1_handler|constructor)\b",
)

# Miden Assembly — exported procedures and program begin block
_MASM_EXPORT = re.compile(r"^\s*export\.([A-Za-z_]\w*)")

# Erlang — -export([...]) declaration
_ERLANG_EXPORT = re.compile(r"^\s*-\s*export\s*\(\s*\[")

# Haskell — `main :: IO ()` / `main = ...` at column 0
_HASKELL_MAIN = re.compile(r"^main\s*(::|=)")

# Objective-C — AppDelegate lifecycle selectors on UIApplicationDelegate
_OBJC_APP_DELEGATE_SELECTORS = frozenset(
    {
        "application:didFinishLaunchingWithOptions:",
        "application:openURL:options:",
        "application:continueUserActivity:restorationHandler:",
        "application:performFetchWithCompletionHandler:",
    }
)

# Kotlin — Ktor routing DSL: `get("/path") { ... }`, `post`, etc.
# These appear as call expressions inside a `routing { ... }` block. The
# file-level heuristic matches the verb calls; without block-level context
# we can't distinguish Ktor routes from bare HTTP client calls, so Ktor
# detection is currently disabled in favor of the override file.

# Kotlin — Android Activity lifecycle overrides. Names and signatures
# are stable across Android SDK versions.
_KOTLIN_ANDROID_LIFECYCLE_METHODS = frozenset(
    {
        "onCreate",
        "onStart",
        "onResume",
        "onNewIntent",
        "onActivityResult",
        "onReceive",  # BroadcastReceiver
        "onBind",  # Service
        "onHandleIntent",  # IntentService
    }
)

# Dart — `@pragma('vm:entry-point')` marks native-callable functions,
# often FFI or platform-channel callback targets. These are attacker-
# reachable from the host platform.
_DART_VM_ENTRY_POINT = re.compile(
    r"^\s*@\s*pragma\s*\(\s*['\"]vm:entry-point['\"]",
)

# Go — `http.HandleFunc("/path", handler)` / `http.Handle("/path", h)`.
# The stdlib net/http registrations accept a string path and a handler
# reference.
_GO_HTTP_HANDLE = re.compile(
    r"\bhttp\.(HandleFunc|Handle)\s*\(\s*\"[^\"]*\"\s*,\s*([A-Za-z_][\w.]*)",
)

# Go — gin/chi/echo/fiber route registration: `<router>.<VERB>("/path", handler)`.
# Matches GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS on any receiver. This is
# looser than the stdlib pattern so it catches most community routers,
# at the cost of possibly matching non-HTTP methods on unrelated types.
_GO_ROUTER_VERB = re.compile(
    r"\b([A-Za-z_]\w*)\.(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|Any|Match)"
    r"\s*\(\s*\"[^\"]*\"\s*,\s*([A-Za-z_][\w.]*)",
)

# Ruby — Rails controller: `class FooController < ApplicationController`
# (or any ActionController base / another controller).
_RUBY_RAILS_CONTROLLER_CLASS = re.compile(
    r"^\s*class\s+(\w+)\s*<\s*(ApplicationController|ActionController::\w+|\w+Controller)\b",
)

# Ruby — Sidekiq worker: either `include Sidekiq::Worker` or
# `include Sidekiq::Job` inside a class body.
_RUBY_SIDEKIQ_INCLUDE = re.compile(
    r"^\s*include\s+Sidekiq::(Worker|Job)\b",
)

# C / C++ — explicit library export markers on the signature line.
#   extern "C" ...             (C++ C-linkage declaration)
#   __attribute__((visibility("default")))
#   __declspec(dllexport)
_C_EXPORT_MARKERS = re.compile(
    r'\bextern\s+"C"'
    r'|__attribute__\s*\(\s*\(\s*visibility\s*\(\s*"default"'
    r"|__declspec\s*\(\s*dllexport\s*\)",
)

_KIND_BY_NAME = {k.value: k for k in EntrypointKind}
_TRUST_BY_NAME = {t.value: t for t in TrustLevel}
_ASSET_BY_NAME = {a.value: a for a in AssetValue}


def detect_entrypoints(graph: CodeGraph, root_path: str) -> dict[str, EntrypointTag]:
    """Return detected entrypoints for ``graph`` rooted at ``root_path``.

    Callers typically merge the result into ``graph.entrypoints``:

        graph.entrypoints.update(detect_entrypoints(graph, path))

    Detection may update node attributes with Solidity override metadata.
    The returned tags are not added to ``graph.entrypoints`` by this function.

    Args:
        graph: The parsed code graph.
        root_path: Absolute or repository-relative path the parser walked.

    Returns:
        Mapping of node id -> EntrypointTag. Empty dict if no entrypoints
        are detected.
    """
    # Priority (least to most specific, later layers override earlier):
    #   1. Generic `main` functions — fallback heuristic.
    #   2. Framework-aware decorator/attribute scan.
    #   3. pyproject.toml [project.scripts] — explicitly-declared CLI targets.
    #   4. Override file — hand-curated, authoritative.
    detected: dict[str, EntrypointTag] = {}
    for tags, rule in (
        (_detect_main_functions(graph), "main_name"),
        (_detect_framework_entrypoints(graph), "source_pattern"),
    ):
        detected.update(tags)
        for node_id, tag in tags.items():
            graph.add_entrypoint_evidence(
                EntrypointEvidence(
                    node_id,
                    "heuristic",
                    rule,
                    graph.nodes[node_id].location,
                    details=(("kind", tag.kind.value), ("description", tag.description)),
                    unresolved=("runtime_activation",),
                )
            )
    for selected, root in _configuration_scopes(graph, root_path):
        repo_root = _find_repo_root(root)
        for tags in (
            _detect_pyproject_scripts(selected, repo_root),
            _load_override_file(selected, repo_root),
        ):
            detected.update(tags)
        graph.entrypoint_evidence.update(selected.entrypoint_evidence)
    return detected


def _configuration_scopes(graph: CodeGraph, root_path: str) -> list[tuple[CodeGraph, Path]]:
    """Evaluate local manifests against their selected root's identities."""
    manifests = source_manifests(graph)
    if not manifests:
        return [(graph, Path(root_path).resolve())]
    result = []
    for manifest in manifests:
        roots = manifest.get("roots")
        if not isinstance(roots, list):
            continue
        for root in roots:
            if not isinstance(root, dict):
                continue
            files = manifest.get("files", [])
            selected = (
                {
                    item["path"]
                    for item in files
                    if isinstance(item, dict)
                    and item.get("root") == root["name"]
                    and item.get("status") == "inspected"
                }
                if isinstance(files, list)
                else set()
            )
            nodes = {
                key: unit
                for key, unit in graph.nodes.items()
                if unit.location.file_path in selected
            }
            result.append((CodeGraph(nodes=nodes, language=graph.language), Path(root["path"])))
    covered = {node_id for selected, _root in result for node_id in selected.nodes}
    uncovered = {node_id: unit for node_id, unit in graph.nodes.items() if node_id not in covered}
    if uncovered:
        result.append(
            (CodeGraph(nodes=uncovered, language=graph.language), Path(root_path).resolve())
        )
    grouped: dict[Path, CodeGraph] = {}
    for selected, root in result:
        grouped.setdefault(_find_repo_root(root), CodeGraph(language=graph.language)).nodes.update(
            selected.nodes
        )
    return [(selected, root) for root, selected in grouped.items()]


def _detect_framework_entrypoints(graph: CodeGraph) -> dict[str, EntrypointTag]:
    """Scan source files for framework-specific entrypoint markers.

    Covers Python web/task/CLI decorators, Rust handler/FFI attributes,
    and Solidity visibility. Dispatch uses the source extension; shared C and
    Objective-C headers also use the node kind to select a detector.
    """
    cache = _SourceCache(graph.language)
    cache.servlet_owners = {
        e.target_id: e.source_id for e in graph.edges if e.kind == EdgeKind.CONTAINS
    }
    for edge in graph.edges:
        if edge.kind == EdgeKind.INHERITS:
            cache.servlet_bases.setdefault(edge.source_id, []).append(edge.target_id)
    result: dict[str, EntrypointTag] = {}
    for node_id, unit in graph.nodes.items():
        if unit.kind.value not in {"function", "method"}:
            continue
        path = unit.location.file_path
        if not path:
            continue

        tag = _detect_for_unit(cache, unit, path, graph)
        if tag is not None:
            result[node_id] = tag
        if Path(path).suffix.lower() in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}:
            _record_module_exports(graph, unit, cache)
    _annotate_overridden_solidity_entrypoints(graph, result)
    _record_python_decorators(graph, cache)
    return result


def _record_module_exports(graph: CodeGraph, unit: CodeUnit, cache: _SourceCache) -> None:
    for fact in cache.declarations.module_exports(unit):
        graph.add_entrypoint_evidence(
            EntrypointEvidence(
                unit.id,
                "declaration",
                "module_export",
                fact.location,
                symbol=fact.name,
                details=(("indirect", fact.indirect),),
                unresolved=("export_value", "runtime_activation")
                if fact.indirect
                else ("runtime_activation",),
            )
        )


def _record_python_decorators(graph: CodeGraph, cache: _SourceCache) -> None:
    edges = set(graph.edges)
    units = {
        (unit.location.file_path, unit.location.start_line, unit.location.start_col): unit
        for unit in graph.nodes.values()
        if unit.kind in {NodeKind.FUNCTION, NodeKind.METHOD}
    }
    owners = {
        edge.target_id: edge.source_id for edge in graph.edges if edge.kind == EdgeKind.CONTAINS
    }
    for path, facts in cache._python_decorators.items():
        for fact in facts:
            unit = units.get((path, fact.handler_line, fact.handler_col))
            if unit is None or fact.disposition == "non_path_decorator":
                continue
            source = owners.get(unit.id)
            details = (
                ("consumer_path", fact.consumer_path),
                ("receiver_binding", fact.receiver_binding),
                ("decorator_method", fact.method),
                ("path", fact.path),
                ("reason", fact.reason),
            )
            graph.add_entrypoint_evidence(
                EntrypointEvidence(
                    unit.id,
                    "heuristic" if fact.disposition == "path_decorator" else "reference",
                    "python.decorator",
                    fact.location,
                    source_id=source,
                    details=details,
                    arguments=fact.arguments,
                    unresolved=("registrar_semantics", "runtime_activation"),
                )
            )
            if source is not None:
                edge = CodeEdge(
                    source,
                    unit.id,
                    EdgeKind.REFERENCES,
                    location=fact.location,
                    attributes=(("reference_context", "decorator"), *details),
                )
                if edge not in edges:
                    edges.add(edge)
                    graph.edges.append(edge)


def _detect_for_unit(
    cache: _SourceCache, unit: CodeUnit, path: str, graph: CodeGraph
) -> EntrypointTag | None:
    extension = Path(path).suffix.lower()
    if extension == ".java":
        return _detect_java(
            cache, unit, path, servlet_callback=_java_servlet_callback(graph, unit, cache)
        )
    if extension == ".h" and unit.kind == NodeKind.METHOD:
        return _detect_objc(unit)
    if extension == ".zig" and dict(unit.attributes).get("zig_export"):
        return _export_tag("Zig exported function")
    if extension == ".ak" and _unit_attr(unit, "aiken_handler"):
        return _export_tag("Aiken validator handler")
    return _detect_registered(extension, cache, unit, path)


def _detect_registered(
    extension: str,
    cache: _SourceCache,
    unit: CodeUnit,
    path: str,
) -> EntrypointTag | None:
    if detector := _SOURCE_DETECTORS.get(extension):
        return detector(cache, unit, path)
    if detector := _UNIT_DETECTORS.get(extension):
        return detector(unit)
    return None


def _export_tag(description: str) -> EntrypointTag:
    return EntrypointTag(
        kind=EntrypointKind.API,
        trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
        description=description,
        asset_value=AssetValue.HIGH,
    )


def _detect_python(
    cache: _SourceCache,
    unit: CodeUnit,
    path: str,
) -> EntrypointTag | None:
    for fact in cache.python_decorators(path):
        if (
            fact.handler_line == unit.location.start_line
            and (unit.location.start_col is None or fact.handler_col == unit.location.start_col)
            and fact.member_access
            and fact.method in _PY_HTTP_METHODS
            and fact.disposition != "non_path_decorator"
        ):
            return EntrypointTag(
                kind=EntrypointKind.API,
                trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
                description="Python HTTP route decorator",
                asset_value=AssetValue.HIGH,
            )
    decorators = cache.declarations.decorators(unit)
    for line in decorators:
        if _PY_CLI_DECORATOR.match(line):
            return EntrypointTag(
                kind=EntrypointKind.USER_INPUT,
                trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
                description="Python CLI command (Click/Typer)",
                asset_value=AssetValue.MEDIUM,
            )
        if _PY_TASK_DECORATOR.match(line):
            return EntrypointTag(
                kind=EntrypointKind.THIRD_PARTY,
                trust_level=TrustLevel.SEMI_TRUSTED_EXTERNAL,
                description="Python task queue handler (Celery)",
                asset_value=AssetValue.MEDIUM,
            )
    return None


def _detect_rust(
    cache: _SourceCache,
    unit: CodeUnit,
    path: str,
) -> EntrypointTag | None:
    decorators = cache.declarations.decorators(unit)
    signature = cache.declarations.signature(unit)

    for line in decorators:
        if _RS_HTTP_ATTR.match(line):
            return EntrypointTag(
                kind=EntrypointKind.API,
                trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
                description="Rust HTTP handler attribute",
                asset_value=AssetValue.HIGH,
            )
        if _RS_NO_MANGLE.match(line):
            return EntrypointTag(
                kind=EntrypointKind.API,
                trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
                description="Rust FFI export (#[no_mangle])",
                asset_value=AssetValue.HIGH,
            )
        if _RS_ASYNC_MAIN_ATTR.match(line) and unit.name == "main":
            return EntrypointTag(
                kind=EntrypointKind.USER_INPUT,
                trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
                description="Rust async main (tokio/actix/async-std)",
                asset_value=AssetValue.HIGH,
            )
    if signature and _RS_EXTERN_C_FN.search(signature):
        return EntrypointTag(
            kind=EntrypointKind.API,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description='Rust FFI export (pub extern "C")',
            asset_value=AssetValue.HIGH,
        )
    return None


def _detect_solidity(
    cache: _SourceCache,
    unit: CodeUnit,
    path: str,
) -> EntrypointTag | None:
    del cache, path
    attributes = dict(unit.attributes)
    if attributes.get("solidity_container_kind") == "interface":
        return None
    if unit.name in {"fallback", "receive"}:
        return EntrypointTag(
            kind=EntrypointKind.API,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description="Solidity fallback/receive",
            asset_value=AssetValue.HIGH,
        )
    if attributes.get("solidity_visibility") in {"external", "public"}:
        return EntrypointTag(
            kind=EntrypointKind.API,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description="Solidity external/public function",
            asset_value=AssetValue.HIGH,
        )
    return None


def _annotate_overridden_solidity_entrypoints(
    graph: CodeGraph,
    detected: dict[str, EntrypointTag],
) -> None:
    """Annotate base implementations shadowed by a derived Solidity contract."""
    containers: dict[str, str] = {}
    for edge in graph.edges:
        if edge.kind == EdgeKind.CONTAINS and edge.target_id in graph.nodes:
            unit = graph.nodes[edge.target_id]
            if (
                unit.kind is NodeKind.METHOD
                and Path(unit.location.file_path).suffix.lower() == ".sol"
            ):
                containers[edge.target_id] = edge.source_id

    methods: dict[tuple[str, tuple[str, ...]], dict[str, str]] = {}
    for method_id, contract_id in containers.items():
        unit = graph.nodes[method_id]
        signature = (
            unit.name,
            tuple(p.type_ref.name if p.type_ref else "" for p in unit.parameters),
        )
        methods.setdefault(signature, {})[contract_id] = method_id

    bases: dict[str, set[str]] = {}
    for edge in graph.edges:
        if edge.kind == EdgeKind.INHERITS:
            bases.setdefault(edge.source_id, set()).add(edge.target_id)

    for by_contract in methods.values():
        for derived, method_id in by_contract.items():
            if method_id not in detected:
                continue
            stack = list(bases.get(derived, ()))
            visited: set[str] = set()
            while stack:
                base = stack.pop()
                if base in visited:
                    continue
                visited.add(base)
                base_method = by_contract.get(base)
                if base_method is not None:
                    _append_overridden_by(graph, base_method, method_id)
                stack.extend(bases.get(base, ()))


def _append_overridden_by(graph: CodeGraph, base_method: str, derived_method: str) -> None:
    unit = graph.nodes.get(base_method)
    if unit is None:
        return
    attributes = dict(unit.attributes)
    current = attributes.get("solidity_overridden_by")
    current_text = current if isinstance(current, str) else ""
    existing = {item.strip() for item in current_text.split(",") if item.strip()}
    existing.add(derived_method)
    attributes["solidity_overridden_by"] = ",".join(sorted(existing))
    graph.nodes[base_method] = dataclasses.replace(unit, attributes=tuple(attributes.items()))


def _detect_js_ts(
    cache: _SourceCache,
    unit: CodeUnit,
    path: str,
) -> EntrypointTag | None:
    route_tag = _detect_file_route(cache, unit, path)
    if route_tag is not None:
        return route_tag

    # AWS Lambda — `exports.handler = ...` or `export const handler = ...`
    if unit.name in {"handler", "lambdaHandler"}:
        signature = cache.line(path, unit.location.start_line) or ""
        if "exports.handler" in signature or "export " in signature:
            return EntrypointTag(
                kind=EntrypointKind.API,
                trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
                description="AWS Lambda handler",
                asset_value=AssetValue.HIGH,
            )

    # NestJS decorators on methods.
    decorators = cache.declarations.decorators(unit)
    for line in decorators:
        if _JS_NEST_DECORATOR.match(line):
            return EntrypointTag(
                kind=EntrypointKind.API,
                trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
                description="NestJS controller method",
                asset_value=AssetValue.HIGH,
            )
    return None


_HTTP_EXPORT_NAMES = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"})


def _detect_file_route(cache: _SourceCache, unit: CodeUnit, path: str) -> EntrypointTag | None:
    """Retain file-route conventions without attributing a framework or activation."""
    exported_names = {fact.name for fact in cache.declarations.module_exports(unit)}
    normalized = path.replace("\\", "/")
    basename = normalized.rsplit("/", 1)[-1]
    if basename.startswith("route.") and exported_names & _HTTP_EXPORT_NAMES:
        return EntrypointTag(
            kind=EntrypointKind.API,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description="HTTP-method export in route module (convention)",
            asset_value=AssetValue.HIGH,
        )
    if "/pages/api/" in normalized and exported_names & {"handler", "default"}:
        return EntrypointTag(
            kind=EntrypointKind.API,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description="Exported handler in API directory (convention)",
            asset_value=AssetValue.HIGH,
        )
    return None


def _java_servlet_callback(graph: CodeGraph, unit: CodeUnit, cache: _SourceCache) -> bool:
    if (
        unit.kind != NodeKind.METHOD
        or dict(unit.attributes).get("java_static")
        or dict(unit.attributes).get("java_private")
        or len(unit.parameters) != 2
        or unit.return_type is None
        or unit.return_type.name != "void"
    ):
        return False
    types = [
        p.type_ref.name.rsplit(".", 1)[-1] if p.type_ref is not None else ""
        for p in unit.parameters
    ]
    if types != ["HttpServletRequest", "HttpServletResponse"]:
        return False
    pending = [cache.servlet_owners.get(unit.id, "")]
    seen = set()
    while pending:
        owner = pending.pop()
        if owner in seen:
            continue
        seen.add(owner)
        declaration = graph.nodes.get(owner)
        if (
            declaration is not None
            and dict(declaration.attributes).get("java_servlet_base") is True
        ):
            return True
        pending.extend(cache.servlet_bases.get(owner, ()))
    return False


def _detect_java(
    cache: _SourceCache,
    unit: CodeUnit,
    path: str,
    *,
    servlet_callback: bool = False,
) -> EntrypointTag | None:
    decorators = cache.declarations.decorators(unit)
    for line in decorators:
        if _JAVA_SPRING_HANDLER.match(line):
            return EntrypointTag(
                kind=EntrypointKind.API,
                trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
                description="Spring MVC/WebFlux handler",
                asset_value=AssetValue.HIGH,
            )
        if _JAVA_JAXRS_HANDLER.match(line):
            return EntrypointTag(
                kind=EntrypointKind.API,
                trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
                description="JAX-RS handler",
                asset_value=AssetValue.HIGH,
            )
        if _JAVA_KAFKA_LISTENER.match(line):
            return EntrypointTag(
                kind=EntrypointKind.THIRD_PARTY,
                trust_level=TrustLevel.SEMI_TRUSTED_EXTERNAL,
                description="Kafka listener",
                asset_value=AssetValue.MEDIUM,
            )
    # Servlet convention: method names doGet/doPost/... on an HttpServlet.
    if servlet_callback and unit.name in {
        "doGet",
        "doPost",
        "doPut",
        "doDelete",
        "doHead",
        "doOptions",
        "doTrace",
    }:
        return EntrypointTag(
            kind=EntrypointKind.API,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description="HttpServlet method",
            asset_value=AssetValue.HIGH,
        )
    return None


def _detect_csharp(
    cache: _SourceCache,
    unit: CodeUnit,
    path: str,
) -> EntrypointTag | None:
    decorators = cache.declarations.decorators(unit)
    for line in decorators:
        if _CS_HTTP_ATTR.match(line):
            return EntrypointTag(
                kind=EntrypointKind.API,
                trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
                description="ASP.NET Core HTTP handler",
                asset_value=AssetValue.HIGH,
            )
        if _CS_AZURE_FUNC_ATTR.match(line):
            return EntrypointTag(
                kind=EntrypointKind.API,
                trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
                description="Azure Function",
                asset_value=AssetValue.HIGH,
            )
    return None


def _detect_php(
    cache: _SourceCache,
    unit: CodeUnit,
    path: str,
) -> EntrypointTag | None:
    decorators = cache.declarations.decorators(unit)
    for line in decorators:
        if _PHP_ROUTE_ATTR.match(line) or _PHP_ROUTE_ANNOTATION.match(line):
            return EntrypointTag(
                kind=EntrypointKind.API,
                trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
                description="Symfony Route attribute",
                asset_value=AssetValue.HIGH,
            )
    return None


def _detect_cairo(
    cache: _SourceCache,
    unit: CodeUnit,
    path: str,
) -> EntrypointTag | None:
    if dict(unit.attributes).get("cairo_abi") is True:
        return EntrypointTag(
            kind=EntrypointKind.API,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description="Cairo/StarkNet embedded ABI method",
            asset_value=AssetValue.HIGH,
        )
    decorators = cache.declarations.decorators(unit)
    for line in decorators:
        if _CAIRO_CONTRACT_ATTR.match(line):
            return EntrypointTag(
                kind=EntrypointKind.API,
                trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
                description="Cairo/StarkNet contract entrypoint",
                asset_value=AssetValue.HIGH,
            )
    return None


def _detect_circom(
    cache: _SourceCache,
    unit: CodeUnit,
    path: str,
) -> EntrypointTag | None:
    """Tag the synthetic callable emitted for a circuit's main component."""
    del cache, path
    if unit.name == "main" and unit.origin == NodeOrigin.SYNTHETIC:
        return EntrypointTag(
            kind=EntrypointKind.USER_INPUT,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description="Circom circuit main component",
            asset_value=AssetValue.HIGH,
        )
    return None


def _detect_masm(
    cache: _SourceCache,
    unit: CodeUnit,
    path: str,
) -> EntrypointTag | None:
    role = _unit_attr(unit, "masm_role")
    if role == "program":
        return EntrypointTag(
            kind=EntrypointKind.USER_INPUT,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description="Miden Assembly program begin block",
            asset_value=AssetValue.HIGH,
        )
    signature = cache.line(path, unit.location.start_line) or ""
    if role == "public" or _MASM_EXPORT.match(signature):
        return EntrypointTag(
            kind=EntrypointKind.API,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description="Miden Assembly exported procedure",
            asset_value=AssetValue.MEDIUM,
        )
    return None


def _detect_haskell(
    cache: _SourceCache,
    unit: CodeUnit,
    path: str,
) -> EntrypointTag | None:
    if unit.name != "main":
        return None
    # Confirm the file actually declares `main ::` or `main =` at column 0.
    for line in cache.iter_lines(path):
        if _HASKELL_MAIN.match(line):
            return EntrypointTag(
                kind=EntrypointKind.USER_INPUT,
                trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
                description="Haskell main",
                asset_value=AssetValue.HIGH,
            )
    return None


def _detect_erlang(
    cache: _SourceCache,
    unit: CodeUnit,
    path: str,
) -> EntrypointTag | None:
    """Mark any function listed in -export([...]) as an entrypoint."""
    if dict(unit.attributes).get("erlang_exported") is True:
        return EntrypointTag(
            kind=EntrypointKind.API,
            trust_level=TrustLevel.SEMI_TRUSTED_EXTERNAL,
            description="Erlang -export declaration",
            asset_value=AssetValue.MEDIUM,
        )
    return None


def _erlang_exported_names(cache: _SourceCache, path: str) -> set[str]:
    """Extract the names inside every -export([fn/arity, ...]) in the file."""
    names: set[str] = set()
    in_export = False
    buf: list[str] = []
    for line in cache.iter_lines(path):
        if not in_export:
            match = _ERLANG_EXPORT.match(line)
            if match:
                in_export = True
                buf = [line[match.end() :]]
        else:
            buf.append(line)
        if in_export and "]" in line:
            joined = " ".join(buf)
            list_section = joined.split("]", 1)[0]
            for entry in list_section.split(","):
                entry = entry.strip()
                if not entry or "/" not in entry:
                    continue
                name, _, _ = entry.partition("/")
                names.add(name.strip())
            in_export = False
            buf = []
    return names


def _detect_swift(
    cache: _SourceCache,
    unit: CodeUnit,
    path: str,
) -> EntrypointTag | None:
    """Detect an explicit type main method declared inside a Swift @main type."""
    del path
    if cache.declarations.is_swift_main(unit):
        return EntrypointTag(
            kind=EntrypointKind.USER_INPUT,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description="Swift @main app entrypoint",
            asset_value=AssetValue.HIGH,
        )
    return None


def _detect_kotlin(
    cache: _SourceCache,
    unit: CodeUnit,
    path: str,
) -> EntrypointTag | None:
    """Detect Kotlin entrypoints.

    Layers tried in order:
    1. Spring MVC / WebFlux annotations (``@GetMapping``, ``@PostMapping``,
       etc.) — shared with the Java detector because the annotations are
       identical across the two languages.
    2. Android Activity / Service / BroadcastReceiver lifecycle method
       names. These are attacker-reachable when the component is exported
       (``android:exported="true"`` in the manifest) — we over-detect
       here and let the override file tighten when appropriate.
    """
    decorators = cache.declarations.decorators(unit)
    for line in decorators:
        if _JAVA_SPRING_HANDLER.match(line):
            return EntrypointTag(
                kind=EntrypointKind.API,
                trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
                description="Spring MVC/WebFlux handler (Kotlin)",
                asset_value=AssetValue.HIGH,
            )
    if unit.name in _KOTLIN_ANDROID_LIFECYCLE_METHODS:
        return EntrypointTag(
            kind=EntrypointKind.API,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description="Android component lifecycle method",
            asset_value=AssetValue.HIGH,
        )
    return None


def _detect_go(
    cache: _SourceCache,
    unit: CodeUnit,
    path: str,
) -> EntrypointTag | None:
    """Detect Go entrypoints via call-site route registrations.

    Go has no annotation syntax for HTTP handlers — they're wired at
    call sites like ``http.HandleFunc("/path", name)`` or
    ``r.GET("/path", handler)``. For each file we compile the set of
    function names referenced as handlers, then tag matching nodes.
    Registrations that pass anonymous closures or method expressions
    (``obj.Method``) are also captured — the second form is recorded
    dotted; we tag any node whose basename matches.
    """
    handlers = cache.go_http_handler_names(path)
    if not handlers:
        return None
    if unit.name in handlers:
        return EntrypointTag(
            kind=EntrypointKind.API,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description="Go HTTP handler (net/http or router DSL)",
            asset_value=AssetValue.HIGH,
        )
    return None


def _detect_ruby(
    cache: _SourceCache,
    unit: CodeUnit,
    path: str,
) -> EntrypointTag | None:
    """Detect Ruby entrypoints: Rails controller actions and Sidekiq workers.

    Rails controllers are classes that inherit from
    ``ApplicationController`` / ``ActionController::*`` — every public
    instance method of such a class is an action. We tag any method
    whose node id starts with ``<Module>:<ClassName>.`` and whose class
    name appears in the file's controller set.

    Sidekiq workers are classes that ``include Sidekiq::Worker`` /
    ``include Sidekiq::Job``. Their ``perform`` method handles queue
    messages — only that one method is tagged per worker class.
    """
    attributes = dict(unit.attributes)
    instance = attributes.get("ruby_receiver") == "instance"
    if (
        instance
        and attributes.get("ruby_controller") is True
        and attributes.get("ruby_visibility") == "public"
        and unit.name != "initialize"
    ):
        return EntrypointTag(
            kind=EntrypointKind.API,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description="Rails controller action",
            asset_value=AssetValue.HIGH,
        )
    if instance and attributes.get("ruby_worker") is True and unit.name == "perform":
        return EntrypointTag(
            kind=EntrypointKind.THIRD_PARTY,
            trust_level=TrustLevel.SEMI_TRUSTED_EXTERNAL,
            description="Sidekiq worker perform method",
            asset_value=AssetValue.MEDIUM,
        )
    return None


def _detect_c_cpp(
    cache: _SourceCache,
    unit: CodeUnit,
    path: str,
) -> EntrypointTag | None:
    """Detect C / C++ library exports by explicit linkage markers.

    Matches ``extern "C"``, ``__attribute__((visibility("default")))``,
    and ``__declspec(dllexport)`` belonging to the declaration.
    Enclosing ``extern "C"`` blocks also apply to their functions.
    Plain non-``static`` functions in headers are NOT flagged by default
    because that inversion ("everything not static is exported") is too
    broad for audit purposes — use the override file for projects where
    that's the intent.
    """
    if any(_C_EXPORT_MARKERS.match(marker) for marker in cache.declarations.c_export_markers(unit)):
        return EntrypointTag(
            kind=EntrypointKind.API,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description='C/C++ exported symbol (extern "C" / visibility / dllexport)',
            asset_value=AssetValue.HIGH,
        )
    return None


def _detect_dart(
    cache: _SourceCache,
    unit: CodeUnit,
    path: str,
) -> EntrypointTag | None:
    """Detect Dart entrypoints.

    Today covers ``@pragma('vm:entry-point')``, the explicit Dart marker
    for functions invoked from native code (FFI callbacks, platform-
    channel handlers, deferred loading targets). Flutter lifecycle
    methods on ``StatefulWidget`` / ``StatelessWidget`` (``build``,
    ``initState``, ``dispose``) are not flagged here because they
    execute in-process and aren't directly attacker-reachable —
    add them via the override file if you want them surfaced.
    """
    decorators = cache.declarations.decorators(unit)
    for line in decorators:
        if _DART_VM_ENTRY_POINT.match(line):
            return EntrypointTag(
                kind=EntrypointKind.API,
                trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
                description="Dart @pragma('vm:entry-point') native callback",
                asset_value=AssetValue.HIGH,
            )
    return None


def _detect_move(
    cache: _SourceCache,
    unit: CodeUnit,
    path: str,
) -> EntrypointTag | None:
    attributes = dict(unit.attributes)
    if unit.kind.value == "function" and (
        attributes.get("move_entry") or attributes.get("move_public")
    ):
        return EntrypointTag(
            kind=EntrypointKind.API,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description="Move public/entry function",
            asset_value=AssetValue.HIGH,
        )
    return None


def _detect_tact(unit: CodeUnit) -> EntrypointTag | None:
    tact_role = _unit_attr(unit, "tact_role") or unit.name
    if unit.kind.value == "method" and tact_role in {"init", "receive", "external", "bounced"}:
        return EntrypointTag(
            kind=EntrypointKind.API,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description="Tact contract receiver/initializer",
            asset_value=AssetValue.HIGH,
        )
    return None


def _detect_func(unit: CodeUnit) -> EntrypointTag | None:
    if unit.name in {"recv_internal", "recv_external", "run_ticktock"} or "func_method_id" in dict(
        unit.attributes
    ):
        return EntrypointTag(
            kind=EntrypointKind.API,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description="Func receiver/getter entrypoint",
            asset_value=AssetValue.HIGH,
        )
    return None


def _detect_sway(
    cache: _SourceCache,
    unit: CodeUnit,
    path: str,
) -> EntrypointTag | None:
    signature = cache.signature_block(path, unit.location.start_line) or ""
    if unit.kind.value == "function" and "pub fn" in signature:
        return EntrypointTag(
            kind=EntrypointKind.API,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description="Sway public function",
            asset_value=AssetValue.HIGH,
        )
    if unit.kind.value == "method" and dict(unit.attributes).get("sway_abi") is True:
        return EntrypointTag(
            kind=EntrypointKind.API,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description="Sway ABI method",
            asset_value=AssetValue.HIGH,
        )
    return None


def _detect_rego(unit: CodeUnit) -> EntrypointTag | None:
    if unit.name in {"allow", "deny", "violation"}:
        return EntrypointTag(
            kind=EntrypointKind.API,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description="Rego policy decision rule",
            asset_value=AssetValue.HIGH,
        )
    return None


def _detect_proto(
    cache: _SourceCache,
    unit: CodeUnit,
    path: str,
) -> EntrypointTag | None:
    del cache, path
    if unit.kind.value == "method" and _unit_attr(unit, "schema_role") == "rpc":
        return EntrypointTag(
            kind=EntrypointKind.API,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description="Protocol Buffers service RPC",
            asset_value=AssetValue.HIGH,
        )
    return None


def _detect_thrift(
    cache: _SourceCache,
    unit: CodeUnit,
    path: str,
) -> EntrypointTag | None:
    del cache, path
    if unit.kind.value == "method" and _unit_attr(unit, "schema_role") == "service_function":
        return EntrypointTag(
            kind=EntrypointKind.API,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description="Thrift service function",
            asset_value=AssetValue.HIGH,
        )
    return None


def _detect_graphql(unit: CodeUnit) -> EntrypointTag | None:
    if unit.kind.value == "method" and _unit_attr(unit, "schema_role") == "root_operation":
        return EntrypointTag(
            kind=EntrypointKind.API,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description="GraphQL root operation field",
            asset_value=AssetValue.HIGH,
        )
    return None


def _unit_attr(unit: CodeUnit, key: str) -> str | None:
    for attr_key, attr_value in unit.attributes:
        if attr_key == key and isinstance(attr_value, str):
            return attr_value
    return None


def _detect_objc(unit: CodeUnit) -> EntrypointTag | None:
    """Detect Objective-C entrypoints by AppDelegate selectors.

    AppDelegate protocol selectors are high-value attack surface —
    ``application:openURL:options:`` handles deep-link invocations and
    ``application:didFinishLaunchingWithOptions:`` is the first code
    reached after launch. Their signatures are stable enough that a
    name match is sufficient.
    """
    if unit.name in _OBJC_APP_DELEGATE_SELECTORS:
        return EntrypointTag(
            kind=EntrypointKind.API,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description="Objective-C UIApplicationDelegate lifecycle method",
            asset_value=AssetValue.HIGH,
        )
    return None


class _SourceCache:
    """Lazily reads and caches source files during a detection pass."""

    def __init__(self, language: str | None = None) -> None:
        self.servlet_owners: dict[str, str] = {}
        self.servlet_bases: dict[str, list[str]] = {}
        self._lines: dict[str, list[str]] = {}
        self._scan_cache: dict[str, set[str]] = {}
        self._python_decorators: dict[str, list[DecoratorFact]] = {}
        self.declarations = DeclarationCache(language)

    def python_decorators(self, path: str) -> list[DecoratorFact]:
        if path not in self._python_decorators:
            self._python_decorators[path] = python_decorators(path)
        return self._python_decorators[path]

    def _read(self, path: str) -> list[str]:
        cached = self._lines.get(path)
        if cached is not None:
            return cached
        try:
            text = Path(path).read_text()
        except (OSError, UnicodeDecodeError):
            text = ""
        lines = text.splitlines()
        self._lines[path] = lines
        return lines

    def line(self, path: str, one_indexed: int) -> str | None:
        lines = self._read(path)
        idx = one_indexed - 1
        if 0 <= idx < len(lines):
            return lines[idx]
        return None

    def iter_lines(self, path: str) -> list[str]:
        """Return all lines of the file (cached) for whole-file scans."""
        return self._read(path)

    def go_http_handler_names(self, path: str) -> set[str]:
        """Extract Go HTTP handler names registered in the file.

        Scans for ``http.HandleFunc("/path", name)``, ``http.Handle(...)``,
        and router-DSL shapes like ``r.GET("/path", handler)``. The
        handler reference is usually a bare identifier (``handler``) but
        can be a method expression (``obj.Method``); we record the last
        dotted segment for lookup against Trailmark node names.
        """
        key = f"go_handlers::{path}"
        cached = self._scan_cache.get(key)
        if cached is not None:
            return cached
        names: set[str] = set()
        for line in self._read(path):
            for match in _GO_HTTP_HANDLE.finditer(line):
                names.add(match.group(2).rsplit(".", 1)[-1])
            for match in _GO_ROUTER_VERB.finditer(line):
                names.add(match.group(3).rsplit(".", 1)[-1])
        self._scan_cache[key] = names
        return names

    def ruby_rails_controller_classes(self, path: str) -> set[str]:
        """Extract class names that inherit from Rails controller bases."""
        key = f"ruby_rails::{path}"
        cached = self._scan_cache.get(key)
        if cached is not None:
            return cached
        names: set[str] = set()
        for line in self._read(path):
            match = _RUBY_RAILS_CONTROLLER_CLASS.match(line)
            if match:
                names.add(match.group(1))
        self._scan_cache[key] = names
        return names

    def ruby_sidekiq_worker_classes(self, path: str) -> set[str]:
        """Return class names whose body contains ``include Sidekiq::Worker``.

        Tracked by scanning for the include directive and associating it
        with the most recently opened ``class <Name>`` block. This is a
        one-level heuristic — nested classes aren't perfectly modeled —
        but matches idiomatic Sidekiq worker layout.
        """
        key = f"ruby_sidekiq::{path}"
        cached = self._scan_cache.get(key)
        if cached is not None:
            return cached
        names: set[str] = set()
        class_stack: list[str] = []
        for line in self._read(path):
            stripped = line.strip()
            # Crude class-open / class-close tracking. Good enough for
            # typical worker files, not a general Ruby parser.
            class_open = re.match(r"^\s*class\s+(\w+)\b", line)
            if class_open:
                class_stack.append(class_open.group(1))
                continue
            if stripped == "end" and class_stack:
                class_stack.pop()
                continue
            if class_stack and _RUBY_SIDEKIQ_INCLUDE.match(line):
                names.add(class_stack[-1])
        self._scan_cache[key] = names
        return names

    def signature_block(self, path: str, start_line: int) -> str | None:
        """Return the function signature as a single line.

        Solidity / Rust signatures can wrap across several lines. Join
        up to 8 lines starting at ``start_line`` and stop at the first
        line containing an opening brace.
        """
        lines = self._read(path)
        idx = start_line - 1
        if idx < 0 or idx >= len(lines):
            return None
        parts: list[str] = []
        for offset in range(8):
            if idx + offset >= len(lines):
                break
            parts.append(lines[idx + offset])
            if "{" in lines[idx + offset]:
                break
        return " ".join(parts)


def _find_repo_root(start: Path) -> Path:
    """Walk up until we find a directory with pyproject.toml, or give up.

    Falls back to ``start`` if nothing is found so the caller still has a
    sensible base path for the override file lookup.
    """
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
        if (candidate / OVERRIDE_FILE).exists():
            return candidate
    return start


def _detect_main_functions(graph: CodeGraph) -> dict[str, EntrypointTag]:
    """Mark any top-level function named ``main`` as a CLI entrypoint.

    Uses TRUSTED_INTERNAL because the developer explicitly invoked it —
    it's an API boundary but not an external attacker surface by default.
    Users who want a stricter posture can override via the override file.
    """
    result: dict[str, EntrypointTag] = {}
    for node_id, unit in graph.nodes.items():
        is_vb = Path(unit.location.file_path).suffix.lower() == ".vb"
        if (is_vb and not dict(unit.attributes).get("vb_main")) or (
            not is_vb and unit.name != "main"
        ):
            continue
        if unit.kind.value not in {"function", "method"}:
            continue
        result[node_id] = EntrypointTag(
            kind=EntrypointKind.USER_INPUT,
            trust_level=TrustLevel.TRUSTED_INTERNAL,
            description="CLI main() entrypoint",
            asset_value=AssetValue.LOW,
        )
    return result


def _detect_pyproject_scripts(
    graph: CodeGraph,
    repo_root: Path,
) -> dict[str, EntrypointTag]:
    """Read ``[project.scripts]`` from pyproject.toml and tag each target.

    Entries take the form ``name = "module.path:function"``. We first try the
    matching node ID directly, then fall back to an unambiguous file path suffix
    match for parses rooted below the import root.
    """
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.exists():
        return {}

    try:
        source = pyproject.read_text()
        data = tomllib.loads(source)
    except (OSError, ValueError):
        return {}

    project = data.get("project")
    if not isinstance(project, dict):
        return {}
    scripts_raw = project.get("scripts")
    if not isinstance(scripts_raw, dict):
        return {}

    result: dict[str, EntrypointTag] = {}
    for script_name, target in scripts_raw.items():
        if not isinstance(target, str) or ":" not in target:
            continue
        module_path, func_name = target.rsplit(":", 1)
        node_id = _resolve_script_target(graph, module_path, func_name)
        graph.add_entrypoint_evidence(
            EntrypointEvidence(
                node_id,
                "declaration",
                "pyproject_script",
                SourceLocation(str(pyproject), 1, max(1, len(source.splitlines()))),
                symbol=target,
                binding="resolved" if node_id else "unresolved",
                details=(("script_name", script_name), ("location_precision", "file")),
                unresolved=() if node_id else ("script_target",),
            )
        )
        if node_id is None:
            continue
        result[node_id] = EntrypointTag(
            kind=EntrypointKind.USER_INPUT,
            trust_level=TrustLevel.UNTRUSTED_EXTERNAL,
            description=f"pyproject.toml [project.scripts] entry ({target})",
            asset_value=AssetValue.MEDIUM,
        )
    return result


def _resolve_script_target(
    graph: CodeGraph,
    module_path: str,
    func_name: str,
) -> str | None:
    """Find the node id matching a ``module.path:function`` script target."""
    exact_id = f"{module_path}:{func_name}"
    unit = graph.nodes.get(exact_id)
    if unit is not None and unit.name == func_name:
        return exact_id

    suffix = module_path.replace(".", "/") + ".py"
    matches: list[str] = []
    for node_id, unit in graph.nodes.items():
        if unit.name != func_name:
            continue
        path = unit.location.file_path.replace("\\", "/")
        if path == suffix or path.endswith(f"/{suffix}"):
            matches.append(node_id)
    if len(matches) == 1:
        return matches[0]
    return None


def _load_override_file(
    graph: CodeGraph,
    repo_root: Path,
) -> dict[str, EntrypointTag]:
    """Parse ``.trailmark/entrypoints.toml`` into EntrypointTag entries.

    An entry may identify a single node by id or reference, OR declare a
    rule that matches many nodes at once. Rule-based entries accept any
    combination of:

    - ``file_glob``: match slash-bounded source path suffixes within the
      configuration root against a shell glob supporting ``**`` (recursive)
      and ``*`` (single-segment).
    - ``param_type``: match functions whose parameter list includes a
      declared type with this name (exact ``TypeRef.name`` match).
    - ``name_regex``: match functions whose ``name`` satisfies the regex.

    When multiple conditions are supplied in one entry they are combined
    with AND. Later entries in the file override earlier ones when two
    rules would tag the same node.

    Examples:

        # Single-node (unchanged)
        [[entrypoint]]
        node = "cli:main"
        kind = "api"

        # All PHP scripts under public_html/
        [[entrypoint]]
        file_glob = "public_html/**/*.php"
        kind = "user_input"
        trust = "untrusted_external"
        asset_value = "high"

        # Any function that takes a PSR-7 request
        [[entrypoint]]
        param_type = "ServerRequestInterface"
        kind = "api"
        trust = "untrusted_external"

        # Functions whose name starts with `handle_`
        [[entrypoint]]
        name_regex = "^handle_"
        kind = "api"
    """
    path = repo_root / OVERRIDE_FILE
    if not path.exists():
        return {}

    try:
        source = path.read_text()
        data = tomllib.loads(source)
    except (OSError, ValueError):
        return {}

    entries = data.get("entrypoint")
    if not isinstance(entries, list):
        return {}

    result: dict[str, EntrypointTag] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        for node_id, tag in _entry_to_matches(graph, entry, repo_root):
            result[node_id] = tag
            graph.add_entrypoint_evidence(
                EntrypointEvidence(
                    node_id,
                    "override",
                    "entrypoint_override",
                    SourceLocation(str(path), 1, max(1, len(source.splitlines()))),
                    symbol=entry.get("node"),
                    details=(("entry_index", index), ("location_precision", "file")),
                )
            )
    return result


def _entry_to_matches(
    graph: CodeGraph,
    entry: dict[str, Any],
    repo_root: Path,
) -> list[tuple[str, EntrypointTag]]:
    """Produce every (node_id, tag) pair an override-file entry implies.

    Returns an empty list if the entry is malformed, references an
    unknown node, or matches nothing.
    """
    # A mistyped condition must not silently broaden a multi-condition rule.
    for field in ("node", "file_glob", "param_type", "name_regex", "description"):
        if field in entry and not isinstance(entry[field], str):
            return []
    tag = _entry_tag(entry)
    if tag is None:
        return []

    node_ref = entry.get("node")
    if isinstance(node_ref, str):
        node_id = _resolve_override_node(graph, node_ref)
        if node_id is None:
            logging.getLogger(__name__).warning("Entrypoint override matches no node: %s", node_ref)
            return []
        return [(node_id, tag)]

    # Rule-based entry — compile match conditions.
    conditions = _compile_rule_conditions(entry, repo_root)
    if conditions is None:
        # No recognized rule fields (and no `node`); nothing to do.
        return []

    matches: list[tuple[str, EntrypointTag]] = []
    for node_id, unit in graph.nodes.items():
        if unit.kind.value not in {"function", "method"}:
            continue
        if all(condition(unit) for condition in conditions):
            matches.append((node_id, tag))
    return matches


def _entry_tag(entry: dict[str, Any]) -> EntrypointTag | None:
    """Build the EntrypointTag shared across every match of an entry."""
    kind_name = entry.get("kind", "user_input")
    trust_name = entry.get("trust", "untrusted_external")
    asset_name = entry.get("asset_value", "medium")
    description = entry.get("description")

    if not all(isinstance(value, str) for value in (kind_name, trust_name, asset_name)):
        return None
    kind = _KIND_BY_NAME.get(kind_name)
    trust = _TRUST_BY_NAME.get(trust_name)
    asset = _ASSET_BY_NAME.get(asset_name)
    if kind is None or trust is None or asset is None:
        return None

    return EntrypointTag(
        kind=kind,
        trust_level=trust,
        description=description if isinstance(description, str) else None,
        asset_value=asset,
    )


def _compile_rule_conditions(
    entry: dict[str, Any],
    repo_root: Path,
) -> list[Any] | None:
    """Translate rule fields into predicates evaluated per CodeUnit.

    Returns None if the entry contains no recognized rule fields.
    """
    conditions: list[Any] = []
    file_glob = entry.get("file_glob")
    if isinstance(file_glob, str):
        pattern = _glob_to_regex(file_glob)
        conditions.append(
            lambda unit, p=pattern: _matches_file_glob(unit.location.file_path, p, repo_root),
        )

    param_type = entry.get("param_type")
    if isinstance(param_type, str):
        conditions.append(
            lambda unit, t=param_type: any(
                p.type_ref is not None and p.type_ref.name == t for p in unit.parameters
            ),
        )

    name_regex = entry.get("name_regex")
    if isinstance(name_regex, str):
        try:
            pattern = re.compile(name_regex)
        except re.error:
            return None
        conditions.append(
            lambda unit, p=pattern: bool(p.search(unit.name)),
        )

    return conditions or None


def _matches_file_glob(path: str, pattern: re.Pattern[str], repo_root: Path) -> bool:
    """Match suffixes within the root, independent of checkout parent names."""
    if not path:
        return False
    source_path = Path(path.replace("\\", "/"))
    root = repo_root.resolve()
    if not source_path.is_absolute():
        cwd_path = source_path.resolve()
        source_path = cwd_path if cwd_path.is_relative_to(root) else root / source_path
    try:
        relative = source_path.resolve().relative_to(root).as_posix()
    except ValueError:
        return False
    return pattern.search(relative) is not None


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    return compile_path_glob(pattern, suffix=True)


def _resolve_override_node(graph: CodeGraph, reference: str) -> str | None:
    """Resolve an override reference to a concrete node id.

    Accepts either a literal node id (``cli:main``) or a Python-style
    ``module.path:function`` reference, which we resolve the same way
    pyproject.toml scripts are resolved.
    """
    if reference in graph.nodes:
        return reference
    scoped = [node_id for node_id in graph.nodes if node_id.endswith("::" + reference)]
    if len(scoped) == 1:
        return scoped[0]
    if ":" in reference:
        module_path, func_name = reference.rsplit(":", 1)
        return _resolve_script_target(graph, module_path, func_name)
    return None


_SOURCE_DETECTORS = {
    extension: detector
    for extensions, detector in (
        ((".py",), _detect_python),
        ((".rs",), _detect_rust),
        ((".sol",), _detect_solidity),
        ((".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"), _detect_js_ts),
        ((".cs",), _detect_csharp),
        ((".php", ".phtml"), _detect_php),
        ((".cairo",), _detect_cairo),
        ((".circom",), _detect_circom),
        ((".masm",), _detect_masm),
        ((".hs",), _detect_haskell),
        ((".erl",), _detect_erlang),
        ((".swift",), _detect_swift),
        ((".kt", ".kts"), _detect_kotlin),
        ((".dart",), _detect_dart),
        ((".move",), _detect_move),
        ((".sw",), _detect_sway),
        ((".proto",), _detect_proto),
        ((".thrift",), _detect_thrift),
        ((".go",), _detect_go),
        ((".rb",), _detect_ruby),
        ((".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hh", ".hxx"), _detect_c_cpp),
    )
    for extension in extensions
}
_UNIT_DETECTORS = {
    ".m": _detect_objc,
    ".mm": _detect_objc,
    ".tact": _detect_tact,
    ".fc": _detect_func,
    ".func": _detect_func,
    ".rego": _detect_rego,
    ".graphql": _detect_graphql,
    ".gql": _detect_graphql,
    ".graphqls": _detect_graphql,
}
