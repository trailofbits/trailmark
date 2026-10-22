"""Rust language parser using tree-sitter."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import replace
from pathlib import Path

from tree_sitter import Language, Node, Parser, Range, Tree
from tree_sitter_language_pack import get_language

from trailmark.models.diagnostics import ParseDiagnostic
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
from trailmark.parsers._bindings import guard_call
from trailmark.parsers._common import (
    add_contains_edge,
    add_module_node,
    collect_body_info,
    compute_complexity,
    extract_type_parameters,
    make_location,
    module_id_from_path,
    node_text,
    parse_directory,
)
from trailmark.parsers.rust._scopes import resolve_calls

_LOGGER = logging.getLogger(__name__)

_BRANCH_NODE_TYPES = frozenset(
    {
        "if_expression",
        "for_expression",
        "while_expression",
        "loop_expression",
        "match_arm",
        "else_clause",
        "conditional_expression",
    }
)

_THROW_TYPES: frozenset[str] = frozenset()

_EXTENSIONS = (".rs",)

_RUST_TYPE_NODES = frozenset(
    {
        "type_identifier",
        "generic_type",
        "reference_type",
        "scoped_type_identifier",
        "primitive_type",
    }
)


class RustParser:
    """Parses Rust source files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "rust"

    def __init__(self) -> None:
        self._parser = Parser(get_language("rust"))
        self._verus_parser: Parser | None = None
        self._verus_load_failed = False
        self._uses: list[tuple[str, Node]] = []
        self._declarations: dict[str, Node] = {}

    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single Rust file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="rust", root_path=file_path)
        module_id = module_id_from_path(file_path)
        self._uses = []
        self._declarations = {}
        add_module_node(tree.root_node, file_path, module_id, graph)
        self._visit_scope(tree.root_node, source, file_path, module_id, graph)
        resolve_calls(graph, self._uses, module_id, self._declarations)
        return graph

    def _visit_scope(
        self, root: Node, source: bytes, file_path: str, module_id: str, graph: CodeGraph
    ) -> None:
        """Visit declarations while retaining their module and method owners."""
        stack: list[tuple[Node, str, str | None]] = [
            (child, module_id, None) for child in reversed(_declarations_in(root))
        ]
        has_verus = b"verus" in source
        while stack:
            child, module_id, container_id = stack.pop()
            if child.type == "macro_invocation" and has_verus:
                tree = self._parse_macro(source, child, file_path, graph)
                declarations = _declarations_in(tree.root_node) if tree is not None else []
            else:
                self._record_use(child, module_id, container_id, graph)
                module_id, container_id, declarations = _visit_declaration(
                    child, file_path, module_id, container_id, graph, source=source
                )
                self._record_declaration(child, module_id, container_id)
                if child.type == "function_item":
                    _warn_hidden_verus(child, file_path, "inside a function body", graph)
            stack.extend((nested, module_id, container_id) for nested in reversed(declarations))

    def _record_use(
        self, node: Node, module_id: str, container_id: str | None, graph: CodeGraph
    ) -> None:
        if node.type == "use_declaration":
            self._uses.append((container_id or module_id, node))

    def _record_declaration(self, node: Node, module: str, container: str | None) -> None:
        if node.type == "mod_item":
            if node.child_by_field_name("body") is not None:
                self._declarations[container or module] = node
        elif container is not None and node.type in {
            "function_item",
            "struct_item",
            "enum_item",
            "trait_item",
        }:
            self._declarations[container] = node

    def _parse_macro(
        self, source: bytes, node: Node, file_path: str, graph: CodeGraph | None = None
    ) -> Tree | None:
        if _enclosing_function(node) is not None:
            return None
        name = _macro_name(node)
        if name is not None and node_text(name) == "verus":
            return self._parse_verus_block(source, node, file_path, graph)
        _warn_hidden_verus(node, file_path, "inside an opaque macro", graph)
        return None

    def _load_verus_parser(self) -> Parser | None:
        if self._verus_parser is None and not self._verus_load_failed:
            try:
                from trailmark.tree_sitter_custom.verus import language as verus_language

                self._verus_parser = Parser(Language(verus_language()))
            except (
                OSError,
                subprocess.SubprocessError,
                ImportError,
                ValueError,
                AttributeError,
            ) as exc:
                self._verus_load_failed = True
                _LOGGER.warning("Could not load the Verus grammar; skipping verus! blocks: %s", exc)
        return self._verus_parser

    def _parse_verus_block(
        self, source: bytes, node: Node, file_path: str, graph: CodeGraph | None = None
    ) -> Tree | None:
        """Parse one block independently so error recovery cannot consume its neighbors."""
        token_tree = next(
            (child for child in node.named_children if child.type == "token_tree"), None
        )
        opening = token_tree.child(0) if token_tree is not None else None
        if opening is None or opening.type != "{":
            _warn_skipped(node, file_path, "unsupported Verus delimiter; expected braces", graph)
            return None
        parser = self._load_verus_parser()
        if parser is None:
            _record_skipped(node, file_path, "Verus grammar unavailable", graph)
            return None
        name = _macro_name(node)
        start = name if name is not None else node
        parser.included_ranges = [
            Range(start.start_point, node.end_point, start.start_byte, node.end_byte)
        ]
        masked = _mask_unsupported_verus(source, node, file_path)
        if masked != source:
            _record_skipped(node, file_path, "unsupported nested Verus delimiter", graph)
        tree = parser.parse(masked)
        if tree.root_node.has_error:
            _warn_skipped(node, file_path, "Verus syntax errors; graph may be incomplete", graph)
        return tree

    def parse_directory(self, dir_path: str) -> CodeGraph:
        """Parse all .rs files under dir_path into a merged graph."""
        return parse_directory(
            self.parse_file,
            "rust",
            dir_path,
            _EXTENSIONS,
        )


def _macro_name(node: Node) -> Node | None:
    macro = node.child_by_field_name("macro")
    if macro is not None and macro.type == "scoped_identifier":
        return macro.child_by_field_name("name")
    return macro


def parse_syntax_roots(source: bytes, file_path: str) -> list[Node]:
    """Return native Rust and supported Verus syntax with original positions.

    Source-based analysis passes need the same macro boundaries and Verus
    grammar as graph extraction. Opaque macros and Verus inside functions
    remain excluded by the parser's existing policy.
    """
    parser = RustParser()
    root = parser._parser.parse(source).root_node
    roots = [root]
    pending = [root]
    while pending:
        node = pending.pop()
        if node.type == "function_item":
            continue
        if node.type == "macro_invocation":
            tree = parser._parse_macro(source, node, file_path)
            if tree is not None:
                roots.append(tree.root_node)
                pending.append(tree.root_node)
        else:
            pending.extend(reversed(node.named_children))
    return roots


def _mask_unsupported_verus(source: bytes, node: Node, file_path: str) -> bytes:
    masked = bytearray(source)
    for name, tokens in _verus_token_trees(node):
        delimiter = tokens.child(0)
        if delimiter is not None and delimiter.type != "{":
            _warn_skipped(name, file_path, "unsupported Verus delimiter; expected braces")
            # An opaque macro remains valid in both declaration and expression positions.
            masked[name.start_byte : name.end_byte] = b"vskip"
    return bytes(masked)


def _verus_token_trees(root: Node) -> list[tuple[Node, Node]]:
    """Find literal Verus invocations, including qualified names in token trees."""
    invocations: list[tuple[Node, Node]] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "macro_definition":
            continue
        if node.type == "function_item" and node != root:
            continue
        if node.type == "verus_block":
            invocations.append((node, node))
            continue
        children = [child for child in node.children if not child.is_extra]
        definitions = {
            children[index + 3].id
            for index, child in enumerate(children[:-3])
            if child.type == "identifier"
            and node_text(child) == "macro_rules"
            and children[index + 1].type == "!"
            and children[index + 2].type == "identifier"
            and children[index + 3].type == "token_tree"
        }
        for index, child in enumerate(children[:-2]):
            if (
                child.type in {"identifier", "scoped_identifier"}
                and node_text(child.child_by_field_name("name") or child) == "verus"
                and children[index + 1].type == "!"
                and children[index + 2].type == "token_tree"
            ):
                name = (
                    child.child_by_field_name("name")
                    if child.type == "scoped_identifier"
                    else child
                )
                if name is not None:
                    invocations.append((name, children[index + 2]))
        stack.extend(
            child for child in reversed(node.named_children) if child.id not in definitions
        )
    return invocations


def _warn_skipped(node: Node, file_path: str, reason: str, graph: CodeGraph | None = None) -> None:
    _LOGGER.warning(
        "%s:%d:%d: %s", file_path, node.start_point.row + 1, node.start_point.column + 1, reason
    )
    _record_skipped(node, file_path, reason, graph)


def _record_skipped(node: Node, file_path: str, reason: str, graph: CodeGraph | None) -> None:
    if graph is not None:
        graph.parse_diagnostics.append(
            ParseDiagnostic(
                "rust",
                "verus",
                make_location(node, file_path),
                "unsupported_syntax",
                reason,
            )
        )


def _warn_hidden_verus(
    node: Node, file_path: str, reason: str, graph: CodeGraph | None = None
) -> None:
    if b"verus" not in (node.text or b""):
        return
    invocations = _verus_token_trees(node)
    if invocations:
        _warn_skipped(invocations[0][0], file_path, f"skipping Verus declarations {reason}", graph)


def _visit_top_level_node(
    child: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
    *,
    container_id: str | None = None,
) -> str | None:
    """Dispatch a single top-level node."""
    if child.type == "struct_item":
        return _extract_type(
            child, file_path, module_id, graph, NodeKind.STRUCT, owner=container_id
        )
    elif child.type == "enum_item":
        return _extract_type(child, file_path, module_id, graph, NodeKind.ENUM, owner=container_id)
    elif child.type == "use_declaration":
        _extract_import(child, graph)
    return None


def _extract_type(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
    kind: NodeKind,
    *,
    owner: str | None = None,
) -> str | None:
    """Extract a struct, enum, or trait with its lexical owner."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    name = node_text(name_node)
    type_id = _scoped_id(node, name, module_id, owner, graph)
    graph.nodes[type_id] = CodeUnit(
        id=type_id,
        name=name,
        kind=kind,
        location=make_location(node, file_path),
        type_parameters=extract_type_parameters(node, field_name="type_parameters"),
        docstring=_extract_docstring(node),
    )
    add_contains_edge(graph, owner or module_id, type_id)
    return type_id


def _extract_impl(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
    *,
    container_id: str | None = None,
) -> str | None:
    """Extract an impl block, with optional trait implementation."""
    type_name = _extract_impl_type_name(node)
    if not type_name:
        return None
    type_id = _impl_type_id(node, type_name, module_id, container_id, graph)

    trait_node = node.child_by_field_name("trait")
    if trait_node is not None:
        _add_implements_edge(trait_node, type_id, module_id, graph, file_path=file_path)

    return type_id


def _impl_type_id(node: Node, name: str, module: str, owner: str | None, graph: CodeGraph) -> str:
    if owner is None:
        return f"{module}:{name}"
    parent = node.parent
    while parent is not None and parent.type != "function_item":
        if parent.type == "block":
            for child in _declarations_in(parent):
                if child.type not in {"struct_item", "enum_item"}:
                    continue
                child_name = child.child_by_field_name("name")
                if child_name is not None and node_text(child_name) == name:
                    return _scoped_id(child, name, module, owner, graph)
        parent = parent.parent
    return f"{owner}.{name}"


def _declarations_in(node: Node) -> list[Node]:
    """Unwrap declarations emitted by either Rust grammar."""
    declarations: list[Node] = []
    stack = list(reversed(node.named_children))
    while stack:
        child = stack.pop()
        if child.type in {"verus_block", "declaration_with_attrs", "expression_statement"}:
            stack.extend(reversed(child.named_children))
        else:
            declarations.append(child)
    return declarations


def _enclosing_function(node: Node) -> Node | None:
    parent = node.parent
    while parent is not None:
        if parent.type == "function_item":
            return parent
        parent = parent.parent
    return None


def _is_function(node_id: str | None, graph: CodeGraph) -> bool:
    return (
        node_id is not None
        and node_id in graph.nodes
        and graph.nodes[node_id].kind
        in {
            NodeKind.FUNCTION,
            NodeKind.METHOD,
        }
    )


def _local_declarations(body: Node | None) -> list[Node]:
    declarations: list[Node] = []
    stack = list(reversed(body.named_children)) if body is not None else []
    while stack:
        child = stack.pop()
        if child.type in {
            "function_item",
            "use_declaration",
            "mod_item",
            "impl_item",
            "trait_item",
            "struct_item",
            "enum_item",
        }:
            declarations.append(child)
        elif child.type not in {
            "macro_invocation",
            "verus_block",
            "macro_definition",
        }:
            stack.extend(reversed(child.named_children))
    return declarations


def _visit_declaration(
    node: Node,
    file_path: str,
    module_id: str,
    container_id: str | None,
    graph: CodeGraph,
    *,
    source: bytes,
) -> tuple[str, str | None, list[Node]]:
    body = node.child_by_field_name("body")
    if node.type == "function_item":
        function_id = _extract_function(
            node, file_path, module_id, container_id, graph, source=source
        )
        return module_id, function_id, _local_declarations(body)
    if node.type == "mod_item":
        name = node.child_by_field_name("name")
        if name is not None and body is not None:
            parent_id = container_id or module_id
            if container_id is not None:
                container_id = _scoped_id(node, node_text(name), module_id, container_id, graph)
                child_id = container_id
            else:
                module_id = f"{module_id}.{node_text(name)}"
                child_id = module_id
            add_module_node(node, file_path, child_id, graph)
            add_contains_edge(graph, parent_id, child_id)
    elif node.type == "trait_item":
        container_id = _extract_type(
            node, file_path, module_id, graph, NodeKind.TRAIT, owner=container_id
        )
    elif node.type == "impl_item":
        container_id = _extract_impl(node, file_path, module_id, graph, container_id=container_id)
    else:
        container_id = _visit_top_level_node(
            node, file_path, module_id, graph, container_id=container_id
        )
        body = None
    return module_id, container_id, _declarations_in(body) if body is not None else []


def _scoped_id(node: Node, name: str, module: str, owner: str | None, graph: CodeGraph) -> str:
    if owner is None:
        return f"{module}:{name}"
    node_id = f"{owner}.{name}"
    existing = graph.nodes.get(node_id)
    local = _is_function(owner, graph) or "." in owner.partition(":")[2]
    if existing is not None and (local or existing.kind in {NodeKind.FUNCTION, NodeKind.METHOD}):
        start = (existing.location.start_line - 1, existing.location.start_col)
        if start != node.start_point:
            return f"{node_id}@{node.start_byte}"
    return node_id


def _extract_impl_type_name(node: Node) -> str:
    """Extract the type name from an impl block."""
    type_node = node.child_by_field_name("type")
    if type_node is None:
        return ""
    if type_node.type == "type_identifier":
        return node_text(type_node)
    if type_node.type == "generic_type":
        for child in type_node.children:
            if child.type == "type_identifier":
                return node_text(child)
    return node_text(type_node)


def _add_implements_edge(
    trait_node: Node,
    type_id: str,
    module_id: str,
    graph: CodeGraph,
    *,
    file_path: str,
) -> None:
    """Add an IMPLEMENTS edge from type to trait."""
    trait_name = node_text(trait_node)
    trait_id = f"{module_id}:{trait_name}"
    graph.edges.append(
        CodeEdge(
            source_id=type_id,
            target_id=trait_id,
            kind=EdgeKind.IMPLEMENTS,
            confidence=EdgeConfidence.CERTAIN,
            location=make_location(trait_node, file_path),
        )
    )


def _extract_function(
    node: Node,
    file_path: str,
    module_id: str,
    container_id: str | None,
    graph: CodeGraph,
    *,
    source: bytes,
) -> str | None:
    """Extract a function or method definition."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    func_name = node_text(name_node)

    func_id = _scoped_id(node, func_name, module_id, container_id, graph)
    owner = container_id or module_id
    owner_node = graph.nodes.get(owner)
    method = container_id is not None and not _is_function(owner, graph)
    if owner_node is not None and owner_node.kind == NodeKind.MODULE:
        method = False
    kind = NodeKind.METHOD if method else NodeKind.FUNCTION

    body = node.child_by_field_name("body")

    branches, exception_types, calls = _collect_func_body(
        body,
        file_path,
        source,
    )

    unit = CodeUnit(
        id=func_id,
        name=func_name,
        kind=kind,
        location=make_location(node, file_path),
        parameters=tuple(_extract_parameters(node)),
        return_type=_extract_return_type(node),
        exception_types=tuple(exception_types),
        type_parameters=extract_type_parameters(node, field_name="type_parameters"),
        cyclomatic_complexity=compute_complexity(branches),
        branches=tuple(branches),
        docstring=_extract_docstring(node),
    )
    graph.nodes[func_id] = unit
    add_contains_edge(graph, owner, func_id)

    _add_call_edges(calls, func_id, module_id, file_path, graph)
    return func_id


def _collect_func_body(
    body: Node | None,
    file_path: str,
    source: bytes,
) -> tuple[list[BranchInfo], list[TypeRef], list[tuple[str, Node]]]:
    """Collect branches, exceptions, and calls from a function body."""
    branches: list[BranchInfo] = []
    exception_types: list[TypeRef] = []
    calls: list[tuple[str, Node]] = []
    if body is not None:
        collect_body_info(
            body,
            file_path,
            _BRANCH_NODE_TYPES,
            "call_expression",
            _THROW_TYPES,
            branches,
            exception_types,
            calls,
            skip_types=frozenset(
                {
                    "verus_block",
                    "macro_invocation",
                    "macro_definition",
                    "function_item",
                    "impl_item",
                    "trait_item",
                    "mod_item",
                }
            ),
        )
        if b"vskip" in (body.text or b""):
            conditions = _original_conditions(body, source, file_path)
            branches = [
                replace(branch, condition=conditions.get(branch.location, branch.condition))
                for branch in branches
            ]
    return branches, exception_types, calls


def _original_conditions(body: Node, source: bytes, file_path: str) -> dict[SourceLocation, str]:
    """Keep synthetic macro names out of user-facing branch conditions."""
    conditions: dict[SourceLocation, str] = {}
    stack = [body]
    while stack:
        node = stack.pop()
        condition = node.child_by_field_name("condition")
        if node.type in _BRANCH_NODE_TYPES and condition is not None:
            conditions[make_location(node, file_path)] = source[
                condition.start_byte : condition.end_byte
            ].decode("utf-8", errors="replace")
        stack.extend(node.named_children)
    return conditions


def _extract_parameters(node: Node) -> list[Parameter]:
    """Extract parameters from a function definition."""
    params_node = node.child_by_field_name("parameters")
    if params_node is None:
        return []
    params: list[Parameter] = []
    for child in params_node.children:
        if child.type == "parameter":
            _extract_single_param(child, params)
    return params


def _extract_single_param(
    param: Node,
    params: list[Parameter],
) -> None:
    """Extract name and type from a parameter node."""
    pattern = param.child_by_field_name("pattern")
    type_node = param.child_by_field_name("type")

    if pattern is None:
        return
    name = node_text(pattern)
    type_ref = None
    if type_node is not None:
        type_ref = TypeRef(name=node_text(type_node))
    params.append(Parameter(name=name, type_ref=type_ref))


def _extract_return_type(node: Node) -> TypeRef | None:
    """Extract the return type from a function definition."""
    return_type = node.child_by_field_name("return_type")
    if return_type is not None and return_type.type == "named_return_type":
        # The final non-extra child is the type, after any name or tracked marker.
        return_type = next(
            (child for child in reversed(return_type.named_children) if not child.is_extra), None
        )
    if return_type is None:
        return None
    return TypeRef(name=node_text(return_type))


def _extract_docstring(node: Node) -> str | None:
    """Extract doc comments (///) preceding a node."""
    lines: list[str] = []
    while True:
        prev = node.prev_named_sibling
        if prev is None:
            parent = node.parent
            if parent is None or parent.type != "declaration_with_attrs":
                break
            node = parent
            continue
        node = prev
        if prev.type == "attribute_item":
            continue
        if prev.type != "line_comment":
            break
        text = node_text(prev)
        if text.startswith("///") and not text.startswith("////"):
            lines.append(text[3:].strip())
        else:
            break
    if not lines:
        return None
    lines.reverse()
    return "\n".join(lines)


def _add_call_edges(
    calls: list[tuple[str, Node]],
    source_id: str,
    module_id: str,
    file_path: str,
    graph: CodeGraph,
) -> None:
    """Add CALLS edges for all collected call expressions."""
    for call_name, call_node in calls:
        target_id = _resolve_call_target(
            call_name,
            module_id,
        )
        confidence = _call_confidence(call_name)
        graph.edges.append(
            guard_call(
                CodeEdge(
                    source_id=source_id,
                    target_id=target_id,
                    kind=EdgeKind.CALLS,
                    confidence=confidence,
                    location=make_location(call_node, file_path),
                ),
                call_node,
                graph=graph,
            )
        )


def _call_confidence(call_name: str) -> EdgeConfidence:
    """Determine confidence for a Rust call expression."""
    if "." in call_name or "::" in call_name:
        return EdgeConfidence.INFERRED
    return EdgeConfidence.CERTAIN


def _resolve_call_target(
    call_name: str,
    module_id: str,
) -> str:
    """Resolve a call name to a target node ID."""
    if "." not in call_name and "::" not in call_name:
        return f"{module_id}:{call_name}"
    return call_name


def _extract_import(node: Node, graph: CodeGraph) -> None:
    """Extract use declarations as dependency info."""
    for child in node.children:
        if child.type in ("scoped_identifier", "identifier", "scoped_use_list", "use_wildcard"):
            dep = _first_path_segment(child)
            if dep and dep not in graph.dependencies:
                graph.dependencies.append(dep)
            return


def _first_path_segment(node: Node) -> str:
    """Extract the first path segment from a use path."""
    if node.type == "identifier":
        return node_text(node)
    for child in node.children:
        if child.type == "identifier":
            return node_text(child)
        if child.type == "scoped_identifier":
            return _first_path_segment(child)
    return ""
