"""Call-resolution diagnostics for the patterns reported in issue #73."""

from __future__ import annotations

from pathlib import Path

import pytest

from trailmark.parse import parse_file
from trailmark.query.api import QueryEngine

SOURCE = """\
struct Engine;
impl Engine { fn execute(&self) {} }
fn make() -> Result<Engine, ()> { Ok(Engine) }
fn local(engine: &Engine) {
    engine.execute();
    make().unwrap().execute();
    Engine::execute(engine);
}
fn standard(values: Vec<u64>) {
    values.clone(); values.len();
    values.first().unwrap(); values.first().expect("missing");
}
fn iterators(values: Vec<u64>) { values.iter().map(|x| *x).collect::<Vec<_>>(); }
fn conversions(value: u64) { value.into(); value.try_into(); TokenId::try_from(value); }
fn external() { UdpSocket::bind("127.0.0.1:0"); prost_build::Config::new(); }
fn builders() { Action::builder().timestamp(0); }
enum ActionKind { Deposit(u64), Withdraw }
fn constructors() { ActionKind::Deposit(1); ActionKind::Withdraw; Ok(1); Err(()); }
"""


@pytest.fixture
def engine(tmp_path: Path) -> QueryEngine:
    path = tmp_path / "patterns.rs"
    path.write_text(SOURCE)
    return QueryEngine.from_graph(parse_file(str(path)))


@pytest.mark.parametrize(
    ("caller", "callees"),
    [
        ("local", {"execute", "unwrap"}),
        ("standard", {"clone", "len", "first", "unwrap", "expect"}),
        ("iterators", {"iter", "map", "collect"}),
        ("conversions", {"into", "try_into", "try_from"}),
        ("external", {"bind", "new"}),
        ("builders", {"builder", "timestamp"}),
        ("constructors", {"Deposit", "Ok", "Err"}),
    ],
)
def test_reported_rust_patterns_remain_visible(
    engine: QueryEngine, caller: str, callees: set[str]
) -> None:
    rows = engine.unresolved_calls(caller)
    assert {row["callee"] for row in rows} == callees
    assert all(row["source"] == f"patterns:{caller}" for row in rows)
    assert all(row["location"]["file_path"].endswith("patterns.rs") for row in rows)
    assert all(row["location"]["start_line"] >= 1 for row in rows)


def test_return_type_metadata_does_not_resolve_chained_receiver(engine: QueryEngine) -> None:
    assert engine.type_references("make")[0]["name"] == "Result<Engine, ()>"
    assert "patterns:make" in {row["id"] for row in engine.callees_of("local")}
    rows = engine.unresolved_calls("local")
    assert {row["raw_target"] for row in rows} == {
        "engine.execute",
        "make().unwrap().execute",
        "make().unwrap",
    }
    assert engine.paths_between("local", "Engine.execute") == [
        ["patterns:local", "patterns:Engine.execute"]
    ]
    # Only the explicit Engine::execute call connects this implementation.
    assert len([row for row in rows if row["callee"] == "execute"]) == 2


def test_opaque_macros_are_outside_diagnostic_denominator(tmp_path: Path) -> None:
    path = tmp_path / "opaque.rs"
    path.write_text(
        "fn helper() {} fn caller() { helper(); } opaque! { fn hidden() { unknown(); } }"
    )
    engine = QueryEngine.from_graph(parse_file(str(path)))
    report = engine.call_resolution()
    assert report["call_sites"] == report["resolved_call_sites"] == 1
    assert report["unresolved_call_sites"] == 0
    assert engine.callees_of("hidden") == []
    assert not any(node["name"] == "hidden" for node in engine.reachable_from("caller"))


def test_unresolved_value_receivers_do_not_reach_the_local_implementation(tmp_path: Path) -> None:
    path = tmp_path / "patterns.rs"
    path.write_text(SOURCE.replace("Engine::execute(engine);", ""))
    engine = QueryEngine.from_graph(parse_file(str(path)))
    assert engine.paths_between("local", "Engine.execute") == []
    assert "patterns:Engine.execute" not in {n["id"] for n in engine.reachable_from("local")}
    assert len(engine.unresolved_calls("local")) == 3
