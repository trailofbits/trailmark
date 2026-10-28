"""Translation-phase line splicing preserves declaration macro identities."""

from __future__ import annotations

from pathlib import Path

import pytest

from trailmark.models import EdgeConfidence, EdgeKind, NodeKind
from trailmark.parse import parse_directory, parse_file


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("header", [False, True], ids=["source", "quoted-header"])
@pytest.mark.parametrize(
    "macro,declaration,name",
    [
        ("#define DECL(n) void pre\\\nfix_ ## n(int code)\n", "DECL(action)", "prefix_action"),
        ("#define DECL(n) vo\\\nid prefix_ ## n(int co\\\nde)\n", "DECL(action)", "prefix_action"),
        ("#define PARAMS int co\\\nde\n", "void action(PARAMS)", "action"),
    ],
    ids=["pasted-name", "return-and-parameter", "parameter-list"],
)
def test_line_splices_join_macro_tokens_without_inserting_whitespace(
    tmp_path: Path, newline: str, header: bool, macro: str, declaration: str, name: str
) -> None:
    if header:
        (tmp_path / "decls.h").write_bytes(
            ("#ifndef DECLS_H\n#define DECLS_H\n" + macro + "#endif\n")
            .replace("\n", newline)
            .encode()
        )
        prefix = '#include "decls.h"\n'
    else:
        prefix = macro
    source = (
        prefix + "void helper(void) {}\n" + declaration + " {\n helper();\n helper();\n}\n"
        f"void invoke(void) {{ {name}(1); }}\n"
    )
    path = tmp_path / "sample.c"
    path.write_bytes(source.replace("\n", newline).encode())
    start = prefix.count("\n") + 2
    for graph in (parse_file(str(path), "c"), parse_directory(str(tmp_path), "c")):
        assert {node.id for node in graph.nodes.values() if node.kind == NodeKind.FUNCTION} == {
            "sample:helper",
            f"sample:{name}",
            "sample:invoke",
        }
        unit = graph.nodes[f"sample:{name}"]
        assert (unit.location.file_path, unit.location.start_line, unit.location.end_line) == (
            str(path),
            start,
            start + 3,
        )
        assert [
            (parameter.name, parameter.type_ref.name if parameter.type_ref else None)
            for parameter in unit.parameters
        ] == [("code", "int")]
        assert unit.return_type is not None and unit.return_type.name == "void"
        attrs = dict(unit.attributes)
        assert not attrs.get("macro_signature_unresolved")
        if name == "prefix_action":
            assert {
                key: attrs[key] for key in ("macro_wrapper", "source_alias", "expansion_alias")
            } == {"macro_wrapper": "DECL", "source_alias": "action", "expansion_alias": name}
        calls = [edge for edge in graph.edges if edge.kind == EdgeKind.CALLS]
        assert [(edge.source_id, edge.target_id) for edge in calls] == [
            (unit.id, "sample:helper"),
            (unit.id, "sample:helper"),
            ("sample:invoke", unit.id),
        ]
        assert [edge.confidence for edge in calls] == [EdgeConfidence.CERTAIN] * 3
        assert [
            (edge.location.start_line, edge.location.start_col) for edge in calls if edge.location
        ] == [(start + 1, 1), (start + 2, 1), (start + 4, 20)]
        assert graph.parse_diagnostics == []
