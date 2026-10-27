"""Native evidence invariants, atomic storage, and bounded host tools."""

from __future__ import annotations

import sys
import time
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from trailmark.analysis.native_binary import _restore_functions
from trailmark.analysis.native_binary.decoder import _tail_calls
from trailmark.analysis.native_binary.formats import Function, Image, Span, Target
from trailmark.analysis.native_binary.mapping import SourceResolver
from trailmark.analysis.native_binary.tools import (
    branch_target,
    call_target,
    instruction_rows,
    run_tool,
)
from trailmark.models import (
    BinaryArtifact,
    BinaryImportOptions,
    BinaryInstruction,
    BinaryLimits,
    BinaryRange,
    BinarySelection,
    BinarySourceMapping,
    CodeGraph,
    CodeUnit,
    NodeKind,
    NodeOrigin,
    SourceLocation,
)
from trailmark.query.api import QueryEngine
from trailmark.storage.binary_index import IntervalIndex
from trailmark.storage.graph_store import GraphStore


@pytest.mark.parametrize(
    "architecture,mnemonic,operands,expected",
    [
        ("x86_64", "jmp", "200 <callee>", 0x200),
        ("i386", "jmp", "*%eax", None),
        ("x86_64", "je", "200", None),
        ("arm", "b.w", "0x200", 0x200),
        ("arm", "bx", "lr", None),
        ("aarch64", "b", "0x200", 0x200),
        ("aarch64", "b.eq", "0x200", None),
        ("aarch64", "br", "x16", None),
        ("riscv32", "c.j", "200", 0x200),
        ("riscv64", "jal", "zero, 200", 0x200),
        ("riscv64", "jal", "ra, 200", None),
        ("ppc64", "b", "200", 0x200),
        ("ppc64le", "ba", "200", 0x200),
        ("s390x", "brcl", "15, 200", 0x200),
        ("s390x", "brcl", "8, 200", None),
        ("mips", "j", "200", 0x200),
        ("mips64el", "b", "200", 0x200),
        ("loongarch64", "b", "0x200", 0x200),
    ],
)
def test_tail_branch_recognition_is_conservative(
    architecture: str, mnemonic: str, operands: str, expected: int | None
) -> None:
    target, symbol = branch_target(architecture, mnemonic, operands)
    assert target == expected
    assert symbol == ("callee" if "<callee>" in operands else None)


def test_tail_inference_requires_another_entry_and_an_established_owner(tmp_path: Path) -> None:
    image = Image(
        None,
        "ELF",
        Target("aarch64", 64, "little", "aarch64"),
        tmp_path / "image",
        "a" * 64,
        0,
        0x500,
        0,
        0,
        [Span(0x100, 0x500, 0)],
        [Span(0x100, 0x500, 0)],
        [
            Function(0x100, 0x20, ("caller",)),
            Function(0x200, 0x20, ("callee",)),
            Function(0x220, 0x20, ("caller.cold",)),
            Function(0x240, 0x20, ("caller",)),  # A distinct same-named function.
            Function(0x300, 0x20, ("stub",), "import_stub", "external"),
            Function(0x400, 0x20, ("resolver",), "linker_resolver"),
        ],
        [],
        False,
    )

    def instruction(address: int, mnemonic: str, operands: str) -> BinaryInstruction:
        return BinaryInstruction(
            address, 4, "00000000", mnemonic, operands, address, address - 0x100
        )

    rows = [
        instruction(0x100, "b", "0x110"),  # Local branch, not an entry.
        instruction(0x104, "b", "0x100"),  # Same entry.
        instruction(0x108, "b", "0x200"),
        instruction(0x10C, "b.ne", "0x200"),
        instruction(0x110, "br", "x16"),
        instruction(0x114, "b", "0x220"),  # GCC-style cold fragment.
        instruction(0x118, "b", "0x240"),
        instruction(0x220, "b", "0x100"),  # Rejoin its hot fragment.
        instruction(0x150, "b", "0x200"),  # Outside the caller's extent.
        instruction(0x300, "b", "0x200"),
        instruction(0x400, "b", "0x200"),
    ]
    result = _tail_calls(image, rows)
    assert [(r.address, r.target) for r in result if r.call_kind] == [
        (0x108, 0x200),
        (0x118, 0x240),
    ]


def test_restored_debug_function_spans_all_ranges() -> None:
    graph = evidence()
    node = graph.nodes["bin.image:sub_1000"]
    graph.nodes[node.id] = replace(node, attributes=(("address", 0x1000), ("import_symbol", None)))
    graph.binary_ranges["last"] = BinaryRange("last", "image", node.id, 0x1030, 0x1040, 0x30, 0x30)
    image = Image(
        None,
        "ELF",
        Target("aarch64", 64, "little", "aarch64"),
        Path("unused"),
        "a" * 64,
        0,
        64,
        0,
        0x1000,
        [Span(0x1000, 0x1040, 0)],
        [Span(0x1000, 0x1040, 0)],
        [],
        [],
        False,
    )
    _restore_functions(image, list(graph.binary_ranges.values()), GraphStore(graph))
    assert [(f.address, f.size, f.import_symbol) for f in image.functions] == [(0x1000, 0x40, "")]


def evidence() -> CodeGraph:
    source = CodeUnit("a:helper", "helper", NodeKind.FUNCTION, SourceLocation("a.c", 1, 10))
    binary = CodeUnit(
        "bin.image:sub_1000",
        "helper",
        NodeKind.FUNCTION,
        SourceLocation("image", 0, 0),
        origin=NodeOrigin.BINARY,
    )
    artifact = BinaryArtifact(
        "image", "image", "a" * 64, "ELF", "x86_64", 64, "little", 0x1000, 0, 64
    )
    row = BinaryRange("range", "image", binary.id, 0x1000, 0x1010, 0, 16)
    first = BinarySourceMapping("first", row.id, source.id, 0x1001, 0x1003, "debug", "a.c", 2)
    second = BinarySourceMapping("second", row.id, source.id, 0x1008, 0x100A, "debug", "a.c", 5)
    return CodeGraph(
        nodes={source.id: source, binary.id: binary},
        binary_artifacts={artifact.id: artifact},
        binary_ranges={row.id: row},
        binary_source_mappings={first.id: first, second.id: second},
    )


def test_binary_evidence_is_frozen_and_discontiguous_source_spans_stay_separate() -> None:
    graph = evidence()
    row = graph.binary_ranges["range"]
    with pytest.raises(FrozenInstanceError):
        row.start = 1  # ty: ignore[invalid-assignment]
    engine = QueryEngine.from_graph(graph)
    assert [(row["start"], row["end"]) for row in engine.binary_ranges("a:helper")] == [
        (0x1001, 0x1003),
        (0x1008, 0x100A),
    ]
    assert [(row["start"], row["end"]) for row in engine.binary_ranges("a:helper", line=5)] == [
        (0x1008, 0x100A)
    ]
    assert engine.binary_ranges("bin.image:sub_1000")[0]["start"] == 0x1000
    assert engine.source_at_address("image", 0x1003) == []
    assert engine.source_at_address("image", 0x1009)[0]["line"] == 5


@pytest.mark.parametrize("broken", ["range", "mapping", "artifact", "node"])
def test_invalid_native_commit_preserves_all_existing_state(broken: str) -> None:
    engine = QueryEngine.from_graph(evidence())
    before = engine.to_json()
    incoming = evidence()
    if broken == "range":
        incoming.binary_ranges["range"] = replace(incoming.binary_ranges["range"], file_offset=100)
    elif broken == "mapping":
        incoming.binary_source_mappings["first"] = replace(
            incoming.binary_source_mappings["first"], source_node_id="missing"
        )
    elif broken == "artifact":
        incoming.binary_artifacts["image"] = replace(
            incoming.binary_artifacts["image"], sha256="b" * 64
        )
    else:
        incoming.nodes["bin.image:sub_1000"] = replace(
            incoming.nodes["bin.image:sub_1000"], origin=NodeOrigin.SOURCE
        )
    with pytest.raises(ValueError):
        engine._store.add_binary_graph(incoming)  # noqa: SLF001
    assert engine.to_json() == before
    assert engine.source_at_address("image", 0x1001)[0]["source_node_id"] == "a:helper"


def test_codegraph_merge_checks_binary_identity_before_mutating_source_nodes() -> None:
    graph, incoming = evidence(), evidence()
    incoming.nodes["new:node"] = CodeUnit(
        "new:node", "node", NodeKind.MODULE, SourceLocation("new.c", 1, 2)
    )
    incoming.binary_artifacts["image"] = replace(
        incoming.binary_artifacts["image"], sha256="b" * 64
    )
    with pytest.raises(ValueError, match="identity"):
        graph.merge(incoming)
    assert "new:node" not in graph.nodes


def test_overlapping_interval_index_retains_nested_frames_and_exclusive_ends() -> None:
    rows = [
        BinarySelection(0, 100),
        BinarySelection(10, 20),
        BinarySelection(11, 12),
        BinarySelection(100, 110),
    ]
    index = IntervalIndex(rows)
    assert index.containing(11, 12) == rows[:3]
    assert index.containing(20, 21) == rows[:1]
    assert index.containing(100, 101) == rows[3:]
    assert index.overlapping(20, 21) == rows[:1]
    assert index.containing(9, 11) == rows[:1]
    assert index.containing(19, 21) == rows[:1]


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, "10"])
def test_binary_limits_reject_invalid_budgets(value: object) -> None:
    with pytest.raises(ValueError):
        BinaryLimits(tool_timeout=value)  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize(
    "start,end,kind",
    [(True, 2, "va"), (-1, 2, "va"), (2, 2, "va"), (3, 2, "va"), (0, 1, "runtime")],
)
def test_binary_selection_rejects_ambiguous_or_invalid_addresses(
    start: int, end: int, kind: str
) -> None:
    with pytest.raises(ValueError):
        BinarySelection(start, end, kind)


def test_import_options_reject_mutable_or_unsupported_configuration() -> None:
    with pytest.raises(ValueError):
        BinaryImportOptions(backend="other")  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError):
        BinaryImportOptions(path_mappings=[("a", "b")])  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError):
        BinaryImportOptions(slice_index=True)
    with pytest.raises(ValueError):
        BinaryImportOptions(limits=None)  # ty: ignore[invalid-argument-type]


def test_host_tool_output_budget_counts_both_pipes() -> None:
    limits = BinaryLimits(max_output_bytes=128)
    result = run_tool(
        [sys.executable, "-c", "import os; os.write(1,b'a'*100); os.write(2,b'b'*100)"],
        limits,
        time.monotonic() + 5,
    )
    assert result.diagnostic is not None
    assert result.diagnostic.code == "output_limit"
    assert len(result.stdout) + len(result.stderr) == 128


def test_host_tool_timeout_terminates_the_process() -> None:
    result = run_tool(
        [sys.executable, "-c", "import time; time.sleep(20)"],
        BinaryLimits(tool_timeout=0.05),
        time.monotonic() + 5,
    )
    assert result.diagnostic is not None
    assert result.diagnostic.code == "timeout"
    assert result.returncode != 0


@pytest.mark.parametrize(
    "architecture,mnemonic,operands,target",
    [
        ("x86_64", "callq", "0x1234 <helper>", 0x1234),
        ("arm", "blx", "0x1234 <helper>", 0x1234),
        ("aarch64", "bl", "0x1234 <helper>", 0x1234),
        ("riscv64", "jal", "ra,0x1234 <helper>", 0x1234),
        ("ppc64", "bl", "0x1234 <helper>", 0x1234),
        ("s390x", "brasl", "%r14,1234 <helper>", 0x1234),
        ("mips64el", "jal", "0x1234 <helper>", 0x1234),
        ("loongarch64", "bl", "0x1234 <helper>", 0x1234),
    ],
)
def test_call_targets_use_addresses_and_do_not_depend_on_symbol_comments(
    architecture: str,
    mnemonic: str,
    operands: str,
    target: int,
) -> None:
    assert call_target(architecture, mnemonic, operands)[1] == target
    assert call_target(architecture, mnemonic, operands.split(" <")[0])[1] == target


def test_register_calls_and_return_jumps_are_distinct() -> None:
    assert call_target("arm", "blx", "r3")[:2] == ("indirect", None)
    assert call_target("x86_64", "callq", "*%rax")[:2] == ("indirect", None)
    assert call_target("riscv64", "jalr", "zero, 0(ra)")[0] is None
    assert call_target("loongarch64", "jirl", "$zero, $ra, 0")[0] is None
    assert call_target("s390x", "brasl", "%r0,1234")[0] is None
    assert call_target("arm", "blne", "0x1000")[1] == 0x1000
    assert call_target("arm", "bls", "0x1000")[0] is None
    assert call_target("arm", "blxne", "r3")[:2] == ("indirect", None)


def test_decoder_rows_support_word_encodings_and_tabbed_operands() -> None:
    output = (
        b"  1000: 4903        \tldr\tr1, [pc, #12]\n  1002:\tea80 00e0 \teor.w\tr0, r0\n"
        b" 1006: 4fffd821 \tjirl \t$ra, $ra, -40\n 100a: R_ARM_CALL helper\n"
    )
    assert instruction_rows(output) == [
        (0x1000, 2, "ldr", "r1, [pc, #12]"),
        (0x1002, 4, "eor.w", "r0, r0"),
        (0x1006, 4, "jirl", "$ra, $ra, -40"),
    ]


def test_source_resolution_preserves_duplicate_symbol_ambiguity() -> None:
    graph = CodeGraph(
        nodes={
            f"{prefix}:same": CodeUnit(
                f"{prefix}:same", "same", NodeKind.FUNCTION, SourceLocation(f"{prefix}.c", 1, 10)
            )
            for prefix in ("a", "b")
        },
        root_path="/checkout",
    )
    resolver = SourceResolver(graph, (("/build", "/checkout"),))
    assert resolver.symbol(("same",)) is None
    assert resolver.location("/build/a.c", 3) == "a:same"
    assert resolver.location("/other/a.c", 3) is None
    assert resolver.location("/build/a.c", 0) is None


def test_empty_store_queries_need_no_native_dependency() -> None:
    store = GraphStore(CodeGraph())
    assert store.binary_artifacts() == []
    assert store.binary_ranges_for_node("missing") == []
    assert store.source_at_address("missing", 0) == []


def test_automatic_tools_prefer_compatible_llvm_even_named_objdump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trailmark.analysis.native_binary import tools

    tools_by_name = {
        "llvm-objdump": tools.Tool("/llvm", "llvm", "LLVM", "    x86 - 32-bit X86\n"),
        "objdump": tools.Tool("/apple", "llvm", "Apple LLVM", "    systemz - SystemZ\n"),
        "gobjdump": tools.Tool("/gnu", "gnu", "GNU"),
    }
    monkeypatch.setattr(tools.shutil, "which", lambda name: name if name in tools_by_name else None)
    monkeypatch.setattr(tools, "identify_tool", lambda name, *_: tools_by_name[name])
    monkeypatch.setattr(
        tools, "run_tool", lambda *_: tools.ToolResult(b"elf64-s390\n  s390:64-bit\n", b"", 0)
    )
    deadline = time.monotonic() + 10
    assert tools.select_objdump(BinaryImportOptions(), deadline, "s390x", "ELF").path == "/apple"
    assert (
        tools.select_objdump(BinaryImportOptions(backend="gnu"), deadline, "s390x", "ELF").path
        == "/gnu"
    )
    assert (
        tools.select_objdump(
            BinaryImportOptions(objdump="llvm-objdump"), deadline, "s390x", "ELF"
        ).path
        == "/llvm"
    )
    with pytest.raises(ValueError, match="backend"):
        tools.select_objdump(
            BinaryImportOptions(objdump="objdump", backend="gnu"), deadline, "s390x", "ELF"
        )


def test_optional_lief_dependency_has_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from trailmark.analysis.native_binary import formats

    def missing(_: str) -> None:
        raise ImportError("missing optional dependency")

    monkeypatch.setattr(formats.importlib, "import_module", missing)
    with pytest.raises(ValueError, match=r"trailmark\[binary\]"):
        formats.lief_module()


def test_fifo_input_is_rejected_without_waiting(tmp_path: Path) -> None:
    import os

    from trailmark.analysis.native_binary.formats import snapshot

    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="regular file"):
        snapshot(fifo, tmp_path / "copy", time.monotonic() + 1)
