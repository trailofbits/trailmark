"""Dart language parser using tree-sitter.

Targets Dart source used in Flutter apps and command-line Dart programs.
The Dart grammar splits each function into two sibling nodes —
``function_signature`` (or ``method_signature`` inside a class) followed
by ``function_body`` — so the extractor walks siblings and pairs them.

Extracts:

- Top-level functions (``function_signature`` + ``function_body``).
- Classes (``class_definition``) and abstract classes.
- Methods inside class bodies (``method_signature`` + ``function_body``).
- Parameters from ``formal_parameter_list`` → ``formal_parameter``.
- Return types from the first type node on the signature.
- Imports (``library_import`` → ``import_specification``).
- Annotations tracked as they precede a declaration so
  ``@pragma('vm:entry-point')`` can be surfaced to the entrypoint detector.

Known gap: ``throw`` statements are not currently captured into
``exception_types``. The ``throw_expression`` node exists but Dart also
exposes standalone ``throw``-statement forms that need a dedicated walk.
"""

from __future__ import annotations

from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import (
    BranchInfo,
    CodeUnit,
    NodeKind,
    Parameter,
    SourceLocation,
    TypeRef,
)
from trailmark.parsers._binding_index import binding_scope
from trailmark.parsers._bindings import guard_call
from trailmark.parsers._common import (
    add_contains_edge,
    add_module_node,
    collect_body_info,
    compute_complexity,
    extract_type_parameters,
    first_child_by_type,
    make_location,
    module_id_from_path,
    node_text,
    parse_directory,
)
from trailmark.parsers._declarations import bind_lexical_calls
from trailmark.parsers._nesting import bounded_nesting
from trailmark.parsers._walk import walk_owned

_EXTENSIONS = (".dart",)

_BRANCH_NODE_TYPES = frozenset(
    {
        "if_statement",
        "for_statement",
        "while_statement",
        "do_statement",
        "switch_statement",
        "switch_case",
        "switch_default",
        "try_statement",
        "catch_clause",
    }
)

_THROW_TYPES: frozenset[str] = frozenset()

_DART_TYPE_NODES = frozenset(
    {
        "type_identifier",
        "void_type",
        "nullable_type",
        "function_type",
    }
)


class DartParser:
    """Parses Dart source files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "dart"

    def __init__(self) -> None:
        self._parser = Parser(get_language("dart"))

    @binding_scope
    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single .dart file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="dart", root_path=file_path)
        module_id = module_id_from_path(file_path)
        _visit_module(tree.root_node, file_path, module_id, graph)
        bind_lexical_calls(graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        """Parse all Dart source files under dir_path."""
        return parse_directory(
            self.parse_file,
            "dart",
            dir_path,
            _EXTENSIONS,
        )


def _visit_module(
    root: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    add_module_node(root, file_path, module_id, graph)
    _walk_siblings(root.children, file_path, module_id, module_id, graph)


_SIGNATURES = frozenset(
    {
        "function_signature",
        "method_signature",
        "constructor_signature",
        "factory_constructor_signature",
    }
)


def _walk_siblings(
    children: list[Node], file_path: str, module_id: str, container_id: str, graph: CodeGraph
) -> None:
    """Pair signatures with sibling bodies; annotations are read from source."""
    idx = 0
    while idx < len(children):
        node = children[idx]
        if node.type == "import_or_export":
            _extract_import(node, graph)
        elif node.type == "class_definition":
            _extract_class(node, file_path, module_id, graph)
        else:
            signature = _sibling_signature(node)
            if signature is not None:
                body = _following_body(children, idx) if node.type in _SIGNATURES else None
                _extract_function_from_signature(
                    signature,
                    body,
                    file_path,
                    module_id,
                    graph,
                    container_id=container_id,
                )
                idx += int(body is not None)
        idx += 1


def _sibling_signature(node: Node) -> Node | None:
    if node.type in _SIGNATURES:
        return node
    if node.type == "declaration":
        return next((child for child in node.children if child.type in _SIGNATURES), None)
    return None


def _following_body(children: list[Node], index: int) -> Node | None:
    body = children[index + 1] if index + 1 < len(children) else None
    return body if body is not None and body.type == "function_body" else None


@bounded_nesting
def _extract_class(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a Dart class (including abstract classes)."""
    name_node = first_child_by_type(node, "identifier")
    if name_node is None:
        return
    class_name = node_text(name_node)
    class_id = f"{module_id}:{class_name}"
    graph.nodes[class_id] = CodeUnit(
        id=class_id,
        name=class_name,
        kind=NodeKind.CLASS,
        location=make_location(node, file_path),
        type_parameters=extract_type_parameters(node),
    )
    add_contains_edge(graph, module_id, class_id)

    body = first_child_by_type(node, "class_body")
    if body is None:
        return
    _walk_siblings(body.children, file_path, module_id, class_id, graph)


@bounded_nesting
def _extract_function_from_signature(
    signature: Node,
    body: Node | None,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
    *,
    container_id: str,
    name_override: str | None = None,
) -> None:
    """Pair a signature with its body (if present) into a CodeUnit."""
    inner = _unwrap_method_signature(signature)
    name_node = _function_name_node(inner)
    if name_node is None and name_override is None:
        return
    constructor = inner.type in {"constructor_signature", "factory_constructor_signature"}
    func_name = (
        ".".join(node_text(c) for c in inner.named_children if c.type == "identifier")
        if constructor
        else name_override or (node_text(name_node) if name_node is not None else "")
    )
    is_method = container_id != module_id and graph.nodes[container_id].kind not in {
        NodeKind.FUNCTION,
        NodeKind.METHOD,
    }
    func_id = (
        f"{container_id}.{func_name}"
        if container_id != module_id
        else f"{container_id}:{func_name}"
    )

    branches, exception_types, calls = _declaration_body(signature, body, file_path)

    location = make_location(signature, file_path)
    # Extend the end line to cover the body too.
    if body is not None:
        location = _merge_locations(signature, body, file_path)

    unit = CodeUnit(
        id=func_id,
        name=func_name,
        kind=NodeKind.METHOD if is_method else NodeKind.FUNCTION,
        location=location,
        parameters=tuple(_extract_parameters(inner)),
        return_type=_extract_return_type(inner),
        exception_types=tuple(exception_types),
        type_parameters=extract_type_parameters(inner),
        cyclomatic_complexity=compute_complexity(branches),
        branches=tuple(branches),
        attributes=(("dart_constructor", constructor),),
    )
    graph.nodes[func_id] = unit
    add_contains_edge(graph, container_id, func_id)

    _extract_nested_functions(body, file_path, module_id, func_id, graph)
    _add_call_edges(calls, func_id, file_path, graph)


def _unwrap_method_signature(node: Node) -> Node:
    """Return the inner function_signature inside a method_signature wrapper."""
    if node.type == "method_signature":
        inner = next(
            (
                c
                for c in node.named_children
                if c.type
                in {"function_signature", "constructor_signature", "factory_constructor_signature"}
            ),
            None,
        )
        if inner is not None:
            return inner
    return node


def _function_name_node(signature: Node) -> Node | None:
    """Find the ``identifier`` that names the function (not type identifiers)."""
    seen_type = False
    for child in signature.children:
        if child.type in _DART_TYPE_NODES:
            seen_type = True
            continue
        if child.type == "type_arguments":
            continue
        if child.type == "identifier":
            if seen_type:
                return child
            # No explicit return type (e.g., constructor) — the first
            # identifier is the name.
            return child
    return None


def _extract_parameters(signature: Node) -> list[Parameter]:
    """Extract parameters from a function_signature's formal_parameter_list."""
    plist = (
        signature
        if signature.type == "formal_parameter_list"
        else first_child_by_type(signature, "formal_parameter_list")
    )
    if plist is None:
        return []
    params: list[Parameter] = []
    for child in plist.children:
        if child.type == "formal_parameter":
            _extract_single_param(child, params)
    return params


def _extract_single_param(decl: Node, params: list[Parameter]) -> None:
    """Extract one formal_parameter."""
    name = ""
    type_ref: TypeRef | None = None
    for child in decl.children:
        if child.type in _DART_TYPE_NODES and type_ref is None:
            type_ref = TypeRef(name=node_text(child))
        elif child.type == "identifier" and not name:
            name = node_text(child)
        elif child.type == "constructor_param":
            # `this.name` — grab the trailing identifier.
            for sub in child.children:
                if sub.type == "identifier":
                    name = node_text(sub)
                    break
    if name:
        params.append(Parameter(name=name, type_ref=type_ref))


def _extract_return_type(signature: Node) -> TypeRef | None:
    """The first type-bearing child is the declared return type."""
    for child in signature.children:
        if child.type in _DART_TYPE_NODES:
            return TypeRef(name=node_text(child))
        if child.type == "identifier":
            # Reached the name before any return type — treat as implicit.
            return None
    return None


def _collect_func_body(
    body: Node | None,
    file_path: str,
) -> tuple[list[BranchInfo], list[TypeRef], list[tuple[str, Node]]]:
    branches: list[BranchInfo] = []
    exception_types: list[TypeRef] = []
    calls: list[tuple[str, Node]] = []
    if body is not None:
        # Branches use the shared helper; Dart represents a call as an
        # `identifier` / `selector` sibling pair rather than a single call
        # node, so calls need a dedicated walk.
        collect_body_info(
            body,
            file_path,
            _BRANCH_NODE_TYPES,
            # Pass a sentinel that will never match so collect_body_info
            # skips its own call extraction; we handle calls separately.
            "__dart_calls_collected_separately__",
            _THROW_TYPES,
            branches,
            exception_types,
            calls,
            skip_types=frozenset({"function_expression", "lambda_expression"}),
        )
        _collect_dart_calls(body, calls)
    return branches, exception_types, calls


def _collect_dart_calls(body: Node, calls: list[tuple[str, Node]]) -> None:
    """Find call sites in a Dart body.

    Dart's AST renders ``foo()`` as two sibling nodes under an
    ``expression_statement`` or similar: an ``identifier`` followed by a
    ``selector`` that contains an ``argument_part``. Method calls
    ``obj.method()`` expand the identifier to a longer chain of
    ``identifier`` + ``selector`` + ``identifier`` + ... with the final
    selector holding the argument_part.
    """
    detached = _detached_arrow_call(body)
    if detached is not None:
        calls.append(detached)
    for node in walk_owned(
        body,
        root_is_owner=False,
        boundaries=frozenset({"function_expression", "lambda_expression"}),
    ):
        if node.type in {"function_expression", "lambda_expression"}:
            continue
        # Check each pair of adjacent children inside this node for the
        # "callee identifier -> arguments-bearing selector" pattern.
        children = node.children
        for idx, child in enumerate(children):
            if child.type != "selector":
                continue
            if not _selector_has_arguments(child):
                continue
            name = _resolve_dart_callee(children, idx)
            if name:
                calls.append((name, child))


def _detached_arrow_call(body: Node) -> tuple[str, Node] | None:
    """Recover arguments placed after a chain of expression closures by the grammar."""
    if body.type != "function_expression_body" or body.named_child_count != 1:
        return None
    callee = body.named_children[0]
    if callee.type != "identifier":
        return None
    current = body
    while current.type in {"function_expression_body", "function_expression"}:
        following = current.next_named_sibling
        if following is not None:
            if following.type == "selector" and _selector_has_arguments(following):
                return node_text(callee), following
            return None
        parent = current.parent
        if parent is None:
            break
        current = parent
    return None


def _selector_has_arguments(selector: Node) -> bool:
    """True if a ``selector`` node carries an ``argument_part`` (a call)."""
    return any(child.type == "argument_part" for child in selector.children)


def _resolve_dart_callee(children: list[Node], selector_idx: int) -> str:
    """Reconstruct the callee name from the prefix preceding a selector.

    Looks backwards from the selector collecting adjacent identifiers and
    non-argument selectors that build up a dotted name, e.g.
    ``obj.method`` from ``identifier('obj')`` + ``selector('.method')`` +
    argument-bearing ``selector('()')``.
    """
    parts: list[str] = []
    i = selector_idx - 1
    while i >= 0:
        prev = children[i]
        if prev.type in {"identifier", "this", "super"}:
            parts.append(node_text(prev))
            i -= 1
            continue
        if prev.type == "selector" and not _selector_has_arguments(prev):
            # e.g. `.method` — extract the identifier inside.
            inner = _selector_identifier(prev)
            if inner:
                parts.append(inner)
                i -= 1
                continue
            break
        break
    if not parts:
        return ""
    parts.reverse()
    return ".".join(parts)


def _selector_identifier(selector: Node) -> str:
    """Pull the identifier out of a dotted selector like `.method`."""
    for child in selector.children:
        if child.type == "identifier":
            return node_text(child)
        if child.type == "unconditional_assignable_selector":
            return _selector_identifier(child)
    return ""


def _add_call_edges(
    calls: list[tuple[str, Node]],
    source_id: str,
    file_path: str,
    graph: CodeGraph,
) -> None:
    for call_name, call_node in calls:
        confidence = EdgeConfidence.CERTAIN if "." not in call_name else EdgeConfidence.INFERRED
        graph.edges.append(
            guard_call(
                CodeEdge(
                    source_id=source_id,
                    target_id=call_name,
                    kind=EdgeKind.CALLS,
                    confidence=confidence,
                    location=make_location(call_node, file_path),
                    attributes=(("bare_name", call_name),) if call_name.isidentifier() else (),
                ),
                call_node,
                graph=graph,
            )
        )


def _extract_import(node: Node, graph: CodeGraph) -> None:
    """Capture `import 'package:foo/bar.dart';` style imports."""
    for child in node.children:
        if child.type == "library_import":
            _extract_library_import(child, graph)


def _extract_library_import(node: Node, graph: CodeGraph) -> None:
    spec = first_child_by_type(node, "import_specification")
    if spec is None:
        return
    raw_text = node_text(spec)
    # Pull the quoted URI and use its last path segment minus .dart.
    for quote in ("'", '"'):
        if quote in raw_text:
            parts = raw_text.split(quote)
            if len(parts) >= 2:
                uri = parts[1]
                dep = uri.rsplit("/", 1)[-1].removesuffix(".dart")
                if dep and dep not in graph.dependencies:
                    graph.dependencies.append(dep)
                return


def _merge_locations(
    start_node: Node,
    end_node: Node,
    file_path: str,
) -> SourceLocation:
    """Build a SourceLocation spanning two sibling nodes."""
    return SourceLocation(
        file_path=file_path,
        start_line=start_node.start_point.row + 1,
        end_line=end_node.end_point.row + 1,
        start_col=start_node.start_point.column,
        end_col=end_node.end_point.column,
    )


def _declaration_body(
    signature: Node, body: Node | None, file_path: str
) -> tuple[list[BranchInfo], list[TypeRef], list[tuple[str, Node]]]:
    branches, exception_types, calls = _collect_func_body(body, file_path)
    declaration = signature.parent
    initializers = first_child_by_type(signature, "initializers")
    if initializers is None and declaration is not None and declaration.type == "declaration":
        initializers = next(
            (c for c in declaration.named_children if c.type in {"initializers", "redirection"}),
            None,
        )
    if initializers is not None:
        extra_branches, extra_exceptions, extra_calls = _collect_func_body(initializers, file_path)
        branches = extra_branches + branches
        exception_types = extra_exceptions + exception_types
        calls = extra_calls + calls
    return branches, exception_types, calls


def _extract_nested_functions(
    body: Node | None, file_path: str, module_id: str, func_id: str, graph: CodeGraph
) -> None:
    if body is not None:
        for nested in walk_owned(
            body,
            root_is_owner=False,
            boundaries=frozenset({"function_expression", "lambda_expression"}),
        ):
            if nested.type in {"function_expression", "lambda_expression"}:
                parameters = nested.child_by_field_name("parameters")
                nested_body = nested.child_by_field_name("body")
                if parameters is not None:
                    name_node = _function_name_node(parameters)
                    name = (
                        node_text(name_node)
                        if name_node is not None
                        else f"$lambda@{nested.start_point.row + 1}_{nested.start_point.column}"
                    )
                    _extract_function_from_signature(
                        parameters,
                        nested_body,
                        file_path,
                        module_id,
                        graph,
                        container_id=func_id,
                        name_override=name,
                    )
