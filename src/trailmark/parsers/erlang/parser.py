"""Erlang language parser using tree-sitter."""

from __future__ import annotations

from pathlib import Path

from tree_sitter import Node
from tree_sitter_language_pack import get_parser

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

_BRANCH_NODE_TYPES = frozenset({"cr_clause", "if_clause", "catch_clause", "receive_after"})

_THROW_ATOMS = frozenset({"throw", "error", "exit"})

_EXTENSIONS = (".erl",)


class ErlangParser:
    """Parses Erlang source files into CodeGraph using tree-sitter."""

    @property
    def language(self) -> str:
        return "erlang"

    def __init__(self) -> None:
        self._parser = get_parser("erlang")

    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single Erlang file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="erlang", root_path=file_path)
        module_id = module_id_from_path(file_path)
        _visit_module(tree.root_node, file_path, module_id, graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        """Parse all .erl files under dir_path into a merged graph."""
        return parse_directory(
            self.parse_file,
            "erlang",
            dir_path,
            _EXTENSIONS,
        )


# ── Module-level walk ───────────────────────────────────────────────


def _visit_module(
    root: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Walk an Erlang source file, extracting nodes and edges."""
    # Check for -module attribute to override module_id.
    for child in root.children:
        if child.type == "module_attribute":
            declared = _get_attribute_name(child)
            if declared:
                module_id = declared
                break

    add_module_node(root, file_path, module_id, graph)

    # First pass: collect -spec nodes keyed by function name.
    specs: dict[str, Node] = {}
    for child in root.children:
        if child.type == "spec":
            name = _get_spec_name(child)
            if name:
                specs[name] = child

    # Second pass: dispatch each top-level form.
    seen_funcs: dict[str, str] = {}
    for child in root.children:
        _dispatch_form(child, file_path, module_id, specs, seen_funcs, graph)


def _dispatch_form(
    child: Node,
    file_path: str,
    module_id: str,
    specs: dict[str, Node],
    seen_funcs: dict[str, str],
    graph: CodeGraph,
) -> None:
    """Dispatch a single top-level form."""
    if child.type == "fun_decl":
        _extract_function(child, file_path, module_id, specs, seen_funcs, graph)
    elif child.type == "record_decl":
        _extract_record(child, file_path, module_id, graph)
    elif child.type == "type_alias":
        _extract_type_alias(child, file_path, module_id, graph)
    elif child.type == "behaviour_attribute":
        _extract_behaviour(child, graph)
    elif child.type == "import_attribute":
        _extract_import(child, graph)


# ── Function extraction ─────────────────────────────────────────────


def _extract_function(
    node: Node,
    file_path: str,
    module_id: str,
    specs: dict[str, Node],
    seen_funcs: dict[str, str],
    graph: CodeGraph,
) -> None:
    """Extract a function clause, merging with previous clauses."""
    clause = node.child_by_field_name("clause")
    if clause is None or clause.type != "function_clause":
        return
    name = _get_clause_name(clause)
    if not name:
        return

    if name in seen_funcs:
        _merge_function_clause(clause, file_path, seen_funcs[name], module_id, graph)
        return

    _create_function_node(clause, name, file_path, module_id, specs, seen_funcs, graph)


def _create_function_node(
    clause: Node,
    name: str,
    file_path: str,
    module_id: str,
    specs: dict[str, Node],
    seen_funcs: dict[str, str],
    graph: CodeGraph,
) -> None:
    """Create a new function node for the first clause."""
    func_id = f"{module_id}:{name}"

    param_names = _extract_param_names(clause)
    spec_param_types, return_type = _extract_spec_types(specs.get(name))
    params = _build_params(param_names, spec_param_types)
    branches = _collect_branches(clause, file_path)
    calls = _collect_calls(clause)
    exception_types = _collect_exceptions(calls)
    complexity = compute_complexity(branches)
    docstring = _extract_docstring(clause)

    unit = CodeUnit(
        id=func_id,
        name=name,
        kind=NodeKind.FUNCTION,
        location=make_location(clause, file_path),
        parameters=tuple(params),
        return_type=return_type,
        exception_types=tuple(exception_types),
        cyclomatic_complexity=complexity,
        branches=tuple(branches),
        docstring=docstring,
    )
    graph.nodes[func_id] = unit
    add_contains_edge(graph, module_id, func_id)
    seen_funcs[name] = func_id

    _add_call_edges(calls, func_id, module_id, file_path, graph)


def _merge_function_clause(
    clause: Node,
    file_path: str,
    func_id: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Merge an additional function clause into an existing node."""
    existing = graph.nodes[func_id]
    loc = existing.location
    new_loc = SourceLocation(
        file_path=loc.file_path,
        start_line=loc.start_line,
        end_line=max(loc.end_line, clause.end_point.row + 1),
        start_col=loc.start_col,
        end_col=clause.end_point.column,
    )

    new_branches = list(existing.branches)
    new_branches.extend(_collect_branches(clause, file_path))
    calls = _collect_calls(clause)
    new_exceptions = list(existing.exception_types)
    new_exceptions.extend(_collect_exceptions(calls))

    graph.nodes[func_id] = CodeUnit(
        id=existing.id,
        name=existing.name,
        kind=existing.kind,
        location=new_loc,
        parameters=existing.parameters,
        return_type=existing.return_type,
        exception_types=tuple(new_exceptions),
        cyclomatic_complexity=compute_complexity(new_branches),
        branches=tuple(new_branches),
        docstring=existing.docstring,
    )
    _add_call_edges(calls, func_id, module_id, file_path, graph)


# ── Spec / parameter extraction ─────────────────────────────────────


def _get_spec_name(node: Node) -> str:
    """Get the function name from a -spec node."""
    fun_node = node.child_by_field_name("fun")
    return node_text(fun_node) if fun_node is not None else ""


def _get_clause_name(clause: Node) -> str:
    """Get the function name from a function_clause node."""
    name_node = clause.child_by_field_name("name")
    return node_text(name_node) if name_node is not None else ""


def _get_attribute_name(node: Node) -> str:
    """Get the name from a module/behaviour/import attribute."""
    name_node = node.child_by_field_name("name")
    return node_text(name_node) if name_node is not None else ""


def _extract_param_names(clause: Node) -> list[str]:
    """Extract parameter names from a function clause's args."""
    args_node = clause.child_by_field_name("args")
    if args_node is None:
        return []
    names: list[str] = []
    for child in args_node.children:
        field = args_node.field_name_for_child(_child_index(args_node, child))
        if field != "args":
            continue
        if child.type == "var":
            names.append(node_text(child))
        else:
            names.append(f"_arg{len(names)}")
    return names


def _child_index(parent: Node, child: Node) -> int:
    """Find the index of a child within its parent."""
    for i, c in enumerate(parent.children):
        if c.id == child.id:
            return i
    return 0


def _extract_spec_types(
    spec_node: Node | None,
) -> tuple[list[TypeRef], TypeRef | None]:
    """Extract parameter types and return type from a -spec."""
    if spec_node is None:
        return [], None
    sigs = _children_by_field(spec_node, "sigs")
    if not sigs:
        return [], None
    sig = sigs[0]
    return _extract_type_sig(sig)


def _extract_type_sig(
    sig: Node,
) -> tuple[list[TypeRef], TypeRef | None]:
    """Extract types from a single type_sig node."""
    # Return type is the ty field.
    ty_node = sig.child_by_field_name("ty")
    return_type = TypeRef(name=_flatten_type_text(ty_node)) if ty_node is not None else None

    # Parameter types from args.
    args_node = sig.child_by_field_name("args")
    param_types: list[TypeRef] = []
    if args_node is not None:
        for child in args_node.children:
            field = args_node.field_name_for_child(_child_index(args_node, child))
            if field != "args":
                continue
            param_types.append(TypeRef(name=_flatten_type_text(child)))
    return param_types, return_type


def _flatten_type_text(node: Node) -> str:
    """Extract a readable type name from a type expression node."""
    if node.type == "ann_type":
        ty = node.child_by_field_name("ty")
        if ty is not None:
            return _flatten_type_text(ty)
    if node.type == "call":
        expr = node.child_by_field_name("expr")
        if expr is not None:
            return node_text(expr)
    return node_text(node)


def _build_params(
    names: list[str],
    types: list[TypeRef],
) -> list[Parameter]:
    """Combine parameter names from patterns with types from spec."""
    count = max(len(names), len(types))
    params: list[Parameter] = []
    for i in range(count):
        name = names[i] if i < len(names) else f"_arg{i}"
        type_ref = types[i] if i < len(types) else None
        params.append(Parameter(name=name, type_ref=type_ref))
    return params


# ── Branch collection ───────────────────────────────────────────────


def _collect_branches(
    node: Node,
    file_path: str,
) -> list[BranchInfo]:
    """Collect all branch points from a function clause."""
    branches: list[BranchInfo] = []
    # Count guard clauses as branches.
    guard = node.child_by_field_name("guard")
    if guard is not None:
        for child in guard.children:
            if child.type == "guard_clause":
                branches.append(
                    BranchInfo(
                        location=make_location(child, file_path),
                        condition=node_text(child),
                    )
                )
    # Walk the body for case/if/receive/try branches.
    body = node.child_by_field_name("body")
    if body is not None:
        _walk_branches(body, file_path, branches)
    return branches


def _walk_branches(
    node: Node,
    file_path: str,
    branches: list[BranchInfo],
) -> None:
    """Recursively find branch nodes."""
    if node.type in _BRANCH_NODE_TYPES:
        branches.append(
            BranchInfo(
                location=make_location(node, file_path),
                condition=_branch_condition(node),
            )
        )
    for child in node.children:
        _walk_branches(child, file_path, branches)


def _branch_condition(node: Node) -> str:
    """Extract a readable condition from a branch node."""
    if node.type == "cr_clause":
        pat = node.child_by_field_name("pat")
        if pat is not None:
            return node_text(pat)
    if node.type == "if_clause":
        guard = node.child_by_field_name("guard")
        if guard is not None:
            return node_text(guard)
    if node.type == "catch_clause":
        cls = node.child_by_field_name("class")
        if cls is not None:
            return node_text(cls)
    return node.type


# ── Call collection ─────────────────────────────────────────────────


def _collect_calls(node: Node) -> list[tuple[str, Node]]:
    """Collect all function calls from a node."""
    calls: list[tuple[str, Node]] = []
    _walk_calls(node, calls)
    return calls


def _walk_calls(
    node: Node,
    calls: list[tuple[str, Node]],
) -> None:
    """Recursively find call nodes and extract targets."""
    if node.type == "call":
        name = _call_target_name(node)
        if name:
            calls.append((name, node))
    for child in node.children:
        _walk_calls(child, calls)


def _call_target_name(node: Node) -> str:
    """Extract the function name from a call node."""
    expr = node.child_by_field_name("expr")
    if expr is None:
        return ""
    if expr.type == "remote":
        return _remote_call_name(expr)
    if expr.type == "atom":
        return node_text(expr)
    return ""


def _remote_call_name(remote: Node) -> str:
    """Extract module:function from a remote node."""
    mod_node = remote.child_by_field_name("module")
    fun_node = remote.child_by_field_name("fun")
    if mod_node is None or fun_node is None:
        return ""
    # remote_module has a module field containing the atom.
    mod_atom = mod_node.child_by_field_name("module")
    mod_name = node_text(mod_atom) if mod_atom is not None else ""
    fun_name = node_text(fun_node)
    if mod_name and fun_name:
        return f"{mod_name}:{fun_name}"
    return ""


def _collect_exceptions(
    calls: list[tuple[str, Node]],
) -> list[TypeRef]:
    """Extract exception types from throw/error/exit calls."""
    exceptions: list[TypeRef] = []
    for name, _ in calls:
        if name in _THROW_ATOMS:
            exceptions.append(TypeRef(name=name))
    return exceptions


# ── Edge helpers ────────────────────────────────────────────────────


def _add_call_edges(
    calls: list[tuple[str, Node]],
    source_id: str,
    module_id: str,
    file_path: str,
    graph: CodeGraph,
) -> None:
    """Add CALLS edges for all collected call expressions."""
    for call_name, call_node in calls:
        if call_name in _THROW_ATOMS:
            continue
        target_id = _resolve_call_target(call_name, module_id)
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
    """Determine confidence for an Erlang call expression."""
    if ":" in call_name:
        return EdgeConfidence.CERTAIN
    return EdgeConfidence.CERTAIN


def _resolve_call_target(
    call_name: str,
    module_id: str,
) -> str:
    """Resolve a call name to a target node ID."""
    if ":" in call_name:
        return call_name
    return f"{module_id}:{call_name}"


# ── Record extraction ───────────────────────────────────────────────


def _extract_record(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a record declaration as a STRUCT node."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    rec_name = node_text(name_node)
    rec_id = f"{module_id}:{rec_name}"

    unit = CodeUnit(
        id=rec_id,
        name=rec_name,
        kind=NodeKind.STRUCT,
        location=make_location(node, file_path),
    )
    graph.nodes[rec_id] = unit
    add_contains_edge(graph, module_id, rec_id)


# ── Type alias extraction ──────────────────────────────────────────


def _extract_type_alias(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a -type declaration as a STRUCT node."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    # type_name node has a nested name field.
    inner = name_node.child_by_field_name("name")
    type_name = node_text(inner) if inner is not None else node_text(name_node)
    type_id = f"{module_id}:{type_name}"

    unit = CodeUnit(
        id=type_id,
        name=type_name,
        kind=NodeKind.STRUCT,
        location=make_location(node, file_path),
    )
    graph.nodes[type_id] = unit
    add_contains_edge(graph, module_id, type_id)


# ── Dependency extraction ───────────────────────────────────────────


def _extract_behaviour(node: Node, graph: CodeGraph) -> None:
    """Extract a -behaviour declaration as a dependency."""
    name = _get_attribute_name(node)
    if name and name not in graph.dependencies:
        graph.dependencies.append(name)


def _extract_import(node: Node, graph: CodeGraph) -> None:
    """Extract a -import declaration as a dependency."""
    mod_node = node.child_by_field_name("module")
    if mod_node is None:
        return
    mod_name = node_text(mod_node)
    if mod_name and mod_name not in graph.dependencies:
        graph.dependencies.append(mod_name)


# ── Docstring extraction ────────────────────────────────────────────


def _extract_docstring(node: Node) -> str | None:
    """Extract EDoc comments preceding a function clause.

    Looks for consecutive comment siblings before the fun_decl
    parent that start with %% @doc or %%%.
    """
    fun_decl = node.parent
    if fun_decl is None:
        return None
    prev = fun_decl.prev_named_sibling
    lines: list[str] = []
    while prev is not None and prev.type == "comment":
        text = node_text(prev)
        lines.append(_strip_comment_prefix(text))
        prev = prev.prev_named_sibling
    if not lines:
        return None
    lines.reverse()
    return "\n".join(lines)


def _strip_comment_prefix(text: str) -> str:
    """Strip Erlang comment prefixes (%%, %%%, %% @doc)."""
    stripped = text.lstrip("%").lstrip()
    if stripped.startswith("@doc"):
        stripped = stripped[4:].lstrip()
    return stripped


# ── Generic helpers ─────────────────────────────────────────────────


def _children_by_field(
    node: Node,
    field_name: str,
) -> list[Node]:
    """Get all children with a specific field name."""
    result: list[Node] = []
    for i, child in enumerate(node.children):
        if node.field_name_for_child(i) == field_name:
            result.append(child)
    return result
