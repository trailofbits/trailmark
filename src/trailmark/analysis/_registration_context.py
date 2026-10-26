"""Shared exact binding and source-policy helpers for registration adapters."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import cast

from trailmark.models.endpoints import EndpointRegistration, HandlerBinding
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeOrigin, SourceLocation
from trailmark.source_policy import SourcePolicy, SourceRoot


class RegistrationContext:
    """Index qualified symbols and source locations; never join by terminal name."""

    def __init__(self, graph: CodeGraph) -> None:
        self.graph = graph
        self._manifest = graph.source_policy
        self.symbols: dict[str, list[CodeUnit]] = {}
        self.locations: dict[str, list[CodeUnit]] = {}
        self.roots = [
            Path(str(r["path"]))
            for r in cast(list[dict[str, object]], graph.source_policy.get("roots", []))
        ] or [Path(graph.root_path).resolve()]
        root_names = {
            str(r["name"]): str(r["path"])
            for r in cast(list[dict[str, object]], graph.source_policy.get("roots", []))
        }
        self._owners = {
            str(Path(f["path"]).resolve()): root_names[f["root"]]
            for f in cast(list[dict[str, str]], graph.source_policy.get("files", []))
            if f["status"] in {"inspected", "auxiliary"} and f["unit"] == "file"
        }
        for unit in graph.nodes.values():
            if unit.origin != NodeOrigin.SOURCE:
                continue
            self.locations.setdefault(str(Path(unit.location.file_path).resolve()), []).append(unit)
            local = unit.id.split("::", 1)[-1]
            module, separator, symbol = local.partition(":")
            if not separator:
                continue
            if Path(unit.location.file_path).suffix.lower() in {".php", ".phtml"}:
                keys = {symbol}
            else:
                file_path = Path(unit.location.file_path).resolve()
                owner = Path(self.owning_root(file_path))
                if owner is not None and file_path.suffix.lower() == ".py":
                    parts = list(file_path.relative_to(owner).with_suffix("").parts)
                    if parts[-1] == "__init__":
                        parts.pop()
                    module = ".".join(parts)
                keys = {f"{module}.{symbol}"}
                for root in self.roots:
                    if (
                        Path(unit.location.file_path).is_relative_to(root)
                        and (root / "__init__.py").exists()
                    ):
                        keys.add(f"{root.name}.{module}.{symbol}")
            for key in keys:
                self.symbols.setdefault(key, []).append(unit)

    def bind(
        self,
        symbol: str | None,
        *,
        file: str | None = None,
        line: int | None = None,
        allow_class: bool = False,
    ) -> tuple[HandlerBinding, ...]:
        """Resolve exact FQNs or explicit file/line evidence without inventing ranges."""
        if symbol is not None and not isinstance(symbol, str):
            return (HandlerBinding(status="unsupported"),)
        normalized = (symbol or "").replace("\\", ".").replace("::", ".").lstrip(".")
        candidates = self.symbols.get(normalized, []) if normalized else []
        if file:
            canonical = str(Path(file).resolve())
            if not any(Path(canonical).is_relative_to(root) for root in self.roots):
                return (
                    HandlerBinding(
                        symbol,
                        location=SourceLocation(file, line, line) if line else None,
                        status="out_of_root",
                    ),
                )
            candidates = self.locations.get(canonical, [])
            if line is not None:
                declaration_lines = {line}
                if candidates and canonical.endswith(".py"):
                    try:
                        tree = ast.parse(Path(canonical).read_bytes())
                    except (OSError, SyntaxError, UnicodeError):
                        tree = None
                    if tree is not None:
                        for node in ast.walk(tree):
                            if isinstance(
                                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                            ):
                                starts = [d.lineno for d in node.decorator_list]
                                if line == min(starts, default=node.lineno):
                                    declaration_lines.add(node.lineno)
                candidates = [u for u in candidates if u.location.start_line in declaration_lines]
            if normalized:
                candidates = [u for u in candidates if u.name == normalized.rsplit(".", 1)[-1]]
        candidates = [
            u
            for u in candidates
            if u.kind.value
            in ({"function", "method", "class"} if allow_class else {"function", "method"})
        ]
        if len(candidates) == 1:
            unit = candidates[0]
            return (HandlerBinding(symbol, unit.id, unit.location, "resolved"),)
        return (HandlerBinding(symbol, status="ambiguous" if candidates else "unresolved"),)

    def selected_files(self, extensions: tuple[str, ...]) -> list[Path]:
        """Return config/schema inputs allowed by the same source policy as code."""
        if not self._manifest:
            self._manifest = SourcePolicy((SourceRoot(str(self.roots[0]), "root"),)).discover()
        files = cast(list[dict[str, str]], self._manifest["files"])
        return [
            Path(f["path"])
            for f in files
            if f["status"] in {"inspected", "auxiliary"}
            and f["path"].lower().endswith(extensions)
            and f["unit"] == "file"
        ]

    def owning_root(self, file: str | Path) -> str:
        path = Path(file).resolve()
        return self._owners.get(
            str(path), str(next((r for r in self.roots if path.is_relative_to(r)), self.roots[0]))
        )

    def add(self, registration: EndpointRegistration) -> None:
        self.graph.add_registration(registration)

    def diagnostic(self, file: str | Path, reason: str, detail: str) -> None:
        record = {"file": str(file), "reason": reason, "detail": detail}
        if record not in self.graph.registration_diagnostics:
            self.graph.registration_diagnostics.append(record)
