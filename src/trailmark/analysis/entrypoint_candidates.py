"""Discover language-level exposure evidence without inventing execution edges."""

from __future__ import annotations

import re
from pathlib import Path
from typing import cast

from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.entrypoints import EntrypointEvidence
from trailmark.models.graph import CodeGraph, source_manifests
from trailmark.models.nodes import CodeUnit, NodeKind, NodeOrigin, SourceLocation

_DECLARATIVE_EXTENSIONS = {
    ".graphql",
    ".gql",
    ".graphqls",
    ".json",
    ".yaml",
    ".yml",
    ".xml",
    ".toml",
}
_QUALIFIED = re.compile(r"[\\]?[A-Za-z_$][\w$]*(?:(?:\.|::?|\\)[A-Za-z_$][\w$]*)+\Z")


def discover_entrypoint_candidates(graph: CodeGraph) -> None:
    """Collect references and file bodies; candidates do not become entrypoint tags."""
    from trailmark.analysis._callable_references import discover_callable_references
    from trailmark.analysis._reference_literals import scan_reference_literals

    for unit in list(graph.nodes.values()):
        if unit.name in {"<script>", "<template>"} and unit.kind == NodeKind.FUNCTION:
            graph.add_entrypoint_evidence(
                EntrypointEvidence(
                    unit.id,
                    "language",
                    "executable_file_body",
                    unit.location,
                    unresolved=("invocation_context",),
                )
            )
    discover_callable_references(graph)
    index = _ReferenceIndex(graph)
    edges = set(graph.edges)
    owners: dict[str, list[CodeUnit]] = {}
    for unit in graph.nodes.values():
        owners.setdefault(unit.location.file_path, []).append(unit)
    for path in _declarative_files(graph):
        scan = scan_reference_literals(path)
        if scan.issues:
            graph.add_entrypoint_evidence(
                EntrypointEvidence(
                    None,
                    "reference",
                    "declarative_scan",
                    SourceLocation(path, 1, 1),
                    binding="unresolved",
                    unresolved=scan.issues,
                )
            )
        for literal in scan.literals:
            match = index.resolve(literal.value, literal.location)
            if match is None:
                continue
            targets, exact = match
            uncertainty = ("consumer_semantics", "invocation_context")
            if not targets:
                graph.add_entrypoint_evidence(
                    EntrypointEvidence(
                        None,
                        "reference",
                        "declarative_reference",
                        literal.location,
                        symbol=literal.value,
                        binding="unresolved",
                        unresolved=("target_not_in_selected_sources", *uncertainty),
                    )
                )
                continue
            owner = _literal_owner(graph, literal.location, owners)
            for target in targets:
                ambiguous = len(targets) > 1 or not exact
                graph.add_entrypoint_evidence(
                    EntrypointEvidence(
                        target,
                        "reference",
                        "declarative_reference",
                        literal.location,
                        source_id=owner,
                        symbol=literal.value,
                        binding="ambiguous" if ambiguous else "resolved",
                        unresolved=(("target_selection",) if ambiguous else ()) + uncertainty,
                    )
                )
                edge = CodeEdge(
                    owner,
                    target,
                    EdgeKind.REFERENCES,
                    EdgeConfidence.UNCERTAIN if ambiguous else EdgeConfidence.INFERRED,
                    location=literal.location,
                    attributes=(
                        ("reference_context", "declarative_value"),
                        ("symbol", literal.value),
                    ),
                )
                if edge not in edges:
                    edges.add(edge)
                    graph.edges.append(edge)


def _declarative_files(graph: CodeGraph) -> list[str]:
    files = {
        node.location.file_path
        for node in graph.nodes.values()
        if Path(node.location.file_path).suffix.lower() in _DECLARATIVE_EXTENSIONS
    }
    for file in [
        file
        for manifest in source_manifests(graph)
        for file in cast(list[dict[str, str]], manifest.get("files", []))
    ]:
        if (
            file["status"] == "unparsed"
            and file["unit"] == "file"
            and Path(file["path"]).suffix.lower() in _DECLARATIVE_EXTENSIONS
        ):
            files.add(file["path"])
    return sorted(files)


def _normalized(symbol: str) -> str:
    return re.sub(r"::?|\\", ".", symbol).strip(".")


class _ReferenceIndex:
    """Exact qualified names and file suffixes; never join terminal names."""

    def __init__(self, graph: CodeGraph) -> None:
        from trailmark.parse import _LANGUAGE_EXTENSIONS

        self.extensions = {ext for exts in _LANGUAGE_EXTENSIONS.values() for ext in exts}
        self.symbols: dict[str, set[str]] = {}
        self.files: dict[str, dict[str, set[str]]] = {}
        self.canonical_files: dict[Path, set[str]] = {}
        roots = [
            root
            for manifest in source_manifests(graph)
            for root in cast(list[dict[str, object]], manifest.get("roots", []))
        ]
        prefixes = tuple(str(root["name"]) + "::" for root in roots)
        for unit in graph.nodes.values():
            if unit.kind == NodeKind.PROXY:
                continue
            if unit.kind == NodeKind.MODULE:
                path = Path(unit.location.file_path)
                self.files.setdefault(path.name, {}).setdefault(path.as_posix(), set()).add(unit.id)
                self.canonical_files.setdefault(path.resolve(), set()).add(unit.id)
                continue
            identifier = unit.id
            for prefix in prefixes:
                if identifier.startswith(prefix):
                    identifier = identifier[len(prefix) :]
                    break
            module, _, symbol = identifier.partition(":")
            for name in (module + "." + symbol, symbol):
                normalized = _normalized(name)
                if "." in normalized:
                    self.symbols.setdefault(normalized, set()).add(unit.id)

    def resolve(self, value: str, location: SourceLocation) -> tuple[list[str], bool] | None:
        """Return possible targets and whether their qualifier/base is exact."""
        normalized_path = value.replace("\\", "/").rsplit("::", 1)[-1]
        if Path(normalized_path).suffix.lower() in self.extensions:
            if "://" in value or any(char in value for char in "*?["):
                return None
            anchored = Path(normalized_path)
            if not anchored.is_absolute():
                anchored = Path(location.file_path).parent / anchored
            direct = self.canonical_files.get(anchored.resolve(), set())
            if "::" not in value and (direct or normalized_path.startswith(("/", "./", "../"))):
                return sorted(direct), True
            suffix = normalized_path.removeprefix("./").lstrip("/")
            return sorted(
                {
                    node
                    for path, nodes in self.files.get(Path(normalized_path).name, {}).items()
                    if path == normalized_path or path.endswith("/" + suffix)
                    for node in nodes
                }
            ), False
        if _QUALIFIED.fullmatch(value):
            return sorted(self.symbols.get(_normalized(value), set())), True
        return None


def _literal_owner(
    graph: CodeGraph, location: SourceLocation, owners: dict[str, list[CodeUnit]]
) -> str:
    candidates = [
        unit for unit in owners.get(location.file_path, []) if _encloses(unit.location, location)
    ]
    if candidates:
        return min(
            candidates,
            key=lambda unit: (
                unit.location.end_line - unit.location.start_line,
                (unit.location.end_col or 0) - (unit.location.start_col or 0),
                unit.id,
            ),
        ).id
    # Data-only files get an explicit provenance node, never an executable body.
    identifier = _data_source_id(graph, location.file_path)
    if identifier not in graph.nodes:
        try:
            end_line = max(
                location.end_line, len(Path(location.file_path).read_bytes().splitlines())
            )
        except OSError:
            end_line = location.end_line
        graph.nodes[identifier] = CodeUnit(
            identifier,
            Path(location.file_path).name,
            NodeKind.MODULE,
            SourceLocation(location.file_path, 1, end_line),
            origin=NodeOrigin.SYNTHETIC,
            attributes=(("analysis_role", "declarative_source"),),
        )
        owners.setdefault(location.file_path, []).append(graph.nodes[identifier])
    return identifier


def _data_source_id(graph: CodeGraph, path: str) -> str:
    file = Path(path)
    for manifest in source_manifests(graph):
        files = cast(list[dict[str, str]], manifest.get("files", []))
        selected = next((item["root"] for item in files if item["path"] == path), None)
        roots = cast(list[dict[str, object]], manifest.get("roots", []))
        for root in roots:
            if root["name"] == selected:
                prefix = f"{selected}::" if len(roots) > 1 else ""
                return prefix + "@source:" + file.relative_to(str(root["path"])).as_posix()
    root_path = Path(graph.root_path)
    root = root_path if root_path.is_dir() else root_path.parent
    relative = file.relative_to(root).as_posix() if file.is_relative_to(root) else file.name
    return "@source:" + relative


def _encloses(outer: SourceLocation, inner: SourceLocation) -> bool:
    return (outer.start_line, outer.start_col or 0) <= (
        inner.start_line,
        inner.start_col or 0,
    ) and (outer.end_line, outer.end_col if outer.end_col is not None else float("inf")) >= (
        inner.end_line,
        inner.end_col or 0,
    )
