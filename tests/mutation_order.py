"""Run a component's unit tests first, retaining every collected test as fallback."""

from __future__ import annotations

from pathlib import Path


def prioritize_tests(source: str, node_ids: list[str]) -> list[str]:
    """Reorder only: an uncaught mutant still runs the entire selected suite."""
    path = Path(source)
    parts = path.parts
    if "tree_sitter_custom" in parts:
        language = parts[parts.index("tree_sitter_custom") + 1]
        preferred = [f"test_{language}_grammar.py", f"test_{language}_parser.py"]
    elif "parsers" in parts and path.name == "parser.py":
        language = path.parent.name
        preferred = [f"test_{language}_parser.py", f"test_{language}_verus_regressions.py"]
    elif path.name == "_common.py":
        preferred = ["test_common_parser.py"]
    elif "models" in parts:
        preferred = ["test_models.py"]
    elif "storage" in parts:
        preferred = ["test_storage.py", "test_storage_edge_cases.py"]
    elif "query" in parts:
        preferred = ["test_query.py", "test_query_edge_cases.py", "test_hidden_apis.py"]
    else:
        preferred = [f"test_{path.stem}.py"]
    ranks = {name: rank for rank, name in enumerate(preferred)}
    return sorted(
        node_ids, key=lambda node: ranks.get(Path(node.split("::", 1)[0]).name, len(ranks))
    )


def relative_node_ids(node_ids: list[str], root: Path) -> list[str]:
    """Keep parameter IDs verbatim; uppercase IDs are real pytest parameters."""
    result = []
    for node_id in node_ids:
        file, separator, test = node_id.partition("::")
        path = Path(file)
        if path.is_absolute() and path.is_relative_to(root):
            file = path.relative_to(root).as_posix()
        result.append(file + separator + test)
    return result
