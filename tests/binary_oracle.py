"""Independent LLVM metadata and raw AArch64 instruction oracles for smoke checks."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OracleSection:
    name: str
    start: int
    end: int
    offset: int


@dataclass(frozen=True)
class OracleImage:
    image_base: int
    sections: tuple[OracleSection, ...]
    symbols: dict[str, tuple[int, ...]]

    def offset(self, address: int) -> int:
        (section,) = (s for s in self.sections if s.start <= address < s.end)
        return section.offset + address - section.start


def _run(command: list[str]) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True, timeout=30).stdout  # noqa: S603


def _field(body: str, name: str) -> int:
    match = re.search(rf"^\s*{name}: (0x[0-9A-Fa-f]+|\d+)", body, re.MULTILINE)
    if match is None:
        raise ValueError(f"llvm-readobj omitted {name}")
    return int(match[1], 0)


def inspect_image(path: Path, readobj: str, nm: str) -> OracleImage:
    """Read executable sections/base with llvm-readobj, and symbols with llvm-nm.

    This deliberately uses neither LIEF nor the importer's metadata readers.
    Fat slices are compared against their independent thin-image oracle.
    """
    with path.open("rb") as stream:
        elf = stream.read(4) == b"\x7fELF"
    output = _run(
        [
            readobj,
            "--sections",
            "--program-headers",
            "--macho-segment",
            *(["--elf-output-style=JSON"] if elf else []),
            str(path),
        ]
    )
    sections = []
    if output.lstrip().startswith("[{"):
        (document,) = json.loads(output)
        headers = [p["ProgramHeader"] for p in document["ProgramHeaders"]]
        image_base = min(
            p["VirtualAddress"] - p["Offset"] for p in headers if p["Type"]["Value"] == 1
        )
        for item in document["Sections"]:
            section = item["Section"]
            if section["Flags"]["Value"] & 4 and section["Size"]:
                sections.append(
                    OracleSection(
                        section["Name"]["Name"],
                        section["Address"],
                        section["Address"] + section["Size"],
                        section["Offset"],
                    )
                )
    else:
        segments = re.findall(r"^Segment \{\n(.*?)^\}", output, re.MULTILINE | re.DOTALL)
        image_base = min(
            _field(s, "vmaddr") - _field(s, "fileoff") for s in segments if _field(s, "filesize")
        )
        for body in re.findall(r"^  Section \{\n(.*?)^  \}", output, re.MULTILINE | re.DOTALL):
            flags = re.search(r"Attributes \[ \(0x([0-9A-Fa-f]+)\)", body)
            if flags is not None and int(flags[1], 16) & 0x800004:
                name = re.search(r"Name: (\S+)", body)
                assert name is not None
                start = _field(body, "Address")
                sections.append(
                    OracleSection(
                        name[1], start, start + _field(body, "Size"), _field(body, "Offset")
                    )
                )
    symbols: dict[str, set[int]] = {}
    listing = _run([nm, "--format=posix", "--numeric-sort", "--defined-only", str(path)])
    if elf and not listing.strip():
        listing = _run(
            [nm, "--format=posix", "--numeric-sort", "--defined-only", "--dynamic", str(path)]
        )
    for line in listing.splitlines():
        fields = line.split()
        # Mach-O nm labels symbols in executable sections other than __text
        # as S/s. The independent section containment check still excludes data.
        if len(fields) >= 3 and fields[1] in ("T", "t", "W", "w", *(() if elf else ("S", "s"))):
            address = int(fields[2], 16)
            if any(s.start <= address < s.end for s in sections):
                symbols.setdefault(fields[0], set()).add(address)
    return OracleImage(
        image_base,
        tuple(sections),
        {name: tuple(sorted(values)) for name, values in symbols.items()},
    )


def merged_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if result and start <= result[-1][1]:
            result[-1] = result[-1][0], max(result[-1][1], end)
        else:
            result.append((start, end))
    return result


def aarch64_direct_calls(
    path: Path, oracle: OracleImage, start: int, end: int
) -> set[tuple[int, int]]:
    """Decode BL immediates from original bytes, independently of objdump."""
    return _aarch64_immediates(path, oracle, start, end, 0x94000000)


def aarch64_branches(path: Path, oracle: OracleImage, start: int, end: int) -> set[tuple[int, int]]:
    """Decode unconditional B immediates; classification remains separate."""
    return _aarch64_immediates(path, oracle, start, end, 0x14000000)


def _aarch64_immediates(
    path: Path, oracle: OracleImage, start: int, end: int, opcode: int
) -> set[tuple[int, int]]:
    data = path.read_bytes()
    result = set()
    for address in range(start, end - 3, 4):
        offset = oracle.offset(address)
        word = int.from_bytes(data[offset : offset + 4], "little")
        if word & 0xFC000000 == opcode:
            immediate = word & 0x3FFFFFF
            if immediate & 0x2000000:
                immediate -= 0x4000000
            result.add((address, address + (immediate << 2)))
    return result
