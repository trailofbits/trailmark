"""Authentication facts distinguish gate evaluation, rejection and success ranges."""

from typing import Any, cast

import pytest

from trailmark.models.endpoints import RegistrationEvidence
from trailmark.models.execution import ExecutionFlow, ExecutionPoint
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind, SourceLocation
from trailmark.query.api import QueryEngine


def _point(line: int, role: str = "unknown") -> ExecutionPoint:
    return ExecutionPoint("views:login", SourceLocation("views.py", line, line), cast(Any, role))


def _engine() -> QueryEngine:
    graph = CodeGraph()
    graph.nodes["views:login"] = CodeUnit(
        "views:login", "login", NodeKind.FUNCTION, SourceLocation("views.py", 1, 20)
    )
    return QueryEngine.from_graph(graph)


def _flow(
    name: str,
    start: ExecutionPoint,
    end: ExecutionPoint,
    kind: str = "dispatch",
    identity: str = "none",
    reviewed: bool = True,
) -> ExecutionFlow:
    from typing import Any, cast

    return ExecutionFlow(
        name,
        start,
        end,
        cast(Any, kind),
        (RegistrationEvidence("manual", start.location, "reviewed fixture"),),
        identity=cast(Any, identity),
        confidence="certain",
        reviewed=reviewed,
    )


def test_success_only_range_after_user_gate_and_rejection_pre_auth() -> None:
    engine = _engine()
    root, gate = _point(1, "credential"), _point(4, "gate")
    rejected, accepted = _point(5, "rejection"), _point(8, "continuation")
    engine.add_execution_flow(_flow("parse", root, gate, "credential"))
    engine.add_execution_flow(_flow("reject", gate, rejected, "gate_reject"))
    engine.add_execution_flow(_flow("accept", gate, accepted, "gate_success", "user"))
    assert engine.authentication_paths((root,), gate)["classification"] == "pre_auth"
    assert engine.authentication_paths((root,), rejected)["classification"] == "pre_auth"
    result = engine.authentication_paths((root,), accepted)
    assert result["classification"] == "post_auth"
    assert result["target"]["location"]["start_line"] == 8
    assert engine.execution_evidence("missing") == []


def test_public_and_protected_paths_prove_mixed_and_unknown_dispatch_abstains() -> None:
    engine = _engine()
    root, gate, accepted, helper = _point(1), _point(3, "gate"), _point(6), _point(15)
    engine.add_execution_flow(_flow("public", root, helper))
    engine.add_execution_flow(_flow("gate", root, gate))
    engine.add_execution_flow(_flow("accept", gate, accepted, "gate_success", "user"))
    engine.add_execution_flow(_flow("private", accepted, helper))
    assert engine.authentication_paths((root,), helper)["classification"] == "mixed"
    uncertain = _flow("dynamic", root, helper, reviewed=False)
    engine.add_execution_flow(uncertain)
    assert engine.authentication_paths((root,), helper)["classification"] == "unknown"


@pytest.mark.parametrize("identity", ["capability", "csrf", "none"])
def test_non_user_gates_do_not_establish_authentication(identity: str) -> None:
    engine = _engine()
    gate, accepted = _point(4, "gate"), _point(8)
    engine.add_execution_flow(_flow("gate", gate, accepted, "gate_success", identity))
    assert engine.authentication_paths((gate,), accepted)["classification"] == "pre_auth"


def test_empty_roots_and_invalid_ranges_fail_closed() -> None:
    engine = _engine()
    with pytest.raises(ValueError, match="pre-auth root"):
        engine.authentication_paths((), _point(2))
    with pytest.raises(ValueError, match="inside"):
        engine.add_execution_flow(_flow("invalid", _point(1), _point(30)))
    with pytest.raises(ValueError, match="rejection sink"):
        _flow("bad", _point(4, "rejection"), _point(7))
    with pytest.raises(ValueError, match="continuation"):
        _flow("bad-success", _point(4, "gate"), _point(5, "rejection"), "gate_success", "user")


def test_raw_graph_execution_facts_are_validated_at_store_construction() -> None:
    graph = CodeGraph()
    graph.add_execution_flow(_flow("missing", _point(1), _point(2)))
    with pytest.raises(ValueError, match="Unknown execution node"):
        QueryEngine.from_graph(graph)


def test_middleware_order_bounds_and_registration_reference() -> None:
    from dataclasses import replace

    engine = _engine()
    root, middleware, target = _point(1), _point(2, "middleware"), _point(5)
    first = replace(_flow("first", root, middleware, "middleware"), order=0)
    second = replace(_flow("second", middleware, target, "middleware"), order=1)
    engine.add_execution_flow(first)
    engine.add_execution_flow(second)
    result = engine.authentication_paths((root,), target)
    assert result["paths"][0]["flow_ids"] == ["first", "second"]
    assert engine.authentication_paths((root,), target, max_steps=1)["classification"] == "unknown"
    assert engine.authentication_paths((root,), target, max_depth=1)["truncated"]
    with pytest.raises(ValueError, match="registration"):
        engine.add_execution_flow(replace(first, id="invalid", registration_id="missing"))
    with pytest.raises(ValueError, match="role"):
        _point(1, "post_auth")
