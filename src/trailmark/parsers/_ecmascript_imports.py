"""Bind literal ECMAScript imports without a project-wide name guess."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language

from trailmark.parsers._common import node_text, planned_source


@dataclass(frozen=True)
class ImportBinding:
    target: str
    type_only: bool


def import_bindings(root: Node, file_path: str, language: str) -> dict[str, ImportBinding]:
    """Index imports once per file; resolve only explicit local named exports."""
    bindings: dict[str, ImportBinding] = {}
    exports: dict[Path, dict[str, str]] = {}
    for statement in root.named_children:
        if statement.type != "import_statement":
            continue
        source = statement.child_by_field_name("source")
        clause = next((n for n in statement.named_children if n.type == "import_clause"), None)
        if source is None or clause is None:
            continue
        specifier = node_text(source)[1:-1]
        type_only = any(n.type == "type" for n in statement.children)
        path = _local_source(specifier, file_path, language)
        selected = planned_source(str(path)) if path is not None else None
        for local, exported, member_type_only in _import_names(clause):
            target = f"import({specifier!r}):{exported}"
            if (
                path is not None
                and selected is not None
                and selected[1] in {"javascript", "typescript"}
                and not (type_only or member_type_only)
            ):
                if path not in exports:
                    exports[path] = _local_exports(path, selected[1])
                name = exports[path].get(exported)
                if name is not None:
                    target = f"{selected[0]}:{name}"
            binding = ImportBinding(target, type_only or member_type_only)
            # Duplicate local imports are invalid; retain an unresolved identity.
            if local in bindings:
                binding = ImportBinding(f"import(ambiguous):{local}", False)
            bindings[local] = binding
    return bindings


def _import_names(clause: Node) -> list[tuple[str, str, bool]]:
    result = []
    for child in clause.named_children:
        if child.type == "named_imports":
            for member in child.named_children:
                if member.type != "import_specifier":
                    continue
                name = member.child_by_field_name("name")
                alias = member.child_by_field_name("alias")
                if name is not None:
                    result.append(
                        (
                            node_text(alias or name),
                            node_text(name),
                            any(n.type == "type" for n in member.children),
                        )
                    )
        elif child.type == "identifier":
            result.append((node_text(child), "$default", False))
        elif child.type == "namespace_import":
            name = next((n for n in child.named_children if n.type == "identifier"), None)
            if name is not None:
                result.append((node_text(name), "$namespace", False))
    return result


def _local_source(specifier: str, file_path: str, language: str) -> Path | None:
    if not specifier.startswith(".") or "\\" in specifier:
        return None
    path = Path(file_path).parent / specifier
    extensions = (".ts", ".tsx") if language == "typescript" else (".js", ".jsx", ".mjs")
    if path.suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}:
        if path.is_file():
            return path
        # TypeScript source commonly spells its eventual JavaScript extension.
        if language == "typescript" and path.suffix in {".js", ".jsx"}:
            return next((p for ext in extensions if (p := path.with_suffix(ext)).is_file()), None)
        return None
    return next(
        (
            p
            for p in [
                *(path.parent / (path.name + ext) for ext in extensions),
                *(path / ("index" + ext) for ext in extensions),
            ]
            if p.is_file()
        ),
        None,
    )


def _local_exports(path: Path, language: str) -> dict[str, str]:
    grammar = "tsx" if path.suffix == ".tsx" else language
    try:
        root = Parser(get_language(grammar)).parse(path.read_bytes()).root_node
    except OSError:
        return {}
    exports: dict[str, str] = {}
    for statement in root.named_children:
        if statement.type != "export_statement" or statement.child_by_field_name("source"):
            continue
        if any(child.type in {"default", "type"} for child in statement.children):
            continue
        declaration = statement.child_by_field_name("declaration")
        if declaration is not None:
            if declaration.type in {
                "function_declaration",
                "generator_function_declaration",
                "class_declaration",
                "abstract_class_declaration",
            }:
                name = declaration.child_by_field_name("name")
                if name is not None:
                    exports[node_text(name)] = node_text(name)
            elif declaration.type in {"lexical_declaration", "variable_declaration"}:
                for declarator in declaration.named_children:
                    name = declarator.child_by_field_name("name")
                    if name is not None and name.type == "identifier":
                        exports[node_text(name)] = node_text(name)
        for clause in statement.named_children:
            if clause.type != "export_clause":
                continue
            for member in clause.named_children:
                if member.type != "export_specifier" or any(
                    n.type == "type" for n in member.children
                ):
                    continue
                name = member.child_by_field_name("name")
                alias = member.child_by_field_name("alias")
                if name is not None:
                    exports[node_text(alias or name)] = node_text(name)
    return exports
