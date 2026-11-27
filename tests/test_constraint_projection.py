"""Projection may weaken a claim but must never exclude a satisfying input."""

from __future__ import annotations

from itertools import product

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trailmark.analysis.constraints import _project
from trailmark.models.constraints import (
    ArgumentRef,
    ConstraintBoolean,
    ConstraintExpr,
    ConstraintLiteral,
    ConstraintPredicate,
    ParameterRef,
)

pytestmark = pytest.mark.mutation_fast

_ATOMS: tuple[ConstraintExpr, ...] = (
    ConstraintBoolean("true"),
    ConstraintBoolean("false"),
    *(
        ConstraintPredicate(ArgumentRef(index), "equals", (ConstraintLiteral("boolean", value),))
        for index, value in product(range(2), (False, True))
    ),
)


def _negate(expression: ConstraintExpr) -> ConstraintExpr:
    return ConstraintBoolean("not", (expression,))


def _and(terms: tuple[ConstraintExpr, ConstraintExpr]) -> ConstraintExpr:
    return ConstraintBoolean("and", terms)


def _or(terms: tuple[ConstraintExpr, ConstraintExpr]) -> ConstraintExpr:
    return ConstraintBoolean("or", terms)


def _evaluate(expression: ConstraintExpr, values: tuple[bool, ...]) -> bool:
    if isinstance(expression, ConstraintPredicate):
        subject = expression.subject
        index = subject.parameter if isinstance(subject, ParameterRef) else subject.index
        assert index is not None
        return values[index] == expression.values[0].value
    if expression.operation in {"true", "false"}:
        return expression.operation == "true"
    results = [_evaluate(term, values) for term in expression.terms]
    if expression.operation == "not":
        return not results[0]
    return all(results) if expression.operation == "and" else any(results)


@given(
    st.recursive(
        st.sampled_from(_ATOMS),
        lambda children: st.one_of(
            children.map(_negate),
            st.tuples(children, children).map(_and),
            st.tuples(children, children).map(_or),
        ),
        max_leaves=20,
    )
)
def test_projection_preserves_every_possible_selected_input(expression: ConstraintExpr) -> None:
    projected, _ = _project(expression, 0, 0)
    for values in product((False, True), repeat=2):
        if _evaluate(expression, values):
            assert _evaluate(projected, values)


@given(
    st.recursive(
        st.sampled_from(_ATOMS[:4]),
        lambda children: st.one_of(
            children.map(_negate),
            st.tuples(children, children).map(_and),
            st.tuples(children, children).map(_or),
        ),
        max_leaves=20,
    )
)
def test_projection_retains_exact_facts_about_the_selected_input(
    expression: ConstraintExpr,
) -> None:
    projected, precise = _project(expression, 0, 0)
    assert precise
    for value in (False, True):
        assert _evaluate(projected, (value,)) == _evaluate(expression, (value,))
