"""Known nullability annotations retain original implementation source."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.parse import parse_file
from trailmark.parsers.objc.parser import ObjCParser

_BODY = (
    "@interface Box\n- (void)run;\n- (void)helper;\n@end\n"
    "@implementation Box\n- (void)run { [self helper]; }\n- (void)helper {}\n@end\n"
)


@pytest.mark.mutation_fast
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_nullability_keeps_calls_and_physical_lines(tmp_path: Path, newline: str) -> None:
    path = tmp_path / "sample.m"
    source = "// λ source\nNS_ASSUME_NONNULL_BEGIN\n" + _BODY + "NS_ASSUME_NONNULL_END\n"
    path.write_bytes(source.replace("\n", newline).encode())
    graph = parse_file(str(path), "objc", strict=True)
    assert dict(graph.nodes["sample"].attributes)["objc_nullability_regions"] == 1
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert graph.nodes[calls[0].source_id].name == "run"
    assert graph.nodes[calls[0].target_id].name == "helper"
    assert calls[0].location and calls[0].location.start_line == 8
    assert path.read_bytes() == source.replace("\n", newline).encode()


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "prefix,suffix",
    [
        ("NS_ASSUME_NONNULL_BEGIN\n", ""),
        (
            "NS_ASSUME_NONNULL_BEGIN\nNS_ASSUME_NONNULL_BEGIN\n",
            "NS_ASSUME_NONNULL_END\nNS_ASSUME_NONNULL_END\n",
        ),
        (
            "#define NS_ASSUME_NONNULL_BEGIN fake\nNS_ASSUME_NONNULL_BEGIN\n",
            "NS_ASSUME_NONNULL_END\n",
        ),
        ("#undef NS_ASSUME_NONNULL_BEGIN\nNS_ASSUME_NONNULL_BEGIN\n", "NS_ASSUME_NONNULL_END\n"),
        ("#if FLAG\nNS_ASSUME_NONNULL_BEGIN\n#else\n", "NS_ASSUME_NONNULL_END\n#endif\n"),
    ],
)
def test_unproven_annotation_regions_stay_untouched(
    tmp_path: Path, prefix: str, suffix: str
) -> None:
    path = tmp_path / "sample.m"
    path.write_text(prefix + _BODY + suffix)
    graph = parse_file(str(path), "objc")
    assert not dict(graph.nodes["sample"].attributes).get("objc_nullability_regions")


@pytest.mark.mutation_fast
def test_comment_text_cannot_supply_annotation_tokens(tmp_path: Path) -> None:
    path = tmp_path / "sample.m"
    path.write_text("/*\nNS_ASSUME_NONNULL_BEGIN\n*/\n" + _BODY + "/*\nNS_ASSUME_NONNULL_END\n*/\n")
    graph = parse_file(str(path), "objc", strict=True)
    assert not dict(graph.nodes["sample"].attributes).get("objc_nullability_regions")
    assert any(e.kind == EdgeKind.CALLS for e in graph.edges)


def test_ranges_do_not_leak_between_parser_calls(tmp_path: Path) -> None:
    parser = ObjCParser()
    path = tmp_path / "sample.m"
    path.write_text("NS_ASSUME_NONNULL_BEGIN\n" + _BODY + "NS_ASSUME_NONNULL_END\n")
    first = parser.parse_file(str(path))
    path.write_text(_BODY)
    second = parser.parse_file(str(path))
    assert dict(first.nodes["sample"].attributes).get("objc_nullability_regions") == 1
    assert not dict(second.nodes["sample"].attributes).get("objc_nullability_regions")
    assert any(e.kind == EdgeKind.CALLS for e in second.edges)
