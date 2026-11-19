"""Independent, bounded syntax evidence; none of these validators use Trailmark.

Acceptance by a language parser is deliberately not described as proof of graph
correctness. Missing optional toolchains are reported, never counted as passes.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def _python(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "validator": "python.ast",
        "version": platform.python_version(),
        "scope": "Python syntax and syntactic declaration/call inventory; not binding or execution",
    }
    try:
        tree = ast.parse(path.read_bytes(), filename=str(path))
    except SyntaxError as exc:
        return {
            **result,
            "status": "rejected",
            "diagnostics": [{"line": exc.lineno, "column": exc.offset, "message": exc.msg}],
        }
    declarations, calls = [], []

    def visit(node: ast.AST, owners: tuple[str, ...] = ()) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            qualified = (*owners, node.name)
            declarations.append(
                {
                    "name": node.name,
                    "qualified_name": ".".join(qualified),
                    "kind": type(node).__name__,
                    "line": node.lineno,
                    "column": node.col_offset,
                    "end_line": node.end_lineno,
                }
            )
            owners = qualified
        if isinstance(node, ast.Call):
            calls.append(
                {
                    "owner": ".".join(owners),
                    "expression": ast.unparse(node.func),
                    "line": node.lineno,
                    "column": node.col_offset,
                    "end_line": node.end_lineno,
                    "end_column": node.end_col_offset,
                }
            )
        for child in ast.iter_child_nodes(node):
            visit(child, owners)

    visit(tree)
    return {
        **result,
        "status": "accepted",
        "diagnostics": [],
        "declarations": declarations,
        "calls": calls,
    }


def _graphql(path: Path) -> dict[str, Any]:
    if importlib.util.find_spec("graphql") is None:
        return {
            "validator": "graphql-core",
            "status": "unavailable",
            "scope": "GraphQL SDL syntax and schema validation",
            "diagnostics": [],
            "reason": "Install the locked graphql-core evaluator dependency",
        }
    graphql = importlib.import_module("graphql")

    result: dict[str, Any] = {
        "validator": "graphql-core",
        "version": importlib.metadata.version("graphql-core"),
        "scope": "GraphQL SDL syntax and whole-file schema consistency; not resolver execution",
    }
    try:
        tree = graphql.parse(path.read_text())
        schema = graphql.build_ast_schema(tree)
        errors = graphql.validate_schema(schema)
    except Exception as exc:  # Validator exceptions are evidence, not harness crashes.
        return {**result, "status": "rejected", "diagnostics": [str(exc)]}
    definitions = [
        {"name": definition.name.value, "kind": definition.kind}
        for definition in tree.definitions
        if getattr(definition, "name", None) is not None
    ]
    return {
        **result,
        "status": "rejected" if errors else "accepted",
        "diagnostics": [str(error) for error in errors],
        "definitions": definitions,
    }


def _postgres(path: Path) -> dict[str, Any]:
    if importlib.util.find_spec("pglast") is None:
        return {
            "validator": "pglast",
            "status": "unavailable",
            "scope": "PostgreSQL outer-statement syntax",
            "diagnostics": [],
            "reason": "Install the locked pglast evaluator dependency",
        }
    pglast = importlib.import_module("pglast")

    result: dict[str, Any] = {
        "validator": "pglast",
        "version": importlib.metadata.version("pglast"),
        "scope": (
            "PostgreSQL outer-statement syntax; no PL/pgSQL body analysis, "
            "psql or extension execution"
        ),
    }
    try:
        statements = pglast.parse_sql(path.read_text())
    except Exception as exc:
        return {**result, "status": "rejected", "diagnostics": [str(exc)]}
    return {
        **result,
        "status": "accepted",
        "diagnostics": [],
        "statements": [
            {
                "kind": type(statement.stmt).__name__,
                "offset": statement.stmt_location,
                "length": statement.stmt_len,
            }
            for statement in statements
        ],
    }


_TYPESCRIPT_SCRIPT = """
const fs = require('fs');
const ts = require('typescript');
const path = process.argv[1];
const source = ts.createSourceFile(path, fs.readFileSync(path, 'utf8'),
    ts.ScriptTarget.Latest, true, path.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
const declarations = [];
function walk(node) {
  if ((ts.isFunctionDeclaration(node) || ts.isClassDeclaration(node) ||
       ts.isInterfaceDeclaration(node) || ts.isMethodDeclaration(node)) && node.name) {
    const p = source.getLineAndCharacterOfPosition(node.name.getStart(source));
    declarations.push({name: node.name.getText(source), kind: ts.SyntaxKind[node.kind],
                       line: p.line + 1, column: p.character});
  }
  ts.forEachChild(node, walk);
}
walk(source);
const diagnostics = source.parseDiagnostics.map(d => {
  const p = source.getLineAndCharacterOfPosition(d.start || 0);
  return {line: p.line + 1, column: p.character,
          message: ts.flattenDiagnosticMessageText(d.messageText, '\\n')};
});
console.log(JSON.stringify({validator: 'typescript', version: ts.version,
    scope: 'TypeScript compiler syntax and syntactic declarations; not typechecking or execution',
    status: diagnostics.length ? 'rejected' : 'accepted', diagnostics, declarations}));
"""


def _typescript(path: Path) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        return {
            "validator": "typescript",
            "status": "unavailable",
            "diagnostics": [],
            "scope": "TypeScript compiler syntax",
            "reason": "Node.js is unavailable",
        }
    try:
        result = subprocess.run(  # noqa: S603
            [node, "-e", _TYPESCRIPT_SCRIPT, str(path.resolve())],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return {
            "validator": "typescript",
            "status": "timeout",
            "diagnostics": [],
            "scope": "TypeScript compiler syntax",
            "timeout_seconds": 30,
        }
    if result.returncode:
        missing = "Cannot find module 'typescript'" in result.stderr
        return {
            "validator": "typescript",
            "status": "unavailable" if missing else "error",
            "scope": "TypeScript compiler syntax",
            "diagnostics": [result.stderr.strip()],
            "returncode": result.returncode,
        }
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {
            "validator": "typescript",
            "status": "error",
            "scope": "TypeScript compiler syntax",
            "diagnostics": [str(exc)],
        }


def validate_source(path: Path, language: str) -> dict[str, Any]:
    """Collect one independent validator's evidence for the named source file."""
    validators = {
        "python": _python,
        "typescript": _typescript,
        "graphql": _graphql,
        "sql": _postgres,
    }
    if language not in validators:
        return {
            "validator": None,
            "status": "not_configured",
            "diagnostics": [],
            "scope": "No independent validator configured for this pilot language",
        }
    try:
        return validators[language](path)
    except (OSError, UnicodeError) as exc:
        return {
            "validator": language,
            "status": "error",
            "diagnostics": [str(exc)],
            "scope": "Source could not be read",
        }


def validate_files(
    root: Path,
    language: str,
    files: list[str],
    destination: Path,
    *,
    python: Path | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Validate selected files with the optional dependencies of a chosen Python.

    Each file has a separate process, so one parser crash cannot suppress the
    evidence for the other selected files. No target code is imported or run.
    """
    root = root.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    observations: list[dict[str, Any]] = []
    for name in sorted(set(files)):
        source = (root / name).resolve()
        if not source.is_relative_to(root) or not source.is_file():
            observations.append(
                {
                    "path": name,
                    "status": "error",
                    "scope": "Source selection",
                    "diagnostics": ["Selected source must be an existing file within the project"],
                }
            )
            continue
        result_path = destination / f"{hashlib.sha256(name.encode()).hexdigest()}.json"
        result_path.unlink(missing_ok=True)
        command = [
            str(python or Path(sys.executable)),
            "-I",
            str(Path(__file__).resolve()),
            "--source",
            str(source),
            "--language",
            language,
            "--output",
            str(result_path),
        ]
        evidence: dict[str, Any]
        try:
            result = subprocess.run(  # noqa: S603
                command,
                cwd=destination,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
            if result.returncode == 0 and result_path.is_file():
                evidence = json.loads(result_path.read_text())
            else:
                evidence = {
                    "status": "crashed",
                    "returncode": result.returncode,
                    "diagnostics": [result.stderr],
                }
        except subprocess.TimeoutExpired:
            evidence = {"status": "timeout", "timeout_seconds": timeout, "diagnostics": []}
        except (OSError, json.JSONDecodeError) as exc:
            evidence = {"status": "error", "diagnostics": [str(exc)]}
        observations.append({"path": name, **evidence})
    statuses: dict[str, int] = {}
    for row in observations:
        statuses[row["status"]] = statuses.get(row["status"], 0) + 1
    evidence = {
        "schema_version": 1,
        "language": language,
        "status": "completed",
        "files": observations,
        "counts": statuses,
    }
    (destination / "validation.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    )
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--language", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = validate_source(args.source, args.language)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
