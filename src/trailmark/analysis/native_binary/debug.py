"""Identity-checked local debug companions and batched source symbolization."""

from __future__ import annotations

import json
import math
import re
import shutil
import struct
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path

from trailmark.analysis.native_binary.formats import (
    Image,
    build_id,
    fat_slices,
    lief_module,
    snapshot,
)
from trailmark.analysis.native_binary.tools import Tool, run_tool
from trailmark.models.binary import BinaryDiagnostic, BinaryLimits


@dataclass(frozen=True)
class DebugFile:
    path: str
    sha256: str
    local_path: str
    pdb: bool = False
    symbol_image: str | None = None


@dataclass(frozen=True)
class Frame:
    function: str
    file: str
    line: int
    column: int = 0


def pdb_identity(path: Path) -> tuple[str, int]:
    """Read GUID/age from MSF's PDB info stream without executing PDB code."""
    size = path.stat().st_size
    with path.open("rb") as stream:
        header = stream.read(56)
        if len(header) < 56 or header[:32] != b"Microsoft C/C++ MSF 7.00\r\n\x1aDS\0\0\0":
            raise ValueError("Unsupported or malformed PDB/MSF header")
        block_size, _, blocks, directory_size, _, block_map = struct.unpack("<6I", header[32:])
        if (
            block_size not in (512, 1024, 2048, 4096, 8192, 16384, 32768, 65536)
            or blocks * block_size > size
            or directory_size > 64 * 1024 * 1024
            or directory_size < 12
        ):
            raise ValueError("Invalid PDB block directory")

        def read_block(index: int) -> bytes:
            if index >= blocks:
                raise ValueError("PDB block index is outside the file")
            stream.seek(index * block_size)
            data = stream.read(block_size)
            if len(data) != block_size:
                raise ValueError("Truncated PDB block")
            return data

        directory_blocks = math.ceil(directory_size / block_size)
        if directory_blocks * 4 > block_size:
            raise ValueError("Unsupported oversized PDB directory map")
        indexes = struct.unpack(
            f"<{directory_blocks}I", read_block(block_map)[: directory_blocks * 4]
        )
        directory = b"".join(read_block(index) for index in indexes)[:directory_size]
        count = struct.unpack_from("<I", directory)[0]
        if count < 2 or 4 + count * 4 > len(directory):
            raise ValueError("Malformed PDB stream directory")
        sizes = struct.unpack_from(f"<{count}I", directory, 4)
        cursor = 4 + count * 4
        for index, extent in enumerate(sizes):
            pages = math.ceil(extent / block_size) if extent != 0xFFFFFFFF else 0
            if cursor + pages * 4 > len(directory):
                raise ValueError("Truncated PDB stream block list")
            if index == 1:
                if extent < 28 or extent > 64 * 1024 * 1024:
                    raise ValueError("Invalid PDB info stream")
                pages_ = struct.unpack_from(f"<{pages}I", directory, cursor)
                info = b"".join(read_block(page) for page in pages_)[:extent]
                return str(uuid.UUID(bytes_le=info[12:28])), struct.unpack_from("<I", info, 8)[0]
            cursor += pages * 4
    raise ValueError("PDB info stream is missing")


def prepare_debug(image: Image, requested: str, directory: Path, deadline: float) -> DebugFile:
    """Reject unrelated debug data before allowing a symbolizer to use it."""
    lief = lief_module()
    source = Path(requested).expanduser().resolve(strict=True)
    if image.format == "Mach-O" and source.is_dir():
        candidates = sorted((source / "Contents/Resources/DWARF").glob("*"))
    else:
        candidates = [source]
    for index, candidate in enumerate(candidates):
        local = directory / f"debug-{image.slice_index}-{index}"
        digest = snapshot(candidate, local, deadline)
        if image.format == "PE":
            entries = [
                entry for entry in image.binary.debug if isinstance(entry, lief.PE.CodeViewPDB)
            ]
            with local.open("rb") as stream:
                is_pdb = stream.read(32).startswith(b"Microsoft C/C++ MSF")
            if is_pdb:
                identity = pdb_identity(local)
                if not entries or not all(
                    (str(uuid.UUID(entry.guid)), entry.age) == identity for entry in entries
                ):
                    raise ValueError("PDB GUID/age does not match the PE image")
                # LLVM's native PDB reader searches the PE's directory first.
                # Isolate a verified companion under its CodeView basename;
                # this also works with LLVM builds predating --pdb overrides.
                name = Path(entries[0].filename.replace("\\", "/")).name
                if not name or name in (".", ".."):
                    raise ValueError("PE image has an invalid CodeView PDB filename")
                symbol_directory = directory / f"pdb-view-{image.slice_index}"
                symbol_directory.mkdir()
                symbol_image = symbol_directory / ("image" if name != "image" else "binary")
                shutil.copyfile(image.path, symbol_image)
                shutil.copyfile(local, symbol_directory / name)
                return DebugFile(str(candidate), digest, str(local), True, str(symbol_image))
        if image.format == "Mach-O":
            for slice_number, (offset, extent) in enumerate(fat_slices(local)):
                thin = directory / f"debug-thin-{image.slice_index}-{index}-{slice_number}"
                with local.open("rb") as stream:
                    stream.seek(offset)
                    data = stream.read(extent)
                thin.write_bytes(data)
                binary = lief.parse(str(thin))
                if (
                    binary is not None
                    and isinstance(binary, lief.MachO.Binary)
                    and binary.has_uuid
                    and bytes(binary.uuid.uuid).hex() == image.uuid
                    and image.uuid
                ):
                    return DebugFile(str(candidate), digest, str(thin))
            continue
        binary = lief.parse(str(local))
        if image.format == "ELF" and isinstance(binary, lief.ELF.Binary):
            companion_id = build_id(binary)
            if image.build_id and companion_id:
                if image.build_id != companion_id:
                    raise ValueError("ELF debug companion build ID does not match the image")
                return DebugFile(str(candidate), digest, str(local))
            section = image.binary.get_section(".gnu_debuglink")
            if section:
                data = bytes(section.content)
                end = data.find(b"\0")
                crc_offset = (end + 4) & ~3
                if end < 0 or crc_offset + 4 > len(data):
                    raise ValueError("Malformed ELF debuglink")
                expected_name = data[:end].decode("utf-8", errors="replace")
                expected_crc = int.from_bytes(
                    data[crc_offset : crc_offset + 4],
                    "little" if image.target.byte_order == "little" else "big",
                )
                if (
                    expected_name != candidate.name
                    or zlib.crc32(local.read_bytes()) != expected_crc
                ):
                    raise ValueError("ELF debuglink filename/checksum does not match the companion")
                return DebugFile(str(candidate), digest, str(local))
        if image.format == "PE":
            raise ValueError("PE external debug companions must be matching PDB files")
        raise ValueError("Debug companion has no verifiable identity matching the image")
    raise ValueError("No dSYM slice has the image's Mach-O UUID")


def symbolize(
    tool: Tool,
    image: Image,
    addresses: list[int],
    debug: DebugFile | None,
    limits: BinaryLimits,
    deadline: float,
) -> tuple[dict[int, tuple[Frame, ...]], list[BinaryDiagnostic]]:
    """Address-delimited batches preserve inline stacks and unknown locations."""
    path = (debug.symbol_image or debug.local_path) if debug else str(image.path)
    if tool.family == "llvm":
        command = [
            tool.path,
            "--output-style=JSON",
            "--inlining",
            "--no-debuginfod",
            f"--debug-file-directory={image.path.parent}",
            f"--fallback-debug-path={image.path.parent}",
            f"--obj={path}",
        ]
    else:
        command = [
            tool.path,
            "--addresses",
            "--functions",
            "--inlines",
            "--demangle",
            f"--exe={path}",
        ]
    output = run_tool(
        command,
        limits,
        deadline,
        input_bytes="".join(f"0x{address:x}\n" for address in addresses).encode(),
    )
    diagnostics = [output.diagnostic] if output.diagnostic else []
    if output.stderr and not output.diagnostic:
        diagnostics.append(
            BinaryDiagnostic(
                "debug_warning", output.stderr.decode("utf-8", errors="replace").strip()
            )
        )
    frames: dict[int, tuple[Frame, ...]] = {}
    if tool.family == "llvm":
        for line in output.stdout.splitlines():
            try:
                row = json.loads(line)
                address = int(row["Address"], 0)
                if "Error" in row:
                    diagnostics.append(BinaryDiagnostic("symbolization_error", str(row["Error"])))
                    continue
                result = []
                for symbol in row.get("Symbol", []):
                    line_number = symbol.get("Line", 0)
                    column = symbol.get("Column", 0)
                    if not isinstance(line_number, int) or not isinstance(column, int):
                        raise ValueError("Noninteger debug source location")
                    result.append(
                        Frame(
                            str(symbol.get("FunctionName", "")),
                            str(symbol.get("FileName", "")),
                            line_number,
                            column,
                        )
                    )
                frames[address] = tuple(result)
            except (ValueError, TypeError, KeyError, AttributeError):
                diagnostics.append(
                    BinaryDiagnostic("symbolization_output", "Malformed LLVM symbolizer output")
                )
    else:
        current: int | None = None
        pending: str | None = None
        collected: dict[int, list[Frame]] = {}
        for line in output.stdout.decode("utf-8", errors="replace").splitlines():
            if re.fullmatch(r"0x[0-9a-fA-F]+", line):
                current, pending = int(line, 16), None
                collected[current] = []
            elif current is not None and pending is None:
                pending = line
            elif current is not None:
                match = re.match(r"^(.*):(\d+|\?)(?: \(discriminator \d+\))?$", line)
                if not match:
                    diagnostics.append(
                        BinaryDiagnostic(
                            "symbolization_output", "Malformed GNU addr2line source location"
                        )
                    )
                else:
                    collected[current].append(
                        Frame(pending or "", match[1], int(match[2]) if match[2] != "?" else 0)
                    )
                pending = None
        frames = {address: tuple(stack) for address, stack in collected.items()}
    if set(frames) != set(addresses) and not output.diagnostic:
        diagnostics.append(
            BinaryDiagnostic(
                "symbolization_output", "Symbolizer omitted or added requested addresses"
            )
        )
    return frames, diagnostics
