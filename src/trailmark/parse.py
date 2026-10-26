"""Public parse-only API for building raw Trailmark code graphs."""

from __future__ import annotations

import importlib
import os
from dataclasses import replace
from pathlib import Path
from typing import cast

from trailmark.analysis.links import apply_repository_links
from trailmark.analysis.proxies import ensure_proxy_nodes, materialize_type_use_edges
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import NodeKind
from trailmark.parsers._common import (
    directory_sources,
    link_cross_file_calls,
    link_type_relationships,
    should_skip_dir,
    walk_source_files,
)
from trailmark.parsers.base import LanguageParser
from trailmark.source_policy import SourcePolicy, SourceRoot

_PARSER_MAP: dict[str, tuple[str, str]] = {
    "python": ("trailmark.parsers.python", "PythonParser"),
    "r": ("trailmark.parsers.r", "RParser"),
    "javascript": ("trailmark.parsers.javascript", "JavaScriptParser"),
    "typescript": ("trailmark.parsers.typescript", "TypeScriptParser"),
    "php": ("trailmark.parsers.php", "PHPParser"),
    "perl": ("trailmark.parsers.perl", "PerlParser"),
    "ruby": ("trailmark.parsers.ruby", "RubyParser"),
    "lua": ("trailmark.parsers.lua", "LuaParser"),
    "c": ("trailmark.parsers.c", "CParser"),
    "bash": ("trailmark.parsers.bash", "BashParser"),
    "cpp": ("trailmark.parsers.cpp", "CppParser"),
    "c_sharp": ("trailmark.parsers.csharp", "CSharpParser"),
    "powershell": ("trailmark.parsers.powershell", "PowerShellParser"),
    "java": ("trailmark.parsers.java", "JavaParser"),
    "vb": ("trailmark.parsers.vb", "VisualBasicParser"),
    "go": ("trailmark.parsers.go", "GoParser"),
    "rust": ("trailmark.parsers.rust", "RustParser"),
    "zig": ("trailmark.parsers.zig", "ZigParser"),
    "solidity": ("trailmark.parsers.solidity", "SolidityParser"),
    "aiken": ("trailmark.parsers.aiken", "AikenParser"),
    "cairo": ("trailmark.parsers.cairo", "CairoParser"),
    "circom": ("trailmark.parsers.circom", "CircomParser"),
    "haskell": ("trailmark.parsers.haskell", "HaskellParser"),
    "julia": ("trailmark.parsers.julia", "JuliaParser"),
    "erlang": ("trailmark.parsers.erlang", "ErlangParser"),
    "masm": ("trailmark.parsers.masm", "MasmParser"),
    "swift": ("trailmark.parsers.swift", "SwiftParser"),
    "objc": ("trailmark.parsers.objc", "ObjCParser"),
    "kotlin": ("trailmark.parsers.kotlin", "KotlinParser"),
    "dart": ("trailmark.parsers.dart", "DartParser"),
    "move": ("trailmark.parsers.move", "MoveParser"),
    "tact": ("trailmark.parsers.tact", "TactParser"),
    "func": ("trailmark.parsers.func", "FuncParser"),
    "sway": ("trailmark.parsers.sway", "SwayParser"),
    "rego": ("trailmark.parsers.rego", "RegoParser"),
    "proto": ("trailmark.parsers.proto", "ProtoParser"),
    "thrift": ("trailmark.parsers.thrift", "ThriftParser"),
    "graphql": ("trailmark.parsers.graphql", "GraphQLParser"),
    "sql": ("trailmark.parsers.sql", "SQLParser"),
}

# Auto-detection uses unambiguous extensions. Explicit C/Objective-C parsers
# also accept .h; polyglot parsing assigns each shared header one owner.
_LANGUAGE_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "python": (".py",),
    "r": (".r",),
    "javascript": (".js", ".jsx", ".mjs", ".cjs"),
    "typescript": (".ts", ".tsx"),
    "php": (".php", ".phtml"),
    "perl": (".pl", ".pm"),
    "ruby": (".rb",),
    "lua": (".lua",),
    "c": (".c",),
    "bash": (".sh", ".bash"),
    "cpp": (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"),
    "c_sharp": (".cs",),
    "powershell": (".ps1", ".psm1"),
    "java": (".java",),
    "vb": (".vb",),
    "go": (".go",),
    "rust": (".rs",),
    "zig": (".zig",),
    "solidity": (".sol",),
    "aiken": (".ak",),
    "cairo": (".cairo",),
    "circom": (".circom",),
    "haskell": (".hs",),
    "julia": (".jl",),
    "erlang": (".erl",),
    "masm": (".masm",),
    "swift": (".swift",),
    # `.h` files can be C, ObjC, or C++; auto-detect only fires on .m/.mm.
    # The parser itself still walks .h when invoked with language="objc".
    "objc": (".m", ".mm"),
    "kotlin": (".kt", ".kts"),
    "dart": (".dart",),
    "move": (".move",),
    "tact": (".tact",),
    "func": (".fc", ".func"),
    "sway": (".sw",),
    "rego": (".rego",),
    "proto": (".proto",),
    "thrift": (".thrift",),
    "graphql": (".graphql", ".gql", ".graphqls"),
    "sql": (".sql",),
}

_SUPPORTED_LANGUAGES = tuple(_PARSER_MAP.keys())


def supported_languages() -> tuple[str, ...]:
    """Return the supported Trailmark parser language names."""
    return _SUPPORTED_LANGUAGES


def parse_file(path: str, language: str | None = None, *, strict: bool = False) -> CodeGraph:
    """Parse a single file into a raw ``CodeGraph``.

    Args:
        path: Source file to parse.
        language: Optional explicit parser language. If omitted, language is
            inferred from the file extension.
        strict: Reject parse or unsupported-syntax diagnostics.

    Raises:
        ValueError: If the language is unsupported, cannot be inferred, or
            strict mode finds parse diagnostics.
    """
    file_path = Path(path)
    resolved_language = _resolve_file_language(file_path, language)
    graph = _get_parser(resolved_language).parse_file(str(file_path))
    graph.source_file_counts = {resolved_language: 1}
    link_type_relationships(graph)
    _check_diagnostics(graph, strict=strict)
    return ensure_proxy_nodes(graph)


def parse_directory(
    path: str,
    language: str = "python",
    *,
    allow_empty: bool = False,
    strict: bool = False,
    source_policy: SourcePolicy | None = None,
) -> CodeGraph:
    """Parse a directory into a raw ``CodeGraph``.

    ``language`` accepts a specific language name (e.g. ``"python"``),
    ``"auto"`` to detect and merge all supported languages found under the
    directory, or a comma-separated list like ``"python,rust"``.

    Raises ``ValueError`` for a missing/non-directory root or zero inspected
    source files. ``allow_empty=True`` permits the latter. Coverage is recorded
    in ``CodeGraph.source_file_counts``, including declaration-free files.
    ``strict=True`` raises ``ValueError`` when any file has parse or
    unsupported-syntax diagnostics.
    """
    if source_policy is not None:
        return parse_roots(source_policy, language, allow_empty=allow_empty, strict=strict)
    root = Path(path)
    if not root.is_dir():
        msg = f"Not a directory: {path}"
        raise ValueError(msg)
    languages = _resolve_directory_languages(path, language, allow_empty=allow_empty)
    manifest = SourcePolicy((SourceRoot(path, "root"),)).discover()
    files = cast(list[dict[str, str]], manifest["files"])
    selected = [f["path"] for f in files if f["status"] == "inspected" and f["unit"] == "file"]
    graph = _parse_and_merge(str(root.resolve()), languages, selected)
    _finish_source_manifest(manifest, languages)
    graph.source_policy = manifest
    if not allow_empty and not sum(graph.source_file_counts.values()):
        msg = (
            f"No source files inspected under {path} for languages: {', '.join(languages)}. "
            "Choose the source language (or 'auto'), or set allow_empty=True "
            "(--allow-empty in the CLI) for an intentional empty input."
        )
        raise ValueError(msg)
    _check_diagnostics(graph, strict=strict)
    return graph


def _check_diagnostics(graph: CodeGraph, *, strict: bool) -> None:
    if strict and graph.parse_diagnostics:
        first = graph.parse_diagnostics[0]
        raise ValueError(
            f"Incomplete source analysis: {len(graph.parse_diagnostics)} diagnostic(s); "
            f"{first.location.file_path}:{first.location.start_line}: {first.message}"
        )


def detect_languages(path: str) -> list[str]:
    """Return the sorted languages with at least one file under ``path``.

    Detection walks the directory once, classifies each file by extension,
    and returns the languages that have at least one match. Order is the
    order languages are registered in ``_LANGUAGE_EXTENSIONS``, which
    roughly corresponds to popularity and keeps deterministic behavior.
    """
    root = Path(path)
    if not root.exists():
        return []

    ext_to_language: dict[str, str] = {}
    for lang, exts in _LANGUAGE_EXTENSIONS.items():
        for ext in exts:
            # When languages share an extension (none currently do, but
            # guard against it), the FIRST registration wins.
            ext_to_language.setdefault(ext, lang)

    found: set[str] = set()
    for _dirpath, dirs, files in os.walk(root):
        # Keep detection aligned with the parser walk rules.
        dirs[:] = [d for d in dirs if not should_skip_dir(d)]
        if Path(_dirpath).name == "var":
            dirs[:] = [d for d in dirs if d != "cache"]
        for name in files:
            ext = _file_extension(name)
            if ext in ext_to_language:
                found.add(ext_to_language[ext])
        if len(found) == len(_LANGUAGE_EXTENSIONS):
            break

    return [lang for lang in _LANGUAGE_EXTENSIONS if lang in found]


def _get_parser(language: str) -> LanguageParser:
    """Lazily import and instantiate a parser for the given language."""
    entry = _PARSER_MAP.get(language)
    if entry is None:
        msg = f"Unsupported language: {language}"
        raise ValueError(msg)
    module = importlib.import_module(entry[0])
    cls = getattr(module, entry[1])
    return cls()


def _resolve_file_language(path: Path, language: str | None) -> str:
    """Resolve a single-file parse request to one concrete language."""
    if language is None or language == "auto":
        detected = _detect_file_language(path)
        if detected is None:
            msg = f"Could not infer supported language from file extension: {path}"
            raise ValueError(msg)
        return detected
    if "," in language:
        msg = f"Single-file parse requires one language, got: {language}"
        raise ValueError(msg)
    if language not in _PARSER_MAP:
        msg = f"Unsupported language: {language}"
        raise ValueError(msg)
    return language


def _resolve_directory_languages(path: str, spec: str, *, allow_empty: bool = False) -> list[str]:
    """Expand a directory ``language`` argument into concrete languages."""
    if spec == "auto":
        detected = detect_languages(path)
        if not detected and not allow_empty:
            msg = f"No supported languages detected under {path}"
            raise ValueError(msg)
        return detected
    names = list(dict.fromkeys(name.strip() for name in spec.split(",") if name.strip()))
    if not names:
        msg = "At least one language must be specified"
        raise ValueError(msg)
    for name in names:
        if name not in _PARSER_MAP:
            msg = f"Unsupported language: {name}"
            raise ValueError(msg)
    return names


def _parse_and_merge(
    path: str, languages: list[str], selected_files: list[str] | None = None
) -> CodeGraph:
    """Assign files once and plan collision-safe module IDs before parsing."""
    extensions = {
        lang: _LANGUAGE_EXTENSIONS[lang] + ((".h",) if lang in {"c", "objc"} else ())
        for lang in languages
    }
    all_extensions = tuple({ext for values in extensions.values() for ext in values})
    owners: dict[str, str] = {}
    paths = walk_source_files(path, all_extensions) if selected_files is None else selected_files
    for file_path in paths:
        suffix = _file_extension(file_path)
        candidates = [lang for lang in languages if suffix in extensions[lang]]
        if not candidates:
            continue
        # Objective-C also accepts C declarations. Its visitor retains C
        # types, so mixed headers need neither reparsing nor record merging.
        owner = "objc" if len(candidates) > 1 and suffix == ".h" else candidates[0]
        owners[file_path] = "csharp" if owner == "c_sharp" else owner
    with directory_sources(path, owners, defer_proxies=True):
        return _merge_language_graphs(path, languages)


def parse_roots(
    policy: SourcePolicy, language: str = "auto", *, allow_empty: bool = False, strict: bool = False
) -> CodeGraph:
    """Parse explicit roots once, retaining root identity and effective policy."""
    manifest = policy.discover()
    files = cast(list[dict[str, str]], manifest["files"])
    merged = CodeGraph(language="polyglot", root_path=str(Path(policy.roots[0].path).resolve()))
    for selected in policy.roots:
        paths = [
            str(f["path"])
            for f in files
            if f["root"] == selected.name and f["status"] == "inspected"
        ]
        if language == "auto":
            languages = [
                lang
                for lang in _PARSER_MAP
                if any(_file_extension(p) in _LANGUAGE_EXTENSIONS[lang] for p in paths)
            ]
        else:
            languages = _resolve_directory_languages(selected.path, language, allow_empty=True)
        sub = _parse_and_merge(str(Path(selected.path).resolve()), languages, paths)
        if len(policy.roots) == 1:
            merged.language = sub.language
        if len(policy.roots) > 1:
            _namespace_root(sub, selected.name)
        merged.merge(sub)
        _finish_source_manifest(manifest, languages, root_name=selected.name)
    merged.source_policy = manifest
    if not allow_empty and not sum(merged.source_file_counts.values()):
        raise ValueError("No source files inspected under the selected source policy")
    _check_diagnostics(merged, strict=strict)
    return merged


def _finish_source_manifest(
    manifest: dict[str, object], languages: list[str], *, root_name: str | None = None
) -> None:
    extensions = {extension for lang in languages for extension in _LANGUAGE_EXTENSIONS[lang]}
    if any(lang in {"c", "objc"} for lang in languages):
        extensions.add(".h")
    for file in cast(list[dict[str, str]], manifest["files"]):
        if root_name is not None and file["root"] != root_name:
            continue
        if file["status"] == "inspected" and _file_extension(file["path"]) not in extensions:
            file["status"], file["reason"] = "auxiliary", "unsupported_extension"
    for root in cast(list[dict[str, object]], manifest["roots"]):
        counts: dict[str, int] = {}
        for file in cast(list[dict[str, str]], manifest["files"]):
            if file["root"] == root["name"]:
                key = file["status"] + ":" + file["reason"]
                counts[key] = counts.get(key, 0) + 1
        root["counts"] = counts


def _namespace_root(graph: CodeGraph, name: str) -> None:
    mapping = {node_id: f"{name}::{node_id}" for node_id in graph.nodes}
    graph.nodes = {mapping[k]: replace(v, id=mapping[k]) for k, v in graph.nodes.items()}
    graph.edges = [
        replace(
            e,
            source_id=mapping.get(e.source_id, e.source_id),
            target_id=mapping.get(e.target_id, e.target_id),
        )
        for e in graph.edges
    ]
    graph.annotations = {mapping.get(k, k): v for k, v in graph.annotations.items()}
    graph.entrypoints = {mapping.get(k, k): v for k, v in graph.entrypoints.items()}
    graph.subgraphs = {k: {mapping.get(n, n) for n in v} for k, v in graph.subgraphs.items()}


def _merge_language_graphs(path: str, languages: list[str]) -> CodeGraph:
    """Parse ``path`` with each language parser and merge into one graph."""
    if len(languages) == 1:
        # Single-language directory parses also apply repository links and proxies.
        graph = _get_parser(languages[0]).parse_directory(path)
        apply_repository_links(graph, path)
        return ensure_proxy_nodes(graph)

    merged = CodeGraph(language="polyglot", root_path=str(Path(path).resolve()))
    node_languages: dict[str, str] = {}
    for lang in languages:
        sub = _get_parser(lang).parse_directory(path)
        # Resolve local type uses before another language can introduce an
        # unrelated same-named type. Calls stay raw until after graph merging.
        materialize_type_use_edges(sub)
        for node_id in sorted(merged.nodes.keys() & sub.nodes.keys()):
            if merged.nodes[node_id].kind == sub.nodes[node_id].kind == NodeKind.PROXY:
                continue
            msg = (
                f"Node ID collision for {node_id!r} between languages "
                f"{node_languages[node_id]!r} and {lang!r}; "
                "parse the languages separately or rename the colliding modules"
            )
            raise ValueError(msg)
        merged.merge(sub)
        node_languages.update(dict.fromkeys(sub.nodes, lang))
    # Headers are owned once, but C-family functions may be called from another
    # selected C-family frontend. Link before proxy normalization, without
    # guessing relationships to free functions in unrelated languages.
    c_family_ids = {
        node_id for node_id, lang in node_languages.items() if lang in {"c", "cpp", "objc"}
    }
    link_cross_file_calls(merged, eligible_ids=c_family_ids)
    # merge() doesn't touch `language`; preserve the polyglot marker.
    merged.language = "polyglot"
    apply_repository_links(merged, path)
    return ensure_proxy_nodes(merged)


def _detect_file_language(path: Path) -> str | None:
    """Infer one supported language from a file extension."""
    ext = _file_extension(path.name)
    for language, exts in _LANGUAGE_EXTENSIONS.items():
        if ext in exts:
            return language
    return None


def _file_extension(name: str) -> str:
    """Return the lowercase extension including leading dot, or ``''``."""
    dot = name.rfind(".")
    if dot < 0:
        return ""
    return name[dot:].lower()


__all__ = [
    "detect_languages",
    "parse_directory",
    "parse_file",
    "parse_roots",
    "supported_languages",
]
