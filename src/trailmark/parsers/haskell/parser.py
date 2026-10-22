"""Haskell language parser using tree-sitter."""

from __future__ import annotations

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
    SourceLocation,
    TypeRef,
)
from trailmark.parsers._common import (
    add_contains_edge,
    add_module_node,
    compute_complexity,
    make_location,
    module_id_from_path,
    node_text,
    parse_directory,
)

_BRANCH_NODE_TYPES = frozenset({"alternative", "guards"})

_THROW_TYPES: frozenset[str] = frozenset()

_EXTENSIONS = (".hs",)


class HaskellParser:
    """Parses Haskell source files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "haskell"

    def __init__(self) -> None:
        self._parser = Parser(get_language("haskell"))

    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single Haskell file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="haskell", root_path=file_path)
        module_id = module_id_from_path(file_path)
        _visit_module(tree.root_node, file_path, module_id, graph)
        _link_lexical_calls(graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        """Parse all .hs files under dir_path into a merged graph."""
        return parse_directory(
            self.parse_file,
            "haskell",
            dir_path,
            _EXTENSIONS,
        )


def _visit_module(
    root: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Walk a Haskell module, extracting nodes and edges."""
    add_module_node(root, file_path, module_id, graph)
    for child in root.children:
        if child.type == "imports":
            _extract_imports(child, graph)
        elif child.type == "declarations":
            _visit_declarations(child, file_path, module_id, graph)


def _visit_declarations(
    decls: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Process all top-level declarations."""
    signatures: dict[str, Node] = {}
    for child in decls.children:
        if child.type == "signature":
            name = _get_sig_name(child)
            if name:
                signatures[name] = child

    seen_funcs: dict[str, str] = {}
    for child in decls.children:
        _dispatch_declaration(
            child,
            file_path,
            module_id,
            signatures,
            seen_funcs,
            graph=graph,
        )


def _dispatch_declaration(
    child: Node,
    file_path: str,
    module_id: str,
    signatures: dict[str, Node],
    seen_funcs: dict[str, str],
    *,
    graph: CodeGraph,
) -> None:
    """Dispatch a single top-level declaration."""
    if child.type in ("function", "bind"):
        _extract_function(
            child,
            file_path,
            module_id,
            None,
            signatures,
            seen_funcs=seen_funcs,
            graph=graph,
        )
    elif child.type == "data_type":
        _extract_data_type(child, file_path, module_id, graph)
    elif child.type == "class":
        _extract_class(child, file_path, module_id, graph)
    elif child.type == "instance":
        _extract_instance(
            child,
            file_path,
            module_id,
            signatures,
            graph,
        )


def _extract_data_type(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a data or newtype declaration as a STRUCT node."""
    name_node = _find_child_by_type(node, "name")
    if name_node is None:
        return
    type_name = node_text(name_node)
    type_id = f"{module_id}:{type_name}"
    docstring = _extract_docstring(node)

    unit = CodeUnit(
        id=type_id,
        name=type_name,
        kind=NodeKind.STRUCT,
        location=make_location(node, file_path),
        docstring=docstring,
    )
    graph.nodes[type_id] = unit
    add_contains_edge(graph, module_id, type_id)


def _extract_class(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a type class as a TRAIT node."""
    name_node = _find_child_by_type(node, "name")
    if name_node is None:
        return
    class_name = node_text(name_node)
    class_id = f"{module_id}:{class_name}"
    docstring = _extract_docstring(node)

    unit = CodeUnit(
        id=class_id,
        name=class_name,
        kind=NodeKind.TRAIT,
        location=make_location(node, file_path),
        docstring=docstring,
    )
    graph.nodes[class_id] = unit
    add_contains_edge(graph, module_id, class_id)

    class_decls = _find_child_by_type(node, "class_declarations")
    if class_decls is None:
        return
    for child in class_decls.children:
        if child.type == "signature":
            _extract_class_method_sig(child, file_path, module_id, class_id, graph)


def _extract_class_method_sig(
    sig: Node,
    file_path: str,
    module_id: str,
    class_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a method signature from a type class body."""
    name = _get_sig_name(sig)
    if not name:
        return
    method_id = f"{class_id}.{name}"
    param_types, return_type = _extract_sig_types(sig)
    params = _build_params([], param_types)

    unit = CodeUnit(
        id=method_id,
        name=name,
        kind=NodeKind.METHOD,
        location=make_location(sig, file_path),
        parameters=tuple(params),
        return_type=return_type,
    )
    graph.nodes[method_id] = unit
    add_contains_edge(graph, class_id, method_id)


def _extract_instance(
    node: Node,
    file_path: str,
    module_id: str,
    signatures: dict[str, Node],
    graph: CodeGraph,
) -> None:
    """Extract an instance declaration: IMPLEMENTS edge + methods."""
    class_name, type_name = _get_instance_names(node)
    if not class_name or not type_name:
        return

    type_id = f"{module_id}:{type_name}"
    class_id = f"{module_id}:{class_name}"
    graph.edges.append(
        CodeEdge(
            source_id=type_id,
            target_id=class_id,
            kind=EdgeKind.IMPLEMENTS,
            confidence=EdgeConfidence.CERTAIN,
        )
    )

    inst_decls = _find_child_by_type(node, "instance_declarations")
    if inst_decls is None:
        return
    seen_funcs: dict[str, str] = {}
    for child in inst_decls.children:
        if child.type in ("function", "bind"):
            _extract_function(
                child,
                file_path,
                module_id,
                type_id,
                signatures,
                seen_funcs=seen_funcs,
                graph=graph,
            )


def _get_instance_names(node: Node) -> tuple[str, str]:
    """Extract the class name and type name from an instance node."""
    class_name = ""
    type_name = ""
    for child in node.children:
        if child.type == "name" and not class_name:
            class_name = node_text(child)
        elif child.type == "type_patterns":
            first_name = _find_child_by_type(child, "name")
            if first_name is not None:
                type_name = node_text(first_name)
    return class_name, type_name


def _extract_function(
    node: Node,
    file_path: str,
    module_id: str,
    container_id: str | None,
    signatures: dict[str, Node],
    *,
    seen_funcs: dict[str, str],
    graph: CodeGraph,
) -> None:
    """Extract a function equation, merging with previous equations."""
    name = _get_func_name(node)
    if not name:
        return

    if name in seen_funcs:
        _merge_function_equation(
            node,
            file_path,
            seen_funcs[name],
            module_id,
            container_id,
            graph=graph,
        )
        return

    _create_function_node(
        node,
        name,
        file_path,
        module_id,
        container_id,
        signatures=signatures,
        seen_funcs=seen_funcs,
        graph=graph,
    )


def _create_function_node(
    node: Node,
    name: str,
    file_path: str,
    module_id: str,
    container_id: str | None,
    *,
    signatures: dict[str, Node],
    seen_funcs: dict[str, str],
    graph: CodeGraph,
) -> None:
    """Create a new function node for the first equation."""
    if container_id is not None:
        func_id = f"{container_id}.{name}"
        owner_unit = graph.nodes.get(container_id)
        kind = (
            NodeKind.FUNCTION
            if owner_unit and owner_unit.kind in (NodeKind.FUNCTION, NodeKind.METHOD)
            else NodeKind.METHOD
        )
        owner = container_id
        if func_id in graph.nodes:
            func_id += f"@{node.start_point.row + 1}_{node.start_point.column}"
    else:
        func_id = f"{module_id}:{name}"
        kind = NodeKind.FUNCTION
        owner = module_id

    param_types, return_type = _extract_sig_types(signatures.get(name))
    param_names = _extract_pattern_names(node)
    params = _build_params(param_names, param_types)
    branches = _collect_branches(node, file_path)
    calls = _collect_calls(node)
    complexity = compute_complexity(branches)
    docstring = _extract_docstring(node)

    unit = CodeUnit(
        id=func_id,
        name=name,
        kind=kind,
        location=make_location(node, file_path),
        parameters=tuple(params),
        return_type=return_type,
        exception_types=(),
        cyclomatic_complexity=complexity,
        branches=tuple(branches),
        docstring=docstring,
    )
    graph.nodes[func_id] = unit
    add_contains_edge(graph, owner, func_id)
    seen_funcs[name] = func_id

    _extract_locals(node, func_id, module_id, file_path, graph)
    _add_call_edges(calls, func_id, module_id, container_id, file_path=file_path, graph=graph)


def _merge_function_equation(
    node: Node,
    file_path: str,
    func_id: str,
    module_id: str,
    container_id: str | None,
    *,
    graph: CodeGraph,
) -> None:
    """Merge an additional function equation into an existing node."""
    existing = graph.nodes[func_id]
    loc = existing.location
    new_loc = SourceLocation(
        file_path=loc.file_path,
        start_line=loc.start_line,
        end_line=max(loc.end_line, node.end_point.row + 1),
        start_col=loc.start_col,
        end_col=node.end_point.column,
    )

    new_branches = list(existing.branches)
    new_branches.extend(_collect_branches(node, file_path))
    calls = _collect_calls(node)

    graph.nodes[func_id] = CodeUnit(
        id=existing.id,
        name=existing.name,
        kind=existing.kind,
        location=new_loc,
        parameters=existing.parameters,
        return_type=existing.return_type,
        exception_types=existing.exception_types,
        cyclomatic_complexity=compute_complexity(new_branches),
        branches=tuple(new_branches),
        docstring=existing.docstring,
    )
    _extract_locals(node, func_id, module_id, file_path, graph)
    _add_call_edges(calls, func_id, module_id, container_id, file_path=file_path, graph=graph)


# ── Signature and parameter extraction ──────────────────────────────


def _get_sig_name(node: Node) -> str:
    """Get the function name from a type signature node."""
    name = node.child_by_field_name("name")
    if name is not None:
        return _callee_name(name)
    for child in node.named_children:
        if child.type in ("variable", "prefix_id"):
            return _callee_name(child)
        if child.type == "infix":
            operator = child.child_by_field_name("operator")
            return _callee_name(operator) if operator is not None else ""
    return ""


def _get_func_name(node: Node) -> str:
    """Get the function name from a function equation node."""
    name = node.child_by_field_name("name")
    if name is not None:
        return _callee_name(name)
    for child in node.named_children:
        if child.type in ("variable", "prefix_id"):
            return _callee_name(child)
        if child.type == "infix":
            operator = child.child_by_field_name("operator")
            return _callee_name(operator) if operator is not None else ""
    return ""


def _extract_sig_types(
    sig_node: Node | None,
) -> tuple[list[TypeRef], TypeRef | None]:
    """Extract parameter types and return type from a signature."""
    if sig_node is None:
        return [], None
    type_node = _find_sig_type_expr(sig_node)
    if type_node is None:
        return [], None
    parts = _flatten_function_type(type_node)
    if not parts:
        return [], None
    return_type = TypeRef(name=node_text(parts[-1]))
    param_types = [TypeRef(name=node_text(t)) for t in parts[:-1]]
    return param_types, return_type


def _find_sig_type_expr(sig: Node) -> Node | None:
    """Find the type expression in a signature (after ::)."""
    found_sep = False
    for child in sig.children:
        if child.type == "::":
            found_sep = True
        elif found_sep:
            return child
    return None


def _flatten_function_type(node: Node) -> list[Node]:
    """Flatten A -> B -> C into [A, B, C] type nodes."""
    if node.type != "function":
        return [node]
    parts: list[Node] = []
    for child in node.children:
        if child.type != "->":
            parts.extend(_flatten_function_type(child))
    return parts


def _extract_pattern_names(node: Node) -> list[str]:
    """Extract parameter names from function equation patterns."""
    patterns = _find_child_by_type(node, "patterns")
    if patterns is None:
        return []
    names: list[str] = []
    for pat in patterns.children:
        if pat.type == "variable":
            names.append(node_text(pat))
        else:
            names.append(f"_arg{len(names)}")
    return names


def _build_params(
    names: list[str],
    types: list[TypeRef],
) -> list[Parameter]:
    """Combine parameter names from patterns with types from signature."""
    count = max(len(names), len(types))
    params: list[Parameter] = []
    for i in range(count):
        name = names[i] if i < len(names) else f"_arg{i}"
        type_ref = types[i] if i < len(types) else None
        params.append(Parameter(name=name, type_ref=type_ref))
    return params


# ── Branch and call collection ──────────────────────────────────────


def _collect_branches(
    node: Node,
    file_path: str,
) -> list[BranchInfo]:
    """Collect all branch points from a function equation."""
    branches: list[BranchInfo] = []
    for match in _matches(node):
        _walk_branches(match, file_path, branches)
    return branches


def _walk_branches(
    node: Node,
    file_path: str,
    branches: list[BranchInfo],
) -> None:
    """Recursively find branch nodes (case alternatives, guards)."""
    if node.type in {"local_binds", "function", "patterns", "signature"}:
        return
    if node.type in _BRANCH_NODE_TYPES:
        branches.append(
            BranchInfo(
                location=make_location(node, file_path),
                condition=_branch_condition(node),
            )
        )
    for child in _expression_children(node):
        _walk_branches(child, file_path, branches)


def _branch_condition(node: Node) -> str:
    """Extract a readable condition from a branch node."""
    if node.type == "guards":
        return node_text(node)
    if node.type == "alternative":
        for child in node.children:
            if child.type in ("variable", "literal", "constructor"):
                return node_text(child)
    return node.type


def _collect_calls(node: Node) -> list[tuple[str, Node]]:
    """Collect all function application calls from a node."""
    calls: list[tuple[str, Node]] = []
    for match in _matches(node):
        _walk_calls(match, calls)
    return calls


def _walk_calls(
    node: Node,
    calls: list[tuple[str, Node]],
) -> None:
    """Recursively find apply nodes and extract call targets."""
    if node.type in {"local_binds", "function", "patterns", "signature"}:
        return
    if node.type == "infix":
        operator = node.child_by_field_name("operator")
        if operator is not None:
            name = _callee_name(operator)
            if name:
                calls.append((name, node))
        for child in _expression_children(node):
            if child != operator:
                _walk_calls(child, calls)
        return
    if node.type == "apply":
        name = _apply_callee(node)
        if name:
            calls.append((name, node))
        _walk_apply_args(node, calls)
        return
    for child in _expression_children(node):
        _walk_calls(child, calls)


def _walk_apply_args(
    node: Node,
    calls: list[tuple[str, Node]],
) -> None:
    """Recurse into argument positions of a curried application."""
    if node.type != "apply" or node.child_count < 2:
        return
    left = node.children[0]
    if left.type == "apply":
        _walk_apply_args(left, calls)
    for arg in node.children[1:]:
        _walk_calls(arg, calls)


def _apply_callee(node: Node) -> str:
    """Extract the function name from a left-nested apply chain."""
    current = node
    while current.type == "apply" and current.child_count > 0:
        current = current.children[0]
    return _callee_name(current)


def _matches(node: Node) -> list[Node]:
    return [child for child in node.named_children if child.type == "match"]


def _expression_children(node: Node) -> list[Node]:
    # Patterns use the same apply/infix grammar as expressions. They bind names;
    # traversing them as expressions would invent constructor calls.
    pattern = node.child_by_field_name("pattern")
    return [child for child in node.named_children if child != pattern]


def _callee_name(node: Node) -> str:
    if node.type in {"parens", "prefix_id", "infix_id"}:
        return _callee_name(node.named_children[0]) if node.named_children else ""
    if node.type in {"variable", "constructor", "operator", "qualified"}:
        return node_text(node)
    return ""


def _pattern_variables(node: Node | None) -> set[str]:
    if node is None:
        return set()
    if node.type == "variable":
        return {node_text(node)}
    return {name for child in node.named_children for name in _pattern_variables(child)}


def _local_declarations(group: Node) -> list[Node]:
    return [child for child in group.named_children if child.type in {"function", "bind"}]


def _lexical_binding(site: Node, name: str) -> Node | None:
    if "." in name:
        return None
    current: Node | None = site
    while current is not None:
        if current.type in {"function", "lambda", "alternative"}:
            patterns = current.child_by_field_name("patterns")
            if patterns is None:
                patterns = _find_child_by_type(current, "patterns")
            if current.type == "alternative":
                patterns = current.child_by_field_name("pattern")
            if name in _pattern_variables(patterns):
                return patterns
            if current.type == "function":
                head = next((c for c in current.named_children if c.type == "infix"), None)
                if head is not None:
                    variables = set().union(
                        *(
                            _pattern_variables(c)
                            for c in head.named_children
                            if c != head.child_by_field_name("operator")
                        )
                    )
                    if name in variables:
                        return head
        group = current.child_by_field_name("binds")
        if group is not None and group.type == "local_binds":
            for declaration in _local_declarations(group):
                if _get_func_name(declaration) == name:
                    return declaration
        current = current.parent
    return None


def _extract_locals(
    root: Node,
    owner: str,
    module_id: str,
    file_path: str,
    graph: CodeGraph,
) -> None:
    pending = list(root.named_children)
    while pending:
        node = pending.pop()
        if node.type == "local_binds":
            signatures = {
                _get_sig_name(child): child
                for child in node.named_children
                if child.type == "signature"
            }
            seen: dict[str, str] = {}
            for declaration in _local_declarations(node):
                _extract_function(
                    declaration,
                    file_path,
                    module_id,
                    owner,
                    signatures,
                    seen_funcs=seen,
                    graph=graph,
                )
        elif node.type not in {"function", "bind", "signature", "patterns"}:
            pending.extend(node.named_children)


def _link_lexical_calls(graph: CodeGraph) -> None:
    declarations = {
        (
            unit.location.file_path,
            unit.location.start_line,
            unit.location.start_col,
            unit.name,
        ): unit.id
        for unit in graph.nodes.values()
        if unit.kind in {NodeKind.FUNCTION, NodeKind.METHOD}
    }
    for index, edge in enumerate(graph.edges):
        if edge.kind == EdgeKind.CALLS and edge.target_id not in graph.nodes:
            graph.edges[index] = replace(edge, confidence=EdgeConfidence.UNCERTAIN)
        attrs = dict(edge.attributes)
        if "haskell_declaration_line" not in attrs or edge.location is None:
            continue
        key = (
            edge.location.file_path,
            attrs["haskell_declaration_line"],
            attrs["haskell_declaration_col"],
            attrs["haskell_declaration_name"],
        )
        target = declarations.get(key)
        if target is not None:
            graph.edges[index] = replace(edge, target_id=target, confidence=EdgeConfidence.CERTAIN)


# ── Edge helpers ────────────────────────────────────────────────────


def _operator_name(name: str) -> bool:
    return all(not char.isalnum() and char not in "_'" for char in name)


def _add_call_edges(
    calls: list[tuple[str, Node]],
    source_id: str,
    module_id: str,
    container_id: str | None,
    *,
    file_path: str,
    graph: CodeGraph,
) -> None:
    """Add CALLS edges for all collected call expressions."""
    for call_name, call_node in calls:
        target_id = _resolve_call_target(call_name, module_id, container_id)
        operator = _operator_name(call_name)
        if operator:
            target_id = f"{module_id}:{call_name}"
        confidence = _call_confidence(call_name)
        binding = _lexical_binding(call_node, call_name)
        attributes: tuple[tuple[str, object], ...] = ()
        if binding is not None:
            target_id = f"{source_id}.$binding.{call_name}"
            confidence = EdgeConfidence.UNCERTAIN
            attributes = (("binding_blocked", True),)
            if binding.type in ("function", "bind"):
                attributes += (
                    ("haskell_declaration_line", binding.start_point.row + 1),
                    ("haskell_declaration_col", binding.start_point.column),
                    ("haskell_declaration_name", call_name),
                )
        elif "." in call_name and not operator:
            confidence = EdgeConfidence.UNCERTAIN
            attributes = (("binding_blocked", True),)
        graph.edges.append(
            CodeEdge(
                source_id=source_id,
                target_id=target_id,
                kind=EdgeKind.CALLS,
                confidence=confidence,
                location=make_location(call_node, file_path),
                attributes=attributes,
            )
        )


def _call_confidence(call_name: str) -> EdgeConfidence:
    """Determine confidence for a Haskell call expression."""
    if "." in call_name:
        return EdgeConfidence.INFERRED
    return EdgeConfidence.CERTAIN


def _resolve_call_target(
    call_name: str,
    module_id: str,
    container_id: str | None,
) -> str:
    """Resolve a call name to a target node ID."""
    if "." not in call_name:
        return f"{module_id}:{call_name}"
    return call_name


# ── Import extraction ───────────────────────────────────────────────


def _extract_imports(imports_node: Node, graph: CodeGraph) -> None:
    """Extract import declarations as dependency info."""
    for child in imports_node.children:
        if child.type == "import":
            _extract_single_import(child, graph)


def _extract_single_import(node: Node, graph: CodeGraph) -> None:
    """Extract a single import statement."""
    for child in node.children:
        if child.type == "module":
            mod_name = node_text(child)
            if mod_name and mod_name not in graph.dependencies:
                graph.dependencies.append(mod_name)
            return


# ── Docstring extraction ────────────────────────────────────────────


def _find_trailing_haddock(parent: Node) -> Node | None:
    """Find a haddock node preceding the parent's container.

    Handles two tree-sitter quirks: haddock at root level before
    declarations, or haddock as last child of the imports node.
    """
    prev_sib = parent.prev_named_sibling
    if prev_sib is not None:
        if prev_sib.type == "haddock":
            return prev_sib
        if prev_sib.type == "imports" and prev_sib.child_count > 0:
            last_child = prev_sib.children[-1]
            if last_child.type == "haddock":
                return last_child
    return None


def _extract_docstring(node: Node) -> str | None:
    """Extract Haddock comments (-- |) preceding a node.

    The first declaration's haddock may sit at the root level,
    or as the last child of the imports node (tree-sitter quirk).
    """
    prev = node.prev_named_sibling
    if prev is None and node.parent is not None:
        prev = _find_trailing_haddock(node.parent)
    lines: list[str] = []
    while prev is not None and prev.type == "haddock":
        text = node_text(prev)
        if text.startswith("-- |"):
            lines.append(text[4:].strip())
        elif text.startswith("--|"):
            lines.append(text[3:].strip())
        else:
            lines.append(text.strip())
        prev = prev.prev_named_sibling
    if not lines:
        return None
    lines.reverse()
    return "\n".join(lines)


# ── Generic helpers ─────────────────────────────────────────────────


def _find_child_by_type(node: Node, type_name: str) -> Node | None:
    """Find the first child with the given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None
