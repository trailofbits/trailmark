"""Miden assembly language parser using a vendored tree-sitter grammar."""

from __future__ import annotations

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

_BRANCH_NODE_TYPES = frozenset(
    {
        "if",
        "while",
        "repeat",
    }
)

_EXTENSIONS = (".masm",)


class MasmParser:
    """Parses Miden assembly source files into CodeGraph."""

    @property
    def language(self) -> str:
        return "masm"

    def __init__(self) -> None:
        from trailmark.tree_sitter_custom.masm import (
            language as masm_language,
        )

        lang = Language(masm_language())
        self._parser = Parser(lang)

    def parse_file(self, file_path: str) -> CodeGraph:
        """Parse a single Miden assembly file into a CodeGraph."""
        source = Path(file_path).read_bytes()
        tree = self._parser.parse(source)
        graph = CodeGraph(language="masm", root_path=file_path)
        module_id = module_id_from_path(file_path)
        _visit_module(tree.root_node, file_path, module_id, graph)
        return graph

    def parse_directory(self, dir_path: str) -> CodeGraph:
        """Parse all .masm files under dir_path."""
        return parse_directory(
            self.parse_file,
            "masm",
            dir_path,
            _EXTENSIONS,
        )


def _visit_module(
    root: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Walk the top-level of a Miden assembly file."""
    add_module_node(root, file_path, module_id, graph)
    for child in root.children:
        _visit_top_level(child, file_path, module_id, graph)


def _visit_top_level(
    child: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Dispatch a single top-level node."""
    if child.type == "procedure":
        _extract_procedure(child, file_path, module_id, graph)
    elif child.type == "entrypoint":
        _extract_entrypoint(child, file_path, module_id, graph)
    elif child.type in ("import", "reexport"):
        _extract_import(child, graph)
    elif child.type == "constant":
        _extract_constant(child, file_path, module_id, graph)


def _extract_procedure(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a procedure definition."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    proc_name = _ident_text(name_node)
    proc_id = f"{module_id}:{proc_name}"

    visibility = _extract_visibility(node)
    body = node.child_by_field_name("body")
    branches, calls = _collect_body(body, file_path)
    complexity = compute_complexity(branches)
    docstring = _extract_docstring(node)

    # Extract num_locals as a synthetic parameter for visibility.
    params = _extract_num_locals(node)

    unit = CodeUnit(
        id=proc_id,
        name=proc_name,
        kind=NodeKind.FUNCTION,
        location=make_location(node, file_path),
        parameters=tuple(params),
        cyclomatic_complexity=complexity,
        branches=tuple(branches),
        docstring=_prepend_visibility(visibility, docstring),
    )
    graph.nodes[proc_id] = unit
    add_contains_edge(graph, module_id, proc_id)
    _add_call_edges(calls, proc_id, module_id, file_path, graph)


def _extract_entrypoint(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract the begin...end entrypoint as a synthetic function."""
    entry_id = f"{module_id}:begin"

    body = node.child_by_field_name("body")
    branches, calls = _collect_body(body, file_path)
    complexity = compute_complexity(branches)
    docstring = _extract_docstring(node)

    unit = CodeUnit(
        id=entry_id,
        name="begin",
        kind=NodeKind.FUNCTION,
        location=make_location(node, file_path),
        cyclomatic_complexity=complexity,
        branches=tuple(branches),
        docstring=docstring,
        origin=NodeOrigin.SYNTHETIC,
    )
    graph.nodes[entry_id] = unit
    add_contains_edge(graph, module_id, entry_id)
    _add_call_edges(calls, entry_id, module_id, file_path, graph)


def _extract_constant(
    node: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
) -> None:
    """Extract a constant definition as a FUNCTION node.

    Constants in Miden assembly are compile-time values (const.NAME=expr).
    We represent them as nodes so they appear in the code graph.
    """
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    const_name = node_text(name_node).strip()
    const_id = f"{module_id}:{const_name}"
    docstring = _extract_docstring(node)

    unit = CodeUnit(
        id=const_id,
        name=const_name,
        kind=NodeKind.FUNCTION,
        location=make_location(node, file_path),
        cyclomatic_complexity=1,
        docstring=docstring,
    )
    graph.nodes[const_id] = unit
    add_contains_edge(graph, module_id, const_id)


def _extract_import(node: Node, graph: CodeGraph) -> None:
    """Extract use statements as dependency info."""
    path_node = node.child_by_field_name("path")
    if path_node is None:
        return
    raw = node_text(path_node).strip()
    # Path is like "std::math::u64" or "::foo::bar"; take the root segment.
    segments = [s for s in raw.split("::") if s]
    if segments:
        dep = segments[0]
        if dep not in graph.dependencies:
            graph.dependencies.append(dep)


def _extract_visibility(node: Node) -> str:
    """Extract the visibility keyword (export or proc) from a procedure."""
    vis_node = node.child_by_field_name("visibility")
    if vis_node is None:
        return "proc"
    return node_text(vis_node).strip()


def _extract_num_locals(node: Node) -> list[Parameter]:
    """Extract @locals(N) annotation as a synthetic num_locals parameter."""
    for i, child in enumerate(node.children):
        if node.field_name_for_child(i) == "annotations" and child.type == "annotation":
            name_child = child.child_by_field_name("name")
            if name_child is not None and node_text(name_child).strip() == "locals":
                value_child = child.child_by_field_name("value")
                if value_child is not None:
                    # annotation_args contains the decimal value
                    for vc in value_child.children:
                        if vc.type == "decimal":
                            text = node_text(vc).strip()
                            if text:
                                return [Parameter(name="num_locals", default=text)]
    return []


def _extract_docstring(node: Node) -> str | None:
    """Extract #! doc comments attached to a procedure or entrypoint."""
    docs_node = node.child_by_field_name("docs")
    if docs_node is None:
        return None
    text = node_text(docs_node).strip()
    if not text:
        return None
    # Clean up doc comment lines: remove #! prefix from each line.
    lines = []
    for line in text.splitlines():
        cleaned = line.strip()
        if cleaned.startswith("#!"):
            cleaned = cleaned[2:].strip()
        lines.append(cleaned)
    result = "\n".join(lines).strip()
    return result if result else None


def _prepend_visibility(visibility: str, docstring: str | None) -> str | None:
    """Prepend visibility info to docstring if exported."""
    if visibility == "pub":
        prefix = "[export]"
        if docstring:
            return f"{prefix} {docstring}"
        return prefix
    return docstring


def _collect_body(
    body: Node | None,
    file_path: str,
) -> tuple[list[BranchInfo], list[tuple[str, Node]]]:
    """Collect branches and invoke calls from a procedure/entrypoint body."""
    branches: list[BranchInfo] = []
    calls: list[tuple[str, Node]] = []
    if body is not None:
        _walk_body(body, file_path, branches, calls)
    return branches, calls


def _walk_body(
    node: Node,
    file_path: str,
    branches: list[BranchInfo],
    calls: list[tuple[str, Node]],
) -> None:
    """Walk the AST collecting branches and invoke expressions."""
    stack: list[Node] = list(reversed(node.children))
    while stack:
        child = stack.pop()
        if child.type in _BRANCH_NODE_TYPES and child.child_count > 0:
            condition = _branch_condition(child)
            branches.append(
                BranchInfo(
                    location=make_location(child, file_path),
                    condition=condition,
                )
            )
        if child.type == "invoke":
            name = _invoke_target(child)
            if name:
                calls.append((name, child))
        stack.extend(reversed(child.children))


def _branch_condition(node: Node) -> str:
    """Describe the branch condition for a control flow node."""
    if node.type == "if":
        return "if.true"
    if node.type == "while":
        return "while.true"
    if node.type == "repeat":
        count = node.child_by_field_name("count")
        if count is not None:
            return f"repeat.{node_text(count).strip()}"
        return "repeat"
    return node.type


def _invoke_target(node: Node) -> str:
    """Extract the invocation target path from an invoke node.

    Invoke nodes have the form: exec.path, call.path, syscall.path, procref.path.
    """
    path_node = node.child_by_field_name("path")
    if path_node is None:
        return ""
    return node_text(path_node).strip()


def _ident_text(node: Node) -> str:
    """Extract identifier text, handling quoted identifiers."""
    text = node_text(node).strip()
    # Quoted identifiers are wrapped in double quotes.
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    return text


def _add_call_edges(
    calls: list[tuple[str, Node]],
    source_id: str,
    module_id: str,
    file_path: str,
    graph: CodeGraph,
) -> None:
    """Add CALLS edges for collected invoke expressions."""
    for call_name, call_node in calls:
        # call_name is a path like "foo::bar" or just "my_proc".
        # Use the last segment as the target name within the module.
        segments = [s for s in call_name.split("::") if s]
        if not segments:
            continue
        target_name = segments[-1]
        target_id = f"{module_id}:{target_name}"
        graph.edges.append(
            CodeEdge(
                source_id=source_id,
                target_id=target_id,
                kind=EdgeKind.CALLS,
                confidence=EdgeConfidence.CERTAIN
                if len(segments) == 1
                else EdgeConfidence.INFERRED,
                location=make_location(call_node, file_path),
            )
        )
