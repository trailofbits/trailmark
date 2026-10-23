"""Deep callable nesting returns partial graphs with explicit diagnostics."""

from pathlib import Path
from time import perf_counter

import pytest

from trailmark.models import EdgeKind
from trailmark.parse import parse_file


@pytest.mark.parametrize(
    ("extension", "source"),
    [
        ("rb", "def run\n" + "items.each do |x|\n" * 3000 + "x\n" + "end\n" * 3000 + "end\n"),
        ("m", "void run(){" + "^{" * 3000 + "work();" + "};" * 3000 + "}"),
        ("dart", "void run(){var x=" + "(){return " * 3000 + "1" + ";}" * 3000 + ";}"),
        ("cs", "class A{ void Run(){var x=" + "()=>" * 3000 + "Work();}}"),
        (
            "cs",
            "class A{ void Run(){var x=" + "()=>{return " * 3000 + "Work()" + ";}" * 3000 + ";}}",
        ),
        (
            "m",
            "@implementation A\n-(void)run {" + "^{" * 3000 + "work();" + "};" * 3000 + "}\n@end",
        ),
        ("dart", "void run(){var x=" + "()=>" * 3000 + "work();}"),
    ],
    ids=[
        "ruby-blocks",
        "objc-blocks",
        "dart-blocks",
        "csharp-arrows",
        "csharp-blocks",
        "objc-method-blocks",
        "dart-arrows",
    ],
)
def test_deep_callable_file_is_bounded(tmp_path: Path, extension: str, source: str) -> None:
    path = tmp_path / f"a.{extension}"
    path.write_text(source)
    start = perf_counter()
    graph = parse_file(str(path))
    assert perf_counter() - start < 2
    assert any(d.kind == "unsupported_syntax" for d in graph.parse_diagnostics)
    assert graph.nodes
    assert all(d.location.file_path == str(path) for d in graph.parse_diagnostics)


def test_ruby_operator_assignment_reads_attribute(tmp_path: Path) -> None:
    path = tmp_path / "a.rb"
    path.write_text("class A\n def total; 1; end\n def run; self.total += tax; end\nend\n")
    graph = parse_file(str(path))
    assert any(
        e.source_id == "a:A.run" and e.target_id == "a:A.total" and e.kind == EdgeKind.CALLS
        for e in graph.edges
    )
