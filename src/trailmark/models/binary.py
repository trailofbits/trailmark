"""Immutable evidence connecting source code to linked native images.

Addresses are link-time virtual addresses and ranges are half-open. Runtime
load addresses are deliberately not accepted as implicit substitutes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

type AddressKind = Literal["va", "rva", "file_offset"]


def _positive(value: int | float, name: str) -> None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or value <= 0
        or (isinstance(value, float) and not math.isfinite(value))
    ):
        raise ValueError(f"{name} must be finite and positive")


@dataclass(frozen=True)
class BinaryLimits:
    """Resource bounds, independent of whether missing evidence is an error."""

    tool_timeout: float = 30.0
    import_timeout: float = 300.0
    max_output_bytes: int = 64 * 1024 * 1024
    max_instructions: int = 1_000_000

    def __post_init__(self) -> None:
        for name in ("tool_timeout", "import_timeout", "max_output_bytes", "max_instructions"):
            value = getattr(self, name)
            _positive(value, name)
            if name.startswith("max_") and not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")


@dataclass(frozen=True)
class BinarySelection:
    """Select an executable byte range in one explicit address space."""

    start: int
    end: int
    address_kind: str = "va"
    mode: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.start, int)
            or isinstance(self.start, bool)
            or not isinstance(self.end, int)
            or isinstance(self.end, bool)
            or self.start < 0
            or self.end <= self.start
        ):
            raise ValueError("Binary ranges require integer addresses with 0 <= start < end")
        if self.address_kind not in ("va", "rva", "file_offset"):
            raise ValueError(f"Unknown binary address kind: {self.address_kind!r}")


@dataclass(frozen=True)
class BinaryImportOptions:
    """Native ingestion configuration; paths refer only to local files/tools."""

    backend: Literal["auto", "llvm", "gnu"] = "auto"
    objdump: str | None = None
    symbolizer: str | None = None
    debug_file: str | None = None
    mapping_file: str | None = None
    path_mappings: tuple[tuple[str, str], ...] = ()
    slice_index: int | None = None
    ranges: tuple[BinarySelection, ...] = ()
    strict: bool = False
    limits: BinaryLimits = field(default_factory=BinaryLimits)

    def __post_init__(self) -> None:
        if self.backend not in ("auto", "llvm", "gnu"):
            raise ValueError(f"Unknown binary backend: {self.backend!r}")
        if not isinstance(self.limits, BinaryLimits) or not isinstance(self.strict, bool):
            raise ValueError("limits must be BinaryLimits and strict must be a boolean")
        for name in ("objdump", "symbolizer", "debug_file", "mapping_file"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{name} must be a nonempty path string")
        if self.slice_index is not None and (
            not isinstance(self.slice_index, int)
            or isinstance(self.slice_index, bool)
            or self.slice_index < 0
        ):
            raise ValueError("slice_index must be a nonnegative integer")
        if not isinstance(self.path_mappings, tuple) or any(
            not isinstance(pair, tuple)
            or len(pair) != 2
            or any(not isinstance(part, str) or not part for part in pair)
            for pair in self.path_mappings
        ):
            raise ValueError("path_mappings must contain nonempty (old, new) prefix tuples")
        if not isinstance(self.ranges, tuple) or any(
            not isinstance(selection, BinarySelection) for selection in self.ranges
        ):
            raise ValueError("ranges must be a tuple of BinarySelection objects")


@dataclass(frozen=True)
class BinaryDiagnostic:
    """A coverage gap within an artifact or a requested disassembly scope."""

    code: str
    message: str
    start: int | None = None
    end: int | None = None


@dataclass(frozen=True)
class BinaryArtifact:
    """Identity, decoding configuration, and coverage of one image slice."""

    id: str
    path: str
    sha256: str
    format: str
    architecture: str
    bits: int
    byte_order: str
    image_base: int
    slice_offset: int
    slice_size: int
    slice_index: int = 0
    cpu_subtype: str = ""
    abi: str = ""
    mode: str = ""
    build_id: str = ""
    uuid: str = ""
    debug_file: str | None = None
    debug_sha256: str | None = None
    backend: str = ""
    objdump: str = ""
    symbolizer: str | None = None
    tool_versions: tuple[tuple[str, str], ...] = ()
    scanned_ranges: tuple[tuple[int, int], ...] = ()
    complete: bool = True
    diagnostics: tuple[BinaryDiagnostic, ...] = ()
    limits: BinaryLimits = field(default_factory=BinaryLimits)


@dataclass(frozen=True)
class BinaryRange:
    """Executable bytes owned by a binary function or an unidentified region."""

    id: str
    artifact_id: str
    node_id: str
    start: int
    end: int
    rva: int
    file_offset: int
    section: str = ""
    mode: str = ""
    provenance: str = "segment"
    symbols: tuple[str, ...] = ()


@dataclass(frozen=True)
class BinarySourceMapping:
    """A source occurrence associated with part of a compiled range."""

    id: str
    range_id: str
    source_node_id: str | None
    start: int
    end: int
    method: str
    file_path: str = ""
    line: int | None = None
    column: int | None = None
    function: str = ""
    inline_depth: int = 0


@dataclass(frozen=True)
class BinaryInstruction:
    """One decoded instruction; not a graph node or persistent code copy."""

    address: int
    size: int
    bytes: str
    mnemonic: str
    operands: str
    rva: int
    file_offset: int
    mode: str = ""
    call_kind: str | None = None
    target: int | None = None
    target_symbol: str | None = None


@dataclass(frozen=True)
class BinaryDisassembly:
    """A bounded result whose completeness applies only to its requested range."""

    artifact_id: str
    start: int
    end: int
    instructions: tuple[BinaryInstruction, ...] = ()
    source_mappings: tuple[BinarySourceMapping, ...] = ()
    truncated: bool = False
    diagnostics: tuple[BinaryDiagnostic, ...] = ()
