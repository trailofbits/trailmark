"""Shared ranking for human graph references, not language call binding."""

import re
from collections.abc import Callable, Mapping

_POSITION_SUFFIX = re.compile(r"@\d+_\d+$")


def resolve_node_id[T](
    nodes: Mapping[str, T],
    reference: str,
    *,
    name_of: Callable[[T], str],
    is_proxy: Callable[[T], bool],
) -> str | None:
    """Prefer exact IDs, then concrete declarations, then proxy fallback.

    Within each non-ID group, rank exact names before colon and dot suffixes.
    Positioned declarations also match their base name, ID, and ID suffixes.
    Ties retain insertion order, preserving the existing first-match policy.
    Explicit proxy IDs remain addressable even when a declaration also matches.
    """
    if reference in nodes:
        return reference

    best_id: str | None = None
    best_rank = (True, 3)
    for node_id, node in nodes.items():
        name = name_of(node)
        base_id = _POSITION_SUFFIX.sub("", node_id)
        if name == reference or _POSITION_SUFFIX.sub("", name) == reference:
            match_rank = 0
        elif (
            base_id == reference
            or node_id.endswith(f":{reference}")
            or base_id.endswith(f":{reference}")
        ):
            match_rank = 1
        elif node_id.endswith(f".{reference}") or base_id.endswith(f".{reference}"):
            match_rank = 2
        else:
            continue
        rank = (is_proxy(node), match_rank)
        if rank < best_rank:
            best_id, best_rank = node_id, rank
    return best_id
