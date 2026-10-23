"""Move declarations and calls using the bundled Move project grammar."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from tree_sitter import Language, Node, Parser

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import (
    BranchInfo,
    CodeUnit,
    NodeKind,
    NodeOrigin,
    Parameter,
    SourceLocation,
    TypeRef,
)
from trailmark.parsers._binding_index import binding_scope
from trailmark.parsers._bindings import lexical_declaration
from trailmark.parsers._common import (
    add_contains_edge,
    add_module_node,
    compute_complexity,
    make_location,
    module_id_from_path,
    node_text,
    parse_directory,
)
from trailmark.parsers._walk import walk_owned
from trailmark.tree_sitter_custom.move import language

_EXTENSIONS = (".move",)
_FUNCTIONS = {"function_definition", "native_function_definition", "macro_function_definition"}
_SCOPES = frozenset({"function_definition", "block", "lambda_expression", "for_expression"})
_BINDINGS = (
    ("let_statement", "binds"),
    ("lambda_expression", "bindings"),
    ("for_expression", "binding"),
)


@dataclass(frozen=True)
class _Module:
    owner: str
    logical: str
    imports: dict[str, str]
    modules: dict[str, str]
    path: str
    graph: CodeGraph


class MoveParser:
    """Parse supported Aptos/Sui syntax without runtime address or value inference."""

    @property
    def language(self) -> str:
        return "move"

    def __init__(self) -> None:
        self._parser = Parser(Language(language()))

    @binding_scope
    def parse_file(self, file_path: str) -> CodeGraph:
        root = self._parser.parse(Path(file_path).read_bytes()).root_node
        graph = CodeGraph(language="move", root_path=file_path)
        module = module_id_from_path(file_path)
        add_module_node(root, file_path, module, graph)
        for declaration in root.named_children:
            if declaration.type == "module_definition":
                _module(declaration, module, file_path, graph)
        _link_calls(graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        graph = parse_directory(self.parse_file, "move", dir_path, _EXTENSIONS)
        _link_calls(graph)
        return graph


def _module(node: Node, module: str, path: str, graph: CodeGraph) -> None:
    identity = node.child_by_field_name("module_identity")
    body = node.child_by_field_name("module_body")
    if identity is None or body is None:
        return
    logical = node_text(identity)
    owner = f"{module}:{logical}"
    graph.nodes[owner] = CodeUnit(
        id=owner, name=logical, kind=NodeKind.MODULE, location=make_location(node, path)
    )
    add_contains_edge(graph, module, owner)
    imports: dict[str, str] = {}
    modules: dict[str, str] = {}
    for item in body.named_children:
        if item.type == "use_declaration":
            _imports(item, imports, modules, owner, graph)
    context = _Module(
        owner=owner, logical=logical, imports=imports, modules=modules, path=path, graph=graph
    )
    for item in body.named_children:
        if item.type in _FUNCTIONS:
            _function(item, context)
        elif item.type in {"struct_definition", "native_struct_definition", "enum_definition"}:
            _type(item, context)


def _imports(
    node: Node, imports: dict[str, str], modules: dict[str, str], owner: str, graph: CodeGraph
) -> None:
    for imported, prefix in _module_imports(node):
        if prefix not in graph.dependencies:
            graph.dependencies.append(prefix)
        graph.edges.append(CodeEdge(owner, prefix, EdgeKind.IMPORTS))
        if _is_module_import(imported):
            alias = imported.child_by_field_name("alias")
            modules[node_text(alias) if alias else prefix.rsplit("::", 1)[-1]] = prefix
        else:
            for member in _imported_members(imported):
                _import_member(member, prefix, imports, modules)


def _module_imports(node: Node) -> list[tuple[Node, str]]:
    entries = []
    for imported in node.named_children:
        target = next((c for c in imported.named_children if c.type == "module_identity"), None)
        if target is not None:
            entries.append((imported, node_text(target)))
        else:
            entries.extend(_address_imports(imported))
    return entries


def _address_imports(node: Node) -> list[tuple[Node, str]]:
    address = node.child_by_field_name("address")
    if address is None:
        return []
    return [
        (member, f"{node_text(address)}::{node_text(module)}")
        for member in node.children_by_field_name("use_member")
        if (module := member.child_by_field_name("module") or member.child_by_field_name("member"))
        is not None
    ]


def _imported_members(node: Node) -> list[Node]:
    if node.type == "use_member" and node.child_by_field_name("member") is not None:
        return [node]
    return node.children_by_field_name("use_member")


def _import_member(
    member: Node, prefix: str, imports: dict[str, str], modules: dict[str, str]
) -> None:
    symbol = member.child_by_field_name("member")
    alias = member.child_by_field_name("alias")
    if symbol is None:
        return
    name = node_text(symbol)
    key = node_text(alias) if alias else (prefix.rsplit("::", 1)[-1] if name == "Self" else name)
    if name == "Self":
        modules[key] = prefix
    else:
        imports[key] = f"{prefix}::{name}"


def _type(node: Node, context: _Module) -> None:
    name = node.child_by_field_name("name")
    if name is None:
        return
    ident = f"{context.owner}:{node_text(name)}"
    context.graph.nodes[ident] = CodeUnit(
        id=ident,
        name=node_text(name),
        kind=NodeKind.ENUM if node.type == "enum_definition" else NodeKind.STRUCT,
        location=make_location(node, context.path),
    )
    add_contains_edge(context.graph, context.owner, ident)


def _parameters(node: Node) -> tuple[Parameter, ...]:
    parameters = node.child_by_field_name("parameters")
    if parameters is None:
        return ()
    params = []
    for parameter in walk_owned(parameters):
        if parameter.type != "function_parameter":
            continue
        name = parameter.child_by_field_name("name")
        annotation = parameter.child_by_field_name("type")
        if name is not None:
            params.append(
                Parameter(
                    ("$" if node_text(parameter).lstrip().startswith("$") else "")
                    + node_text(name),
                    TypeRef(node_text(annotation)) if annotation else None,
                )
            )
    return tuple(params)


def _function(node: Node, context: _Module) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = node_text(name_node)
    ident = f"{context.owner}:{name}"
    if ident in context.graph.nodes:
        ident += f"@{node.start_point.row + 1}_{node.start_point.column}"
    parameters = _parameters(node)
    branches, calls = _body(node.child_by_field_name("body"), context.path)
    result = node.child_by_field_name("return_type")
    modifiers = [node_text(c) for c in node.named_children if c.type == "modifier"]
    context.graph.nodes[ident] = CodeUnit(
        id=ident,
        name=name,
        kind=NodeKind.FUNCTION,
        location=make_location(node, context.path),
        parameters=parameters,
        return_type=TypeRef(node_text(result).removeprefix(":").strip()) if result else None,
        branches=tuple(branches),
        cyclomatic_complexity=compute_complexity(branches),
        attributes=(
            ("move_symbol", f"{context.logical}::{name}"),
            ("move_entry", "entry" in modifiers),
            ("move_public", any(m.startswith("public") or m == "package" for m in modifiers)),
        ),
    )
    add_contains_edge(context.graph, context.owner, ident)
    for site, access, broken in calls:
        context.graph.edges.append(_call(site, access, broken, ident, context))


def _body(body: Node | None, path: str) -> tuple[list[BranchInfo], list[tuple[Node, Node, bool]]]:
    branches, calls = [], []
    pending = [(body, False)] if body is not None else []
    while pending:
        node, broken = pending.pop()
        broken = broken or node.type == "ERROR"
        if node.type in {
            "if_expression",
            "while_expression",
            "loop_expression",
            "for_expression",
            "match_arm",
        }:
            branches.append(BranchInfo(make_location(node, path), node.type))
        call = _body_call(node, broken)
        if call is not None:
            calls.append(call)
        pending.extend((child, broken) for child in reversed(node.named_children))
    return branches, calls


def _body_call(node: Node, broken: bool) -> tuple[Node, Node, bool] | None:
    if node.type not in {"call_expression", "macro_call_expression"}:
        return None
    access = _access(node)
    return (node, access, broken or node.has_error) if access is not None else None


def _access(site: Node) -> Node | None:
    expression = next((c for c in site.named_children if c.type == "name_expression"), None)
    return (expression or site).child_by_field_name("access")


def _call(site: Node, access: Node, broken: bool, ident: str, context: _Module) -> CodeEdge:
    raw = _access_name(access)
    blocked = broken or _shadowed_call(site, raw, ident, context)
    imported = _imported_target(raw, context)
    confidence = EdgeConfidence.UNCERTAIN if blocked or "::" in imported else EdgeConfidence.CERTAIN
    raw = imported
    target, builtin = _call_target(site, raw, context)
    if blocked:
        target = f"{ident}.$binding.{raw}"
    return CodeEdge(
        ident,
        target,
        EdgeKind.CALLS,
        confidence=confidence,
        location=make_location(site, context.path),
        attributes=(
            ("binding_blocked", blocked),
            ("move_target", raw if not blocked and not builtin else ""),
        ),
    )


def _call_target(site: Node, raw: str, context: _Module) -> tuple[str, bool]:
    if site.type == "macro_call_expression" and raw in {"assert", "vector"}:
        target = f"builtin.move:{raw}!"
        context.graph.nodes.setdefault(
            target,
            CodeUnit(
                target,
                f"{raw}!",
                NodeKind.FUNCTION,
                SourceLocation("<builtin>", 1, 1),
                origin=NodeOrigin.SYNTHETIC,
                attributes=(("move_builtin", True),),
            ),
        )
        return target, True
    return (raw if "::" in raw else f"{context.owner}:{raw}"), False


def _shadowed_call(site: Node, raw: str, ident: str, context: _Module) -> bool:
    local = "::" not in raw
    parameters = context.graph.nodes[ident].parameters
    return local and (
        any(p.name == raw for p in parameters)
        or lexical_declaration(site, raw, scopes=_SCOPES, declarations=_BINDINGS) is not None
    )


def _imported_target(raw: str, context: _Module) -> str:
    if "::" in raw:
        first, tail = raw.split("::", 1)
        return context.modules.get(first, first) + "::" + tail
    return context.imports.get(raw, raw)


def _access_name(access: Node) -> str:
    text = access.text or b""
    spans = [
        (n.start_byte - access.start_byte, n.end_byte - access.start_byte)
        for n in walk_owned(access, boundaries=frozenset({"type_arguments"}))
        if n.type == "type_arguments"
    ]
    for start, end in reversed(spans):
        text = text[:start] + text[end:]
    return text.decode().removesuffix("!")


def _link_calls(graph: CodeGraph) -> None:
    symbols: dict[str, list[str]] = {}
    for unit in graph.nodes.values():
        symbol = dict(unit.attributes).get("move_symbol")
        if isinstance(symbol, str):
            symbols.setdefault(symbol, []).append(unit.id)
    edges = []
    for edge in graph.edges:
        raw = dict(edge.attributes).get("move_target")
        candidates = symbols.get(raw, []) if isinstance(raw, str) else []
        if len(candidates) == 1:
            source = graph.nodes[edge.source_id]
            target = graph.nodes[candidates[0]]
            edge = replace(
                edge,
                target_id=target.id,
                confidence=EdgeConfidence.CERTAIN
                if source.location.file_path == target.location.file_path
                else EdgeConfidence.INFERRED,
            )
        edges.append(edge)
    graph.edges[:] = edges


def _is_module_import(node: Node) -> bool:
    if node.type == "use_module":
        return True
    parent = node.parent
    return (
        node.type == "use_member"
        and parent is not None
        and parent.child_by_field_name("address") is not None
        and node.child_by_field_name("module") is None
    )
