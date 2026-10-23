"""Ruby language parser using tree-sitter."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
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
    TypeRef,
)
from trailmark.parsers._binding_index import binding_scope, scope_index, syntax_nodes
from trailmark.parsers._common import (
    add_contains_edge,
    add_module_node,
    compute_complexity,
    make_location,
    module_id_from_path,
    node_text,
    parse_directory,
)
from trailmark.parsers._nesting import bounded_nesting, nesting_diagnostic

_BRANCH_NODE_TYPES = frozenset(
    {
        "if",
        "unless",
        "while",
        "until",
        "for",
        "case",
        "when",
        "rescue",
        "conditional",
    }
)

_THROW_TYPES = frozenset[str]()

_EXTENSIONS = (".rb",)
_BINDING_SCOPES = frozenset({"method", "singleton_method", "lambda", "block", "do_block"})
_BINDING_DECLARATIONS = (
    ("assignment", "left"),
    ("operator_assignment", "left"),
    ("for", "pattern"),
    ("exception_variable", "name"),
)


class RubyParser:
    """Parses Ruby source files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "ruby"

    def __init__(self) -> None:
        self._parser = Parser(get_language("ruby"))

    @binding_scope
    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single Ruby file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="ruby", root_path=file_path)
        module_id = module_id_from_path(file_path)
        _visit_module(tree.root_node, file_path, module_id, graph)
        _bind_ruby_methods(graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        graph = parse_directory(self.parse_file, "ruby", dir_path, _EXTENSIONS)
        _bind_ruby_methods(graph)
        return graph


def _visit_module(
    root: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Walk the top-level of a Ruby module, extracting nodes."""
    add_module_node(root, file_path, module_id, graph)
    for child in root.children:
        _visit_top_level_node(child, file_path, module_id, graph)


def _visit_top_level_node(
    child: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Dispatch a single top-level Ruby node."""
    if child.type in ("method", "singleton_method"):
        _extract_function(
            child,
            file_path,
            module_id,
            None,
            graph,
        )
    elif child.type == "class":
        _extract_class(child, file_path, module_id, graph)
    elif child.type == "module":
        _extract_ruby_module(child, file_path, module_id, graph)
    elif child.type == "call":
        _check_require(child, graph)


@bounded_nesting
def _extract_class(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
    container_id: str | None = None,
) -> None:
    """Extract a Ruby class definition and its methods."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    class_name = node_text(name_node)
    owner = container_id or module_id
    class_id = (
        f"{owner}.{class_name.replace('::', '.')}"
        if container_id
        else f"{owner}:{class_name.replace('::', '.')}"
    )
    location = make_location(node, file_path)

    class_unit = CodeUnit(
        id=class_id,
        name=class_name,
        kind=NodeKind.CLASS,
        location=location,
        docstring=_extract_docstring(node),
    )
    graph.nodes[class_id] = class_unit
    add_contains_edge(graph, owner, class_id)

    _extract_superclass(node, class_id, module_id, graph)
    _visit_class_body(node, file_path, module_id, class_id, graph)


@bounded_nesting
def _extract_ruby_module(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
    container_id: str | None = None,
) -> None:
    """Extract a Ruby module definition."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    mod_name = node_text(name_node)
    owner = container_id or module_id
    mod_node_id = f"{owner}.{mod_name}" if container_id else f"{owner}:{mod_name}"
    location = make_location(node, file_path)

    mod_unit = CodeUnit(
        id=mod_node_id,
        name=mod_name,
        kind=NodeKind.MODULE,
        location=location,
        docstring=_extract_docstring(node),
    )
    graph.nodes[mod_node_id] = mod_unit
    add_contains_edge(graph, owner, mod_node_id)

    _visit_class_body(node, file_path, module_id, mod_node_id, graph)


def _extract_superclass_name(superclass: Node) -> str:
    """Extract the class name from a superclass node."""
    for child in superclass.children:
        if child.type in ("constant", "scope_resolution"):
            return node_text(child)
    return ""


def _extract_superclass(
    node: Node,
    class_id: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract INHERITS edge from superclass field."""
    superclass = node.child_by_field_name("superclass")
    if superclass is None:
        return
    base_name = _extract_superclass_name(superclass)
    if not base_name:
        return
    base_id = f"{module_id}:{base_name}"
    graph.edges.append(
        CodeEdge(
            source_id=class_id,
            target_id=base_id,
            kind=EdgeKind.INHERITS,
            confidence=EdgeConfidence.INFERRED,
        )
    )


def _visit_class_body(
    node: Node,
    file_path: str,
    module_id: str,
    class_id: str,
    graph: CodeGraph,
    *,
    singleton: bool = False,
) -> None:
    body = node.child_by_field_name("body")
    if body is None:
        return
    controller, worker = _class_traits(node, body)
    visibility = "public"
    named_visibility = {}
    for child in body.named_children:
        if child.type == "identifier" and node_text(child) in {"private", "protected", "public"}:
            visibility = node_text(child)
        elif child.type == "call":
            _named_visibility(child, named_visibility)
        elif child.type in {"method", "singleton_method"}:
            _extract_function(
                child,
                file_path,
                module_id,
                class_id,
                graph,
                singleton=singleton,
                visibility=visibility,
                controller=controller,
                worker=worker,
            )
        elif child.type in {"class", "module"}:
            extractor = _extract_class if child.type == "class" else _extract_ruby_module
            extractor(child, file_path, module_id, graph, class_id)
        elif _self_singleton(child):
            _visit_class_body(child, file_path, module_id, class_id, graph, singleton=True)
    _apply_named_visibility(graph, class_id, named_visibility)


def _extract_function(
    node: Node,
    file_path: str,
    module_id: str,
    class_id: str | None,
    graph: CodeGraph,
    *,
    singleton: bool = False,
    visibility: str = "public",
    controller: bool = False,
    worker: bool = False,
) -> None:
    """Extract a Ruby method definition."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    func_name = node_text(name_node)

    singleton = singleton or node.type == "singleton_method"
    func_id, kind, container_id = _function_identity(module_id, class_id, func_name, singleton)
    params = _extract_parameters(node)
    body = node.child_by_field_name("body")

    branches, exception_types, calls = _ruby_body_info(body, file_path, params, graph)
    unit = CodeUnit(
        id=func_id,
        name=func_name,
        kind=kind,
        location=make_location(node, file_path),
        parameters=tuple(params),
        return_type=None,
        exception_types=tuple(exception_types),
        cyclomatic_complexity=compute_complexity(branches),
        branches=tuple(branches),
        docstring=_extract_docstring(node),
        attributes=_ruby_attributes(singleton, visibility, controller, worker),
    )
    graph.nodes[func_id] = unit
    add_contains_edge(graph, container_id, func_id)

    _add_call_edges(
        calls,
        func_id,
        module_id,
        class_id,
        file_path,
        graph=graph,
    )


def _ruby_attributes(
    singleton: bool,
    visibility: str,
    controller: bool,
    worker: bool,
) -> tuple[tuple[str, str | bool], ...]:
    return (
        ("ruby_receiver", "singleton" if singleton else "instance"),
        ("ruby_visibility", visibility),
        ("ruby_controller", controller),
        ("ruby_worker", worker),
    )


def _add_call_edges(
    calls: list[tuple[str, Node]],
    func_id: str,
    module_id: str,
    class_id: str | None,
    file_path: str,
    *,
    graph: CodeGraph,
) -> None:
    """Add CALLS edges for all collected call sites."""
    for call_name, call_node in calls:
        target_id = _resolve_call_target(
            call_name,
            module_id,
            class_id,
        )
        confidence = _call_confidence(call_name)
        bare = "." not in call_name or call_name.startswith("self.")
        attributes: tuple[tuple[str, str | bool], ...] = (("binding_blocked", False),)
        if bare:
            attributes += (("ruby_bare_name", call_name.removeprefix("self.")),)
        if call_name.startswith("self."):
            attributes += (("ruby_explicit_self", True),)

        graph.edges.append(
            CodeEdge(
                source_id=func_id,
                target_id=target_id,
                kind=EdgeKind.CALLS,
                confidence=confidence,
                location=make_location(call_node, file_path),
                attributes=attributes,
            )
        )


def _call_confidence(call_name: str) -> EdgeConfidence:
    """Determine confidence for a Ruby call."""
    if "." in call_name:
        return EdgeConfidence.INFERRED
    return EdgeConfidence.CERTAIN


def _collect_ruby_site(
    node: Node,
    file_path: str,
    branches: list[BranchInfo],
    exception_types: list[TypeRef],
    calls: list[tuple[str, Node]],
) -> None:
    if node.type in _BRANCH_NODE_TYPES:
        condition = _extract_condition_text(node)
        branches.append(
            BranchInfo(
                location=make_location(node, file_path),
                condition=condition,
            )
        )

    if _is_raise_call(node):
        _collect_raise_type(node, exception_types)

    if node.type == "call" and not _attribute_writer(node):
        call_name = _extract_ruby_call_name(node)
        if call_name and call_name not in ("require", "require_relative"):
            calls.append((call_name, node))


def _attribute_writer(node: Node) -> bool:
    parent = node.parent
    return (
        parent is not None
        and parent.type == "assignment"
        and parent.child_by_field_name("left") == node
    )


def _extract_condition_text(node: Node) -> str:
    """Extract the condition expression from a branch node."""
    condition = node.child_by_field_name("condition")
    if condition is not None:
        return node_text(condition)
    return node.type


def _is_raise_call(node: Node) -> bool:
    """Check if a node is a `raise` call."""
    if node.type != "call":
        return False
    method = node.child_by_field_name("method")
    if method is not None and node_text(method) == "raise":
        return True
    return bool(node.children) and node_text(node.children[0]) == "raise"


def _collect_raise_type(
    node: Node,
    exception_types: list[TypeRef],
) -> None:
    """Extract the exception type from a raise call."""
    args = node.child_by_field_name("arguments")
    if args is None:
        return
    for child in args.children:
        if child.type == "constant":
            exception_types.append(TypeRef(name=node_text(child)))
            return
        if child.type == "call":
            method = child.child_by_field_name("method")
            if method is not None:
                exception_types.append(TypeRef(name=node_text(method)))
                return


def _extract_ruby_call_name(node: Node) -> str:
    """Extract the method name from a Ruby call node."""
    receiver = node.child_by_field_name("receiver")
    method = node.child_by_field_name("method")
    if method is None:
        # Try first child as method name
        if node.children:
            first = node.children[0]
            if first.type == "identifier":
                return node_text(first)
        return ""
    method_name = node_text(method)
    if receiver is not None:
        return f"{node_text(receiver)}.{method_name}"
    return method_name


def _extract_parameters(node: Node) -> list[Parameter]:
    """Extract parameters from a Ruby method definition."""
    params_node = node.child_by_field_name("parameters")
    if params_node is None:
        return []

    params: list[Parameter] = []
    for child in params_node.children:
        if child.type == "identifier":
            params.append(Parameter(name=node_text(child)))
        elif child.type == "optional_parameter":
            param = _parse_optional_parameter(child)
            if param is not None:
                params.append(param)
        elif child.type in {
            "splat_parameter",
            "hash_splat_parameter",
            "block_parameter",
            "keyword_parameter",
        }:
            name = child.child_by_field_name("name")
            value = child.child_by_field_name("value")
            if name is not None:
                params.append(
                    Parameter(node_text(name), default=node_text(value) if value else None)
                )
        elif child.type == "forward_parameter":
            params.append(Parameter("..."))
    return params


def _parse_optional_parameter(node: Node) -> Parameter | None:
    """Parse an optional_parameter node into a Parameter."""
    children = [c for c in node.children if c.type != "="]
    if len(children) < 2:
        return None
    name = node_text(children[0])
    default = node_text(children[-1])
    return Parameter(name=name, default=default)


def _extract_docstring(node: Node) -> str | None:
    """Extract comment block preceding a definition."""
    prev = node.prev_named_sibling
    if prev is not None and prev.type == "comment":
        text = node_text(prev)
        if text.startswith("#"):
            return text.lstrip("# ").strip()
    return None


def _check_require(node: Node, graph: CodeGraph) -> None:
    """Check if a call node is a require/require_relative."""
    method = node.child_by_field_name("method")
    if method is None:
        if node.children and node.children[0].type == "identifier":
            method = node.children[0]
        else:
            return
    method_name = node_text(method)
    if method_name not in ("require", "require_relative"):
        return
    args = node.child_by_field_name("arguments")
    if args is None:
        return
    for child in args.children:
        if child.type == "string":
            dep = _clean_string(node_text(child))
            if dep and dep not in graph.dependencies:
                graph.dependencies.append(dep)


def _clean_string(text: str) -> str:
    """Strip quote delimiters from a Ruby string."""
    if text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    if text.startswith("'") and text.endswith("'"):
        return text[1:-1]
    return text


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


def _ruby_bound_before(node: Node, name: str, parameters: set[str]) -> bool:
    if name in parameters:
        return True
    index = scope_index(node, _BINDING_SCOPES, _BINDING_DECLARATIONS)
    return index.declaration(node, name, True) is not None or index.callback_parameter(node, name)


def _bind_ruby_methods(graph: CodeGraph) -> None:
    owners = {e.target_id: e.source_id for e in graph.edges if e.kind == EdgeKind.CONTAINS}
    bases: dict[str, list[str]] = {}
    methods: dict[tuple[str, str, str], list[str]] = {}
    for edge in graph.edges:
        if edge.kind == EdgeKind.INHERITS:
            bases.setdefault(edge.source_id, []).append(edge.target_id)
    for unit in graph.nodes.values():
        _index_ruby_method(unit, owners, methods)
    graph.edges[:] = [_ruby_method_edge(e, graph, owners, bases, methods) for e in graph.edges]


def _index_ruby_method(
    unit: CodeUnit,
    owners: dict[str, str],
    methods: dict[tuple[str, str, str], list[str]],
) -> None:
    receiver = dict(unit.attributes).get("ruby_receiver")
    if unit.kind == NodeKind.METHOD and isinstance(receiver, str):
        methods.setdefault((owners.get(unit.id, ""), unit.name, receiver), []).append(unit.id)


def _ruby_method_edge(
    edge: CodeEdge,
    graph: CodeGraph,
    owners: dict[str, str],
    bases: dict[str, list[str]],
    methods: dict[tuple[str, str, str], list[str]],
) -> CodeEdge:
    attributes = dict(edge.attributes)
    name = attributes.get("ruby_bare_name")
    source = graph.nodes.get(edge.source_id)
    if not isinstance(name, str) or source is None or attributes.get("binding_blocked"):
        return edge
    owner = owners.get(source.id, "")
    receiver = str(dict(source.attributes).get("ruby_receiver", "instance"))
    candidates = _ruby_candidates(owner, name, receiver, bases, methods)
    if len(candidates) == 1:
        target = candidates[0]
        return replace(
            edge,
            target_id=target,
            confidence=EdgeConfidence.CERTAIN
            if owners.get(target) == owner
            else EdgeConfidence.INFERRED,
        )
    if candidates:
        return replace(
            edge, target_id=f"{owner}.{name}@ambiguous", confidence=EdgeConfidence.UNCERTAIN
        )
    return edge


def _ruby_candidates(
    owner: str,
    name: str,
    receiver: str,
    bases: dict[str, list[str]],
    methods: dict[tuple[str, str, str], list[str]],
) -> list[str]:
    pending, seen, result = [owner], set(), []
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        local = methods.get((current, name, receiver), [])
        if local:
            result.extend(local)
        else:
            pending.extend(bases.get(current, ()))
    return result


def _ruby_body_info(
    body: Node | None, file_path: str, params: list[Parameter], graph: CodeGraph
) -> tuple[list[BranchInfo], list[TypeRef], list[tuple[str, Node]]]:
    bound = {p.name for p in params}
    branches: list[BranchInfo] = []
    exceptions: list[TypeRef] = []
    calls: list[tuple[str, Node]] = []
    if body is not None:
        for current in _ruby_sites(body, file_path, graph):
            if current.type == "identifier":
                if _ruby_vcall(current, bound):
                    calls.append((node_text(current), current))
            else:
                _collect_ruby_site(current, file_path, branches, exceptions, calls)
        calls.sort(key=lambda call: call[1].start_byte)
    return branches, exceptions, calls


def _ruby_sites(body: Node, path: str, graph: CodeGraph) -> Iterator[Node]:
    boundaries = frozenset({"method", "singleton_method", "class", "module"})
    blocks = frozenset({"block", "do_block", "lambda"})
    types = boundaries | blocks | _BRANCH_NODE_TYPES | {"call", "identifier"}
    enclosing: list[int] = []
    skipped_until = -1
    for node in syntax_nodes(body, types, tokens=_BRANCH_NODE_TYPES):
        if node.start_byte < skipped_until:
            continue
        if node.type in boundaries:
            skipped_until = node.end_byte
            continue
        while enclosing and node.start_byte >= enclosing[-1]:
            enclosing.pop()
        if node.type in blocks:
            if len(enclosing) >= 47:
                graph.parse_diagnostics.append(
                    nesting_diagnostic(node, path, "ruby", construct="Block")
                )
                skipped_until = node.end_byte
                continue
            enclosing.append(node.end_byte)
        yield node


def _ruby_vcall(node: Node, parameters: set[str]) -> bool:
    if node.type != "identifier":
        return False
    parent = node.parent
    if parent is None:
        return False
    if _numbered_parameter(node):
        return False
    if parent.type in {
        "call",
        "assignment",
        "method_parameters",
        "block_parameters",
        "lambda_parameters",
        "destructured_parameter",
        "splat_parameter",
        "block_parameter",
        "left_assignment_list",
        "hash_splat_parameter",
        "exception_variable",
        "for",
    }:
        return False
    if (
        parent.type in {"optional_parameter", "keyword_parameter"}
        and parent.child_by_field_name("name") == node
    ):
        return False
    return not _ruby_bound_before(node, node_text(node), parameters)


def _numbered_parameter(node: Node) -> bool:
    """Bare numbered arguments are implicit values inside blocks and lambdas."""
    if node.text not in {b"it", b"_1", b"_2", b"_3", b"_4", b"_5", b"_6", b"_7", b"_8", b"_9"}:
        return False
    parent = node.parent
    while parent is not None:
        if parent.type in {"block", "do_block", "lambda"}:
            return not (node.text == b"it" and parent.child_by_field_name("parameters") is not None)
        if parent.type in {"method", "singleton_method", "class", "module"}:
            return False
        parent = parent.parent
    return False


def _class_traits(node: Node, body: Node) -> tuple[bool, bool]:
    superclass = node.child_by_field_name("superclass")
    base = _extract_superclass_name(superclass) if superclass is not None else ""
    controller = base == "ApplicationController" or base in {
        "ActionController::Base",
        "ActionController::API",
    }
    return controller, any(_worker_include(child) for child in body.named_children)


def _worker_include(node: Node) -> bool:
    method = node.child_by_field_name("method")
    args = node.child_by_field_name("arguments")
    if node.type != "call" or method is None or node_text(method) != "include" or args is None:
        return False
    return any(node_text(arg) in {"Sidekiq::Worker", "Sidekiq::Job"} for arg in args.named_children)


def _apply_named_visibility(
    graph: CodeGraph, class_id: str, named_visibility: dict[str, str]
) -> None:
    owned = {
        edge.target_id
        for edge in graph.edges
        if edge.kind == EdgeKind.CONTAINS and edge.source_id == class_id
    }
    for unit in list(graph.nodes.values()):
        if (
            unit.id in owned
            and unit.name in named_visibility
            and dict(unit.attributes).get("ruby_receiver") == "instance"
        ):
            attributes = dict(unit.attributes)
            attributes["ruby_visibility"] = named_visibility[unit.name]
            graph.nodes[unit.id] = replace(unit, attributes=tuple(attributes.items()))


def _function_identity(
    module_id: str, class_id: str | None, func_name: str, singleton: bool
) -> tuple[str, NodeKind, str]:
    if class_id is not None:
        func_id = f"{class_id}.{'self.' if singleton else ''}{func_name}"
        kind = NodeKind.METHOD
        container_id = class_id
    else:
        func_id = f"{module_id}:{func_name}"
        kind = NodeKind.FUNCTION
        container_id = module_id

    return func_id, kind, container_id


def _named_visibility(child: Node, named_visibility: dict[str, str]) -> None:
    method = child.child_by_field_name("method")
    args = child.child_by_field_name("arguments")
    if (
        method is not None
        and node_text(method) in {"private", "protected", "public"}
        and args is not None
    ):
        for arg in args.named_children:
            if arg.type == "simple_symbol":
                named_visibility[node_text(arg).removeprefix(":")] = node_text(method)


def _self_singleton(child: Node) -> bool:
    value = child.child_by_field_name("value")
    return child.type == "singleton_class" and value is not None and value.type == "self"
