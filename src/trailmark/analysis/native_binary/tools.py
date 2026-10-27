"""Bounded, shell-free GNU/LLVM adapters. The inspected image is never run."""

from __future__ import annotations

import os
import re
import selectors
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from trailmark.models.binary import BinaryDiagnostic, BinaryImportOptions, BinaryLimits


@dataclass(frozen=True)
class ToolResult:
    stdout: bytes
    stderr: bytes
    returncode: int
    diagnostic: BinaryDiagnostic | None = None


@dataclass(frozen=True)
class Tool:
    path: str
    family: str
    version: str
    capabilities: str = ""


def run_tool(
    command: list[str],
    limits: BinaryLimits,
    deadline: float,
    *,
    input_bytes: bytes = b"",
) -> ToolResult:
    """Drain both pipes under one byte/time budget, including on tool failure."""
    timeout = min(limits.tool_timeout, deadline - time.monotonic())
    if timeout <= 0:
        return ToolResult(b"", b"", -1, BinaryDiagnostic("timeout", "Import deadline reached"))
    environment = dict(os.environ)
    environment.update(
        LC_ALL="C", LANG="C", DEBUGINFOD_URLS="", LLVM_SYMBOLIZER_OPTS="", LLVM_ADDR2LINE_OPTS=""
    )
    # A file avoids deadlocking while writing a large batch to a child's stdin.
    with tempfile.TemporaryFile() as incoming:
        incoming.write(input_bytes)
        incoming.seek(0)
        try:
            process = subprocess.Popen(  # noqa: S603 -- Validated host tool, no shell.
                command,
                stdin=incoming,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
        except OSError as error:
            return ToolResult(b"", b"", -1, BinaryDiagnostic("tool_error", str(error)))
        output: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
        diagnostic = None
        stop = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            for label, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
                if pipe is not None:
                    selector.register(pipe, selectors.EVENT_READ, label)
            try:
                while selector.get_map():
                    remaining = stop - time.monotonic()
                    if remaining <= 0:
                        diagnostic = BinaryDiagnostic("timeout", "Host tool exceeded its deadline")
                        break
                    for key, _ in selector.select(min(remaining, 0.1)):
                        used = sum(map(len, output.values()))
                        chunk = os.read(key.fd, min(65536, limits.max_output_bytes - used + 1))
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        room = limits.max_output_bytes - used
                        output[key.data].extend(chunk[:room])
                        if len(chunk) > room:
                            diagnostic = BinaryDiagnostic(
                                "output_limit", "Host tool output limit reached"
                            )
                            break
                    if diagnostic is not None:
                        break
                if diagnostic is not None:
                    process.kill()
                try:
                    process.wait(timeout=max(0.01, stop - time.monotonic()))
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    diagnostic = BinaryDiagnostic("timeout", "Host tool exceeded its deadline")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                for pipe in (process.stdout, process.stderr):
                    if pipe is not None:
                        pipe.close()
    if process.returncode and diagnostic is None:
        detail = bytes(output["stderr"]).decode("utf-8", errors="replace").strip()
        diagnostic = BinaryDiagnostic("tool_error", detail or f"Tool exited {process.returncode}")
    return ToolResult(
        bytes(output["stdout"]), bytes(output["stderr"]), process.returncode, diagnostic
    )


def identify_tool(path: str, limits: BinaryLimits, deadline: float) -> Tool:
    """Identify the implementation, never inferring it from the basename."""
    candidate = shutil.which(path) if not os.path.isabs(path) else path
    if candidate is None:
        raise ValueError(f"Binary host tool was not found: {path}")
    resolved = Path(candidate).resolve(strict=True)
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ValueError(f"Binary host tool is not an executable regular file: {resolved}")
    result = run_tool([str(resolved), "--version"], limits, deadline)
    if result.diagnostic:
        raise ValueError(
            f"Cannot identify binary host tool {resolved}: {result.diagnostic.message}"
        )
    version = result.stdout.decode("utf-8", errors="replace")
    family = "llvm" if "LLVM" in version or "llvm" in version else "gnu" if "GNU" in version else ""
    if not family:
        raise ValueError(f"Unsupported binary host tool implementation: {resolved}")
    return Tool(str(resolved), family, version.splitlines()[0], version)


def _supports_target(
    tool: Tool, architecture: str, image_format: str, options: BinaryImportOptions, deadline: float
) -> bool:
    if tool.family == "llvm":
        target = {
            "i386": "x86",
            "x86_64": "x86-64",
            "ppc64le": "ppc64le",
            "ppc64": "ppc64",
            "s390x": "systemz",
        }.get(architecture, architecture)
        return bool(re.search(rf"(?m)^\s+{re.escape(target)}\s+-", tool.capabilities))
    result = run_tool([tool.path, "-i"], options.limits, deadline)
    if result.diagnostic:
        return False
    capabilities = result.stdout.decode("utf-8", errors="replace").lower()
    target = {
        "x86_64": "i386",
        "ppc64le": "powerpc:common64",
        "ppc64": "powerpc:common64",
        "s390x": "s390:64-bit",
        "riscv32": "riscv",
        "riscv64": "riscv",
        "mipsel": "mips",
        "mips64": "mips",
        "mips64el": "mips",
    }.get(architecture, architecture)
    container = {"ELF": "elf", "Mach-O": "mach-o", "PE": "pei-"}[image_format]
    return container in capabilities and bool(
        re.search(rf"(?m)^\s+{re.escape(target)}\s*$", capabilities)
    )


def select_objdump(
    options: BinaryImportOptions, deadline: float, architecture: str, image_format: str
) -> Tool:
    if options.objdump:
        tool = identify_tool(options.objdump, options.limits, deadline)
        if options.backend != "auto" and options.backend != tool.family:
            raise ValueError("Explicit objdump implementation does not match the requested backend")
        return tool
    available = []
    for name in ("llvm-objdump", "objdump", "gobjdump"):
        if shutil.which(name):
            tool = identify_tool(name, options.limits, deadline)
            if (options.backend == "auto" or options.backend == tool.family) and tool.path not in {
                item.path for item in available
            }:
                available.append(tool)
    # Implementations named simply objdump participate by capability, including
    # Apple's LLVM tool. Prefer any compatible LLVM build before GNU.
    available.sort(key=lambda tool: tool.family != "llvm")
    for tool in available:
        if _supports_target(tool, architecture, image_format, options, deadline):
            return tool
    if available:
        # Keep a concrete failed decode as an incomplete-import diagnostic.
        return available[0]
    raise ValueError(
        f"No {options.backend} objdump found; set the objdump option to a host tool path"
    )


def select_symbolizer(
    options: BinaryImportOptions, objdump: Tool, deadline: float, *, needs_llvm: bool = False
) -> Tool | None:
    if options.symbolizer:
        tool = identify_tool(options.symbolizer, options.limits, deadline)
        if needs_llvm and tool.family != "llvm":
            raise ValueError("Mach-O/PDB symbolization requires llvm-symbolizer")
        return tool
    sibling = Path(objdump.path).parent
    preferred = [str(sibling / "llvm-symbolizer"), "llvm-symbolizer"]
    if not needs_llvm:
        prefix = Path(objdump.path).name.removesuffix("objdump")
        gnu = [str(sibling / f"{prefix}addr2line"), "addr2line", "gaddr2line"]
        preferred = preferred + gnu if objdump.family == "llvm" else gnu + preferred
    for path in dict.fromkeys(preferred):
        if shutil.which(path):
            return identify_tool(path, options.limits, deadline)
    return None


def disassembly_command(
    tool: Tool, path: str, start: int, end: int, *, triple: str, mode: str
) -> list[str]:
    command = [tool.path, "--disassemble", "--disassemble-zeroes"]
    if tool.family == "llvm":
        command.extend([f"--triple={triple}", "--print-imm-hex"])
    else:
        command.extend(["--wide", "--insn-width=16"])
        if mode == "thumb":
            command.append("--disassembler-options=force-thumb")
    command.extend([f"--start-address={start}", f"--stop-address={end}"])
    if tool.family == "gnu":
        command.append("--")
    # LLVM builds do not universally accept '--'. This is always an absolute
    # path to our own snapshot, so it cannot be parsed as an option or @file.
    command.append(path)
    return command


_INSTRUCTION = re.compile(r"^\s*([0-9a-fA-F]+):\s+(.*)$")
_ENCODING = re.compile(r"^(?:(?:[0-9a-fA-F]{2}){1,8}(?: +|$))+")


def instruction_rows(output: bytes) -> list[tuple[int, int, str, str]]:
    """Read GNU/LLVM encodings without treating symbol or relocation rows as code."""
    rows = []
    for line in output.decode("utf-8", errors="replace").splitlines():
        match = _INSTRUCTION.match(line)
        if not match:
            continue
        remainder = match[2]
        # Both adapters delimit the encoding from assembly with a tab. The
        # fallback supports tool builds that align with spaces instead.
        if "\t" in remainder:
            encoding, assembly = remainder.split("\t", 1)
        else:
            raw = _ENCODING.match(remainder)
            if raw is None:
                continue
            encoding, assembly = raw[0], remainder[raw.end() :]
        words = encoding.split()
        if not words or any(not re.fullmatch(r"(?:[0-9a-fA-F]{2}){1,8}", w) for w in words):
            continue
        assembly = assembly.strip()
        if not assembly:
            continue
        parts = assembly.split(None, 1)
        mnemonic, operands = parts[0], parts[1] if len(parts) > 1 else ""
        rows.append(
            (int(match[1], 16), sum(len(w) // 2 for w in words), mnemonic, operands.strip())
        )
    return rows


def call_target(
    architecture: str, mnemonic: str, operands: str
) -> tuple[str | None, int | None, str | None]:
    """Recognize link instructions conservatively; register calls stay unresolved."""
    mnemonic = mnemonic.lower()
    direct = False
    indirect = False
    if architecture in ("i386", "x86_64"):
        direct = mnemonic in ("call", "callq", "calll") and not operands.startswith("*")
        indirect = mnemonic in ("call", "callq", "calll") and not direct
    elif architecture == "arm":
        link = bool(
            re.fullmatch(
                r"blx?(?:eq|ne|cs|hs|cc|lo|mi|pl|vs|vc|hi|ls|ge|lt|gt|le|al)?(?:\.w)?", mnemonic
            )
        )
        direct = link and not re.match(r"(?:r\d+|ip|lr)\b", operands)
        indirect = link and not direct
    elif architecture == "aarch64":
        direct, indirect = (
            mnemonic == "bl",
            mnemonic in ("blr", "blraa", "blrab", "blraaz", "blrabz"),
        )
    elif architecture.startswith("riscv"):
        direct = mnemonic in ("call", "c.jal") or (
            mnemonic == "jal" and not re.match(r"(?:zero|x0),", operands)
        )
        indirect = mnemonic in ("jalr", "c.jalr") and not re.match(r"(?:zero|x0),", operands)
    elif architecture.startswith("ppc64"):
        direct, indirect = mnemonic in ("bl", "bla", "bcl", "bcla"), mnemonic in ("bctrl", "blrl")
    elif architecture == "s390x":
        direct = mnemonic in ("brasl", "bras") and bool(re.match(r"%?r14\s*,", operands))
        indirect = mnemonic == "basr" and bool(re.match(r"%?r14\s*,", operands))
    elif architecture.startswith("mips"):
        direct = mnemonic in ("jal", "bal", "jalx", "bgezal", "bltzal", "bgezall", "bltzall")
        indirect = mnemonic in ("jalr", "jalr.hb") and not re.match(r"\$?(?:zero|0),", operands)
    elif architecture == "loongarch64":
        direct = mnemonic == "bl"
        indirect = mnemonic == "jirl" and bool(re.match(r"\$?(?:ra|r1)\s*,", operands))
    if not direct and not indirect:
        return None, None, None
    symbol = re.search(r"<([^>]+)>", operands)
    target = None
    if direct:
        text = operands.split("<", 1)[0].strip().rsplit(",", 1)[-1].strip().split()[0:1]
        if text and re.fullmatch(r"(?:0x)?[0-9a-fA-F]+", text[0]):
            target = int(text[0], 16)
        else:
            indirect = True
    return "indirect" if indirect else "direct", target, symbol[1] if symbol else None


def branch_target(architecture: str, mnemonic: str, operands: str) -> tuple[int | None, str | None]:
    """Recognize only unconditional immediate branches, never register returns."""
    mnemonic = mnemonic.lower()
    direct = (
        architecture in ("i386", "x86_64")
        and mnemonic in ("jmp", "jmpq", "jmpl")
        and not operands.startswith("*")
        or architecture == "arm"
        and mnemonic in ("b", "b.w", "bal", "bal.w")
        or architecture == "aarch64"
        and mnemonic == "b"
        or architecture.startswith("riscv")
        and (
            mnemonic in ("j", "c.j", "tail")
            or mnemonic == "jal"
            and bool(re.match(r"(?:zero|x0)\s*,", operands))
        )
        or architecture.startswith("ppc64")
        and mnemonic in ("b", "ba")
        or architecture == "s390x"
        and (
            mnemonic in ("j", "jg")
            or mnemonic in ("brc", "brcl")
            and bool(re.match(r"15\s*,", operands))
        )
        or architecture.startswith("mips")
        and mnemonic in ("j", "b")
        or architecture == "loongarch64"
        and mnemonic == "b"
    )
    if not direct:
        return None, None
    text = operands.split("<", 1)[0].strip().rsplit(",", 1)[-1].strip().split()
    symbol = re.search(r"<([^>]+)>", operands)
    target = int(text[0], 16) if text and re.fullmatch(r"(?:0x)?[0-9a-fA-F]+", text[0]) else None
    return target, symbol[1] if symbol else None
