"""Rust language parser using tree-sitter."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from tree_sitter import Language, Node, Parser, Range, Tree
from tree_sitter_language_pack import get_language

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import (
    BranchInfo,
    CodeUnit,
    NodeKind,
    Parameter,
    TypeRef,
)
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

    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single Rust file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="rust", root_path=file_path)
        module_id = module_id_from_path(file_path)
        add_module_node(tree.root_node, file_path, module_id, graph)
        self._visit_scope(tree.root_node, source, file_path, module_id, graph)
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
            body = None
            if child.type == "function_item":
                _extract_function(child, file_path, module_id, container_id, graph)
                if has_verus:
                    _warn_hidden_verus(child, file_path, "inside a function body")
            elif child.type == "mod_item":
                name = child.child_by_field_name("name")
                body = child.child_by_field_name("body")
                if name is not None and body is not None:
                    parent_id = module_id
                    module_id = f"{module_id}.{node_text(name)}"
                    container_id = None
                    add_module_node(child, file_path, module_id, graph)
                    add_contains_edge(graph, parent_id, module_id)
            elif child.type == "trait_item":
                container_id = _extract_trait(child, file_path, module_id, graph)
                body = child.child_by_field_name("body")
            elif child.type == "impl_item":
                container_id = _extract_impl(child, file_path, module_id, graph)
                body = child.child_by_field_name("body")
            elif child.type == "macro_invocation" and has_verus:
                macro = child.child_by_field_name("macro")
                if macro is not None and node_text(macro) == "verus":
                    tree = self._parse_verus_block(source, child, file_path)
                    body = tree.root_node if tree is not None else None
                else:
                    _warn_hidden_verus(child, file_path, "inside an opaque macro")
            else:
                _visit_top_level_node(child, file_path, module_id, graph)
            if body is not None:
                stack.extend(
                    (nested, module_id, container_id) for nested in reversed(_declarations_in(body))
                )

    def _parse_verus_block(self, source: bytes, node: Node, file_path: str) -> Tree | None:
        """Parse one block independently so error recovery cannot consume its neighbors."""
        token_tree = next(
            (child for child in node.named_children if child.type == "token_tree"), None
        )
        opening = token_tree.child(0) if token_tree is not None else None
        if opening is None or opening.type != "{":
            _warn_skipped(node, file_path, "unsupported Verus delimiter; expected braces")
            return None
        if self._verus_load_failed:
            return None
        if self._verus_parser is None:
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
                # Avoid retrying the compiler for every file in a directory parse.
                self._verus_load_failed = True
                _LOGGER.warning("Could not load the Verus grammar; skipping verus! blocks: %s", exc)
                return None
        # Unsupported nested delimiters confuse the Verus grammar's recovery.
        # Mask them without changing any byte offsets or line endings.
        masked = None
        for name, tokens in _verus_token_trees(node):
            delimiter = tokens.child(0)
            if delimiter is not None and delimiter.type != "{":
                _warn_skipped(name, file_path, "unsupported Verus delimiter; expected braces")
                if masked is None:
                    masked = bytearray(source)
                start, end = name.start_byte, tokens.end_byte
                masked[start:end] = bytes(b if b in (10, 13) else 32 for b in source[start:end])
        self._verus_parser.included_ranges = [
            Range(node.start_point, node.end_point, node.start_byte, node.end_byte)
        ]
        tree = self._verus_parser.parse(bytes(masked) if masked is not None else source)
        if tree.root_node.has_error:
            _warn_skipped(node, file_path, "Verus syntax errors; graph may be incomplete")
        return tree

    def parse_directory(self, dir_path: str) -> CodeGraph:
        """Parse all .rs files under dir_path into a merged graph."""
        return parse_directory(
            self.parse_file,
            "rust",
            dir_path,
            _EXTENSIONS,
        )


def _verus_token_trees(root: Node) -> list[tuple[Node, Node]]:
    """Find literal bare Verus invocations, including unexpanded token trees."""
    invocations: list[tuple[Node, Node]] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "macro_definition":
            continue
        if node.type == "verus_block":
            invocations.append((node, node))
            continue
        children = [child for child in node.children if not child.is_extra]
        definitions = {
            children[index + 3].id
            for index, child in enumerate(children[:-3])
            if node_text(child) == "macro_rules"
            and children[index + 1].type == "!"
            and children[index + 2].type == "identifier"
            and children[index + 3].type == "token_tree"
        }
        for index, child in enumerate(children[:-2]):
            if (
                child.type == "identifier"
                and node_text(child) == "verus"
                and children[index + 1].type == "!"
                and children[index + 2].type == "token_tree"
                and (index == 0 or children[index - 1].type != "::")
            ):
                invocations.append((child, children[index + 2]))
        stack.extend(
            child for child in reversed(node.named_children) if child.id not in definitions
        )
    return invocations


def _warn_skipped(node: Node, file_path: str, reason: str) -> None:
    _LOGGER.warning(
        "%s:%d:%d: %s", file_path, node.start_point.row + 1, node.start_point.column + 1, reason
    )


def _warn_hidden_verus(node: Node, file_path: str, reason: str) -> None:
    if b"verus" not in (node.text or b""):
        return
    invocations = _verus_token_trees(node)
    if invocations:
        _warn_skipped(invocations[0][0], file_path, f"skipping Verus declarations {reason}")


def _visit_top_level_node(
    child: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Dispatch a single top-level node."""
    if child.type == "struct_item":
        _extract_struct(child, file_path, module_id, graph)
    elif child.type == "enum_item":
        _extract_enum(child, file_path, module_id, graph)
    elif child.type == "use_declaration":
        _extract_import(child, graph)


def _extract_struct(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a struct definition."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    struct_name = node_text(name_node)
    struct_id = f"{module_id}:{struct_name}"
    docstring = _extract_docstring(node)

    unit = CodeUnit(
        id=struct_id,
        name=struct_name,
        kind=NodeKind.STRUCT,
        location=make_location(node, file_path),
        type_parameters=extract_type_parameters(node, field_name="type_parameters"),
        docstring=docstring,
    )
    graph.nodes[struct_id] = unit
    add_contains_edge(graph, module_id, struct_id)


def _extract_trait(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> str | None:
    """Extract a trait definition and its method signatures."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    trait_name = node_text(name_node)
    trait_id = f"{module_id}:{trait_name}"
    docstring = _extract_docstring(node)

    unit = CodeUnit(
        id=trait_id,
        name=trait_name,
        kind=NodeKind.TRAIT,
        location=make_location(node, file_path),
        type_parameters=extract_type_parameters(node, field_name="type_parameters"),
        docstring=docstring,
    )
    graph.nodes[trait_id] = unit
    add_contains_edge(graph, module_id, trait_id)

    return trait_id


def _extract_enum(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract an enum definition."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    enum_name = node_text(name_node)
    enum_id = f"{module_id}:{enum_name}"
    docstring = _extract_docstring(node)

    unit = CodeUnit(
        id=enum_id,
        name=enum_name,
        kind=NodeKind.ENUM,
        location=make_location(node, file_path),
        type_parameters=extract_type_parameters(node, field_name="type_parameters"),
        docstring=docstring,
    )
    graph.nodes[enum_id] = unit
    add_contains_edge(graph, module_id, enum_id)


def _extract_impl(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> str | None:
    """Extract an impl block, with optional trait implementation."""
    type_name = _extract_impl_type_name(node)
    if not type_name:
        return None
    type_id = f"{module_id}:{type_name}"

    trait_node = node.child_by_field_name("trait")
    if trait_node is not None:
        _add_implements_edge(trait_node, type_id, module_id, graph)

    return type_id


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
        )
    )


def _extract_function(
    node: Node,
    file_path: str,
    module_id: str,
    container_id: str | None,
    graph: CodeGraph,
) -> None:
    """Extract a function or method definition."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    func_name = node_text(name_node)

    if container_id is not None:
        func_id = f"{container_id}.{func_name}"
        kind = NodeKind.METHOD
        owner = container_id
    else:
        func_id = f"{module_id}:{func_name}"
        kind = NodeKind.FUNCTION
        owner = module_id

    params = _extract_parameters(node)
    return_type = _extract_return_type(node)
    body = node.child_by_field_name("body")

    branches, exception_types, calls = _collect_func_body(
        body,
        file_path,
    )
    complexity = compute_complexity(branches)
    docstring = _extract_docstring(node)

    unit = CodeUnit(
        id=func_id,
        name=func_name,
        kind=kind,
        location=make_location(node, file_path),
        parameters=tuple(params),
        return_type=return_type,
        exception_types=tuple(exception_types),
        type_parameters=extract_type_parameters(node, field_name="type_parameters"),
        cyclomatic_complexity=complexity,
        branches=tuple(branches),
        docstring=docstring,
    )
    graph.nodes[func_id] = unit
    add_contains_edge(graph, owner, func_id)

    _add_call_edges(
        calls,
        func_id,
        module_id,
        container_id,
        file_path,
        graph,
    )


def _collect_func_body(
    body: Node | None,
    file_path: str,
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
                {"verus_block", "macro_invocation", "macro_definition", "function_item"}
            ),
        )
    return branches, exception_types, calls


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
    container_id: str | None,
    file_path: str,
    graph: CodeGraph,
) -> None:
    """Add CALLS edges for all collected call expressions."""
    for call_name, call_node in calls:
        target_id = _resolve_call_target(
            call_name,
            module_id,
            container_id,
        )
        confidence = _call_confidence(call_name)
        graph.edges.append(
            CodeEdge(
                source_id=source_id,
                target_id=target_id,
                kind=EdgeKind.CALLS,
                confidence=confidence,
                location=make_location(call_node, file_path),
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
    container_id: str | None,
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
