"""Shared declaration indexes and lookup mechanics; adapters own visibility rules."""

from collections.abc import Callable, Iterable

from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind, NodeOrigin


def module_visibility(imports: dict[str, set[str]]) -> dict[str, set[str]]:
    visible = {}
    for module in imports:
        reached, pending = {module}, [module]
        while pending:
            for target in imports.get(pending.pop(), set()) - reached:
                reached.add(target)
                pending.append(target)
        visible[module] = reached
    return visible


class SymbolIndex:
    def __init__(
        self, graph: CodeGraph, name_key: str, *, module_origin: NodeOrigin | None = None
    ) -> None:
        self.files: dict[str, str] = {}
        self.by_file: dict[str, list[CodeUnit]] = {}
        self.names: dict[str, list[CodeUnit]] = {}
        for unit in graph.nodes.values():
            self.by_file.setdefault(unit.location.file_path, []).append(unit)
            if unit.kind == NodeKind.MODULE and (
                module_origin is None or unit.origin == module_origin
            ):
                self.files[unit.location.file_path] = unit.id
            name = dict(unit.attributes).get(name_key)
            if isinstance(name, str):
                self.names.setdefault(name, []).append(unit)

    def resolve(
        self,
        tiers: Iterable[Iterable[str]],
        select: Callable[[list[tuple[str, CodeUnit]]], Iterable[CodeUnit]],
    ) -> tuple[bool, str | None]:
        """Stop at the first matching tier, including an ambiguous match."""
        for tier in tiers:
            units = [(name, unit) for name in tier for unit in self.names.get(name, ())]
            found = {unit.id for unit in select(units)}
            if found:
                return True, next(iter(found)) if len(found) == 1 else None
        return False, None
