"""Link only unique, visible source declarations; retain source evidence roles."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

from trailmark.models.diagnostics import ParseDiagnostic
from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind, NodeOrigin


class _Symbols:
    def __init__(self, graph: CodeGraph, qualified_prefixes: tuple[str, ...] = ()) -> None:
        self.graph = graph
        self.qualified_prefixes = qualified_prefixes
        self.files = {
            n.location.file_path: n.id
            for n in graph.nodes.values()
            if n.kind == NodeKind.MODULE and n.origin == NodeOrigin.SOURCE
        }
        self.paths = {os.path.abspath(path): module for path, module in self.files.items()}
        self.modules: dict[str, set[str]] = {}
        self.names: dict[str, list[CodeUnit]] = {}
        self.exports: dict[str, set[str]] = {}
        self.open_exports: dict[str, set[str]] = {}
        self.ordered = {
            unit.location.file_path
            for unit in graph.nodes.values()
            if dict(unit.attributes).get("ordered_declarations")
        }
        for unit in graph.nodes.values():
            attrs = dict(unit.attributes)
            for name in json.loads(str(attrs.get("formal_modules", "[]"))):
                self.modules.setdefault(name, set()).add(unit.id)
            for name, imports in json.loads(str(attrs.get("formal_exports", "{}"))).items():
                self.exports.setdefault(name, set()).update(imports)
            for name, imports in json.loads(str(attrs.get("formal_open_exports", "{}"))).items():
                self.open_exports.setdefault(name, set()).update(imports)
            qualified = attrs.get("qualified_name")
            if isinstance(qualified, str):
                self.names.setdefault(qualified, []).append(unit)
            for alias in json.loads(str(attrs.get("qualified_aliases", "[]"))):
                self.names.setdefault(alias, []).append(unit)
        self.interfaces = {
            str(Path(path).with_suffix("")): {
                dict(n.attributes).get("qualified_name")
                for n in graph.nodes.values()
                if n.location.file_path == path
            }
            for path in self.files
            if path.endswith(".fsti")
        }
        self.imports = self.link_imports()
        self.visible = {module: self.reachable(module) for module in self.files.values()}

    def link_imports(self) -> dict[str, set[str]]:
        imports: dict[str, set[str]] = {module: set() for module in self.files.values()}
        edges = []
        for edge in self.graph.edges:
            targets = self.import_targets(edge)
            if edge.kind == EdgeKind.IMPORTS and edge.target_id in imports:
                targets = targets or {edge.target_id}
            if targets:
                resolved = {self.import_node(edge, target) for target in targets}
                edges.extend(
                    replace(edge, target_id=target)
                    for target in sorted(resolved)
                    if edge.source_id != target
                )
                source = self.files[self.graph.nodes[edge.source_id].location.file_path]
                imports[source].update(targets)
            else:
                edges.append(edge)
        self.graph.edges = edges
        return imports

    def import_node(self, edge: CodeEdge, target: str) -> str:
        name = str(dict(edge.attributes).get("import_name", ""))
        path = self.graph.nodes[target].location.file_path
        candidates = {
            unit.id
            for unit in self.graph.nodes.values()
            if unit.kind == NodeKind.NAMESPACE
            and unit.location.file_path == path
            and isinstance(qualified := dict(unit.attributes).get("qualified_name"), str)
            and (
                qualified == name
                or name.endswith("." + qualified)
                or qualified.endswith("." + name)
            )
        }
        return next(iter(candidates)) if len(candidates) == 1 else target

    def import_targets(self, edge: CodeEdge) -> set[str]:
        if edge.kind != EdgeKind.IMPORTS:
            return set()
        attrs = dict(edge.attributes)
        path = attrs.get("import_path")
        if isinstance(path, str):
            source = self.graph.nodes[edge.source_id].location.file_path
            target = self.paths.get(os.path.abspath(Path(source).parent / path))
            return {target} if target is not None else set()
        name = str(attrs.get("import_name"))
        targets = self.modules.get(name, set())
        if not targets and "." in name:
            parts = name.split(".")
            targets = {
                target
                for module, units in self.modules.items()
                if module == parts[-1] or module == ".".join(parts[-2:])
                for target in units
                if tuple(
                    Path(self.graph.nodes[target].location.file_path)
                    .with_suffix("")
                    .parts[-len(parts[:-1]) :]
                )
                == tuple(parts[:-1])
            }
        source = self.graph.nodes[edge.source_id].location.file_path
        local = {
            target
            for target in targets
            if Path(self.graph.nodes[target].location.file_path).parent == Path(source).parent
        }
        candidates = local or targets
        identities = {
            str(Path(self.graph.nodes[target].location.file_path).with_suffix(""))
            for target in candidates
        }
        return candidates if len(identities) == 1 else set()

    def reachable(self, module: str) -> set[str]:
        reached, pending = {module}, [module]
        while pending:
            for target in self.imports.get(pending.pop(), set()) - reached:
                reached.add(target)
                pending.append(target)
        return reached

    def resolve(self, edge: CodeEdge, tiers: list[list[str]]) -> str | None:
        source = self.graph.nodes[edge.source_id]
        source_file = source.location.file_path
        visible = self.visible[self.files[source_file]]
        spelling = str(dict(edge.attributes).get("spelling", ""))
        qualified = {
            prefix + spelling
            for prefix in self.qualified_prefixes
            if "." in spelling and prefix + spelling.rpartition(".")[0] in self.modules
        }
        if qualified:
            tiers = [*tiers, sorted(qualified)]
        for tier in tiers:
            found = [
                unit
                for name in self.expanded(tier, spelling)
                for unit in self.names.get(name, [])
                if (self.files[unit.location.file_path] in visible or name in qualified)
                and not (
                    self.graph.language == "rocq"
                    and (unit.kind in {NodeKind.MODULE, NodeKind.NAMESPACE})
                    != (dict(edge.attributes).get("reference_context") == "module")
                )
                and self.available(unit, edge, source_file)
                and (
                    unit.location.file_path == source_file
                    or not dict(unit.attributes).get("private")
                )
            ]
            local = [unit for unit in found if unit.location.file_path == source_file]
            if local:
                found = local
                definitions = [n for n in found if not dict(n.attributes).get("signature")]
                found = definitions or found
            else:
                signatures = [n for n in found if dict(n.attributes).get("signature")]
                found = signatures or found
            targets = {unit.id for unit in found}
            if targets:
                return next(iter(targets)) if len(targets) == 1 else None
        if self.graph.language == "proverif" and source_file.endswith(".pv"):
            libraries = {
                unit.id
                for unit in self.names.get(spelling, [])
                if unit.location.file_path.endswith(".pvl")
                and Path(unit.location.file_path).parent == Path(source_file).parent
            }
            if len(libraries) == 1:
                return next(iter(libraries))
            if libraries:
                message = "Ambiguous ProVerif directory libraries: " + ", ".join(
                    sorted(
                        {
                            Path(self.graph.nodes[ident].location.file_path).name
                            for ident in libraries
                        }
                    )
                )
                if not any(
                    d.location.file_path == source_file and d.message == message
                    for d in self.graph.parse_diagnostics
                ):
                    self.graph.parse_diagnostics.append(
                        ParseDiagnostic(
                            "proverif", "proverif", source.location, "unsupported", message
                        )
                    )
        return None

    def expanded(self, names: list[str], spelling: str = "") -> set[str]:
        result: set[str] = set()
        pending = list(names)
        while pending:
            name = pending.pop()
            if name in result:
                continue
            result.add(name)
            if spelling and name.endswith("." + spelling):
                prefix = name[: -len(spelling) - 1]
                pending.extend(
                    f"{parent}.{spelling}" for parent in self.open_exports.get(prefix, ())
                )
            namespace, _, member = name.rpartition(".")
            pending.extend(f"{parent}.{member}" for parent in self.exports.get(namespace, ()))
        if self.graph.language == "rocq":
            for name in names:
                prefix, dot, member = name.rpartition(".")
                if dot:
                    result.update(
                        f"{module}.{member}"
                        for module in self.modules
                        if module == prefix or module.endswith("." + prefix)
                    )
        return result

    def available(self, unit: CodeUnit, edge: CodeEdge, source_file: str) -> bool:
        if unit.location.file_path != source_file:
            exported = self.interfaces.get(str(Path(unit.location.file_path).with_suffix("")))
            return exported is None or dict(unit.attributes).get("qualified_name") in exported
        if source_file not in self.ordered:
            return True
        group = dict(unit.attributes).get("recursive_group")
        if group is not None and group == dict(self.graph.nodes[edge.source_id].attributes).get(
            "recursive_group"
        ):
            return True
        if edge.location is None:
            return False
        return (unit.location.start_line, unit.location.start_col or 0) <= (
            edge.location.start_line,
            edge.location.start_col or 0,
        )

    def correspondence(self) -> list[CodeEdge]:
        result = []
        for units in self.names.values():
            signatures = [u for u in units if dict(u.attributes).get("signature")]
            definitions = [u for u in units if not dict(u.attributes).get("signature")]
            if len(definitions) == 1:
                result.extend(
                    CodeEdge(
                        signature.id,
                        definitions[0].id,
                        EdgeKind.CORRESPONDS_TO,
                        EdgeConfidence.INFERRED,
                        signature.location,
                    )
                    for signature in signatures
                )
        return result


@dataclass(frozen=True)
class _Linked:
    edge: CodeEdge
    application: bool = False
    position: int | None = None
    enclosing: str | None = None


def _link(edge: CodeEdge, symbols: _Symbols) -> _Linked | None:
    attrs = dict(edge.attributes)
    raw = attrs.pop("_formal_candidates", None)
    application = attrs.pop("_formal_application", False)
    bound_application = attrs.pop("_formal_bound_application", False)
    position = attrs.pop("_formal_position", None)
    enclosing = attrs.pop("_formal_enclosing", None)
    target = (
        (None if bound_application else symbols.resolve(edge, json.loads(raw)))
        if isinstance(raw, str)
        else edge.target_id
    )
    if target is None:
        if not (
            application
            and (
                (
                    symbols.graph.language == "fstar"
                    and attrs.get("reference_context")
                    in {"body", "argument", "proof", "definition"}
                )
                or (
                    symbols.graph.language == "whyml"
                    and attrs.get("reference_context") in {"body", "argument"}
                )
            )
        ):
            return None
        name = str(attrs["spelling"])
        role = "bound" if bound_application else "unresolved"
        target = f"proxy.{role}:{edge.source_id}.$application.{name}"
        attrs["unresolved_application"] = True
        if bound_application:
            attrs["binding_blocked"] = True
        symbols.graph.nodes.setdefault(
            target,
            CodeUnit(
                target,
                name,
                NodeKind.PROXY,
                edge.location or symbols.graph.nodes[edge.source_id].location,
                origin=NodeOrigin.PROXY,
                attributes=(
                    ("executable", symbols.graph.language == "whyml"),
                    ("unresolved_application", True),
                ),
            ),
        )
    if (
        symbols.graph.language == "proverif"
        and symbols.graph.nodes[target].location.file_path.endswith(".pvl")
        and symbols.graph.nodes[edge.source_id].location.file_path.endswith(".pv")
    ):
        attrs["library_resolution"] = "directory"
    linked = replace(
        edge,
        target_id=target,
        attributes=tuple(attrs.items()),
        confidence=EdgeConfidence.UNCERTAIN
        if attrs.get("unresolved_application")
        or (
            (unit := symbols.graph.nodes.get(target)) is not None
            and dict(unit.attributes).get("unresolved_application")
        )
        else EdgeConfidence.INFERRED
        if isinstance(raw, str)
        else edge.confidence,
    )
    return _Linked(
        linked,
        application is True,
        position if isinstance(position, int) else None,
        enclosing if isinstance(enclosing, str) else None,
    )


def _computational(graph: CodeGraph, ident: str) -> bool:
    return dict(graph.nodes[ident].attributes).get("executable") is True


def _argument_evaluated(unit: CodeUnit | None, argument: int | str | None) -> bool | None:
    if unit is None or dict(unit.attributes).get("unresolved_application"):
        return None
    attrs = dict(unit.attributes)
    if attrs.get("executable") is not True:
        return False
    ghost = json.loads(str(attrs.get("ghost_parameters", "[]")))
    if not ghost:
        return True
    if isinstance(argument, int) and 0 <= argument < len(unit.parameters):
        argument = unit.parameters[argument].name
    return isinstance(argument, str) and argument not in ghost


def _runtime(
    reference: _Linked,
    evaluated: dict[tuple[str, int | None, int | str | None], bool | None],
    parents: dict[str, dict[str, list]],
    cache: dict[tuple[str, str], bool | None],
) -> bool | None:
    context = dict(reference.edge.attributes).get("reference_context")
    if context == "body":
        return True
    if context != "argument" or reference.enclosing is None:
        return False
    owner = reference.edge.source_id
    key = reference.enclosing
    pending = []
    while key is not None and (owner, key) not in cache:
        _, _, parent = parents[owner][key]
        pending.append(key)
        key = parent
    value = cache.get((owner, key), True) if key is not None else True
    for key in reversed(pending):
        position, argument, _ = parents[owner][key]
        local = evaluated.get((owner, position, argument))
        value = (
            False
            if local is False or value is False
            else (None if local is None or value is None else True)
        )
        cache[owner, key] = value
    return value


def link_symbols(graph: CodeGraph, *, qualified_prefixes: tuple[str, ...] = ()) -> None:
    symbols = _Symbols(graph, qualified_prefixes)
    references = [linked for edge in graph.edges if (linked := _link(edge, symbols)) is not None]
    callees = {
        (reference.edge.source_id, reference.position): graph.nodes[reference.edge.target_id]
        for reference in references
        if reference.application
    }
    parents = {
        ident: json.loads(str(dict(unit.attributes).get("_formal_argument_scopes", "{}")))
        for ident, unit in graph.nodes.items()
    }
    evaluated = {
        (owner, position, argument): _argument_evaluated(callees.get((owner, position)), argument)
        for owner, scopes in parents.items()
        for position, argument, _ in scopes.values()
    }
    cache: dict[tuple[str, str], bool | None] = {}
    for ident, unit in tuple(graph.nodes.items()):
        graph.nodes[ident] = replace(
            unit,
            attributes=tuple((k, v) for k, v in unit.attributes if k != "_formal_argument_scopes"),
        )
    result = []
    for reference in references:
        edge = reference.edge
        result.append(edge)
        runtime = _runtime(reference, evaluated, parents, cache)
        if (
            reference.application
            and (runtime is True or (runtime is None and graph.language == "whyml"))
            and _computational(graph, edge.source_id)
            and _computational(graph, edge.target_id)
        ):
            result.append(
                replace(
                    edge,
                    kind=EdgeKind.CALLS,
                    confidence=EdgeConfidence.UNCERTAIN if runtime is None else edge.confidence,
                )
            )
    graph.edges = list(dict.fromkeys([*result, *symbols.correspondence()]))
    targets = {e.target_id for e in graph.edges}
    for ident in list(graph.nodes):
        if ident.startswith("@import:") and ident not in targets:
            del graph.nodes[ident]
