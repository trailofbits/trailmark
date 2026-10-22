"""Perl package semantics, dynamic boundaries and generator data."""

from pathlib import Path

import pytest
from tree_sitter import Parser
from tree_sitter_language_pack import get_language

from trailmark.models.edges import EdgeConfidence, EdgeKind
from trailmark.models.nodes import NodeKind
from trailmark.parse import detect_languages, parse_directory, parse_file
from trailmark.query.api import QueryEngine


def _file(tmp_path: Path, text: str) -> Path:
    assert not Parser(get_language("perl")).parse(text.encode()).root_node.has_error
    path = tmp_path / "sample.pl"
    path.write_text(text)
    return path


def test_packages_signatures_prototypes_and_qualified_calls(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        """package Example;
sub helper;
sub helper($x, $y = 1) { if ($x) { return $y; } }
sub legacy ($$) { return shift; }
sub caller { helper(1); Example::helper(2); }
package Other { sub helper { return 0; } }
sub after { helper(3); }
""",
    )
    graph = parse_file(str(path))
    helper = graph.nodes["sample:Example.helper"]
    assert [p.name for p in helper.parameters] == ["$x", "$y"]
    assert helper.parameters[1].default == "1" and helper.cyclomatic_complexity == 2
    legacy = graph.nodes["sample:Example.legacy"]
    assert legacy.parameters == () and dict(legacy.attributes)["perl_prototype"] == "($$)"
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS and e.target_id == helper.id]
    assert len(calls) == 3 and all(e.confidence == EdgeConfidence.INFERRED for e in calls)
    assert "sample:Other.helper" in graph.nodes and "sample:Example.after" in graph.nodes


@pytest.mark.parametrize("mutation", ["*helper = \\&other;", "undef &helper;", "eval $code;"])
def test_symbol_mutation_disables_inference(tmp_path: Path, mutation: str) -> None:
    path = _file(tmp_path, f"sub helper {{}} sub main {{ helper(); }} {mutation}")
    graph = parse_file(str(path))
    assert all(
        e.confidence == EdgeConfidence.UNCERTAIN for e in graph.edges if e.kind == EdgeKind.CALLS
    )


def test_coderefs_receivers_lexical_subs_and_namesakes_stay_proxies(tmp_path: Path) -> None:
    _file(
        tmp_path,
        """sub helper {}
sub main { my sub helper {}; helper(); $cb->(); $obj->helper(); Missing::helper(); }
""",
    )
    (tmp_path / "other.pm").write_text("package Missing; sub helper {}")
    graph = parse_directory(str(tmp_path), language="auto")
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 4 and all(graph.nodes[e.target_id].kind == NodeKind.PROXY for e in calls)
    assert detect_languages(str(tmp_path)) == ["perl"]
    assert graph.source_file_counts == {"perl": 2}


def test_dependencies_and_anonymous_body_ownership(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        """use Helper qw(check);
require "one.pl";
require Other;
do 'two.pl';
require "$dynamic";
sub helper {}
my $cb = sub { if (1) { helper(); } };
""",
    )
    graph = parse_file(str(path))
    assert graph.dependencies == ["Helper", "one.pl", "Other", "two.pl"]
    anonymous = [u for u in graph.nodes.values() if u.name.startswith("<lambda>")]
    assert len(anonymous) == 1 and anonymous[0].cyclomatic_complexity == 2
    assert graph.nodes["sample:<script>"].cyclomatic_complexity == 1
    assert any(
        e.source_id == anonymous[0].id and e.target_id == "sample:helper" for e in graph.edges
    )


def test_generator_heredoc_is_data_and_scalar_undef_does_not_hide_calls(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        """sub emit { print "code"; }
my $code = <<'ASM';
sub fake { dangerous(); }
call emit
ASM
undef $code;
sub main { emit(); }
""",
    )
    graph = parse_file(str(path))
    assert not any(u.name == "fake" for u in graph.nodes.values())
    assert not any("dangerous" in e.target_id for e in graph.edges)
    assert QueryEngine.from_directory(str(tmp_path), language="perl").entrypoint_paths_to("emit")


def test_parse_errors_warn(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "broken.pl"
    path.write_text("sub broken {")
    parse_file(str(path))
    assert "Perl syntax errors" in caplog.text


def test_unsupported_regex_syntax_warns_instead_of_claiming_clean_coverage(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "regex.pl"
    path.write_text(r"while ($line =~ m/\G[^@\/\{\"]*/g) { print $line; }")
    parse_file(str(path))
    assert "Perl syntax errors" in caplog.text
