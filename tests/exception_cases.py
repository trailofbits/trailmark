"""Reviewed expectations for the bounded, 39-frontend exception audit corpus.

These are syntax checks, not language conformance or runtime-safety claims.
"""

from __future__ import annotations

# language, filename, event kinds exercised, expected syntax diagnostics
EXCEPTION_CASES: tuple[tuple[str, str, frozenset[str], bool], ...] = tuple(
    (language, filename, frozenset(kinds.split()), diagnostic)
    for language, filename, kinds, diagnostic in [
        ("python", "python.py", "raise rethrow handler cleanup", False),
        ("r", "r.r", "raise handler cleanup", False),
        ("javascript", "javascript.js", "raise handler cleanup", False),
        ("typescript", "typescript.ts", "raise handler cleanup", False),
        ("php", "php.php", "raise handler cleanup", False),
        ("perl", "perl.pl", "raise handler", False),
        ("ruby", "ruby.rb", "raise rethrow handler cleanup", False),
        ("lua", "lua.lua", "raise handler", False),
        ("c", "c.c", "handler propagate", False),
        ("bash", "bash.sh", "handler", False),
        ("cpp", "cpp.cpp", "raise rethrow handler", False),
        ("c_sharp", "csharp.cs", "raise rethrow handler cleanup", False),
        ("powershell", "powershell.ps1", "raise handler cleanup", False),
        ("java", "java.java", "declares raise handler cleanup", False),
        ("vb", "vb.vb", "raise rethrow handler cleanup", False),
        ("go", "go.go", "raise cleanup", False),
        ("rust", "rust.rs", "raise propagate", False),
        ("zig", "zig.zig", "cleanup propagate handler", False),
        ("solidity", "solidity.sol", "raise handler", False),
        ("aiken", "aiken.ak", "terminate", False),
        ("cairo", "cairo.cairo", "terminate", False),
        ("circom", "circom.circom", "terminate", False),
        ("haskell", "haskell.hs", "raise handler", False),
        ("julia", "julia.jl", "raise rethrow handler cleanup", False),
        ("erlang", "erlang.erl", "raise handler cleanup", False),
        ("masm", "masm.masm", "terminate", False),
        ("swift", "swift.swift", "declares raise handler cleanup", False),
        ("objc", "objc.m", "raise rethrow handler cleanup", False),
        ("kotlin", "kotlin.kt", "raise handler cleanup", False),
        ("dart", "dart.dart", "raise rethrow handler cleanup", False),
        ("move", "move.move", "terminate", False),
        ("tact", "tact.tact", "raise handler", False),
        ("func", "func.fc", "raise handler", False),
        ("sway", "sway.sw", "terminate", False),
        ("rego", "rego.rego", "", False),
        ("proto", "proto.proto", "", False),
        ("thrift", "thrift.thrift", "declares", False),
        ("graphql", "graphql.graphql", "", False),
        ("sql", "sql.sql", "", True),
    ]
)
