"""Shared utilities for tree-sitter language parsers."""

from __future__ import annotations

import dataclasses
import os
import re
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING

from trailmark.analysis.proxies import ensure_proxy_nodes
from trailmark.models.diagnostics import ParseDiagnostic
from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import (
    BranchInfo,
    CodeUnit,
    NodeKind,
    NodeOrigin,
    SourceLocation,
    TypeParameter,
    TypeRef,
)
from trailmark.parsers._binding import resolve_inherited_method_calls
from trailmark.parsers._binding_index import initialize_bindings, record_unit
from trailmark.parsers._php_linking import link_php_functions

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tree_sitter import Node


def node_text(node: Node) -> str:
    """Get the UTF-8 text content of a tree-sitter node."""
    return node.text.decode("utf-8") if node.text else ""


def make_location(node: Node, file_path: str) -> SourceLocation:
    """Create a SourceLocation from a tree-sitter node."""
    return SourceLocation(
        file_path=file_path,
        start_line=node.start_point.row + 1,
        end_line=node.end_point.row + 1,
        start_col=node.start_point.column,
        end_col=node.end_point.column,
    )


@dataclasses.dataclass(frozen=True)
class _DirectorySources:
    root: Path
    owners: dict[Path, str]
    module_ids: dict[Path, str]
    defer_proxies: bool


_DIRECTORY_SOURCES: ContextVar[_DirectorySources | None] = ContextVar(
    "_DIRECTORY_SOURCES", default=None
)


def _relative_module_id(path: Path, root: Path) -> str:
    relative = path.relative_to(root)
    if relative.stem == "__init__":
        module_path = relative.parent
        if module_path == Path("."):
            return _escape_module_path_part(root.name)
    else:
        module_path = relative.with_suffix("")
    return ".".join(_escape_module_path_part(part) for part in module_path.parts)


@contextmanager
def directory_sources(
    dir_path: str, owners: dict[str, str], *, defer_proxies: bool = False
) -> Generator[None]:
    """Plan source ownership and module identities before any graph is merged.

    Unique module IDs retain their historical form. Colliding stems use the
    full root-relative filename, including its escaped extension. Planning all
    selected languages together makes identities independent of parser order.
    """
    root = _absolute_lexical_path(Path(dir_path))
    absolute_owners = {_absolute_lexical_path(Path(path)): lang for path, lang in owners.items()}
    module_ids = {path: _relative_module_id(path, root) for path in absolute_owners}
    # An expanded name can itself meet an unexpanded stem (a.py vs a.py.py).
    # Expand the whole collision group until all relative filenames are unique.
    # Each collision pass expands at least one previously unexpanded path.
    # All full relative filenames are unique, so at most N passes are needed.
    for _ in range(len(module_ids) + 1):
        groups: dict[str, list[Path]] = {}
        for path, module_id in module_ids.items():
            groups.setdefault(module_id, []).append(path)
        collisions = [paths for paths in groups.values() if len(paths) > 1]
        if not collisions:
            break
        for paths in collisions:
            for path in paths:
                module_ids[path] = ".".join(
                    _escape_module_path_part(part) for part in path.relative_to(root).parts
                )
    else:
        raise ValueError("Unable to assign distinct source module IDs")
    token = _DIRECTORY_SOURCES.set(
        _DirectorySources(root, absolute_owners, module_ids, defer_proxies)
    )
    try:
        yield
    finally:
        _DIRECTORY_SOURCES.reset(token)


def module_id_from_path(file_path: str) -> str:
    """Return a planned directory identity or a single file's historical stem."""
    path = Path(file_path)
    sources = _DIRECTORY_SOURCES.get()
    if sources is not None:
        module_id = sources.module_ids.get(_absolute_lexical_path(path))
        if module_id is not None:
            return module_id
    return path.stem if path.stem != "__init__" else path.parent.name


def _absolute_lexical_path(path: Path) -> Path:
    """Return an absolute path without resolving symlink targets."""
    return Path(os.path.abspath(path))


def _escape_module_path_part(part: str) -> str:
    """Escape separators used in dotted module IDs."""
    return part.replace("\\", "\\\\").replace(".", "\\.")


_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "build",
        "dist",
        ".eggs",
        "vendor",
    }
)


def should_skip_dir(dirname: str) -> bool:
    """Return whether a directory should be skipped during source walks."""
    return dirname in _EXCLUDED_DIRS or dirname.startswith(".")


def walk_source_files(
    dir_path: str,
    extensions: tuple[str, ...],
) -> Iterator[str]:
    """Yield source paths with case-insensitive extensions under dir_path.

    Skips common non-source directories (VCS, caches, vendored deps)
    and does not traverse directory symlinks.
    """
    for root, dirs, files in os.walk(dir_path, followlinks=False):
        dirs[:] = [d for d in dirs if not should_skip_dir(d)]
        dirs.sort()
        for fname in sorted(files):
            if fname.lower().endswith(extensions):
                yield os.path.join(root, fname)


def parse_directory(
    parse_file_fn: Callable[[str], CodeGraph],
    language: str,
    dir_path: str,
    extensions: tuple[str, ...],
    *,
    link_calls: Callable[[CodeGraph], None] | None = None,
) -> CodeGraph:
    """Parse all matching files under dir_path into a merged graph.

    Args:
        parse_file_fn: A callable(file_path: str) -> CodeGraph.
        language: Language name for the merged graph.
        dir_path: Directory to walk.
        extensions: File extensions to include.
    """
    sources = _DIRECTORY_SOURCES.get()
    if sources is None or sources.root != _absolute_lexical_path(Path(dir_path)):
        owners = dict.fromkeys(walk_source_files(dir_path, extensions), language)
        with directory_sources(dir_path, owners):
            return parse_directory(
                parse_file_fn, language, dir_path, extensions, link_calls=link_calls
            )

    merged = CodeGraph(language=language, root_path=dir_path)
    inspected = 0
    for fpath in walk_source_files(dir_path, extensions):
        if sources.owners.get(_absolute_lexical_path(Path(fpath))) != language:
            continue
        file_graph = _parse_with_diagnostics(parse_file_fn, fpath, language)
        merged.merge(file_graph)
        inspected += 1
    coverage_language = "c_sharp" if language == "csharp" else language
    merged.source_file_counts = {coverage_language: inspected}
    link_type_relationships(merged)
    resolve_inherited_method_calls(merged)
    (link_calls or link_cross_file_calls)(merged)
    return merged if sources.defer_proxies else ensure_proxy_nodes(merged)


def _parse_with_diagnostics(
    parse_file_fn: Callable[[str], CodeGraph],
    path: str,
    language: str,
) -> CodeGraph:
    try:
        return parse_file_fn(path)
    except RecursionError:
        graph = CodeGraph(language=language, root_path=path)
        ident = module_id_from_path(path)
        location = SourceLocation(path, 1, 1)
        graph.nodes[ident] = CodeUnit(ident, ident, NodeKind.MODULE, location)
        graph.parse_diagnostics.append(
            ParseDiagnostic(
                language,
                language,
                location,
                "unsupported_syntax",
                "File exceeds parser recursion limit; remaining files are still analyzed",
            )
        )
        return graph


def collect_body_info(
    body: Node,
    file_path: str,
    branch_types: frozenset[str],
    call_type: str,
    throw_types: frozenset[str],
    branches: list[BranchInfo],
    exception_types: list[TypeRef],
    calls: list[tuple[str, Node]],
    *,
    skip_types: frozenset[str] = frozenset(),
) -> None:
    """Collect branches, exceptions, and calls from a function body.

    Uses an explicit stack instead of recursion to avoid stack overflow
    on deeply nested ASTs from untrusted source files.
    """
    stack: list[Node] = [body]
    while stack:
        node = stack.pop()
        if node.type in skip_types:
            continue
        if node.type in branch_types:
            condition = _extract_condition_text(node)
            branches.append(
                BranchInfo(
                    location=make_location(node, file_path),
                    condition=condition,
                )
            )
        if node.type in throw_types:
            _collect_throw_type(node, exception_types)
        if node.type == call_type:
            call_name = extract_call_name(node)
            if call_name:
                calls.append((call_name, node))
        stack.extend(reversed(node.children))


def _extract_condition_text(node: Node) -> str:
    """Extract the condition expression from a branch node."""
    if node.type == "boolean_operator":
        return "boolean_operator"
    condition = node.child_by_field_name("condition")
    if condition is not None:
        return node_text(condition)
    return node.type


def _collect_throw_type(
    node: Node,
    exception_types: list[TypeRef],
) -> None:
    """Extract the exception type from a raise/throw statement."""
    for child in node.children:
        if child.type in ("call", "call_expression"):
            func = child.child_by_field_name("function")
            if func is not None:
                name = node_text(func)
                exception_types.append(TypeRef(name=name))
            return
        if child.type == "new_expression":
            ctor = child.child_by_field_name("constructor")
            if ctor is not None:
                exception_types.append(TypeRef(name=node_text(ctor)))
            return
        if child.type == "identifier":
            exception_types.append(TypeRef(name=node_text(child)))
            return


_CALL_NAME_TYPES = frozenset(
    {
        "identifier",
        "attribute",
        "field_expression",
        "member_expression",
        "scoped_identifier",
        "qualified_identifier",  # C++ namespace-qualified calls
        "selector_expression",
        "simple_identifier",  # Swift
        "navigation_expression",  # Swift dot access (e.g. self.method)
    }
)


def extract_call_name(node: Node) -> str:
    """Extract the function/method name from a call node."""
    func = node.child_by_field_name("function")
    if func is None:
        # Fallback for grammars that don't label the callable (e.g. Swift):
        # the callable is conventionally the first named child.
        for child in node.children:
            if child.type in _CALL_NAME_TYPES:
                return node_text(child)
        return ""
    if func.type in _CALL_NAME_TYPES:
        return node_text(func)
    # Unwrap wrapper nodes (e.g., Solidity wraps in "expression").
    for child in func.children:
        if child.type in _CALL_NAME_TYPES:
            return node_text(child)
    return ""


def compute_complexity(branches: list[BranchInfo]) -> int:
    """Compute cyclomatic complexity from collected branches."""
    return 1 + len(branches)


def add_module_node(
    root: Node,
    file_path: str,
    module_id: str,
    graph: CodeGraph,
    *,
    grammar: str | None = None,
) -> None:
    """Create a MODULE node and add it to the graph."""
    initialize_bindings(root)
    module_loc = make_location(root, file_path)
    module_unit = CodeUnit(
        id=module_id,
        name=module_id,
        kind=NodeKind.MODULE,
        location=module_loc,
    )
    graph.nodes[module_id] = module_unit
    if root.has_error and not _comments_only(root):
        seen = set(graph.parse_diagnostics)
        graph.parse_diagnostics.extend(
            _syntax_diagnostics(root, file_path, graph.language, grammar, seen)
        )


def _comments_only(root: Node) -> bool:
    """Recognize file-level comment trivia without walking declaration bodies."""
    source = root.text or b""
    cursor = 0
    for node in root.named_children:
        if "comment" not in node.type:
            continue
        start, end = node.start_byte - root.start_byte, node.end_byte - root.start_byte
        if source[cursor:start].strip():
            return False
        cursor = end
    return not source[cursor:].strip()


def _syntax_diagnostics(
    root: Node, path: str, language: str, grammar: str | None, seen: set[ParseDiagnostic]
) -> Iterator[ParseDiagnostic]:
    pending = [root]
    while pending:
        node = pending.pop()
        pending.extend(
            reversed(
                [
                    child
                    for child in node.children
                    if child.has_error or child.is_missing or child.type == "ERROR"
                ]
            )
        )
        if node.type != "ERROR" and not node.is_missing:
            continue
        diagnostic = ParseDiagnostic(
            language="c_sharp" if language == "csharp" else language,
            grammar=grammar or language,
            location=make_location(node, path),
            kind="missing_syntax" if node.is_missing else "syntax_error",
            message=f"Missing {node.type}"
            if node.is_missing
            else "Grammar could not parse this source range",
        )
        if diagnostic not in seen:
            seen.add(diagnostic)
            yield diagnostic


def add_contains_edge(
    graph: CodeGraph,
    container_id: str,
    child_id: str,
) -> None:
    """Add a CONTAINS edge from container to child."""
    unit = graph.nodes.get(child_id)
    if unit is not None:
        record_unit(unit)
    graph.edges.append(
        CodeEdge(
            source_id=container_id,
            target_id=child_id,
            kind=EdgeKind.CONTAINS,
        )
    )


def extract_type_parameters(
    node: Node, *, field_name: str | None = None
) -> tuple[TypeParameter, ...]:
    """Best-effort extraction of generic parameter declarations."""
    if field_name is None:
        raw = _generic_parameter_block(node_text(node))
    else:
        parameters = node.child_by_field_name(field_name)
        raw = node_text(parameters)[1:-1] if parameters is not None else None
    if raw is None:
        return ()
    params = [_parse_type_parameter(part) for part in _split_top_level(raw)]
    return tuple(param for param in params if param is not None)


def _generic_parameter_block(text: str) -> str | None:
    """Return the first likely generic parameter block from a declaration."""
    template = re.search(r"\btemplate\s*<(?P<params>[^<>]*)>", text)
    if template is not None:
        return template.group("params")

    header = text.split("{", 1)[0].split("=>", 1)[0]
    leading = re.search(r"^\s*(?:[\w@[\]().,\"']+\s+)*<", header)
    if leading is not None:
        result = _delimited_block_at(header, leading.end() - 1, "<", ">")
        if result is not None:
            return result

    declaration = re.search(
        r"\b(?:class|interface|struct|enum|trait|protocol|extension|type)"
        r"\s+[A-Za-z_$][\w$]*\s*(?P<open><|\[)",
        header,
    )
    if declaration is not None:
        return _block_from_match(header, declaration)

    function = re.search(
        r"\b(?:def|fn|func|function|fun)\s+[A-Za-z_$][\w$]*\s*(?P<open><|\[)",
        header,
    )
    if function is not None:
        return _block_from_match(header, function)

    method = re.search(r"\b[A-Za-z_$][\w$]*\s*(?P<open><|\[)\s*[^\]>\n]*[\]>]\s*\(", header)
    if method is not None:
        return _block_from_match(header, method)

    arrow = re.search(r"^\s*(?P<open><|\[)", header)
    if arrow is not None:
        result = _block_from_match(header, arrow)
        if result is not None:
            return result
    return None


def _block_from_match(text: str, match: re.Match[str]) -> str | None:
    opener = match.group("open")
    closer = ">" if opener == "<" else "]"
    return _delimited_block_at(text, match.end("open") - 1, opener, closer)


def _delimited_block_at(
    text: str,
    start: int,
    opener: str,
    closer: str,
) -> str | None:
    depth = 0
    for idx in range(start, len(text)):
        char = text[idx]
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start + 1 : idx]
    return None


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    pairs = {"<": ">", "[": "]", "(": ")"}
    closers = set(pairs.values())
    for idx, char in enumerate(text):
        if char in pairs:
            depth += 1
        elif char in closers and depth > 0:
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:idx].strip())
            start = idx + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_type_parameter(text: str) -> TypeParameter | None:
    item = text.strip()
    if not item:
        return None

    item = re.sub(r"^(typename|class|const|in|out)\s+", "", item)
    item = re.sub(r"^\?\s*(extends|super)\s+", "", item)

    default_ref: TypeRef | None = None
    if "=" in item:
        item, default = item.split("=", 1)
        default_ref = TypeRef(name=default.strip())

    constraints: list[TypeRef] = []
    for marker in (" extends ", " super ", " implements ", ":"):
        if marker in item:
            name, raw_constraints = item.split(marker, 1)
            constraints = [
                TypeRef(name=constraint.strip())
                for constraint in re.split(r"[&|+]", raw_constraints)
                if constraint.strip()
            ]
            item = name
            break

    tokens = item.strip().split()
    if not tokens:
        return None
    name = tokens[0]
    if len(tokens) > 1 and not constraints:
        constraints = [TypeRef(name=" ".join(tokens[1:]))]
    return TypeParameter(
        name=name,
        constraints=tuple(constraints),
        default=default_ref,
    )


def first_child_by_type(node: Node, type_name: str) -> Node | None:
    """Return the first direct child of ``node`` whose type matches.

    Shared helper used by parsers that hand-walk AST children by type.
    """
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def link_cross_file_calls(
    graph: CodeGraph,
    *,
    eligible_ids: set[str] | None = None,
    included_files: Callable[[str], set[str]] | None = None,
) -> None:
    """Infer unique free-function targets without guessing receiver semantics.

    Bare calls cannot safely be assigned to an unrelated class method. Existing
    exact targets are retained; overloaded and ambiguous names stay unresolved.
    A unique name across files is a heuristic, so its confidence is inferred.
    """
    if graph.language == "php":
        link_php_functions(graph)
        return
    name_to_ids = _free_functions(graph, eligible_ids)
    targets = _CrossFileTargets(graph, included_files)
    new_edges: list[CodeEdge] = []
    for edge in graph.edges:
        if not _needs_cross_file_target(edge, graph, eligible_ids):
            new_edges.append(edge)
            continue
        candidates = _cross_file_candidates(edge, targets, name_to_ids)
        if len(candidates) == 1:
            edge = dataclasses.replace(
                edge,
                target_id=candidates[0],
                confidence=EdgeConfidence.UNCERTAIN
                if edge.confidence == EdgeConfidence.UNCERTAIN
                else EdgeConfidence.INFERRED,
            )
        elif candidates:
            edge = dataclasses.replace(edge, confidence=EdgeConfidence.UNCERTAIN)
        new_edges.append(edge)
    graph.edges[:] = new_edges


def _free_functions(graph: CodeGraph, eligible_ids: set[str] | None) -> dict[str, list[str]]:
    owners = {e.target_id: e.source_id for e in graph.edges if e.kind == EdgeKind.CONTAINS}
    names: dict[str, list[str]] = {}
    for node_id, unit in graph.nodes.items():
        owner = graph.nodes.get(owners.get(node_id, ""))
        if (
            unit.kind == NodeKind.FUNCTION
            and (eligible_ids is None or node_id in eligible_ids)
            and (owner is None or owner.kind not in {NodeKind.FUNCTION, NodeKind.METHOD})
        ):
            names.setdefault(unit.name, []).append(node_id)
    return names


def _needs_cross_file_target(
    edge: CodeEdge, graph: CodeGraph, eligible_ids: set[str] | None
) -> bool:
    target = edge.target_id
    return (
        edge.kind == EdgeKind.CALLS
        and target not in graph.nodes
        and edge.source_id in graph.nodes
        and _allows_cross_file_lookup(edge, graph.language)
        and (eligible_ids is None or edge.source_id in eligible_ids)
        and _bare_cross_file_target(target)
    )


def _allows_cross_file_lookup(edge: CodeEdge, language: str) -> bool:
    attributes = dict(edge.attributes)
    return language != "erlang" and not any(
        attributes.get(key) for key in ("binding_blocked", "import_binding", "circom_dependency")
    )


class _CrossFileTargets:
    """Cache path and linkage metadata once for the current linking pass."""

    def __init__(self, graph: CodeGraph, included_files: Callable[[str], set[str]] | None) -> None:
        self.graph = graph
        self.included_files = included_files
        paths = {unit.location.file_path for unit in graph.nodes.values()}
        resolved = {path: str(Path(path).resolve()) for path in paths}
        self.files = {
            node_id: resolved[unit.location.file_path] for node_id, unit in graph.nodes.items()
        }
        self.parents = {node_id: path.rpartition(os.sep)[0] for node_id, path in self.files.items()}
        self.attributes = {node_id: dict(unit.attributes) for node_id, unit in graph.nodes.items()}

    def visible(self, source: str, candidate: str) -> bool:
        if not self.attributes[candidate].get("internal_linkage"):
            return True
        path = self.files[source]
        target = self.files[candidate]
        if ":$anonymous" in candidate or ".$anonymous" in candidate:
            return path == target
        if path == target or Path(target).suffix.lower() in {
            ".h",
            ".hh",
            ".hpp",
            ".hxx",
            ".inl",
            ".inc",
            ".ipp",
        }:
            return True
        if self.included_files is None:
            self.included_files = _include_resolver(self.graph.root_path)
        return target in self.included_files(path) or path in self.included_files(target)


def _cross_file_candidates(
    edge: CodeEdge, targets: _CrossFileTargets, names: dict[str, list[str]]
) -> list[str]:
    source = targets.attributes.get(edge.source_id, {})
    candidates = [
        candidate
        for candidate in names.get(edge.target_id.rsplit(":", 1)[-1], [])
        if targets.visible(edge.source_id, candidate)
    ]
    if "go_package" in source:
        candidates = [
            candidate
            for candidate in candidates
            if targets.parents[candidate] == targets.parents.get(edge.source_id)
            and targets.attributes[candidate].get("go_package") == source["go_package"]
        ]
    return candidates


def link_type_relationships(graph: CodeGraph) -> None:
    """Link unambiguous inheritance/implementation targets within one language.

    Exact local targets win. Qualified and bare type names are indexed only for
    type declarations; ambiguous imports are left unresolved for later analysis.
    """
    type_kinds = {
        NodeKind.CLASS,
        NodeKind.STRUCT,
        NodeKind.INTERFACE,
        NodeKind.TRAIT,
        NodeKind.ENUM,
        NodeKind.CONTRACT,
        NodeKind.LIBRARY,
    }
    names: dict[str, set[str]] = {}
    for unit in graph.nodes.values():
        if unit.kind not in type_kinds or unit.origin != NodeOrigin.SOURCE:
            continue
        for name in (unit.name, unit.id, unit.id.replace(":", ".", 1)):
            names.setdefault(name, set()).add(unit.id)
    edges: list[CodeEdge] = []
    for edge in graph.edges:
        if edge.kind in {EdgeKind.INHERITS, EdgeKind.IMPLEMENTS}:
            raw = edge.target_id.rsplit(":", 1)[-1]
            # Nested types can refer to siblings in an enclosing lexical type.
            # Walk outward before falling back to the language-wide name index.
            local_target = None
            module, _, qualified = edge.source_id.partition(":")
            enclosing = qualified.split(".")[:-1]
            if "." not in raw:
                for depth in range(len(enclosing), 0, -1):
                    candidate = f"{module}:{'.'.join(enclosing[:depth])}.{raw}"
                    unit = graph.nodes.get(candidate)
                    if unit is not None and unit.kind in type_kinds:
                        local_target = candidate
                        break
            candidates = {local_target} if local_target is not None else names.get(raw, set())
            if local_target is None and edge.target_id in graph.nodes:
                edges.append(edge)
                continue
            if len(candidates) == 1:
                edge = dataclasses.replace(
                    edge, target_id=next(iter(candidates)), confidence=EdgeConfidence.INFERRED
                )
        edges.append(edge)
    graph.edges[:] = edges


def _include_resolver(root_path: str) -> Callable[[str], set[str]]:
    """Share bounded include visibility with C++, Objective-C and polyglot runs."""
    from tree_sitter import Parser
    from tree_sitter_language_pack import get_language

    from trailmark.parsers.c._headers import HeaderSources

    root = Path(root_path)
    headers = HeaderSources(root.parent if root.is_file() else root, Parser(get_language("cpp")))
    return headers.included_files


def _bare_cross_file_target(target: str) -> bool:
    """Exclude qualified receivers from free-function name matching."""
    return "::" not in target and "->" not in target and "." not in target.rsplit(":", 1)[-1]
