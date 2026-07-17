"""Public parse-only API for building raw Trailmark code graphs."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

from trailmark.analysis.links import apply_repository_links
from trailmark.analysis.proxies import ensure_proxy_nodes
from trailmark.models.graph import CodeGraph
from trailmark.parsers._common import should_skip_dir
from trailmark.parsers.base import LanguageParser

_PARSER_MAP: dict[str, tuple[str, str]] = {
    "python": ("trailmark.parsers.python", "PythonParser"),
    "javascript": ("trailmark.parsers.javascript", "JavaScriptParser"),
    "typescript": ("trailmark.parsers.typescript", "TypeScriptParser"),
    "php": ("trailmark.parsers.php", "PHPParser"),
    "ruby": ("trailmark.parsers.ruby", "RubyParser"),
    "c": ("trailmark.parsers.c", "CParser"),
    "cpp": ("trailmark.parsers.cpp", "CppParser"),
    "c_sharp": ("trailmark.parsers.csharp", "CSharpParser"),
    "java": ("trailmark.parsers.java", "JavaParser"),
    "go": ("trailmark.parsers.go", "GoParser"),
    "rust": ("trailmark.parsers.rust", "RustParser"),
    "solidity": ("trailmark.parsers.solidity", "SolidityParser"),
    "cairo": ("trailmark.parsers.cairo", "CairoParser"),
    "circom": ("trailmark.parsers.circom", "CircomParser"),
    "haskell": ("trailmark.parsers.haskell", "HaskellParser"),
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

# Extensions used for language auto-detection. Keep these aligned with each
# parser's internal _EXTENSIONS tuple. Shared extensions (e.g., `.h` between C
# and C++) are handled by prioritizing the more specific language — C++ is
# tried before plain C when both report files.
_LANGUAGE_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "python": (".py",),
    "javascript": (".js", ".jsx", ".mjs", ".cjs"),
    "typescript": (".ts", ".tsx"),
    "php": (".php",),
    "ruby": (".rb",),
    "c": (".c",),
    "cpp": (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"),
    "c_sharp": (".cs",),
    "java": (".java",),
    "go": (".go",),
    "rust": (".rs",),
    "solidity": (".sol",),
    "cairo": (".cairo",),
    "circom": (".circom",),
    "haskell": (".hs",),
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
    "graphql": (".graphql", ".gql"),
    "sql": (".sql",),
}

_SUPPORTED_LANGUAGES = tuple(_PARSER_MAP.keys())


def supported_languages() -> tuple[str, ...]:
    """Return the supported Trailmark parser language names."""
    return _SUPPORTED_LANGUAGES


def parse_file(path: str, language: str | None = None) -> CodeGraph:
    """Parse a single file into a raw ``CodeGraph``.

    Args:
        path: Source file to parse.
        language: Optional explicit parser language. If omitted, language is
            inferred from the file extension.

    Raises:
        ValueError: If the language is unsupported or cannot be inferred.
    """
    file_path = Path(path)
    resolved_language = _resolve_file_language(file_path, language)
    return ensure_proxy_nodes(_get_parser(resolved_language).parse_file(str(file_path)))


def parse_directory(path: str, language: str = "python") -> CodeGraph:
    """Parse a directory into a raw ``CodeGraph``.

    ``language`` accepts a specific language name (e.g. ``"python"``),
    ``"auto"`` to detect and merge all supported languages found under the
    directory, or a comma-separated list like ``"python,rust"``.
    """
    languages = _resolve_directory_languages(path, language)
    return _parse_and_merge(path, languages)


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


def _resolve_directory_languages(path: str, spec: str) -> list[str]:
    """Expand a directory ``language`` argument into concrete languages."""
    if spec == "auto":
        detected = detect_languages(path)
        if not detected:
            msg = f"No supported languages detected under {path}"
            raise ValueError(msg)
        return detected
    names = [name.strip() for name in spec.split(",") if name.strip()] if "," in spec else [spec]
    for name in names:
        if name not in _PARSER_MAP:
            msg = f"Unsupported language: {name}"
            raise ValueError(msg)
    return names


def _parse_and_merge(path: str, languages: list[str]) -> CodeGraph:
    """Parse ``path`` with each language parser and merge into one graph."""
    if len(languages) == 1:
        # Single-language directory parses also apply repository links and proxies.
        graph = _get_parser(languages[0]).parse_directory(path)
        apply_repository_links(graph, path)
        return ensure_proxy_nodes(graph)

    merged = CodeGraph(language="polyglot", root_path=str(Path(path).resolve()))
    for lang in languages:
        sub = _get_parser(lang).parse_directory(path)
        merged.merge(sub)
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
    "supported_languages",
]
