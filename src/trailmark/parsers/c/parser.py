"""C language parser using tree-sitter."""

from __future__ import annotations

from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

from trailmark.models.diagnostics import ParseDiagnostic
from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import (
    Attribute,
    BranchInfo,
    CodeUnit,
    NodeKind,
    Parameter,
    TypeRef,
)
from trailmark.parsers._bindings import guard_call
from trailmark.parsers._common import (
    add_contains_edge,
    add_module_node,
    collect_body_info,
    compute_complexity,
    make_location,
    module_id_from_path,
    node_text,
    parse_directory,
)
from trailmark.parsers._declarations import add_callable, add_type, callable_scope
from trailmark.parsers._walk import walk_owned
from trailmark.parsers.c._callbacks import resolve_constant_callbacks
from trailmark.parsers.c._macros import DeclarationMacros
from trailmark.parsers.c._recovery import recover_bodies

_BRANCH_NODE_TYPES = frozenset(
    {
        "if_statement",
        "for_statement",
        "while_statement",
        "do_statement",
        "case_statement",
        "conditional_expression",
    }
)

_THROW_TYPES: frozenset[str] = frozenset()

_EXTENSIONS = (".c", ".h")

_C_TYPE_NODES = frozenset(
    {
        "type_identifier",
        "primitive_type",
        "sized_type_specifier",
    }
)


class CParser:
    """Parses C source files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "c"

    def __init__(self) -> None:
        self._parser = Parser(get_language("c"))

    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single C file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        original = tree.root_node
        tree, recovered = recover_bodies(source, tree, self._parser)
        macros = DeclarationMacros(original, file_path, self._parser)
        graph = CodeGraph(language="c", root_path=file_path)
        module_id = module_id_from_path(file_path)
        with callable_scope(graph):
            add_module_node(original, file_path, module_id, graph)
            for child in tree.root_node.children:
                visit_c_declaration(
                    child, file_path, module_id, graph, macros=macros, recovered=recovered
                )
        resolve_constant_callbacks(tree.root_node, module_id, graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        """Parse all C files under dir_path into a merged graph."""
        return parse_directory(
            self.parse_file,
            "c",
            dir_path,
            _EXTENSIONS,
        )


_PREPROC_CONTAINER_TYPES = frozenset(
    {
        "preproc_if",
        "preproc_ifdef",
        "preproc_else",
        "preproc_elif",
        "ERROR",
    }
)


def visit_c_declaration(
    child: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
    *,
    macros: DeclarationMacros | None = None,
    recovered: frozenset[int] = frozenset(),
) -> None:
    """Dispatch a single top-level C node."""
    if child.type == "function_definition":
        _extract_function(
            child,
            file_path,
            module_id,
            graph,
            macros=macros,
            recovered=child.start_byte in recovered,
        )
    elif child.type == "preproc_include":
        _extract_include(child, graph)
    elif child.type in ("type_definition", "declaration"):
        _extract_type_def(child, file_path, module_id, graph)
    elif child.type == "struct_specifier":
        _extract_struct(child, file_path, module_id, graph)
    elif child.type == "enum_specifier":
        _extract_enum(child, file_path, module_id, graph)
    elif child.type in _PREPROC_CONTAINER_TYPES:
        for nested in child.children:
            visit_c_declaration(
                nested, file_path, module_id, graph, macros=macros, recovered=recovered
            )


def _extract_type_def(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract struct or enum from a type_definition or declaration."""
    typedef_name = _get_typedef_name(node)
    for child in node.children:
        if child.type == "struct_specifier":
            _extract_struct(
                child,
                file_path,
                module_id,
                graph,
                fallback_name=typedef_name,
            )
        elif child.type == "enum_specifier":
            _extract_enum(
                child,
                file_path,
                module_id,
                graph,
                fallback_name=typedef_name,
            )


def _get_typedef_name(node: Node) -> str | None:
    """Get the typedef alias name from a type_definition node."""
    for child in node.children:
        if child.type == "type_identifier":
            return node_text(child)
    return None


def _extract_struct(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
    fallback_name: str | None = None,
) -> None:
    """Extract a struct definition."""
    name_node = node.child_by_field_name("name")
    struct_name = node_text(name_node) if name_node else fallback_name
    if not struct_name:
        return
    struct_id = f"{module_id}:{struct_name}"
    existing = graph.nodes.get(struct_id)
    if (
        existing is not None
        and existing.kind == NodeKind.STRUCT
        and node.child_by_field_name("body") is None
    ):
        return
    location = make_location(node, file_path)

    unit = CodeUnit(
        id=struct_id,
        name=struct_name,
        kind=NodeKind.STRUCT,
        location=location,
    )
    add_type(graph, unit)
    if existing is None or existing.kind != NodeKind.STRUCT:
        add_contains_edge(graph, module_id, struct_id)


def _extract_enum(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
    fallback_name: str | None = None,
) -> None:
    """Extract an enum definition."""
    name_node = node.child_by_field_name("name")
    enum_name = node_text(name_node) if name_node else fallback_name
    if not enum_name:
        return
    enum_id = f"{module_id}:{enum_name}"
    location = make_location(node, file_path)

    unit = CodeUnit(
        id=enum_id,
        name=enum_name,
        kind=NodeKind.ENUM,
        location=location,
    )
    add_type(graph, unit)
    add_contains_edge(graph, module_id, enum_id)


def _extract_function(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
    *,
    macros: DeclarationMacros | None = None,
    recovered: bool = False,
) -> None:
    """Extract a C function definition."""
    signature = macros.signature(node) if macros is not None else None
    wrapper = macros.wrapper(node) if macros is not None else None
    declaration = signature.declaration if signature is not None else node
    func_name = signature.name if signature is not None else _get_function_name(node)
    if wrapper is not None and signature is None:
        func_name = f"{wrapper[0]}({wrapper[1]})"
    if not func_name:
        return
    func_id = f"{module_id}:{func_name}"

    params = _extract_parameters(declaration)
    return_type = _extract_return_type(declaration)
    if wrapper is not None and signature is None:
        params, return_type = [], None
    body = node.child_by_field_name("body")

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
        )

    complexity = compute_complexity(branches)
    location = make_location(node, file_path)
    docstring = _extract_docstring(node)

    attributes: list[Attribute] = [
        (
            "internal_linkage",
            any(
                c.type == "storage_class_specifier" and node_text(c) == "static"
                for source_node in (node, declaration)
                for c in source_node.named_children
            ),
        )
    ]
    if signature is not None:
        attributes.extend(
            [
                ("macro_wrapper", signature.wrapper),
                ("source_alias", signature.source_alias),
                ("expansion_alias", signature.name),
            ]
        )
    elif wrapper is not None:
        attributes.extend(
            [
                ("macro_wrapper", wrapper[0]),
                ("source_alias", wrapper[1]),
                ("macro_signature_unresolved", True),
            ]
        )
        graph.parse_diagnostics.append(
            ParseDiagnostic(
                "c",
                "c",
                location,
                "unsupported_syntax",
                "Declaration macro could not be expanded to one unambiguous function signature",
            )
        )
    if recovered or node.has_error or wrapper is not None and signature is None:
        attributes.append(("parse_incomplete", True))
    if recovered:
        attributes.append(("syntax_recovered", True))
        graph.parse_diagnostics.append(
            ParseDiagnostic(
                "c",
                "c",
                location,
                "syntax_recovery",
                "Recovered a balanced physical function body around unexpanded block macros; "
                "preprocessor control flow is not modeled",
            )
        )
    unit = CodeUnit(
        id=func_id,
        name=func_name,
        kind=NodeKind.FUNCTION,
        location=location,
        parameters=tuple(params),
        return_type=return_type,
        exception_types=tuple(exception_types),
        cyclomatic_complexity=complexity,
        branches=tuple(branches),
        docstring=docstring,
        attributes=tuple(attributes),
    )
    func_id = add_callable(graph, unit)
    add_contains_edge(graph, module_id, func_id)

    if body is not None:
        recorded = {site.id for _, site in calls}
        for site in walk_owned(body):
            function = (
                site.child_by_field_name("function") if site.type == "call_expression" else None
            )
            if function is not None and site.id not in recorded:
                calls.append((node_text(function), site))
        calls.sort(key=lambda call: call[1].start_byte)
    _add_call_edges(
        calls,
        func_id,
        module_id,
        file_path,
        graph,
        recovered=recovered,
        incomplete=node.has_error,
        unknown_signature=wrapper is not None and signature is None,
    )


def _add_call_edges(
    calls: list[tuple[str, Node]],
    func_id: str,
    module_id: str,
    file_path: str,
    graph: CodeGraph,
    *,
    recovered: bool = False,
    incomplete: bool = False,
    unknown_signature: bool = False,
) -> None:
    """Create CALLS edges from collected call expressions."""
    for call_name, call_node in calls:
        target_id = _resolve_call_target(call_name, module_id)
        confidence = _call_confidence(call_name)
        indirect = "(" in call_name or "*" in call_name
        if indirect:
            target_id = f"{func_id}.$indirect.{call_name}"
            confidence = EdgeConfidence.UNCERTAIN
        if unknown_signature:
            target_id = f"{func_id}.$macro.{call_name}"
        graph.edges.append(
            guard_call(
                CodeEdge(
                    source_id=func_id,
                    target_id=target_id,
                    kind=EdgeKind.CALLS,
                    confidence=EdgeConfidence.UNCERTAIN
                    if recovered or incomplete or unknown_signature
                    else confidence,
                    location=make_location(call_node, file_path),
                    attributes=(
                        *((("binding_blocked", True),) if indirect or unknown_signature else ()),
                        *((("syntax_recovered", True),) if recovered else ()),
                    ),
                ),
                call_node,
                graph=graph,
            )
        )


def _call_confidence(call_name: str) -> EdgeConfidence:
    """Determine confidence for a C call expression."""
    if "." in call_name or "->" in call_name:
        return EdgeConfidence.UNCERTAIN
    return EdgeConfidence.CERTAIN


def _resolve_call_target(
    call_name: str,
    module_id: str,
) -> str:
    """Resolve a call name to a target node ID."""
    if "." not in call_name and "->" not in call_name:
        return f"{module_id}:{call_name}"
    return call_name


def _get_function_name(node: Node) -> str:
    """Extract function name from a C function_definition.

    Handles nested declarator structures: the declarator may be
    a function_declarator, pointer_declarator, or nested
    combination thereof.
    """
    declarator = node.child_by_field_name("declarator")
    if declarator is None:
        return ""
    return _dig_identifier(declarator)


def _dig_identifier(node: Node) -> str:
    """Recursively dig through declarator nodes to find the name."""
    if node.type == "identifier":
        return node_text(node)
    if node.type == "field_identifier":
        return node_text(node)
    declarator = node.child_by_field_name("declarator")
    if declarator is not None:
        return _dig_identifier(declarator)
    for child in node.children:
        if child.type in ("identifier", "field_identifier"):
            return node_text(child)
    for child in node.children:
        result = _dig_identifier(child)
        if result:
            return result
    return ""


def _extract_parameters(node: Node) -> list[Parameter]:
    """Extract parameters from a C function definition."""
    declarator = node.child_by_field_name("declarator")
    func_decl = _find_func_declarator(declarator)
    if func_decl is None:
        return []

    params_node = func_decl.child_by_field_name("parameters")
    if params_node is None:
        return []

    params: list[Parameter] = []
    for child in params_node.children:
        if child.type == "parameter_declaration":
            param = _parse_param_declaration(child)
            if param is not None:
                params.append(param)
    return params


def _find_func_declarator(node: Node | None) -> Node | None:
    """Find the function_declarator inside a declarator chain."""
    if node is None:
        return None
    if node.type == "function_declarator":
        return node
    declarator = node.child_by_field_name("declarator")
    if declarator is not None:
        return _find_func_declarator(declarator)
    for child in node.children:
        if child.type == "function_declarator":
            return child
    return None


def _parse_param_declaration(node: Node) -> Parameter | None:
    """Parse a parameter_declaration into a Parameter."""
    type_node = node.child_by_field_name("type")
    type_name = node_text(type_node) if type_node else None
    type_ref = TypeRef(name=type_name) if type_name else None

    decl = node.child_by_field_name("declarator")
    if decl is not None:
        name = _dig_identifier(decl)
        if name:
            return Parameter(name=name, type_ref=type_ref)

    return None


def _extract_return_type(node: Node) -> TypeRef | None:
    """Extract return type from a C function definition."""
    type_node = node.child_by_field_name("type")
    if type_node is None:
        return None
    text = node_text(type_node)
    if text:
        return TypeRef(name=text)
    return None


def _extract_docstring(node: Node) -> str | None:
    """Extract a doc comment preceding a function definition.

    Looks for /** ... */ or /// style comments immediately
    before the function node.
    """
    prev = node.prev_named_sibling
    if prev is None or prev.type != "comment":
        return None
    text = node_text(prev)
    if text.startswith("/**") or text.startswith("///"):
        return _clean_c_docstring(text)
    return None


def _clean_c_docstring(text: str) -> str:
    """Strip C doc-comment delimiters."""
    if text.startswith("/**"):
        text = text[3:]
        if text.endswith("*/"):
            text = text[:-2]
    elif text.startswith("///"):
        text = text[3:]
    return text.strip()


def _extract_include(node: Node, graph: CodeGraph) -> None:
    """Extract #include directives as dependencies."""
    for child in node.children:
        if child.type in ("string_literal", "system_lib_string"):
            path = node_text(child)
            path = path.strip('"').strip("<").strip(">")
            if path and path not in graph.dependencies:
                graph.dependencies.append(path)
            return
