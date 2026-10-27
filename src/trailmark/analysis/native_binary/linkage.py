"""Establish linker entries from container metadata and relocation-backed labels."""

from __future__ import annotations

import re
from itertools import chain

from trailmark.analysis.native_binary.formats import Function, Image
from trailmark.analysis.native_binary.tools import Tool, disassembly_command, run_tool
from trailmark.models.binary import BinaryDiagnostic, BinaryLimits

_LABEL = re.compile(rb"^\s*([0-9a-fA-F]+) <([^>]+)>:\s*$", re.MULTILINE)


def prepare_linkage(
    image: Image, tool: Tool, limits: BinaryLimits, deadline: float
) -> list[BinaryDiagnostic]:
    """Keep distinct imported targets without assuming architecture-specific strides.

    Mach-O defines stub strides and indirect-symbol indices in its section
    metadata. ELF objdump labels supply PLT entry extents; only names backed by
    a dynamic relocation are accepted. Unknown layouts retain ordinary regions.
    """
    known = {function.address: function for function in image.functions}
    diagnostics = []
    if image.target.unsupported:
        return diagnostics
    if image.format == "Mach-O" and image.binary.has_dynamic_symbol_command:
        symbols = list(image.binary.dynamic_symbol_command.indirect_symbols)
        for section in image.binary.sections:
            if section.name == "__stub_helper" and section.size:
                known[section.virtual_address] = Function(
                    section.virtual_address, section.size, ("dyld_stub_helper",), "linker_resolver"
                )
            if int(section.type) != 8:  # S_SYMBOL_STUBS
                continue
            stride, first = section.reserved2, section.reserved1
            if not stride or section.size % stride or first + section.size // stride > len(symbols):
                diagnostics.append(
                    BinaryDiagnostic("linkage_unresolved", "Invalid Mach-O stub table")
                )
                continue
            for index in range(section.size // stride):
                symbol = symbols[first + index].name
                address = section.virtual_address + index * stride
                if symbol and image.span_at(address) is not None:
                    known[address] = Function(
                        address, stride, (symbol + "@stub",), "import_stub", symbol
                    )
    elif image.format == "ELF":
        imports = {
            r.symbol.name
            for r in chain(image.binary.dynamic_relocations, image.binary.pltgot_relocations)
            if r.has_symbol
        }
        for span in image.code_spans:
            if span.section not in (".plt", ".plt.sec", ".plt.got", ".iplt") or not imports:
                continue
            output = run_tool(
                disassembly_command(
                    tool,
                    str(image.path),
                    span.start,
                    span.end,
                    triple=image.target.triple,
                    mode=image.target.mode,
                ),
                limits,
                deadline,
            )
            if output.diagnostic:
                diagnostics.append(output.diagnostic)
                continue
            labels = sorted(
                {
                    (int(m[1], 16), m[2].decode("utf-8", errors="replace"))
                    for m in _LABEL.finditer(output.stdout)
                    if span.start <= int(m[1], 16) < span.end
                }
            )
            entries = [
                (address, name[:-4])
                for address, name in labels
                if name.endswith("@plt") and name[:-4] in imports
            ]
            for address, symbol in entries:
                stop = min((value for value, _ in labels if value > address), default=span.end)
                known[address] = Function(
                    address, stop - address, (symbol + "@plt",), "import_stub", symbol
                )
            if entries and entries[0][0] > span.start:
                known[span.start] = Function(
                    span.start, entries[0][0] - span.start, ("plt_resolver",), "linker_resolver"
                )
    image.functions = sorted(known.values(), key=lambda function: function.address)
    return diagnostics
