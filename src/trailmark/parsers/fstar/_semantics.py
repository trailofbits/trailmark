"""F* computation evidence and signature metadata shared across inspected files."""

from dataclasses import replace
from pathlib import Path

from trailmark.models.edges import EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.parsers._formal_linking import link_symbols


def operator_name(text: str) -> str:
    text = "".join(text.split())
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    if text.startswith("`") and text.endswith("`"):
        return text[1:-1]
    mixfix = {
        ".[]": "op_String_Access",
        ".[]<-": "op_String_Assignment",
        ".()": "op_Array_Access",
        ".()<-": "op_Array_Assignment",
    }
    if text in mixfix:
        return mixfix[text]
    names = {
        "+": "Plus",
        "-": "Minus",
        "*": "Star",
        "/": "Slash",
        "%": "Percent",
        "=": "Equals",
        "<": "Less",
        ">": "Greater",
        "!": "Bang",
        "@": "At",
        "^": "Hat",
        "|": "Bar",
        "&": "Amp",
        "?": "Qmark",
        ":": "Colon",
        ".": "Dot",
        "$": "Dollar",
        "~": "Tilde",
    }
    return (
        "op_" + "_".join(names[c] for c in text) if text and all(c in names for c in text) else text
    )


def _ghost_effects(graph: CodeGraph) -> set[str]:
    ghost = {"Lemma", "GTot", "Ghost"}
    aliases = [
        (str(dict(n.attributes).get("qualified_name")), str(dict(n.attributes)["effect_base"]))
        for n in graph.nodes.values()
        if "effect_base" in dict(n.attributes)
    ]
    changed = True
    while changed:
        changed = False
        for name, base in aliases:
            if (base in ghost or base.rsplit(".", 1)[-1] in ghost) and name not in ghost:
                ghost.update((name, name.rsplit(".", 1)[-1]))
                changed = True
    return ghost


def link_fstar(graph: CodeGraph) -> None:
    ghost = _ghost_effects(graph)
    signatures = {}
    for unit in graph.nodes.values():
        attrs = dict(unit.attributes)
        if attrs.get("effect") in ghost:
            attrs["executable"] = False
            graph.nodes[unit.id] = replace(unit, attributes=tuple(attrs.items()))
            unit = graph.nodes[unit.id]
        if attrs.get("signature"):
            signatures.setdefault(attrs.get("qualified_name"), []).append(unit)
    _inherit_signatures(graph, signatures)
    for index, edge in enumerate(graph.edges):
        if (
            edge.kind == EdgeKind.REFERENCES
            and dict(graph.nodes[edge.source_id].attributes).get("declaration_role") == "lemma"
        ):
            attrs = dict(edge.attributes)
            if attrs.get("reference_context") in {"body", "argument"}:
                attrs["reference_context"] = "proof"
                graph.edges[index] = replace(edge, attributes=tuple(attrs.items()))
    link_symbols(graph, qualified_prefixes=("", "FStar."))


def _inherit_signatures(graph: CodeGraph, signatures: dict) -> None:
    for ident, unit in tuple(graph.nodes.items()):
        attrs = dict(unit.attributes)
        candidates = [
            candidate
            for candidate in signatures.get(attrs.get("qualified_name"), [])
            if Path(candidate.location.file_path).with_suffix("")
            == Path(unit.location.file_path).with_suffix("")
        ]
        local = [
            candidate
            for candidate in candidates
            if candidate.location.file_path == unit.location.file_path
        ]
        candidates = local or candidates
        inherit = attrs.pop("_inherit_effect", False)
        graph.nodes[ident] = replace(unit, attributes=tuple(attrs.items()))
        if attrs.get("signature") or len(candidates) != 1:
            continue
        signature = dict(candidates[0].attributes)
        attrs["private"] = bool(attrs.get("private") or signature.get("private"))
        if inherit:
            attrs["executable"] = signature.get("executable", False)
            if signature.get("declaration_role") == "lemma":
                attrs["declaration_role"] = "lemma"
        graph.nodes[ident] = replace(unit, attributes=tuple(attrs.items()))
