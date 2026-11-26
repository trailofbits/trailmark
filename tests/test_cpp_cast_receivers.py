"""Reference casts to an injected class name provide bounded member navigation."""

from pathlib import Path

import pytest

from trailmark.models.edges import EdgeKind
from trailmark.models.nodes import NodeOrigin
from trailmark.parse import parse_directory
from trailmark.query.api import QueryEngine


@pytest.mark.mutation_fast
def test_out_of_line_member_cast_to_own_class_navigates_across_header(tmp_path: Path) -> None:
    (tmp_path / "stream.hpp").write_text(
        "namespace detail { struct Base {}; } class Stream : public detail::Base { public: "
        "void flush() {} static void grow(detail::Base& value); };"
    )
    (tmp_path / "stream.cpp").write_text(
        '#include "stream.hpp"\nvoid Stream::grow(detail::Base& value) { '
        "static_cast<Stream&>(value).flush(); }"
    )
    graph = parse_directory(str(tmp_path), "cpp", strict=True)
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert graph.nodes[call.target_id].name == "flush" and call.confidence.value == "uncertain"
    assert graph.nodes[call.target_id].location.file_path.endswith("stream.hpp")
    assert dict(call.attributes)["cpp_cast_dispatch_uncertain"] is True
    assert call.location is not None and call.location.start_line == 2
    assert {n["id"] for n in QueryEngine.from_graph(graph).callers_of(call.target_id)} == {
        call.source_id
    }


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "declarations,body",
    [
        ("void flush() {} void flush(int);", "static_cast<Stream&>(value).flush(1);"),
        ("void flush() {} int* flush(int);", "static_cast<Stream&>(value).flush(1);"),
        ("void flush(), flush(int);", "static_cast<Stream&>(value).flush(1);"),
        ("void flush() {}", "using Stream = Other; static_cast<Stream&>(value).flush();"),
        ("void flush() {}", "typedef Other Stream; static_cast<Stream&>(value).flush();"),
        (
            "void flush() {}",
            "struct Stream : detail::Base { void flush() {} }; "
            "static_cast<Stream&>(value).flush();",
        ),
        ("void flush() {}", "static_cast<Other&>(value).flush();"),
        ("void flush() {}", "reinterpret_cast<Stream&>(value).flush();"),
        ("void flush() {}", "static_cast<Stream&&>(value).flush();"),
        ("void flush() const {}", "static_cast<const Stream&>(value).flush();"),
    ],
)
def test_incomplete_or_shadowed_casts_stay_uncertain(
    tmp_path: Path, declarations: str, body: str
) -> None:
    (tmp_path / "stream.cpp").write_text(
        "namespace detail { struct Base {}; } struct Other : detail::Base { void flush() {} }; "
        "class Stream : detail::Base { "
        + declarations
        + " void grow(detail::Base& value) { "
        + body
        + " } };"
    )
    graph = parse_directory(str(tmp_path), "cpp", strict=True)
    calls = [
        e
        for e in graph.edges
        if e.kind == EdgeKind.CALLS and not dict(e.attributes).get("deferred")
    ]
    assert calls and all(graph.nodes[e.target_id].origin == NodeOrigin.PROXY for e in calls)


def test_generic_class_casts_stay_uncertain(tmp_path: Path) -> None:
    (tmp_path / "stream.cpp").write_text(
        "template<typename T> class Stream { void flush() {} "
        "void grow(Stream& value) { static_cast<Stream&>(value).flush(); } };"
    )
    graph = parse_directory(str(tmp_path), "cpp", strict=True)
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


def test_conditional_prototype_overload_counts_as_an_unseen_candidate(tmp_path: Path) -> None:
    (tmp_path / "stream.cpp").write_text(
        "class Stream { void flush() {}\n#if EXTRA\nvoid flush(int);\n#endif\n"
        "void grow(Stream& value) { static_cast<Stream&>(value).flush(1); } };"
    )
    graph = parse_directory(str(tmp_path), "cpp", strict=True)
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


def test_later_duplicate_definition_retracts_per_file_selection(tmp_path: Path) -> None:
    (tmp_path / "stream.cpp").write_text(
        "class Stream { void flush() {} void grow(Stream& value) { "
        "static_cast<Stream&>(value).flush(); } };"
    )
    (tmp_path / "extra.cpp").write_text("void Stream::flush() {}")
    graph = parse_directory(str(tmp_path), "cpp", strict=True)
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


@pytest.mark.mutation_fast
@pytest.mark.parametrize("base", ["struct Base { virtual void flush() {} };", "struct Base;"])
def test_inherited_virtual_or_unknown_base_keeps_declared_target_uncertain(
    tmp_path: Path, base: str
) -> None:
    (tmp_path / "stream.cpp").write_text(
        "namespace detail { " + base + " } class Stream : detail::Base { void flush() {} "
        "void grow(detail::Base& value) { static_cast<Stream&>(value).flush(); } };"
    )
    graph = parse_directory(str(tmp_path), "cpp", strict=True)
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert graph.nodes[call.target_id].name == "flush"
    assert graph.nodes[call.target_id].origin != NodeOrigin.PROXY
    assert call.confidence.value == "uncertain"
    assert dict(call.attributes)["cpp_cast_dispatch_uncertain"] is True


def test_inspected_primitive_template_base_without_member_allows_navigation(tmp_path: Path) -> None:
    (tmp_path / "stream.cpp").write_text(
        "namespace detail { template<typename T> struct Base {}; } "
        "class Stream : detail::Base<char> { void flush() {} "
        "void grow(detail::Base<char>& value) { static_cast<Stream&>(value).flush(); } };"
    )
    graph = parse_directory(str(tmp_path), "cpp", strict=True)
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert graph.nodes[call.target_id].origin != NodeOrigin.PROXY


def test_specialized_base_keeps_declared_member_dispatch_uncertain(tmp_path: Path) -> None:
    (tmp_path / "stream.cpp").write_text(
        "namespace detail { template<typename T> struct Base {}; "
        "template<> struct Base<char> { virtual void flush() {} }; } "
        "class Stream : detail::Base<char> { void flush() {} "
        "void grow(detail::Base<char>& value) { static_cast<Stream&>(value).flush(); } };"
    )
    graph = parse_directory(str(tmp_path), "cpp", strict=True)
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert graph.nodes[call.target_id].origin != NodeOrigin.PROXY
    assert call.confidence.value == "uncertain"
    assert dict(call.attributes)["cpp_cast_dispatch_uncertain"] is True


@pytest.mark.mutation_fast
def test_member_using_declaration_keeps_family_conservative(tmp_path: Path) -> None:
    (tmp_path / "stream.cpp").write_text(
        "namespace detail { struct Base { void other(); }; } "
        "class Stream : detail::Base { using detail::Base::other; void flush() {} "
        "void grow(detail::Base& value) { static_cast<Stream&>(value).flush(); } };"
    )
    graph = parse_directory(str(tmp_path), "cpp", strict=True)
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert graph.nodes[call.target_id].origin == NodeOrigin.PROXY


def test_nearer_base_alias_keeps_declared_member_dispatch_uncertain(tmp_path: Path) -> None:
    (tmp_path / "stream.cpp").write_text(
        "namespace detail { struct Base {}; } "
        "namespace Other { struct Base { virtual void flush() {} }; } "
        "namespace N { namespace detail = Other; class Stream : detail::Base { void flush() {} "
        "void grow(detail::Base& value) { static_cast<Stream&>(value).flush(); } }; }"
    )
    graph = parse_directory(str(tmp_path), "cpp", strict=True)
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert graph.nodes[call.target_id].origin != NodeOrigin.PROXY
    assert call.confidence.value == "uncertain"
    assert dict(call.attributes)["cpp_cast_dispatch_uncertain"] is True


@pytest.mark.mutation_fast
@pytest.mark.parametrize("virtual", [False, True])
def test_no_base_plain_or_explicit_virtual_dispatch_confidence(
    tmp_path: Path, virtual: bool
) -> None:
    modifier = "virtual " if virtual else ""
    (tmp_path / "stream.cpp").write_text(
        "class Stream { " + modifier + "void flush() {} "
        "void grow(Stream& value) { static_cast<Stream&>(value).flush(); } };"
    )
    graph = parse_directory(str(tmp_path), "cpp", strict=True)
    call = next(e for e in graph.edges if e.kind == EdgeKind.CALLS)
    assert graph.nodes[call.target_id].origin != NodeOrigin.PROXY
    assert call.confidence.value == ("uncertain" if virtual else "inferred")
    assert bool(dict(call.attributes).get("cpp_cast_dispatch_uncertain")) == virtual
