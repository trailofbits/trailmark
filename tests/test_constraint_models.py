"""Constraint grammar rejects ambiguous units, mutable evidence, and malformed identities."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict, fields, replace
from typing import Any

import pytest

from trailmark.models import (
    ArgumentRef,
    CallArgument,
    CallSite,
    ConstraintBoolean,
    ConstraintClaim,
    ConstraintEvidence,
    ConstraintLiteral,
    ConstraintPredicate,
    ConstraintSlice,
    ConstraintSummary,
    ParameterBinding,
    ParameterRef,
    SourceLocation,
)
from trailmark.models.constraint_support import constraint_support_for

pytestmark = pytest.mark.mutation_fast

_LOCATION = SourceLocation("app.py", 1, 1, 0, 6)
_DIGEST = "0123456789abcdef" * 4
_ARGUMENT = CallArgument(0, "value", _LOCATION)
_SITE = CallSite(
    "app:caller",
    "python",
    _LOCATION,
    _DIGEST,
    "target(value)",
    (_ARGUMENT,),
    source_key=("root", "app.py"),
)
_VALUE = ConstraintLiteral("integer", 0)
_PREDICATE = ConstraintPredicate(ArgumentRef(0), "equals", (_VALUE,))
_EVIDENCE = ConstraintEvidence(_LOCATION, _DIGEST)
_CLAIM = ConstraintClaim(_SITE.id, _PREDICATE, _DIGEST, "python:text", (_EVIDENCE,))


@pytest.mark.parametrize(
    ("sort", "value"),
    [
        ("bytes", "00ff"),
        ("bytes", ""),
        ("text", "λ\x00"),
        ("integer", 2**100),
        ("integer", -1),
        ("real", 0.0),
        ("real", -1.5),
        ("boolean", False),
        ("null", None),
    ],
)
def test_literals_keep_their_sort_without_conversion(sort: Any, value: Any) -> None:
    literal = ConstraintLiteral(sort, value)
    assert asdict(literal) == {"sort": sort, "value": value}
    assert type(literal.value) is type(value)


@pytest.mark.parametrize(
    "changes",
    [
        {"sort": "unknown"},
        {"value": True},
        {"value": 1.0},
        {"sort": "bytes", "value": "FF"},
        {"sort": "bytes", "value": "f"},
        {"sort": "bytes", "value": "ff 00"},
        {"sort": "text", "value": b"text"},
        {"sort": "real", "value": 1},
        {"sort": "real", "value": float("inf")},
        {"sort": "real", "value": float("-inf")},
        {"sort": "real", "value": float("nan")},
        {"sort": "null", "value": "null"},
    ],
)
def test_literals_reject_lossy_or_untyped_values(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        replace(_VALUE, **changes)


@pytest.mark.parametrize(
    ("model", "changes"),
    [
        (ConstraintSlice(0, None, "bytes"), {"start": -1}),
        (ConstraintSlice(0, None, "bytes"), {"start": True}),
        (ConstraintSlice(1, None, "bytes"), {"stop": 0}),
        (ConstraintSlice(0, None, "bytes"), {"stop": False}),
        (ConstraintSlice(0, None, "bytes"), {"unit": "characters"}),
        (ArgumentRef(0), {"index": None}),
        (ArgumentRef(0), {"index": -1}),
        (ArgumentRef(0), {"index": True}),
        (ArgumentRef(0), {"receiver": True}),
        (ArgumentRef(0), {"receiver": 1}),
        (ArgumentRef(0), {"slices": []}),
        (ArgumentRef(0), {"slices": ("slice",)}),
        (ParameterRef(0), {"parameter": -1}),
        (ParameterRef(0), {"parameter": True}),
        (ParameterRef(0), {"slices": []}),
        (ParameterRef(0), {"slices": ("slice",)}),
    ],
)
def test_references_require_one_subject_and_explicit_immutable_slices(
    model: Any, changes: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        replace(model, **changes)


@pytest.mark.parametrize("unit", ["bytes", "codepoints", "utf16_code_units", "elements"])
def test_empty_and_open_slices_preserve_their_units(unit: Any) -> None:
    for stop in (0, None):
        part = ConstraintSlice(0, stop, unit)
        assert ArgumentRef(None, receiver=True, slices=(part,)).slices == (part,)
        assert ParameterRef(0, (part,)).slices == (part,)


@pytest.mark.parametrize("operation", ["prefix", "suffix", "charset"])
@pytest.mark.parametrize(
    ("sort", "unit", "value"), [("bytes", "bytes", "00ff"), ("text", "codepoints", "λ")]
)
def test_sequence_predicates_preserve_literal_and_slice_units(
    operation: Any, sort: Any, unit: Any, value: str
) -> None:
    subject = ArgumentRef(0, slices=(ConstraintSlice(0, None, unit),))
    literal = ConstraintLiteral(sort, value)
    predicate = ConstraintPredicate(subject, operation, (literal,), unit)
    assert predicate.values == (literal,)
    with pytest.raises(ValueError):
        replace(predicate, unit="elements")


@pytest.mark.parametrize(
    ("operation", "values"),
    [
        ("equals", (ConstraintLiteral("text", "abc"),)),
        ("one_of", (ConstraintLiteral("integer", 1),)),
        ("range", (ConstraintLiteral("integer", 1), ConstraintLiteral("integer", 3))),
        ("type", (ConstraintLiteral("text", "mapping"),)),
    ],
)
def test_sliced_bytes_reject_predicates_with_incompatible_sorts(
    operation: Any, values: tuple[ConstraintLiteral, ...]
) -> None:
    subject = ArgumentRef(0, slices=(ConstraintSlice(0, 3, "bytes"),))
    with pytest.raises(ValueError):
        ConstraintPredicate(subject, operation, values)


@pytest.mark.parametrize(
    "changes",
    [
        {"subject": "value"},
        {"values": []},
        {"values": (0,)},
        {"values": ()},
        {"operation": "regex"},
        {"operation": "one_of", "values": ()},
        {"values": (_VALUE, _VALUE)},
        {"operation": "type"},
        {"operation": "type", "values": (ConstraintLiteral("text", "resource"),)},
        {"operation": "range", "values": (_VALUE, ConstraintLiteral("integer", -1))},
        {"operation": "range", "values": (_VALUE, ConstraintLiteral("real", 1.0))},
        {"operation": "range", "values": (ConstraintLiteral("text", "x"),) * 2},
        {"operation": "length", "values": (ConstraintLiteral("integer", -1),), "unit": "bytes"},
        {"operation": "length", "values": (ConstraintLiteral("real", 1.0),), "unit": "bytes"},
        {"operation": "length"},
        {"operation": "prefix", "unit": "bytes"},
        {"unit": "bytes"},
        {
            "subject": ArgumentRef(
                0,
                slices=(ConstraintSlice(0, None, "bytes"), ConstraintSlice(0, None, "codepoints")),
            )
        },
        {
            "operation": "length",
            "subject": ArgumentRef(0, slices=(ConstraintSlice(0, None, "bytes"),)),
            "unit": "codepoints",
        },
        {
            "operation": "prefix",
            "values": (ConstraintLiteral("bytes", "ff"),),
            "unit": "codepoints",
        },
        {"operation": "prefix", "values": (ConstraintLiteral("text", "x"),), "unit": "bytes"},
    ],
)
def test_predicates_reject_ambiguous_or_inconsistent_meanings(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        replace(_PREDICATE, **changes)


@pytest.mark.parametrize(
    "sort", ["bytes", "text", "integer", "real", "boolean", "null", "sequence", "mapping"]
)
def test_type_predicates_name_the_grammar_sorts(sort: str) -> None:
    assert ConstraintPredicate(ArgumentRef(0), "type", (ConstraintLiteral("text", sort),))


def test_numeric_bounds_are_inclusive_and_finite_sets_allow_multiple_values() -> None:
    for operation, unit in (("range", None), ("length", "elements")):
        assert replace(_PREDICATE, operation=operation, values=(_VALUE, _VALUE), unit=unit)
    assert replace(_PREDICATE, operation="range", values=(ConstraintLiteral("real", -1.5),) * 2)
    assert replace(_PREDICATE, operation="one_of", values=(_VALUE, ConstraintLiteral("integer", 1)))


@pytest.mark.parametrize(
    "changes",
    [
        {"operation": "xor"},
        {"operation": "true", "terms": (_PREDICATE,)},
        {"operation": "false", "terms": (_PREDICATE,)},
        {"operation": "not"},
        {"operation": "not", "terms": (_PREDICATE, _PREDICATE)},
        {"operation": "and"},
        {"operation": "or"},
        {"terms": []},
        {"terms": (True,)},
    ],
)
def test_boolean_grammar_has_no_implicit_arity_or_truth_conversion(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        replace(ConstraintBoolean("true"), **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"file_path": ""},
        {"file_path": " "},
        {"start_line": 0},
        {"start_line": True},
        {"end_line": 0},
        {"end_line": 1.0},
        {"start_col": -1},
        {"end_col": -1},
        {"start_col": False},
        {"end_col": False},
        {"start_col": 7},
    ],
)
def test_evidence_spans_reject_invalid_coordinates(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        ConstraintEvidence(replace(_LOCATION, **changes), _DIGEST)


def test_evidence_accepts_unknown_columns_and_multiline_boundaries() -> None:
    assert ConstraintEvidence(SourceLocation("app.py", 1, 1), _DIGEST)
    assert ConstraintEvidence(SourceLocation("app.py", 1, 2, 50, 0), _DIGEST)
    assert ConstraintEvidence(SourceLocation("app.py", 1, 1, 0, 0), _DIGEST)


@pytest.mark.parametrize("digest", ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64])
def test_source_and_context_digests_require_canonical_sha256(digest: str) -> None:
    for model, name in (
        (_EVIDENCE, "source_sha256"),
        (_SITE, "source_sha256"),
        (_CLAIM, "graph_fingerprint"),
    ):
        with pytest.raises(ValueError):
            replace(model, **{name: digest})


def test_source_display_is_lossless_for_non_utf8_bytes() -> None:
    argument = replace(_ARGUMENT, expression="\\xff", expression_bytes="ff")
    site = replace(
        _SITE, syntax="target(\\xff)", syntax_bytes=b"target(\xff)".hex(), arguments=(argument,)
    )
    assert bytes.fromhex(argument.expression_bytes or "") == b"\xff"
    assert bytes.fromhex(site.syntax_bytes or "") == b"target(\xff)"
    for changes in (
        {"expression_bytes": "FF"},
        {"expression_bytes": "f"},
        {"expression_bytes": "00"},
        {"expression_bytes": 1},
        {"expression": 1},
    ):
        with pytest.raises(ValueError):
            replace(argument, **changes)


def test_call_identity_is_canonical_and_changes_with_source_context() -> None:
    assert _SITE.id == "call:32e463655b8b2cacb1c6701f"
    variants = (
        replace(_SITE, caller_id="app:other"),
        replace(_SITE, language="php"),
        replace(_SITE, location=replace(_LOCATION, start_col=1)),
        replace(_SITE, source_sha256="f" * 64),
        replace(_SITE, source_key=("dependency", "app.py")),
        replace(_SITE, source_key=("root", "nested/app.py")),
    )
    assert len({_SITE.id, *(site.id for site in variants)}) == 7
    assert replace(_SITE, syntax="another display").id == _SITE.id
    assert replace(_SITE, caller_id="root::elsewhere:caller").id == _SITE.id
    assert (
        replace(_SITE, location=replace(_LOCATION, file_path="/other/checkout/app.py")).id
        == _SITE.id
    )


@pytest.mark.parametrize(
    "key",
    [
        None,
        [],
        ("root",),
        ("root", "app.py", "extra"),
        ("", "app.py"),
        ("root", 1),
        ("bad:root", "app.py"),
        ("root", "/app.py"),
        ("root", "../app.py"),
        ("root", "."),
        ("root", "nested//app.py"),
    ],
)
def test_call_site_source_keys_require_a_named_root_and_normalized_relative_file(key: Any) -> None:
    with pytest.raises(ValueError):
        replace(_SITE, source_key=key)


@pytest.mark.parametrize(
    ("unit", "sort", "value"),
    [
        ("bytes", "bytes", "616263"),
        ("codepoints", "text", "abc"),
        ("utf16_code_units", "text", "abc"),
    ],
)
@pytest.mark.parametrize("operation", ["equals", "one_of"])
def test_sliced_sequence_literals_keep_the_corresponding_sort(
    unit: Any, sort: Any, value: str, operation: Any
) -> None:
    subject = ParameterRef(0, slices=(ConstraintSlice(0, 3, unit),))
    predicate = ConstraintPredicate(subject, operation, (ConstraintLiteral(sort, value),))
    assert predicate.subject == subject


@pytest.mark.parametrize("unit", ["bytes", "codepoints", "utf16_code_units", "elements"])
def test_slice_can_be_constrained_as_a_sequence(unit: Any) -> None:
    subject = ArgumentRef(0, slices=(ConstraintSlice(0, None, unit),))
    predicate = ConstraintPredicate(subject, "type", (ConstraintLiteral("text", "sequence"),))
    assert predicate.subject == subject


def test_type_predicates_still_reject_incompatible_nested_slice_units() -> None:
    subject = ArgumentRef(
        0,
        slices=(
            ConstraintSlice(0, 3, "bytes"),
            ConstraintSlice(0, 1, "codepoints"),
        ),
    )
    with pytest.raises(ValueError, match="units differ"):
        ConstraintPredicate(subject, "type", (ConstraintLiteral("text", "sequence"),))


def test_unverified_and_alias_frontend_profiles_remain_explicit() -> None:
    assert constraint_support_for("csharp") == constraint_support_for("c_sharp")
    profile = constraint_support_for("imported-custom-language")
    assert profile.language == "imported-custom-language"
    assert (
        profile.call_sites,
        profile.argument_syntax,
        profile.parameter_binding,
        profile.automatic_inference,
    ) == ("unverified",) * 4
    with pytest.raises(FrozenInstanceError):
        field = fields(profile)[0]
        setattr(profile, field.name, "changed")
    assert ParameterBinding("value", "positional_only").value_preserving is False


@pytest.mark.parametrize(
    ("model", "changes"),
    [
        (_ARGUMENT, {"index": -1}),
        (_ARGUMENT, {"index": True}),
        (_ARGUMENT, {"expression": ""}),
        (_ARGUMENT, {"kind": "reference"}),
        (_ARGUMENT, {"name": ""}),
        (_ARGUMENT, {"kind": "named"}),
        (_ARGUMENT, {"kind": "named", "name": " "}),
        (_SITE, {"caller_id": " "}),
        (_SITE, {"language": ""}),
        (_SITE, {"execution": "immediate"}),
        (_SITE, {"syntax": 1}),
        (_SITE, {"arguments": []}),
        (_SITE, {"arguments": (_VALUE,)}),
        (_SITE, {"arguments": (replace(_ARGUMENT, index=1),)}),
        (_SITE, {"receiver": "self"}),
        (_SITE, {"gaps": []}),
        (_SITE, {"gaps": ("",)}),
        (_SITE, {"gaps": ("gap", "gap")}),
        (ParameterBinding("value", "positional_only"), {"name": " "}),
        (ParameterBinding("value", "positional_only"), {"kind": "named"}),
        (ParameterBinding("value", "positional_only"), {"value_preserving": 1}),
    ],
)
def test_call_evidence_requires_stable_and_immutable_structure(
    model: Any, changes: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        replace(model, **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"call_site_id": " "},
        {"semantics": " "},
        {"predicate": True},
        {"evidence": ()},
        {"evidence": [_EVIDENCE]},
        {"evidence": (_VALUE,)},
        {"assumptions": ["deployment"]},
        {"assumptions": ("",)},
        {"assumptions": (" ",)},
        {"assumptions": (1,)},
        {"assumptions": ("deployment", "deployment")},
        {"justification": "proved"},
        {"grammar_version": True},
        {"grammar_version": 0},
        {"grammar_version": 2},
    ],
)
def test_claims_cannot_omit_context_or_promote_assertions_to_proofs(
    changes: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        replace(_CLAIM, **changes)


@pytest.mark.parametrize(
    "model",
    [
        _VALUE,
        ConstraintSlice(0, None, "bytes"),
        ArgumentRef(0),
        ParameterRef(0),
        _PREDICATE,
        ConstraintBoolean("true"),
        _ARGUMENT,
        _SITE,
        ParameterBinding("value", "positional_only"),
        _EVIDENCE,
        _CLAIM,
        ConstraintSummary("app:target", 0, _PREDICATE, (), (), (), (), "unknown", _DIGEST),
    ],
)
def test_constraint_values_are_frozen_and_hashable(model: Any) -> None:
    field = fields(model)[0]
    with pytest.raises(FrozenInstanceError):
        setattr(model, field.name, getattr(model, field.name))
    assert hash(model) == hash(model)
