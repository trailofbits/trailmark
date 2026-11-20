"""Bounded receiver candidates from straight-line immutable factory results."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

from tree_sitter import Node

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind
from trailmark.parsers._common import link_cross_file_calls, node_text

_IMPORT = re.compile(r"import\s+(['\"])([^'\"\\]+)\1\s*(?:as\s+(\w+)\s*)?;")


def record_return_receivers(root: Node, module_id: str, graph: CodeGraph) -> None:
    """Keep source-local evidence while the original syntax tree is available."""
    if root.has_error or any(c.type.startswith("part") for c in root.named_children):
        module = graph.nodes[module_id]
        graph.nodes[module_id] = replace(
            module, attributes=(*module.attributes, ("dart_type_scope_incomplete", True))
        )
        return
    if any(
        c.type == "import_or_export" and "library_export" in {n.type for n in c.named_children}
        for c in root.named_children
    ):
        module = graph.nodes[module_id]
        graph.nodes[module_id] = replace(
            module, attributes=(*module.attributes, ("dart_type_scope_incomplete", True))
        )
    aliases = _alias_names(root)
    sites: dict[tuple[int, int], Node] = {}
    functions: dict[tuple[int, int], str] = {}
    pending = [root]
    while pending:
        node = pending.pop()
        if node.type == "selector":
            sites[(node.start_point.row + 1, node.start_point.column)] = node
        if node.type == "function_signature":
            signature = (
                node.parent if node.parent and node.parent.type == "method_signature" else node
            )
            name = node.child_by_field_name("name")
            prefix = [
                c for c in node.children if name is not None and c.end_byte <= name.start_byte
            ]
            if len(prefix) == 1 and prefix[0].type == "type_identifier":
                functions[(signature.start_point.row + 1, signature.start_point.column)] = (
                    node_text(prefix[0])
                )
        pending.extend(node.named_children)
    owners = {e.target_id: e.source_id for e in graph.edges if e.kind == EdgeKind.CONTAINS}
    by_site = {
        (e.source_id, e.location.start_line, e.location.start_col): e
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and e.location is not None
    }
    recorded = False
    for index, edge in enumerate(graph.edges):
        if edge.kind != EdgeKind.CALLS or edge.location is None:
            continue
        parts = edge.target_id.split(".")
        if len(parts) != 2 or not all(part.isidentifier() for part in parts):
            continue
        call = sites.get((edge.location.start_line, edge.location.start_col or 0))
        if call is None:
            continue
        initializer = _final_initializer(call, parts[0])
        if initializer is None:
            continue
        factory_edge = by_site.get(
            (edge.source_id, initializer.start_point.row + 1, initializer.start_point.column)
        )
        if factory_edge is None or dict(factory_edge.attributes).get("binding_blocked"):
            continue
        factory = graph.nodes.get(factory_edge.target_id)
        if factory is None or factory.location.file_path != edge.location.file_path:
            continue
        result = factory.return_type
        if result is None or not result.name.isidentifier() or result.name in aliases:
            continue
        if (
            functions.get((factory.location.start_line, factory.location.start_col or 0))
            != result.name
        ):
            continue
        owner = factory.id
        generic = False
        while owner in graph.nodes:
            generic |= any(p.name == result.name for p in graph.nodes[owner].type_parameters)
            owner = owners.get(owner, "")
        if generic:
            continue
        graph.edges[index] = replace(
            edge,
            attributes=(
                *edge.attributes,
                ("dart_receiver_factory", factory.id),
                ("dart_receiver_method", parts[1]),
            ),
        )
        recorded = True
    if recorded:
        imports, complete = _direct_imports(root, graph.nodes[module_id].location.file_path)
        module = graph.nodes[module_id]
        graph.nodes[module_id] = replace(
            module,
            attributes=(
                *module.attributes,
                ("dart_type_imports", json.dumps(imports)),
                ("dart_type_imports_complete", complete),
            ),
        )


def _alias_names(root: Node) -> set[str]:
    aliases = set()
    for child in root.named_children:
        if child.type != "type_alias":
            continue
        names = []
        for part in child.children:
            if part.type in {"=", "formal_parameter_list"}:
                break
            if part.type == "type_identifier":
                names.append(node_text(part))
        if names:
            aliases.add(names[-1])
    return aliases


def _final_initializer(call: Node, name: str) -> Node | None:
    statement = call.parent
    block = statement.parent if statement is not None else None
    if statement is None or statement.type not in {"expression_statement", "return_statement"}:
        return None
    if block is None or block.type != "block":
        return None
    declarations = []
    for sibling in block.named_children:
        if sibling.type != "local_variable_declaration":
            continue
        for declaration in sibling.named_children:
            variable = declaration.child_by_field_name("name")
            if variable is not None and node_text(variable) == name:
                declarations.append(declaration)
    if len(declarations) != 1:
        return None
    declaration = declarations[0]
    if declaration.end_byte >= call.start_byte or declaration.has_error:
        return None
    if [c.type for c in declaration.named_children] != [
        "final_builtin",
        "identifier",
        "identifier",
        "selector",
    ]:
        return None
    values = declaration.children_by_field_name("value")
    if len(values) != 2 or values[0].type != "identifier" or values[1].type != "selector":
        return None
    return values[1]


def _direct_imports(root: Node, file_path: str) -> tuple[list[str], bool]:
    imports, complete = [], True
    for child in root.named_children:
        if child.type != "import_or_export":
            continue
        match = _IMPORT.fullmatch(node_text(child).strip())
        if match is None:
            complete = False
            continue
        if match[3]:
            continue
        uri = match[2]
        if ":" in uri or not uri.endswith(".dart") or Path(uri).is_absolute():
            complete = False
            continue
        imports.append(str((Path(file_path).parent / uri).resolve()))
    return imports, complete


def link_return_receivers(graph: CodeGraph) -> None:
    """Resolve only inspected class declarations exposed by direct imports."""
    owners = {e.target_id: e.source_id for e in graph.edges if e.kind == EdgeKind.CONTAINS}
    modules = {
        str(Path(n.location.file_path).resolve()): n
        for n in graph.nodes.values()
        if n.kind == NodeKind.MODULE
    }
    methods: dict[tuple[str, str], list[str]] = {}
    classes: dict[tuple[str, str], list[CodeUnit]] = {}
    for unit in graph.nodes.values():
        if unit.kind == NodeKind.METHOD:
            methods.setdefault((owners.get(unit.id, ""), unit.name), []).append(unit.id)
        if unit.kind == NodeKind.CLASS:
            classes.setdefault(
                (str(Path(unit.location.file_path).resolve()), unit.name), []
            ).append(unit)
    for index, edge in enumerate(graph.edges):
        attrs = dict(edge.attributes)
        factory = graph.nodes.get(str(attrs.get("dart_receiver_factory", "")))
        if factory is None or factory.return_type is None:
            continue
        file_path = str(Path(factory.location.file_path).resolve())
        name = factory.return_type.name
        candidates = classes.get((file_path, name), [])
        if not candidates:
            module = modules.get(file_path)
            metadata = dict(module.attributes) if module else {}
            if not metadata.get("dart_type_imports_complete") or name.startswith("_"):
                continue
            paths = json.loads(str(metadata.get("dart_type_imports", "[]")))
            if any(
                path not in modules
                or dict(modules[path].attributes).get("dart_type_scope_incomplete")
                for path in paths
            ):
                continue
            candidates = [c for path in paths for c in classes.get((path, name), [])]
        if len(candidates) != 1:
            continue
        targets = methods.get((candidates[0].id, str(attrs.get("dart_receiver_method"))), [])
        if len(targets) == 1:
            graph.edges[index] = replace(
                edge, target_id=targets[0], confidence=EdgeConfidence.INFERRED
            )
    link_cross_file_calls(graph)
