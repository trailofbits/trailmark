"""Stable mutation identities shared by comparison and execution reports."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from pytest_gremlins.instrumentation.gremlin import Gremlin


def inventory(gremlins: list[Gremlin], root: Path) -> list[dict[str, Any]]:
    """Describe every assigned mutation using its source rather than its ordinal ID."""
    from tests.mutation_gate import describe, functions_in

    sources = {}
    result = []
    for gremlin in gremlins:
        path = Path(gremlin.file_path).resolve()
        if path not in sources:
            source = path.read_text()
            tree = ast.parse(source)
            sources[path] = source, (tree, list(functions_in(tree)))
        source, context = sources[path]
        detail = describe(gremlin, source, path.relative_to(root.resolve()).as_posix(), context)
        result.append({"gremlin_id": gremlin.gremlin_id, **detail})
    return result
