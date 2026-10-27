"""Optional LIEF-backed readers and explicit native address spaces."""

from __future__ import annotations

import hashlib
import importlib
import os
import stat
import struct
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from trailmark.models.binary import BinarySelection


@dataclass(frozen=True)
class Target:
    architecture: str
    bits: int
    byte_order: str
    triple: str
    mode: str = ""
    abi: str = ""
    unsupported: str = ""


# The complete constant-time-analysis/brotli-checker native target union.
ELF_TARGETS: dict[tuple[int, int, str], tuple[str, str]] = {
    (3, 32, "little"): ("i386", "i386"),
    (62, 64, "little"): ("x86_64", "x86_64"),
    (40, 32, "little"): ("arm", "arm"),
    (183, 64, "little"): ("aarch64", "aarch64"),
    (243, 32, "little"): ("riscv32", "riscv32"),
    (243, 64, "little"): ("riscv64", "riscv64"),
    (21, 64, "little"): ("ppc64le", "powerpc64le"),
    (21, 64, "big"): ("ppc64", "powerpc64"),
    (22, 64, "big"): ("s390x", "s390x"),
    (8, 32, "little"): ("mipsel", "mipsel"),
    (8, 32, "big"): ("mips", "mips"),
    (8, 64, "little"): ("mips64el", "mips64el"),
    (8, 64, "big"): ("mips64", "mips64"),
    (258, 64, "little"): ("loongarch64", "loongarch64"),
}


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    file_offset: int
    section: str = ""


@dataclass(frozen=True)
class Function:
    address: int
    size: int
    names: tuple[str, ...]
    provenance: str = "symbol"
    import_symbol: str = ""


@dataclass
class Image:
    binary: Any
    format: str
    target: Target
    path: Path
    sha256: str
    slice_offset: int
    slice_size: int
    slice_index: int
    image_base: int
    spans: list[Span]
    code_spans: list[Span]
    functions: list[Function]
    modes: list[tuple[int, str]]
    has_debug: bool
    cpu_subtype: str = ""
    build_id: str = ""
    uuid: str = ""

    @property
    def id(self) -> str:
        return f"sha256_{self.sha256}.slice_{self.slice_index}"

    def span_at(self, address: int) -> Span | None:
        return next((span for span in self.spans if span.start <= address < span.end), None)

    def offset(self, address: int) -> int:
        span = self.span_at(address)
        if span is None:
            raise ValueError(f"Address 0x{address:x} is outside file-backed executable bytes")
        return span.file_offset + address - span.start

    def selection(self, selection: BinarySelection) -> tuple[int, int]:
        start, end = selection.start, selection.end
        if selection.address_kind == "rva":
            start, end = start + self.image_base, end + self.image_base
        elif selection.address_kind == "file_offset":
            matches = [
                span
                for span in self.spans
                if span.file_offset <= start < end <= span.file_offset + span.end - span.start
            ]
            if len(matches) != 1:
                raise ValueError(
                    "File-offset selection must fit one unambiguous executable segment"
                )
            span = matches[0]
            start, end = span.start + start - span.file_offset, span.start + end - span.file_offset
        span = self.span_at(start)
        if span is None or end > span.end:
            raise ValueError("Binary selection must fit one file-backed executable segment")
        return start, end


def lief_module() -> Any:  # noqa: ANN401 -- Optional library with format-specific native APIs.
    try:
        return importlib.import_module("lief")
    except ImportError as error:
        raise ValueError(
            "Native binary ingestion requires the optional trailmark[binary] extra"
        ) from error


def snapshot(path: str | Path, destination: Path, deadline: float) -> str:
    """Copy and hash one stable regular file before passing it to native tools."""
    source = Path(path).expanduser().resolve(strict=True)
    descriptor = os.open(
        source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    with os.fdopen(descriptor, "rb") as incoming, destination.open("wb") as outgoing:
        before = os.fstat(incoming.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Binary input must be a regular file")
        digest = hashlib.sha256()
        while chunk := incoming.read(1024 * 1024):
            if time.monotonic() >= deadline:
                raise ValueError("Binary snapshot exceeded the import deadline")
            digest.update(chunk)
            outgoing.write(chunk)
        after = os.fstat(incoming.fileno())
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ValueError("Binary input changed while it was being inspected")
    return digest.hexdigest()


def fat_slices(path: Path) -> list[tuple[int, int]]:
    """Read exact fat slice extents, including repeated CPU families/subtypes."""
    size = path.stat().st_size
    with path.open("rb") as stream:
        header = stream.read(8)
        formats = {
            b"\xca\xfe\xba\xbe": (">", False),
            b"\xbe\xba\xfe\xca": ("<", False),
            b"\xca\xfe\xba\xbf": (">", True),
            b"\xbf\xba\xfe\xca": ("<", True),
        }
        if header[:4] not in formats:
            return [(0, size)]
        if len(header) != 8:
            raise ValueError("Truncated universal Mach-O header")
        endian, wide = formats[header[:4]]
        count = struct.unpack(f"{endian}I", header[4:])[0]
        if not 0 < count <= 256:
            raise ValueError("Invalid universal Mach-O slice count")
        row = struct.Struct(f"{endian}IIQQII" if wide else f"{endian}IIIII")
        slices = []
        table_end = 8 + row.size * count
        for _ in range(count):
            data = stream.read(row.size)
            if len(data) != row.size:
                raise ValueError("Truncated universal Mach-O slice table")
            cpu, subtype, offset, length, *_ = row.unpack(data)
            if offset < table_end or length <= 0 or offset + length > size:
                raise ValueError("Universal Mach-O slice is outside the artifact")
            if any(
                offset < previous + extent and previous < offset + length
                for previous, extent in slices
            ):
                raise ValueError("Overlapping universal Mach-O slices")
            cursor = stream.tell()
            stream.seek(offset)
            thin = stream.read(12)
            stream.seek(cursor)
            thin_order = {
                b"\xce\xfa\xed\xfe": "<",
                b"\xcf\xfa\xed\xfe": "<",
                b"\xfe\xed\xfa\xce": ">",
                b"\xfe\xed\xfa\xcf": ">",
            }.get(thin[:4])
            if (
                thin_order is None
                or len(thin) != 12
                or struct.unpack(f"{thin_order}II", thin[4:12]) != (cpu, subtype)
            ):
                raise ValueError("Universal Mach-O CPU/subtype disagrees with its thin slice")
            slices.append((offset, length))
    return slices


def _elf_target(binary: Any, header: bytes) -> Target:  # noqa: ANN401
    bits = 64 if header[4] == 2 else 32
    order = "little" if header[5] == 1 else "big"
    machine = int(binary.header.machine_type)
    architecture, triple = ELF_TARGETS.get((machine, bits, order), (f"elf_machine_{machine}", ""))
    unsupported = (
        "" if triple else f"Unsupported ELF machine/class/byte order: {machine}/{bits}/{order}"
    )
    abi, mode = "", ""
    if machine == 8:
        flags = binary.header.processor_flag
        isa = flags & 0xF0000000
        abi = "n32" if flags & 0x20 else "n64" if bits == 64 else "o32"
        if flags & 0x0FFF0000:
            unsupported = "Unsupported MIPS compressed ISA, ASE, or vendor-machine flags"
        if isa not in (0x70000000, 0x80000000):
            unsupported = "Only classic MIPS32r2/MIPS64r2 instruction modes are supported"
        mode = "mips64r2" if isa == 0x80000000 else "mips32r2"
        if abi == "n32":
            architecture, triple = (
                ("mips64el", "mips64el") if order == "little" else ("mips64", "mips64")
            )
    if machine == 21:
        abi = f"elfv{binary.header.processor_flag & 3 or (1 if order == 'big' else 2)}"
    return Target(architecture, bits, order, triple, mode, abi, unsupported)


def _target(binary: Any, format_: str, header: bytes) -> Target:  # noqa: ANN401
    if format_ == "ELF":
        return _elf_target(binary, header)
    if format_ == "Mach-O":
        machine = int(binary.header.cpu_type)
        architectures = {
            7: ("i386", 32, "i386"),
            0x1000007: ("x86_64", 64, "x86_64"),
            12: ("arm", 32, "arm"),
            0x100000C: ("aarch64", 64, "aarch64"),
            0x1000012: ("ppc64", 64, "powerpc64"),
        }
        architecture, bits, triple = architectures.get(machine, (f"macho_cpu_{machine}", 0, ""))
        order = "big" if header[:4] in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf") else "little"
        return Target(
            architecture,
            bits,
            order,
            triple,
            unsupported="" if triple else "Unsupported Mach-O CPU",
        )
    machine = int(binary.header.machine)
    architectures = {
        0x14C: ("i386", 32, "i386"),
        0x8664: ("x86_64", 64, "x86_64"),
        0x1C0: ("arm", 32, "arm"),
        0x1C2: ("arm", 32, "thumb"),
        0x1C4: ("arm", 32, "thumb"),
        0xAA64: ("aarch64", 64, "aarch64"),
        0x6264: ("loongarch64", 64, "loongarch64"),
    }
    architecture, bits, triple = architectures.get(machine, (f"pe_machine_{machine}", 0, ""))
    return Target(
        architecture,
        bits,
        "little",
        triple,
        "thumb" if triple == "thumb" else "",
        unsupported="" if triple else "Unsupported PE machine or hybrid ABI",
    )


def _spans(binary: Any, format_: str, offset: int, size: int) -> tuple[list[Span], list[Span]]:  # noqa: ANN401
    spans, code = [], []
    if format_ == "ELF":
        for segment in binary.segments:
            if int(segment.type) == 1 and int(segment.flags) & 1 and segment.physical_size:
                spans.append(
                    Span(
                        segment.virtual_address,
                        segment.virtual_address + segment.physical_size,
                        offset + segment.file_offset,
                    )
                )
        for section in binary.sections:
            if section.flags & 4 and section.size and int(section.type) != 8:
                code.append(
                    Span(
                        section.virtual_address,
                        section.virtual_address + section.size,
                        offset + section.offset,
                        section.name,
                    )
                )
    elif format_ == "Mach-O":
        for segment in binary.segments:
            if int(segment.init_protection) & 4 and segment.file_size:
                spans.append(
                    Span(
                        segment.virtual_address,
                        segment.virtual_address + segment.file_size,
                        offset + segment.file_offset,
                        segment.name,
                    )
                )
        for section in binary.sections:
            if int(section.flags) & 0x80000400 and section.size:
                code.append(
                    Span(
                        section.virtual_address,
                        section.virtual_address + section.size,
                        offset + section.offset,
                        f"{section.segment_name},{section.name}",
                    )
                )
    else:
        for section in binary.sections:
            extent = min(section.virtual_size or section.sizeof_raw_data, section.sizeof_raw_data)
            if section.characteristics & 0x20000000 and extent:
                start = binary.imagebase + section.virtual_address
                spans.append(
                    Span(start, start + extent, offset + section.pointerto_raw_data, section.name)
                )
        code = spans.copy()
    for span in spans + code:
        if (
            span.start < 0
            or span.end <= span.start
            or not offset <= span.file_offset < offset + size
        ):
            raise ValueError("Malformed executable segment")
        if span.file_offset + span.end - span.start > offset + size:
            raise ValueError("Executable segment extends beyond the artifact slice")
    for index, left in enumerate(spans):
        if any(left.start < right.end and right.start < left.end for right in spans[index + 1 :]):
            raise ValueError("Overlapping executable segments have ambiguous addresses")
    for section in code:
        if not any(
            span.start <= section.start < section.end <= span.end
            and section.file_offset - span.file_offset == section.start - span.start
            for span in spans
        ):
            raise ValueError("Executable section does not agree with its load segment")
    return spans, code or spans.copy()


def _functions(
    image: Image,
    symbols: Any = None,  # noqa: ANN401 -- LIEF symbol containers.
) -> tuple[list[Function], list[tuple[int, str]]]:
    binary = image.binary
    names: dict[int, set[str]] = {}
    sizes: dict[int, int] = {}
    descriptors: set[int] = set()
    modes = []
    if image.format in ("ELF", "Mach-O"):
        for symbol in (symbols if symbols is not None else binary).symbols:
            value = symbol.value
            name = symbol.name
            if image.format == "ELF":
                if image.target.architecture == "arm" and name.split(".", 1)[0] in (
                    "$a",
                    "$t",
                    "$d",
                ):
                    modes.append(
                        (value, {"$a": "arm", "$t": "thumb", "$d": "data"}[name.split(".", 1)[0]])
                    )
                if int(symbol.type) not in (2, 10) or not symbol.shndx:
                    continue
                if image.target.architecture.startswith("mips") and symbol.other & 0xF0:
                    image.target = replace(
                        image.target,
                        unsupported="Unsupported symbol-declared MIPS instruction mode",
                    )
                if image.target.architecture == "arm":
                    if value & 1:
                        modes.append((value & ~1, "thumb"))
                    value &= ~1
                # ELFv1 exported function symbols can point into .opd, not code.
                if image.target.architecture.startswith("ppc64") and image.span_at(value) is None:
                    opd = binary.get_section(".opd")
                    if opd and opd.virtual_address <= value < opd.virtual_address + opd.size - 7:
                        raw = bytes(opd.content)[
                            value - opd.virtual_address : value - opd.virtual_address + 8
                        ]
                        value = int.from_bytes(
                            raw, "little" if image.target.byte_order == "little" else "big"
                        )
                        relocation = next(
                            (
                                relocation
                                for relocation in binary.relocations
                                if relocation.address == symbol.value
                                and str(relocation.type).endswith("PPC64_RELATIVE")
                            ),
                            None,
                        )
                        if relocation is not None:
                            value = relocation.addend
                        descriptors.add(value)
            elif not name or name.startswith(("ltmp", "Ltmp")):
                continue
            elif image.target.architecture == "arm":
                # Mach-O n_desc N_ARM_THUMB_DEF, also accepting a Thumb bit.
                if value & 1 or symbol.description & 0x0008:
                    modes.append((value & ~1, "thumb"))
                value &= ~1
            if name and any(span.start <= value < span.end for span in image.code_spans):
                names.setdefault(value, set()).add(name)
                sizes[value] = (
                    0
                    if value in descriptors
                    else max(sizes.get(value, 0), getattr(symbol, "size", 0))
                )
    if image.format == "Mach-O" and binary.has_function_starts:
        for relative in binary.function_starts.functions:
            value = image.image_base + relative
            if image.span_at(value):
                names.setdefault(value, set())
    if image.format == "PE":
        for function in binary.functions:
            value = image.image_base + function.address
            if image.target.architecture == "arm":
                if value & 1:
                    modes.append((value & ~1, "thumb"))
                value &= ~1
            if image.span_at(value):
                names.setdefault(value, set()).update([function.name] if function.name else [])
                sizes[value] = max(sizes.get(value, 0), function.size)
    return [
        Function(
            address,
            sizes.get(address, 0),
            tuple(sorted(aliases)),
            "function_descriptor" if address in descriptors else "symbol",
        )
        for address, aliases in sorted(names.items())
    ], sorted(set(modes))


def include_debug_symbols(image: Image, path: str) -> None:
    """Use only already identity-checked companions to establish function extents."""
    binary = lief_module().parse(path)
    if binary is None:
        return
    additional, modes = _functions(image, binary)
    known = {function.address: function for function in image.functions}
    for function in additional:
        previous = known.get(function.address)
        if previous is None:
            known[function.address] = replace(function, provenance="verified_debug_symbol")
        else:
            known[function.address] = replace(
                previous,
                size=max(previous.size, function.size),
                names=tuple(sorted(set(previous.names + function.names))),
            )
    image.functions = sorted(known.values(), key=lambda function: function.address)
    image.modes = sorted(set(image.modes + modes))


def build_id(binary: Any) -> str:  # noqa: ANN401
    return next(
        (
            bytes(note.description).hex()
            for note in binary.notes
            if str(note.type).endswith("GNU_BUILD_ID")
        ),
        "",
    )


def load_images(
    container: Path, sha256: str, directory: Path, slice_index: int | None
) -> list[Image]:
    lief = lief_module()
    slices = fat_slices(container)
    if slice_index is not None and slice_index >= len(slices):
        raise ValueError("Requested binary slice does not exist")
    images = []
    for index, (offset, size) in enumerate(slices):
        if slice_index is not None and index != slice_index:
            continue
        path = directory / f"image-{index}"
        with container.open("rb") as source, path.open("wb") as target:
            source.seek(offset)
            remaining = size
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError("Truncated binary image")
                target.write(chunk)
                remaining -= len(chunk)
        with path.open("rb") as stream:
            header = stream.read(64)
        binary = lief.parse(str(path))
        if binary is None:
            raise ValueError("Input is not a supported linked ELF, Mach-O, or PE image")
        format_ = str(binary.format).split(".")[-1]
        format_ = {"MACHO": "Mach-O"}.get(format_, format_)
        if format_ == "ELF" and int(binary.header.file_type) not in (2, 3):
            raise ValueError("Relocatable ELF objects and core files are not supported")
        if format_ == "Mach-O" and int(binary.header.file_type) not in (2, 6, 8):
            raise ValueError("Only linked Mach-O executables, dylibs, and bundles are supported")
        if format_ not in ("ELF", "Mach-O", "PE"):
            raise ValueError("Input is not a linked ELF, Mach-O, or PE image")
        spans, code = _spans(binary, format_, offset, size)
        if not spans:
            raise ValueError("Binary has no file-backed executable segments")
        target = _target(binary, format_, header)
        section_names = {section.name for section in binary.sections}
        image = Image(
            binary,
            format_,
            target,
            path,
            sha256,
            offset,
            size,
            index,
            binary.imagebase,
            spans,
            code,
            [],
            [],
            bool(section_names & {".debug_info", ".zdebug_info", "__debug_info"}),
            str(binary.header.cpu_subtype) if format_ == "Mach-O" else "",
            build_id(binary) if format_ == "ELF" else "",
            bytes(binary.uuid.uuid).hex() if format_ == "Mach-O" and binary.has_uuid else "",
        )
        image.functions, image.modes = _functions(image)
        images.append(image)
    return images
