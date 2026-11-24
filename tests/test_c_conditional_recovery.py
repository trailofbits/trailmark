import shutil
import subprocess
from pathlib import Path

import pytest

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.parse import parse_directory

SOURCE = """void parse(void) {}
void handler(int b) {
#ifdef TLS
    if (b) {
#ifdef PSK
        if (b) {} else {
#endif
            parse();
#ifdef PSK
        }
#endif
    } else
#endif
    { parse(); }
}
"""


def test_split_conditional_braces_retain_the_handler_and_each_call(tmp_path: Path) -> None:
    path = tmp_path / "handler.c"
    path.write_text(SOURCE)
    graph = parse_directory(str(tmp_path), "c")
    assert graph.nodes["handler:handler"].location.start_line == 2
    assert dict(graph.nodes["handler:handler"].attributes)["conditional_source"]
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert {(e.target_id, e.location.start_line if e.location else None) for e in calls} == {
        ("handler:parse", 8),
        ("handler:parse", 14),
    }
    assert all(e.confidence == EdgeConfidence.UNCERTAIN for e in calls)
    assert all("conditional_profiles" in dict(e.attributes) for e in calls)
    assert any(d.kind == "syntax_error" for d in graph.parse_diagnostics)


@pytest.mark.parametrize("defines", [[], ["-DTLS"], ["-DPSK"], ["-DTLS", "-DPSK"]])
def test_conditional_fixture_is_valid_for_each_macro_profile(
    tmp_path: Path, defines: list[str]
) -> None:
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("C compiler unavailable")
    path = tmp_path / "handler.c"
    path.write_text(SOURCE)
    subprocess.run(  # noqa: S603
        [compiler, "-fsyntax-only", *defines, str(path)], check=True, capture_output=True
    )
