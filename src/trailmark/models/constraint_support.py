"""Reviewed capability dispositions for every source frontend, not completeness claims."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

type ConstraintSupportStatus = Literal["partial", "unsupported", "not_applicable", "unverified"]

_SYNTAX_LANGUAGES = frozenset(
    {
        "python",
        "r",
        "javascript",
        "typescript",
        "php",
        "perl",
        "ruby",
        "lua",
        "c",
        "bash",
        "cpp",
        "c_sharp",
        "powershell",
        "java",
        "vb",
        "go",
        "rust",
        "zig",
        "solidity",
        "aiken",
        "cairo",
        "circom",
        "haskell",
        "lean",
        "julia",
        "erlang",
        "masm",
        "swift",
        "objc",
        "kotlin",
        "dart",
        "move",
        "tact",
        "func",
        "sway",
        "rego",
        "dafny",
        "fstar",
        "whyml",
    }
)
_DECLARATIVE_LANGUAGES = frozenset(
    {"proto", "thrift", "graphql", "rocq", "tlaplus", "tamarin", "proverif"}
)
CONSTRAINT_LANGUAGES = _SYNTAX_LANGUAGES | _DECLARATIVE_LANGUAGES | {"sql"}


@dataclass(frozen=True)
class ConstraintSupport:
    language: str
    call_sites: ConstraintSupportStatus
    argument_syntax: ConstraintSupportStatus
    parameter_binding: ConstraintSupportStatus
    automatic_inference: ConstraintSupportStatus
    boundary: str


def constraint_support_for(language: str) -> ConstraintSupport:
    language = "c_sharp" if language == "csharp" else language
    if language in _DECLARATIVE_LANGUAGES:
        return ConstraintSupport(
            language,
            "not_applicable",
            "not_applicable",
            "not_applicable",
            "not_applicable",
            "This frontend retains schema or specification relationships, not invocation calls.",
        )
    if language == "sql":
        return ConstraintSupport(
            language,
            "unsupported",
            "unsupported",
            "unsupported",
            "unsupported",
            "SQL dependencies are corresponds_to edges; invocation operands are not modeled.",
        )
    if language not in _SYNTAX_LANGUAGES:
        return ConstraintSupport(
            language,
            "unverified",
            "unverified",
            "unverified",
            "unverified",
            "No reviewed constraint-evidence profile is registered.",
        )
    return ConstraintSupport(
        language,
        "partial",
        "unsupported" if language == "masm" else "partial",
        "partial" if language in {"python", "php"} else "unsupported",
        "unsupported",
        "Located syntax and explicit operands only; implicit operands, recovery, unpacking, "
        "and unknown bindings retain gaps.",
    )
