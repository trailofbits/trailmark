"""Python language parser using tree-sitter."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

from trailmark.analysis._python_decorators import expression_bindings, imported_callees
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
from trailmark.parsers._binding_index import binding_scope, record_unit, syntax_nodes
from trailmark.parsers._bindings import guard_call
from trailmark.parsers._common import (
    _collect_throw_type,
    _extract_condition_text,
    add_contains_edge,
    add_module_node,
    compute_complexity,
    extract_call_name,
    extract_type_parameters,
    make_location,
    module_id_from_path,
    node_text,
    parse_directory,
)
from trailmark.parsers._declarations import add_callable, callable_scope, prepare_call_bindings
from trailmark.parsers._evidence import collect_source_evidence
from trailmark.parsers._nesting import bounded_nesting
from trailmark.parsers._walk import owned_syntax

_BRANCH_NODE_TYPES = frozenset(
    {
        "if_statement",
        "elif_clause",
        "for_statement",
        "while_statement",
        "except_clause",
        "with_statement",
        "boolean_operator",
        "conditional_expression",
        "case_clause",
    }
)

_THROW_TYPES = frozenset({"raise_statement"})

_EXTENSIONS = (".py",)
_CALL_SITES: ContextVar[dict[SourceLocation, Node] | None] = ContextVar(
    "_PYTHON_CALL_SITES", default=None
)
_CROSS_ROOT_IMPORTS: ContextVar[bool] = ContextVar("_CROSS_ROOT_IMPORTS", default=False)
_CROSS_ROOT_IMPORT = "_python_cross_root_import"

_EAGER_SITES: ContextVar[set[int] | None] = ContextVar("_PYTHON_EAGER_SITES", default=None)
_OVERLOAD_NAMES: ContextVar[frozenset[str]] = ContextVar(
    "_PYTHON_OVERLOAD_NAMES",
    default=frozenset({"overload", "typing.overload", "typing_extensions.overload"}),
)
_CALLABLE_BOUNDARIES = frozenset({"function_definition", "class_definition"})
_IMPORT_STATEMENTS = frozenset({"import_statement", "import_from_statement"})


class PythonParser:
    """Parses Python source files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "python"

    def __init__(self) -> None:
        self._parser = Parser(get_language("python"))

    @binding_scope
    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single Python file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="python", root_path=file_path)
        module_id = module_id_from_path(file_path)
        with callable_scope(graph), _call_sites() as calls, _overload_scope(tree.root_node):
            _visit_module(tree.root_node, file_path, module_id, graph)
            _bind_bases(tree.root_node, file_path, module_id, graph)
            _bind_complete_calls(calls, graph, tree.root_node, module_id, file_path)
            _record_imports(tree.root_node, graph, module_id, module_id, file_path)
        _annotation_diagnostics(tree.root_node, file_path, graph)
        collect_source_evidence(tree.root_node, file_path, graph, source=source)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        """Parse all .py files under dir_path into a merged graph."""
        return parse_directory(
            self.parse_file,
            "python",
            dir_path,
            _EXTENSIONS,
            link_calls=_link_python_imports,
        )


def _visit_module(
    root: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Walk the top-level of a module, extracting nodes and edges."""
    add_module_node(root, file_path, module_id, graph)
    for child in root.children:
        _visit_top_level_node(child, file_path, module_id, graph)
    _, _, calls = _python_body_info(root, file_path)
    _add_call_edges(calls, module_id, module_id, None, file_path=file_path, graph=graph)


def _visit_top_level_node(
    child: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Dispatch a single top-level node."""
    if child.type == "decorated_definition":
        for sub in child.children:
            if sub.type in ("function_definition", "class_definition"):
                _visit_top_level_node(sub, file_path, module_id, graph)
    elif child.type == "function_definition":
        _extract_function(child, file_path, module_id, None, graph)
    elif child.type == "class_definition":
        _extract_class(child, file_path, module_id, graph)
    elif child.type in ("import_statement", "import_from_statement"):
        _extract_import(child, graph)


@bounded_nesting
def _extract_class(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
    *,
    owner_id: str | None = None,
) -> None:
    """Extract a class definition and its methods."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    class_name = node_text(name_node)
    class_id = f"{owner_id}.{class_name}" if owner_id else f"{module_id}:{class_name}"
    location = make_location(node, file_path)

    class_unit = CodeUnit(
        id=class_id,
        name=class_name,
        kind=NodeKind.CLASS,
        location=location,
        type_parameters=extract_type_parameters(node, field_name="type_parameters"),
        docstring=_extract_docstring(node),
    )
    graph.nodes[class_id] = class_unit
    record_unit(class_unit)
    add_contains_edge(graph, owner_id or module_id, class_id)

    _extract_base_classes(node, class_id, module_id, graph)

    _extract_class_methods(node, file_path, module_id, class_id, graph)


def _extract_class_methods(
    node: Node, file_path: str, module_id: str, class_id: str, graph: CodeGraph
) -> None:
    body = node.child_by_field_name("body")
    if body is None:
        return
    for child in body.children:
        actual = child
        if child.type == "decorated_definition":
            actual = next(
                (sub for sub in child.children if sub.type == "function_definition"), child
            )
        if actual.type == "function_definition":
            _extract_function(
                actual,
                file_path,
                module_id,
                class_id,
                graph,
            )


def _extract_base_classes(
    node: Node,
    class_id: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract inheritance edges from class base list."""
    arg_list = node.child_by_field_name("superclasses")
    if arg_list is None:
        return
    for child in arg_list.named_children:
        if child.type not in {"keyword_argument", "list_splat", "dictionary_splat"}:
            base_name = node_text(child)
            base_id = f"{module_id}:{base_name}"
            graph.edges.append(
                CodeEdge(
                    source_id=class_id,
                    target_id=base_id,
                    kind=EdgeKind.INHERITS,
                    confidence=EdgeConfidence.INFERRED,
                    location=make_location(child, graph.nodes[class_id].location.file_path),
                    attributes=(("python_base", True), ("exact_target", True)),
                )
            )


def _bind_bases(root: Node, path: str, module_id: str, graph: CodeGraph) -> None:
    expressions = [
        base
        for definition in syntax_nodes(root, frozenset({"class_definition"}))
        if (bases := definition.child_by_field_name("superclasses")) is not None
        for base in bases.named_children
        if base.type not in {"keyword_argument", "list_splat", "dictionary_splat"}
    ]
    bindings = expression_bindings(root, path, expressions)
    by_location = {make_location(node, path): value for node, value in bindings.items()}
    definitions = {unit.location: unit.id for unit in graph.nodes.values()}
    for index, edge in enumerate(graph.edges):
        if not dict(edge.attributes).get("python_base"):
            continue
        if edge.location is None:
            continue
        value = by_location[edge.location]
        target = edge.target_id
        attributes = edge.attributes
        confidence = EdgeConfidence.UNCERTAIN
        if value.binding in {"import_module", "import_name"} and value.path:
            target = _absolute_import(value.path, module_id, path) or target
            attributes = (*attributes, ("import_binding", True))
            confidence = EdgeConfidence.INFERRED
        elif value.node is not None and value.node.type == "class_definition":
            target = definitions.get(make_location(value.node, path), target)
            confidence = EdgeConfidence.INFERRED
        else:
            if value.binding != "unbound" or target in graph.nodes:
                target = (
                    f"unresolved.python.base:{edge.source_id}:"
                    f"{edge.location.start_line}:{edge.location.start_col}"
                )
            attributes = (*attributes, ("unresolved_reason", "python_base_binding"))
        graph.edges[index] = replace(
            edge, target_id=target, confidence=confidence, attributes=attributes
        )


@bounded_nesting
def _extract_function(
    node: Node,
    file_path: str,
    module_id: str,
    class_id: str | None,
    graph: CodeGraph,
    *,
    owner_id: str | None = None,
) -> None:
    """Extract a function or method definition."""
    name_node = node.child_by_field_name("name")
    if name_node is None or _is_overload(node):
        return
    func_name = node_text(name_node)

    if owner_id is not None:
        func_id = f"{owner_id}.{func_name}"
        kind = NodeKind.FUNCTION
        container_id = owner_id
    elif class_id is not None:
        func_id = f"{class_id}.{func_name}"
        kind = NodeKind.METHOD
        container_id = class_id
    else:
        func_id = f"{module_id}:{func_name}"
        kind = NodeKind.FUNCTION
        container_id = module_id

    unit, calls = _function_unit(
        node,
        file_path,
        func_id=func_id,
        func_name=func_name,
        kind=kind,
        method=class_id is not None and owner_id is None,
    )
    body = node.child_by_field_name("body")
    func_id = add_callable(graph, unit)

    add_contains_edge(graph, container_id, func_id)

    _nested_functions(body, file_path, module_id, func_id, graph, class_id=class_id)

    _add_call_edges(calls, func_id, module_id, class_id, file_path=file_path, graph=graph)
    if body is not None:
        _record_imports(body, graph, func_id, module_id, file_path)


def _nested_functions(
    body: Node | None,
    file_path: str,
    module_id: str,
    func_id: str,
    graph: CodeGraph,
    *,
    class_id: str | None,
) -> None:
    if body is not None:
        for nested in owned_syntax(body, _CALLABLE_BOUNDARIES, boundaries=_CALLABLE_BOUNDARIES):
            if nested.type == "function_definition":
                _extract_function(nested, file_path, module_id, class_id, graph, owner_id=func_id)
            elif nested.type == "class_definition":
                _extract_class(nested, file_path, module_id, graph, owner_id=func_id)


def _is_overload(node: Node) -> bool:
    parent = node.parent
    if parent is None or parent.type != "decorated_definition":
        return False
    return any(
        child.type == "decorator" and node_text(child).lstrip("@").strip() in _OVERLOAD_NAMES.get()
        for child in parent.named_children
    )


def _extract_parameters(node: Node, *, method: bool = False) -> list[Parameter]:
    """Extract parameters from a function definition."""
    params_node = node.child_by_field_name("parameters")
    if params_node is None:
        return []

    params: list[Parameter] = []
    for child in params_node.children:
        if child.type == "identifier":
            name = node_text(child)
            params.append(Parameter(name=name))
        elif child.type in {"list_splat_pattern", "dictionary_splat_pattern"}:
            params.append(Parameter(name=node_text(child).lstrip("*")))
        elif child.type == "typed_parameter":
            name = _param_name_from_typed(child)
            type_ref = _type_from_annotation(child)
            params.append(Parameter(name=name, type_ref=type_ref))
        elif child.type == "default_parameter":
            name, default = _parse_default_param(child)
            params.append(Parameter(name=name, default=default))
        elif child.type == "typed_default_parameter":
            name, type_ref, default = _parse_typed_default(child)
            params.append(
                Parameter(
                    name=name,
                    type_ref=type_ref,
                    default=default,
                )
            )
    decorators = node.parent
    static = (
        decorators is not None
        and decorators.type == "decorated_definition"
        and any(node_text(child).strip() == "@staticmethod" for child in decorators.named_children)
    )
    if method and not static and params and params[0].name in {"self", "cls"}:
        params.pop(0)
    return params


def _param_name_from_typed(node: Node) -> str:
    """Get parameter name from a typed_parameter node."""
    for child in node.children:
        if child.type == "identifier":
            return node_text(child)
        if child.type in {"list_splat_pattern", "dictionary_splat_pattern"}:
            return node_text(child).lstrip("*")
    return ""


def _type_from_annotation(node: Node) -> TypeRef | None:
    """Extract TypeRef from a typed_parameter node."""
    for child in node.children:
        if child.type == "type":
            return _parse_type_node(child)
    return None


def _parse_type_node(type_node: Node) -> TypeRef:
    """Parse a type annotation node into a TypeRef."""
    if len(type_node.children) == 1:
        inner = type_node.children[0]
        if inner.type == "identifier":
            return TypeRef(name=node_text(inner))
        if inner.type == "none":
            return TypeRef(name="None")
        if inner.type == "generic_type":
            return _parse_generic_type(inner)
        if inner.type == "union_type":
            return TypeRef(name=node_text(type_node))
    return TypeRef(name=node_text(type_node))


def _parse_generic_type(node: Node) -> TypeRef:
    """Parse a generic type like list[int] into a TypeRef."""
    name = ""
    args: list[TypeRef] = []
    for child in node.children:
        if child.type == "identifier":
            name = node_text(child)
        elif child.type == "type_parameter":
            for sub in child.children:
                if sub.type == "type":
                    args.append(_parse_type_node(sub))
    return TypeRef(name=name, generic_args=tuple(args))


def _parse_default_param(node: Node) -> tuple[str, str]:
    """Parse name and default from a default_parameter node."""
    name = ""
    default = ""
    for child in node.children:
        if child.type == "identifier" and not name:
            name = node_text(child)
        elif child.type == "=":
            continue
        elif name:
            default = node_text(child)
    return name, default


def _parse_typed_default(
    node: Node,
) -> tuple[str, TypeRef | None, str]:
    """Parse name, type, default from typed_default_parameter."""
    name = ""
    type_ref = None
    default = ""
    seen_eq = False
    for child in node.children:
        if child.type == "identifier" and not name:
            name = node_text(child)
        elif child.type == "type":
            type_ref = _parse_type_node(child)
        elif child.type == "=":
            seen_eq = True
        elif seen_eq:
            default = node_text(child)
    return name, type_ref, default


def _extract_return_type(node: Node) -> TypeRef | None:
    """Extract return type annotation from a function definition."""
    return_type = node.child_by_field_name("return_type")
    if return_type is None:
        return None
    return _parse_type_node(return_type)


def _extract_docstring(node: Node) -> str | None:
    """Extract docstring from a function or class definition."""
    body = node.child_by_field_name("body")
    if body is None or len(body.children) == 0:
        return None

    first = body.children[0]
    if first.type == "expression_statement":
        expr = first.children[0] if first.children else None
        if expr is not None and expr.type == "string":
            return _clean_docstring(node_text(expr))
    if first.type == "string":
        return _clean_docstring(node_text(first))
    return None


def _clean_docstring(text: str) -> str:
    """Strip quote delimiters from a docstring."""
    if text.startswith('"""') or text.startswith("'''"):
        text = text[3:-3]
    elif text.startswith('"') or text.startswith("'"):
        text = text[1:-1]
    return text.strip()


def _resolve_call_target(
    call_name: str,
    module_id: str,
    class_id: str | None,
) -> str:
    """Resolve a call name to a target node ID (best effort)."""
    if call_name.startswith("self.") and class_id is not None:
        method_name = call_name[5:]
        return f"{class_id}.{method_name}"
    if "." not in call_name:
        return f"{module_id}:{call_name}"
    return call_name


def _extract_import(node: Node, graph: CodeGraph) -> None:
    """Extract import statements as dependency info."""
    text = node_text(node)
    parts = text.split()
    if len(parts) >= 2:
        module_name = parts[1].split(".")[0]
        if module_name not in graph.dependencies:
            graph.dependencies.append(module_name)


def _add_call_edges(
    calls: list[tuple[str, Node]],
    func_id: str,
    module_id: str,
    class_id: str | None,
    *,
    file_path: str,
    graph: CodeGraph,
) -> None:
    for call_name, call_node in calls:
        target_id = _resolve_call_target(
            call_name,
            module_id,
            class_id,
        )
        confidence = (
            EdgeConfidence.CERTAIN
            if "." not in call_name or call_name.startswith("self.")
            else EdgeConfidence.INFERRED
        )
        location = make_location(call_node, file_path)
        sites = _CALL_SITES.get()
        if sites is not None:
            sites[location] = call_node
        graph.edges.append(
            CodeEdge(
                source_id=func_id,
                target_id=target_id,
                kind=EdgeKind.CALLS,
                confidence=confidence,
                location=location,
                attributes=_execution_attributes(call_node),
            )
        )


def _function_unit(
    node: Node, file_path: str, *, func_id: str, func_name: str, kind: NodeKind, method: bool
) -> tuple[CodeUnit, list[tuple[str, Node]]]:
    params = _extract_parameters(node, method=method)
    return_type = _extract_return_type(node)
    body = node.child_by_field_name("body")

    branches, exception_types, calls = _python_body_info(body, file_path)

    complexity = compute_complexity(branches)

    location = make_location(node, file_path)

    unit = CodeUnit(
        id=func_id,
        name=func_name,
        kind=kind,
        location=location,
        parameters=tuple(params),
        return_type=return_type,
        exception_types=tuple(exception_types),
        type_parameters=extract_type_parameters(node, field_name="type_parameters"),
        cyclomatic_complexity=complexity,
        branches=tuple(branches),
        docstring=_extract_docstring(node),
    )
    return unit, calls


def _python_body_info(
    body: Node | None, file_path: str
) -> tuple[list[BranchInfo], list[TypeRef], list[tuple[str, Node]]]:
    branches: list[BranchInfo] = []
    exceptions: list[TypeRef] = []
    calls: list[tuple[str, Node]] = []
    if body is None:
        return branches, exceptions, calls
    for node in _executed_syntax(body):
        if node.type in _BRANCH_NODE_TYPES:
            branches.append(
                BranchInfo(
                    location=make_location(node, file_path), condition=_extract_condition_text(node)
                )
            )
        if node.type in _THROW_TYPES:
            _collect_throw_type(node, exceptions)
        if node.type == "call" and (name := extract_call_name(node)):
            calls.append((name, node))
    return branches, exceptions, calls


def _executed_syntax(root: Node) -> Generator[Node]:
    """Visit expressions evaluated now, keeping nested callable bodies deferred."""
    pending = [(root, False)]
    eager_sites = _EAGER_SITES.get()
    while pending:
        node, eager = pending.pop()
        if node.type in {"function_definition", "lambda"}:
            parameters = node.child_by_field_name("parameters")
            if parameters is not None:
                pending.extend(
                    reversed(
                        [
                            (value, True)
                            for parameter in parameters.named_children
                            if (value := parameter.child_by_field_name("value")) is not None
                        ]
                    )
                )
            if node.type == "lambda" and not eager and root.type != "module":
                body = node.child_by_field_name("body")
                if body is not None:
                    pending.append((body, False))
            continue
        if node.type in {"type", "type_parameter"}:
            continue
        eager |= node.type in {"class_definition", "decorator"}
        if node.type == "call" and eager and eager_sites is not None:
            eager_sites.add(node.id)
        yield node
        pending.extend((child, eager) for child in reversed(node.named_children))


def _execution_attributes(call: Node) -> tuple[tuple[str, str], ...]:
    eager = _EAGER_SITES.get()
    return (
        (("python_execution", "definition_time"),) if eager is not None and call.id in eager else ()
    )


def _annotation_diagnostics(root: Node, path: str, graph: CodeGraph) -> None:
    if any(
        node.type == "future_import_statement"
        and any(node_text(name) == "annotations" for name in node.children_by_field_name("name"))
        for node in root.named_children
    ):
        return
    skipped_until = -1
    for annotation in syntax_nodes(root, frozenset({"type"})):
        if annotation.start_byte < skipped_until:
            continue
        skipped_until = annotation.end_byte
        if syntax_nodes(annotation, frozenset({"call"})):
            graph.parse_diagnostics.append(
                ParseDiagnostic(
                    "python",
                    "python",
                    make_location(annotation, path),
                    "unsupported_syntax",
                    "Python annotation calls are retained as type syntax; evaluation depends on "
                    "the target Python version and annotation policy, which are not configured",
                )
            )


def _absolute_import(name: str, module_id: str, file_path: str) -> str:
    if not name.startswith("."):
        return name
    suffix = name.lstrip(".")
    level = len(name) - len(suffix)
    parts = module_id.split(".")
    package = parts if Path(file_path).stem == "__init__" else parts[:-1]
    if not package or level > len(package):
        return ""
    prefix = package[: len(package) + 1 - level]
    return ".".join((*prefix, suffix)) if suffix else ".".join(prefix)


def _record_imports(
    root: Node,
    graph: CodeGraph,
    scope: str,
    module_id: str,
    file_path: str,
) -> None:
    for node in owned_syntax(root, _IMPORT_STATEMENTS, boundaries=_CALLABLE_BOUNDARIES):
        module = node.child_by_field_name("module_name")
        if node.type == "import_from_statement" and module is None:
            continue
        raw_module = node_text(module) if module is not None else ""
        prefix = _absolute_import(raw_module, module_id, file_path)
        if module is not None and not prefix:
            continue
        names = [
            node_text(name.child_by_field_name("name") or name)
            for name in node.children_by_field_name("name")
        ]
        attributes = (("python_import", True), *_cross_root_import_attributes(raw_module))
        for name in ([prefix] if prefix else []) + [
            f"{prefix}.{name}" if prefix else name for name in names
        ]:
            graph.edges.append(
                CodeEdge(
                    scope,
                    name,
                    EdgeKind.IMPORTS,
                    EdgeConfidence.INFERRED,
                    location=make_location(node, file_path),
                    attributes=attributes,
                )
            )


@contextmanager
def collect_cross_root_imports() -> Generator[None]:
    """Keep absolute import bindings available until explicit roots are merged."""
    token = _CROSS_ROOT_IMPORTS.set(True)
    try:
        yield
    finally:
        _CROSS_ROOT_IMPORTS.reset(token)


def _cross_root_import_attributes(import_path: str) -> tuple[tuple[str, bool], ...]:
    if _CROSS_ROOT_IMPORTS.get() and not import_path.startswith("."):
        return ((_CROSS_ROOT_IMPORT, True),)
    return ()


def _import_prefixes(path: str) -> Iterator[tuple[str, str]]:
    parts = path.split(".")
    for index in range(1, len(parts) + 1):
        yield ".".join(parts[:index]), ".".join(parts[index:])


def _without_pending_import(edge: CodeEdge) -> CodeEdge:
    return replace(
        edge, attributes=tuple(item for item in edge.attributes if item[0] != _CROSS_ROOT_IMPORT)
    )


def link_cross_root_imports(graph: CodeGraph) -> None:
    """Resolve absolute Python import bindings against explicitly selected roots."""
    modules: dict[str, list[str]] = defaultdict(list)
    for node_id, unit in graph.nodes.items():
        root, _, module = node_id.partition("::")
        if (
            module
            and unit.kind == NodeKind.MODULE
            and Path(unit.location.file_path).suffix == ".py"
        ):
            modules[module].append(root)

    linked: list[CodeEdge] = []
    imports: set[CodeEdge] = set()
    replaced_proxies: set[str] = set()
    for edge in graph.edges:
        attrs = dict(edge.attributes)
        if not attrs.get(_CROSS_ROOT_IMPORT):
            linked.append(edge)
            continue
        edge = _without_pending_import(edge)
        root = edge.source_id.partition("::")[0]
        if edge.kind == EdgeKind.IMPORTS:
            candidates, _ = _cross_root_modules(modules, edge.target_id)
            for owner, module, _ in candidates:
                if owner != root:
                    imported = replace(edge, target_id=f"{owner}::{module}")
                    if imported not in imports:
                        imports.add(imported)
                        linked.append(imported)
            continue
        raw = attrs.get("raw_target")
        if edge.kind == EdgeKind.CALLS and isinstance(raw, str):
            target = _cross_root_call_target(graph, modules, root, raw)
            if target is not None:
                replaced_proxies.add(edge.target_id)
                edge = replace(
                    edge,
                    target_id=target,
                    attributes=tuple(item for item in edge.attributes if item[0] != "raw_target"),
                )
        linked.append(edge)

    graph.edges[:] = linked
    if replaced_proxies:
        used = {node for edge in linked for node in (edge.source_id, edge.target_id)}
        for proxy in replaced_proxies - used:
            del graph.nodes[proxy]


def _cross_root_modules(
    modules: dict[str, list[str]], path: str
) -> tuple[list[tuple[str, str, str]], bool]:
    """Find module prefixes without assuming precedence or mixing package roots."""
    candidates: list[tuple[str, str, str]] = []
    owner: str | None = None
    for module, member in _import_prefixes(path):
        owners = modules.get(module)
        if not owners:
            continue
        if len(owners) > 1 or (owner is not None and owners[0] != owner):
            return candidates, False
        owner = owners[0]
        candidates.append((owner, module, member))
    return candidates, True


def _cross_root_call_target(
    graph: CodeGraph, modules: dict[str, list[str]], root: str, path: str
) -> str | None:
    candidates, unambiguous = _cross_root_modules(modules, path)
    if not unambiguous:
        return None
    targets = []
    for owner, module, member in candidates:
        if owner == root or not member:
            continue
        target_id = f"{owner}::{module}:{member}"
        target = graph.nodes.get(target_id)
        if target is not None and target.kind in {
            NodeKind.FUNCTION,
            NodeKind.METHOD,
            NodeKind.CLASS,
        }:
            targets.append(target_id)
    return targets[0] if len(targets) == 1 else None


def _link_python_imports(graph: CodeGraph) -> None:
    """Resolve only imported paths with a unique Python source candidate."""
    from trailmark.parsers._common import link_cross_file_calls

    linked = []
    for edge in graph.edges:
        attributes = dict(edge.attributes)
        if attributes.get("import_binding"):
            candidates = [
                f"{module}:{member}"
                for module, member in _import_prefixes(edge.target_id)
                if member
            ]
            targets = [target for target in candidates if target in graph.nodes]
            if len(targets) == 1:
                edge = replace(edge, target_id=targets[0])
        elif attributes.get("python_import"):
            local = _without_pending_import(edge) if attributes.get(_CROSS_ROOT_IMPORT) else edge
            for target, _ in _import_prefixes(edge.target_id):
                unit = graph.nodes.get(target)
                if unit is not None and unit.kind == NodeKind.MODULE and target != edge.source_id:
                    linked.append(replace(local, target_id=target))
            if attributes.get(_CROSS_ROOT_IMPORT):
                linked.append(edge)
            continue
        linked.append(edge)
    graph.edges[:] = list(dict.fromkeys(linked))
    link_cross_file_calls(graph)


def _bind_complete_calls(
    calls: dict[SourceLocation, Node],
    graph: CodeGraph,
    root: Node,
    module_id: str,
    file_path: str,
) -> None:
    """Bind each call once after all file-local definitions are extracted."""
    prepare_call_bindings(graph)
    imported = imported_callees(root, file_path, list(calls.values()))
    eager = [
        calls[edge.location].child_by_field_name("function")
        for edge in graph.edges
        if edge.kind == EdgeKind.CALLS
        and edge.location in calls
        and dict(edge.attributes).get("python_execution") == "definition_time"
    ]
    bindings = expression_bindings(root, file_path, [node for node in eager if node is not None])
    definitions = {unit.location: unit.id for unit in graph.nodes.values()}
    bound = []
    for edge in graph.edges:
        call = calls.get(edge.location) if edge.kind == EdgeKind.CALLS else None
        if call is not None:
            imported_path = imported.get(call, "")
            target = _absolute_import(imported_path, module_id, file_path)
            if target:
                edge = replace(
                    edge,
                    target_id=target,
                    confidence=EdgeConfidence.INFERRED,
                    attributes=(
                        *edge.attributes,
                        ("import_binding", True),
                        *_cross_root_import_attributes(imported_path),
                    ),
                )
            else:
                callee = call.child_by_field_name("function")
                value = bindings.get(callee)
                if value is not None and value.node is not None:
                    local = definitions.get(make_location(value.node, file_path))
                    if local is not None:
                        edge = replace(edge, target_id=local)
                    else:
                        edge = guard_call(edge, call, graph=graph)
                elif value is not None and callee is not None and callee.type == "identifier":
                    edge = replace(
                        edge,
                        target_id=f"{module_id}:$eager@{call.start_byte}",
                        confidence=EdgeConfidence.UNCERTAIN,
                        attributes=(*edge.attributes, ("binding_blocked", True)),
                    )
                else:
                    edge = guard_call(edge, call, graph=graph)
        bound.append(edge)
    graph.edges[:] = bound


@contextmanager
def _call_sites() -> Generator[dict[SourceLocation, Node]]:
    sites: dict[SourceLocation, Node] = {}
    token = _CALL_SITES.set(sites)
    eager_token = _EAGER_SITES.set(set())
    try:
        yield sites
    finally:
        _EAGER_SITES.reset(eager_token)
        _CALL_SITES.reset(token)


@contextmanager
def _overload_scope(root: Node) -> Generator[None]:
    names = {"overload", "typing.overload", "typing_extensions.overload"}
    for statement in root.named_children:
        names.update(_overload_from_aliases(statement))
        if statement.type != "import_statement":
            continue
        for imported in statement.named_children:
            if imported.type != "aliased_import":
                continue
            module = imported.child_by_field_name("name")
            alias = imported.child_by_field_name("alias")
            if (
                module is not None
                and node_text(module) in {"typing", "typing_extensions"}
                and alias is not None
            ):
                names.add(node_text(alias) + ".overload")
    token = _OVERLOAD_NAMES.set(frozenset(names))
    try:
        yield
    finally:
        _OVERLOAD_NAMES.reset(token)


def _overload_from_aliases(statement: Node) -> set[str]:
    module = statement.child_by_field_name("module_name")
    if statement.type != "import_from_statement" or module is None:
        return set()
    if node_text(module) not in {"typing", "typing_extensions"}:
        return set()
    return {
        node_text(alias)
        for imported in statement.named_children
        if imported.type == "aliased_import"
        and (name := imported.child_by_field_name("name")) is not None
        and node_text(name) == "overload"
        and (alias := imported.child_by_field_name("alias")) is not None
    }
