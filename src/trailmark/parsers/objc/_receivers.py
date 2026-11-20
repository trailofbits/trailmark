"""Bind simple instance-variable messages from inspected Objective-C types."""

from dataclasses import replace

from tree_sitter import Node

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers._bindings import namespace_receiver_matches
from trailmark.parsers._common import first_child_by_type, link_cross_file_calls, node_text


def record_class(node: Node, owner: str, graph: CodeGraph) -> None:
    """Keep explicit pointer ivars and ordinary property declarations separate."""
    if node.type not in {"class_interface", "class_implementation"}:
        return
    attrs = dict(graph.nodes[owner].attributes)
    attrs["objc_class"] = True
    if any(child.type == "(" for child in node.children):
        # This grammar represents categories/extensions with ordinary class nodes.
        graph.nodes[owner] = replace(graph.nodes[owner], attributes=tuple(attrs.items()))
        return
    if node.type == "class_implementation":
        count = attrs.get("objc_implementation_count", 0)
        attrs["objc_implementation_count"] = (count if isinstance(count, int) else 0) + 1
    superclass = node.child_by_field_name("superclass")
    if superclass is not None:
        name = node_text(superclass)
        attrs["objc_superclass"] = (
            name if "objc_superclass" not in attrs or attrs["objc_superclass"] == name else None
        )
    pending = list(node.named_children)
    while pending:
        child = pending.pop()
        if child.type in {"instance_variables", "instance_variable", "implementation_definition"}:
            pending.extend(child.named_children)
        elif child.type in {"property_declaration", "struct_declaration"}:
            declaration = (
                first_child_by_type(child, "struct_declaration")
                if child.type == "property_declaration"
                else child
            )
            if declaration is None or declaration.has_error:
                continue
            for declarator in declaration.named_children:
                if declarator.type != "struct_declarator" or len(declarator.named_children) != 1:
                    continue
                pointer = declarator.named_children[0]
                if child.type == "property_declaration":
                    name_node = pointer
                    while name_node.type != "identifier":
                        nested = name_node.child_by_field_name("declarator")
                        if nested is None:
                            break
                        name_node = nested
                    if name_node.type == "identifier":
                        attrs["objc_declared_property:" + node_text(name_node)] = True
                    else:
                        attrs["objc_opaque_property"] = True
                if pointer.type != "pointer_declarator":
                    continue
                type_node = first_child_by_type(declaration, "type_identifier")
                if type_node is None:
                    continue
                type_name = node_text(type_node)
                names = [n for n in pointer.named_children if n.type == "identifier"]
                if len(names) != 1 or any(
                    n.type == "pointer_declarator" for n in pointer.named_children
                ):
                    continue
                name = node_text(names[0])
                prefix = "objc_property:" if child.type == "property_declaration" else "objc_ivar:"
                key = prefix + name
                attrs[key] = type_name if key not in attrs or attrs[key] == type_name else None
                if child.type == "property_declaration":
                    properties = first_child_by_type(child, "property_attributes_declaration")
                    if properties is not None and any(
                        node_text(prop) == "class"
                        or any(token.type == "=" for token in prop.children)
                        for prop in properties.named_children
                    ):
                        attrs["objc_property_custom:" + name] = True
        elif child.type == "property_implementation":
            for name in child.named_children:
                if name.type == "identifier":
                    attrs["objc_property_custom:" + node_text(name)] = True
    graph.nodes[owner] = replace(graph.nodes[owner], attributes=tuple(attrs.items()))


def message_receiver(edge: CodeEdge, node: Node, graph: CodeGraph) -> str | None:
    """A bare ivar spelling must retain its lexical instance-variable binding."""
    receiver = node.child_by_field_name("receiver")
    if receiver is None or receiver.type != "identifier":
        return None
    name = node_text(receiver)
    if name in {"self", "super"}:
        return None
    ancestor = node.parent
    while ancestor is not None:
        if ancestor.type == "method_definition":
            if any(c.type == "+" for c in ancestor.children) or any(
                child.type == "identifier" and node_text(child) == name
                for parameter in ancestor.named_children
                if parameter.type == "method_parameter"
                for child in parameter.named_children
            ):
                return None
        elif ancestor.type == "block_literal":
            parameters = first_child_by_type(ancestor, "parameter_list")
            if parameters is not None:
                for parameter in parameters.named_children:
                    declarator = parameter.child_by_field_name("declarator")
                    while declarator is not None and declarator.type != "identifier":
                        declarator = declarator.child_by_field_name("declarator")
                    if declarator is not None and node_text(declarator) == name:
                        return None
        elif ancestor.type == "for_statement":
            body = ancestor.child_by_field_name("body")
            if body is None and ancestor.named_children:
                body = ancestor.named_children[-1]
            if body is not None and body.start_byte <= node.start_byte < body.end_byte:
                before_in = []
                for child in ancestor.children:
                    if child.type == "in":
                        if any(n.type == "identifier" and node_text(n) == name for n in before_in):
                            return None
                        break
                    before_in.append(child)
        elif ancestor.type == "catch_clause":
            declaration = first_child_by_type(ancestor, "type_name")
            if declaration is not None and any(
                child.type == "type_identifier" and node_text(child) == name
                for child in declaration.named_children
            ):
                return None
        ancestor = ancestor.parent
    target = edge.source_id.split(":", 1)[0] + ":" + name
    probe = replace(edge, target_id=target, attributes=())
    return name if namespace_receiver_matches(probe, node, graph, target) else None


def bind_ivar_messages(graph: CodeGraph) -> None:
    owners = {e.target_id: e.source_id for e in graph.edges if e.kind == EdgeKind.CONTAINS}
    classes: dict[str, list[str]] = {}
    methods: dict[str, list[str]] = {}
    for unit in graph.nodes.values():
        if dict(unit.attributes).get("objc_class"):
            classes.setdefault(unit.name, []).append(unit.id)
        if unit.kind == NodeKind.METHOD and unit.id in owners:
            methods.setdefault(owners[unit.id], []).append(unit.id)

    def cluster(name: str) -> list[str]:
        ids = classes.get(name, [])
        implementations = sum(
            count
            for key in ids
            if isinstance(
                count := dict(graph.nodes[key].attributes).get("objc_implementation_count"), int
            )
        )
        inconsistent = any(
            value is None and key.startswith(("objc_ivar:", "objc_property:", "objc_superclass"))
            for ident in ids
            for key, value in graph.nodes[ident].attributes
        )
        return ids if implementations <= 1 and not inconsistent else []

    def inherited_property(local: list[str], name: str) -> bool:
        """An inherited declaration prevents a new default backing ivar."""
        seen: set[str] = set()
        while local:
            bases = {
                value
                for ident in local
                for key, value in graph.nodes[ident].attributes
                if key == "objc_superclass" and isinstance(value, str)
            }
            if not bases:
                return False
            if len(bases) != 1:
                return True
            base = bases.pop()
            if base in seen:
                return True
            seen.add(base)
            if base == "NSObject" and base not in classes:
                return name in {"description", "debugDescription", "hash", "superclass"}
            local = cluster(base)
            if not local or any(
                dict(graph.nodes[ident].attributes).get("objc_declared_property:" + name)
                or dict(graph.nodes[ident].attributes).get("objc_opaque_property")
                for ident in local
            ):
                return True
        return False

    edges = []
    for edge in graph.edges:
        attrs = dict(edge.attributes)
        receiver, selector = attrs.get("objc_bare_receiver"), attrs.get("objc_selector")
        if (
            edge.kind != EdgeKind.CALLS
            or not isinstance(receiver, str)
            or not isinstance(selector, str)
        ):
            edges.append(edge)
            continue
        # Directory linking can reveal conflicts absent during the per-file pass.
        original = attrs.setdefault("objc_untyped_target", edge.target_id)
        if not isinstance(original, str):
            edges.append(edge)
            continue
        attrs.pop("objc_receiver_type", None)
        attrs["binding_blocked"] = True
        edge = replace(
            edge,
            target_id=original,
            confidence=EdgeConfidence.UNCERTAIN,
            attributes=tuple(attrs.items()),
        )
        owner = edge.source_id
        while owner in graph.nodes and graph.nodes[owner].kind != NodeKind.CLASS:
            owner = owners.get(owner, "")
        if owner not in graph.nodes:
            edges.append(edge)
            continue
        local = cluster(graph.nodes[owner].name)
        types = set()
        for key in local:
            type_name = dict(graph.nodes[key].attributes).get("objc_ivar:" + receiver)
            if isinstance(type_name, str):
                types.add(type_name)
        if receiver.startswith("_"):
            property_name = receiver[1:]
            custom = any(
                dict(graph.nodes[key].attributes).get("objc_property_custom:" + property_name)
                for key in local
            )
            custom = custom or any(
                graph.nodes[method].name
                in {property_name, "set" + property_name[:1].upper() + property_name[1:] + ":"}
                and dict(graph.nodes[method].attributes).get("objc_method_definition")
                for key in local
                for method in methods.get(key, [])
            )
            if not custom and not inherited_property(local, property_name):
                for key in local:
                    type_name = dict(graph.nodes[key].attributes).get(
                        "objc_property:" + property_name
                    )
                    if isinstance(type_name, str):
                        types.add(type_name)
        if len(types) == 1:
            target_classes = cluster(next(iter(types)))
            targets = [
                method
                for key in target_classes
                for method in methods.get(key, [])
                if graph.nodes[method].name == selector and ".+" not in method
            ]
            definitions = [
                key
                for key in targets
                if dict(graph.nodes[key].attributes).get("objc_method_definition")
            ]
            targets = definitions or targets
            if (
                len(targets) == 1
                and dict(graph.nodes[targets[0]].attributes).get("objc_definition_count", 1) == 1
            ):
                attrs.update(binding_blocked=False, objc_receiver_type=next(iter(types)))
                edge = replace(
                    edge,
                    target_id=targets[0],
                    confidence=EdgeConfidence.INFERRED,
                    attributes=tuple(attrs.items()),
                )
        edges.append(edge)
    graph.edges[:] = edges


def link_calls(graph: CodeGraph) -> None:
    bind_ivar_messages(graph)
    link_cross_file_calls(graph)
