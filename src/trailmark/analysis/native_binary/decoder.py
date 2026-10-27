"""Normalize instruction evidence, including section-header-free images."""

from __future__ import annotations

import re
import struct
from bisect import bisect_right
from dataclasses import replace
from pathlib import Path

from trailmark.analysis.native_binary.formats import Image, Span
from trailmark.analysis.native_binary.tools import (
    Tool,
    branch_target,
    call_target,
    disassembly_command,
    instruction_rows,
    run_tool,
)
from trailmark.models.binary import BinaryDiagnostic, BinaryInstruction, BinaryLimits


def _tail_calls(image: Image, instructions: list[BinaryInstruction]) -> list[BinaryInstruction]:
    """Infer transfers to a different established entry, excluding local jumps."""
    starts = [function.address for function in image.functions]
    entries = {function.address: function for function in image.functions}
    for index, instruction in enumerate(instructions):
        if instruction.call_kind is not None:
            continue
        target, symbol = branch_target(
            image.target.architecture, instruction.mnemonic, instruction.operands
        )
        if target not in entries:
            continue
        owner_index = bisect_right(starts, instruction.address) - 1
        if owner_index < 0:
            continue
        owner = image.functions[owner_index]
        # GCC can give a cold fragment its own FUNC symbol. Moving between
        # those fragments is intra-function control flow, not a tail call.
        target_names = {re.sub(r"\.cold(?:\.\d+)?$", "", name) for name in entries[target].names}
        owner_names = {re.sub(r"\.cold(?:\.\d+)?$", "", name) for name in owner.names}
        fragments = target_names != set(entries[target].names) or owner_names != set(owner.names)
        if fragments and target_names & owner_names:
            continue
        section = next(
            (span for span in image.code_spans if span.start <= owner.address < span.end), None
        )
        stop = min(
            section.end if section else owner.address,
            owner.address + owner.size
            if owner.size
            else starts[owner_index + 1]
            if owner_index + 1 < len(starts)
            else image.spans[-1].end,
        )
        if (
            owner.address <= instruction.address < stop
            and target != owner.address
            and not owner.import_symbol
            and owner.provenance != "linker_resolver"
        ):
            instructions[index] = replace(
                instruction, call_kind="tail", target=target, target_symbol=symbol
            )
    return instructions


def _signed(value: int, bits: int) -> int:
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def _pc_relative_calls(
    image: Image, instructions: list[BinaryInstruction]
) -> list[BinaryInstruction]:
    """Resolve adjacent architectural PC-relative call pairs as inferred edges.

    Use encodings rather than objdump's optional symbol comments. References:
    LoongArch Vol. 1 PCADDU18I/JIRL and RISC-V unprivileged AUIPC/JALR.
    """
    for index in range(1, len(instructions)):
        previous, instruction = instructions[index - 1 : index + 1]
        if (
            previous.size != 4
            or instruction.size != 4
            or previous.address + 4 != instruction.address
            or instruction.call_kind != "indirect"
        ):
            continue
        order = "little" if image.target.byte_order == "little" else "big"
        first = int.from_bytes(bytes.fromhex(previous.bytes), order)
        second = int.from_bytes(bytes.fromhex(instruction.bytes), order)
        target = None
        if image.target.architecture == "loongarch64":
            if (
                first & 0xFE000000 == 0x1E000000
                and second & 0xFC000000 == 0x4C000000
                and first & 31 != 0
                and first & 31 == (second >> 5) & 31
                and second & 31 == 1
            ):
                target = previous.address + (_signed((first >> 5) & 0xFFFFF, 20) << 18)
                target += _signed((second >> 10) & 0xFFFF, 16) << 2
        elif image.target.architecture.startswith("riscv") and (
            first & 0x7F == 0x17
            and second & 0x707F == 0x67
            and (first >> 7) & 31 != 0
            and (first >> 7) & 31 == (second >> 15) & 31
        ):
            target = (
                previous.address + _signed(first & 0xFFFFF000, 32) + _signed(second >> 20, 12)
            ) & ~1
        if target is not None:
            instructions[index] = replace(instruction, call_kind="pc_relative", target=target)
    return instructions


def _carrier(image: Image, span: Span, directory: Path) -> Path:
    """Give objdump an executable section view of explicitly selected raw bytes.

    The view preserves virtual addresses and ISA flags; it never supplies
    source identities or inferred symbols. Byte evidence is read from the
    original snapshot, not this temporary object header.
    """
    machine = {
        "i386": 3,
        "x86_64": 62,
        "arm": 40,
        "aarch64": 183,
        "riscv32": 243,
        "riscv64": 243,
        "ppc64": 21,
        "ppc64le": 21,
        "s390x": 22,
        "mips": 8,
        "mipsel": 8,
        "mips64": 8,
        "mips64el": 8,
        "loongarch64": 258,
    }[image.target.architecture]
    wide = image.target.bits == 64
    endian = "<" if image.target.byte_order == "little" else ">"
    header = struct.Struct(endian + ("16sHHIQQQIHHHHHH" if wide else "16sHHIIIIIHHHHHH"))
    section = struct.Struct(endian + ("IIQQQQIIQQ" if wide else "IIIIIIIIII"))
    with image.path.open("rb") as stream:
        stream.seek(span.file_offset - image.slice_offset)
        data = stream.read(span.end - span.start)
    strings = b"\0.text\0.shstrtab\0"
    string_offset = header.size + len(data)
    section_offset = (string_offset + len(strings) + 7) & ~7
    ident = b"\x7fELF" + bytes([2 if wide else 1, 1 if endian == "<" else 2, 1, 0]) + bytes(8)
    flags = image.binary.header.processor_flag if image.format == "ELF" else 0
    payload = header.pack(
        ident,
        2,
        machine,
        1,
        span.start,
        0,
        section_offset,
        flags,
        header.size,
        0,
        0,
        section.size,
        3,
        2,
    )
    payload += data + strings + bytes(section_offset - string_offset - len(strings))
    payload += bytes(section.size)
    payload += section.pack(1, 1, 6, span.start, header.size, len(data), 0, 0, 1, 0)
    payload += section.pack(7, 3, 0, 0, string_offset, len(strings), 0, 0, 1, 0)
    path = directory / f"code-view-{image.slice_index}-{span.start:x}-{span.end:x}"
    path.write_bytes(payload)
    return path


def decode(
    image: Image,
    span: Span,
    mode: str,
    tool: Tool,
    limits: BinaryLimits,
    deadline: float,
    directory: Path,
    *,
    max_instructions: int,
) -> tuple[list[BinaryInstruction], list[BinaryDiagnostic], bool]:
    path = image.path
    if not span.section or (image.format == "Mach-O" and "," not in span.section):
        path = _carrier(image, span, directory)
    triple = "thumb" if mode == "thumb" else image.target.triple
    command = disassembly_command(tool, str(path), span.start, span.end, triple=triple, mode=mode)
    output = run_tool(command, limits, deadline)
    diagnostics = [output.diagnostic] if output.diagnostic else []
    if output.stderr and not output.diagnostic:
        diagnostics.append(
            BinaryDiagnostic(
                "decoder_warning",
                output.stderr.decode("utf-8", errors="replace").strip(),
                span.start,
                span.end,
            )
        )
    instructions = []
    truncated = output.diagnostic is not None
    previous_end = span.start
    with image.path.open("rb") as stream:
        for address, size, mnemonic, operands in instruction_rows(output.stdout):
            if not span.start <= address < span.end:
                continue
            if len(instructions) >= max_instructions:
                diagnostics.append(
                    BinaryDiagnostic(
                        "instruction_limit", "Instruction scan limit reached", address, span.end
                    )
                )
                truncated = True
                break
            if address < previous_end or size <= 0 or address + size > span.end:
                diagnostics.append(
                    BinaryDiagnostic(
                        "instruction_boundary",
                        "Requested bytes do not end on decoded instruction boundaries",
                        address,
                        span.end,
                    )
                )
                continue
            if address > previous_end:
                diagnostics.append(
                    BinaryDiagnostic(
                        "undecoded_bytes",
                        "Decoder left bytes without instruction evidence",
                        previous_end,
                        address,
                    )
                )
            previous_end = address + size
            if mnemonic.startswith(".") or mnemonic in ("<unknown>", "(bad)"):
                diagnostics.append(
                    BinaryDiagnostic(
                        "unknown_instruction",
                        "Decoder could not establish an instruction",
                        address,
                        address + size,
                    )
                )
                continue
            offset = image.offset(address)
            stream.seek(offset - image.slice_offset)
            raw = stream.read(size)
            if len(raw) != size:
                raise ValueError("Instruction is outside the stable image snapshot")
            kind, target, symbol = call_target(image.target.architecture, mnemonic, operands)
            instructions.append(
                BinaryInstruction(
                    address,
                    size,
                    raw.hex(),
                    mnemonic,
                    operands,
                    address - image.image_base,
                    offset,
                    mode,
                    kind,
                    target,
                    symbol,
                )
            )
    if previous_end < span.end and not truncated:
        diagnostics.append(
            BinaryDiagnostic(
                "undecoded_bytes",
                "Decoder did not cover the requested range",
                previous_end,
                span.end,
            )
        )
    return (
        _tail_calls(image, _pc_relative_calls(image, instructions)),
        list(dict.fromkeys(diagnostics)),
        truncated,
    )
