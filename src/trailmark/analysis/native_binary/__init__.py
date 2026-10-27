"""Native linked-image ingestion, deliberately separate from source parsing."""

from __future__ import annotations

import tempfile
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from trailmark.analysis.native_binary.debug import DebugFile, prepare_debug, symbolize
from trailmark.analysis.native_binary.decoder import decode
from trailmark.analysis.native_binary.formats import (
    Function,
    Image,
    Span,
    include_debug_symbols,
    load_images,
    snapshot,
)
from trailmark.analysis.native_binary.linkage import prepare_linkage
from trailmark.analysis.native_binary.mapping import (
    ExplicitMapping,
    MappingBuilder,
    SourceResolver,
    explicit_mappings,
)
from trailmark.analysis.native_binary.tools import (
    Tool,
    identify_tool,
    select_objdump,
    select_symbolizer,
)
from trailmark.models.binary import (
    BinaryArtifact,
    BinaryDiagnostic,
    BinaryDisassembly,
    BinaryImportOptions,
    BinaryInstruction,
    BinaryRange,
    BinarySelection,
    BinarySourceMapping,
)
from trailmark.models.edges import CodeEdge, EdgeConfidence, EdgeKind
from trailmark.models.graph import CodeGraph
from trailmark.models.nodes import CodeUnit, NodeKind, NodeOrigin, SourceLocation
from trailmark.storage.binary_index import IntervalIndex
from trailmark.storage.graph_store import GraphStore


def _scan_spans(image: Image, options: BinaryImportOptions) -> list[tuple[Span, str]]:
    selections: list[tuple[Span, str | None]] = []
    for selection in options.ranges:
        start, end = image.selection(selection)
        containing = next(
            (span for span in image.code_spans if span.start <= start < end <= span.end), None
        )
        if selection.mode is not None and (
            image.target.architecture != "arm" or selection.mode not in ("arm", "thumb")
        ):
            raise ValueError("Explicit instruction mode must be arm or thumb on an ARM image")
        selections.append(
            (
                Span(start, end, image.offset(start), containing.section if containing else ""),
                selection.mode,
            )
        )
    if not selections:
        selections = [(span, None) for span in image.code_spans]
    ordered = sorted(selections, key=lambda pair: pair[0].start)
    if any(left[0].end > right[0].start for left, right in zip(ordered, ordered[1:], strict=False)):
        raise ValueError("Binary selections overlap")
    result = []
    for span, override in ordered:
        cuts = sorted(
            {span.start, span.end}
            | {address for address, _ in image.modes if span.start < address < span.end}
        )
        for start, end in zip(cuts, cuts[1:], strict=False):
            mode = (
                override
                or image.target.mode
                or ("arm" if image.target.architecture == "arm" else "")
            )
            if override is None:
                prior = [value for address, value in image.modes if span.start <= address <= start]
                if prior:
                    mode = prior[-1]
            if mode != "data":
                result.append((Span(start, end, image.offset(start), span.section), mode))
    return result


def _ranges(
    image: Image, scans: list[tuple[Span, str]], path: str, graph: CodeGraph
) -> list[BinaryRange]:
    ranges = []
    code_index = IntervalIndex(image.code_spans)
    for span, mode in scans:
        owned: list[tuple[int, int]] = []
        for index, function in enumerate(image.functions):
            containing_rows = code_index.containing(function.address, function.address + 1)
            containing = containing_rows[0] if len(containing_rows) == 1 else None
            if containing is None:
                continue
            stop = containing.end
            if function.size:
                stop = min(stop, function.address + function.size)
            else:
                if index + 1 < len(image.functions):
                    stop = min(stop, image.functions[index + 1].address)
            start, end = max(span.start, function.address), min(span.end, stop)
            if start >= end:
                continue
            node_id = f"bin.{image.id}:sub_{function.address:x}"
            graph.nodes[node_id] = CodeUnit(
                node_id,
                function.names[0] if len(function.names) == 1 else f"sub_{function.address:x}",
                NodeKind.BINARY_REGION
                if function.provenance == "linker_resolver"
                else NodeKind.FUNCTION,
                SourceLocation(path, 0, 0),
                origin=NodeOrigin.BINARY,
                attributes=(
                    ("artifact", image.id),
                    ("artifact_hash", image.sha256),
                    ("architecture", image.target.architecture),
                    ("address", function.address),
                    ("rva", function.address - image.image_base),
                    ("alias_count", len(function.names)),
                    ("import_symbol", function.import_symbol),
                ),
            )
            ranges.append(
                BinaryRange(
                    f"{node_id}:range_{start:x}-{end:x}.{mode}",
                    image.id,
                    node_id,
                    start,
                    end,
                    start - image.image_base,
                    image.offset(start),
                    span.section,
                    mode,
                    function.provenance
                    if function.provenance != "symbol"
                    else "symbol_size"
                    if function.size
                    else "symbol_extent",
                    function.names,
                )
            )
            owned.append((start, end))
        cursor = span.start
        for start, end in sorted(owned) + [(span.end, span.end)]:
            if cursor < start:
                node_id = f"bin.{image.id}:region_{cursor:x}"
                graph.nodes[node_id] = CodeUnit(
                    node_id,
                    f"region_{cursor:x}",
                    NodeKind.BINARY_REGION,
                    SourceLocation(path, 0, 0),
                    origin=NodeOrigin.BINARY,
                    attributes=(
                        ("artifact", image.id),
                        ("architecture", image.target.architecture),
                    ),
                )
                ranges.append(
                    BinaryRange(
                        f"{node_id}:range_{cursor:x}-{start:x}.{mode}",
                        image.id,
                        node_id,
                        cursor,
                        start,
                        cursor - image.image_base,
                        image.offset(cursor),
                        span.section,
                        mode,
                        "executable_region",
                    )
                )
            cursor = max(cursor, end)
    return sorted(ranges, key=lambda row: (row.start, row.end, row.id))


def _explicit_for_image(
    image: Image, mappings: list[ExplicitMapping], images: list[Image]
) -> list[tuple[ExplicitMapping, int, int]]:
    result = []
    for mapping in mappings:
        if mapping.slice_index is not None and mapping.slice_index != image.slice_index:
            continue
        if (
            mapping.slice_index is None
            and len(images) > 1
            and mapping.selection.address_kind != "file_offset"
        ):
            raise ValueError("Virtual/RVA mappings for universal images require slice_index")
        if (
            mapping.slice_index is None
            and mapping.selection.address_kind == "file_offset"
            and not image.slice_offset
            <= mapping.selection.start
            < image.slice_offset + image.slice_size
        ):
            continue
        start, end = image.selection(mapping.selection)
        result.append((mapping, start, end))
    return result


def _add_call(
    graph: CodeGraph,
    image: Image,
    owner: BinaryRange,
    instruction: BinaryInstruction,
    ranges: IntervalIndex[BinaryRange],
    path: str,
) -> None:
    if instruction.call_kind is None:
        return
    candidates = (
        {row.node_id for row in ranges.containing(instruction.target, instruction.target + 1)}
        if instruction.target is not None
        else set()
    )
    confidence = (
        EdgeConfidence.INFERRED
        if instruction.call_kind in ("pc_relative", "tail")
        else EdgeConfidence.CERTAIN
    )
    if instruction.call_kind in ("direct", "pc_relative", "tail") and len(candidates) == 1:
        target_id = next(iter(candidates))
    else:
        kind = (
            "unresolved"
            if instruction.call_kind == "indirect" or len(candidates) > 1
            else "external"
        )
        raw = instruction.target_symbol or (
            hex(instruction.target) if instruction.target is not None else instruction.operands
        )
        target_id = f"proxy.{kind}:{image.id}_call_{instruction.address:x}"
        graph.nodes[target_id] = CodeUnit(
            target_id,
            raw,
            NodeKind.PROXY,
            SourceLocation(path, 0, 0),
            origin=NodeOrigin.PROXY,
            attributes=(("proxy_kind", kind), ("raw_symbol", raw), ("artifact", image.id)),
        )
        confidence = EdgeConfidence.UNCERTAIN
    graph.edges.append(
        CodeEdge(
            owner.node_id,
            target_id,
            EdgeKind.CALLS,
            confidence,
            attributes=(
                ("artifact", image.id),
                ("callsite", instruction.address),
                ("kind", instruction.call_kind),
                ("raw_target", instruction.operands),
                ("target_address", instruction.target),
            ),
        )
    )


def _link_imports(graph: CodeGraph, image: Image, ranges: list[BinaryRange], path: str) -> None:
    """Retain the entry actually called and a separate dynamic-binding edge."""
    definitions: dict[str, set[str]] = {}
    for row in ranges:
        if row.provenance not in ("import_stub", "linker_resolver"):
            for name in row.symbols:
                definitions.setdefault(name, set()).add(row.node_id)
    for row in ranges:
        symbol = dict(graph.nodes[row.node_id].attributes).get("import_symbol")
        if row.provenance != "import_stub" or not isinstance(symbol, str) or not symbol:
            continue
        candidates = definitions.get(symbol, set())
        if len(candidates) == 1:
            target = next(iter(candidates))
            confidence = EdgeConfidence.INFERRED
        else:
            target = f"proxy.external:{image.id}_stub_{row.start:x}"
            graph.nodes[target] = CodeUnit(
                target,
                symbol,
                NodeKind.PROXY,
                SourceLocation(path, 0, 0),
                origin=NodeOrigin.PROXY,
                attributes=(
                    ("proxy_kind", "external"),
                    ("raw_symbol", symbol),
                    ("artifact", image.id),
                ),
            )
            confidence = EdgeConfidence.UNCERTAIN
        graph.edges.append(
            CodeEdge(
                row.node_id,
                target,
                EdgeKind.CALLS,
                confidence,
                attributes=(
                    ("artifact", image.id),
                    ("kind", "linkage"),
                    ("stub_address", row.start),
                    ("import_symbol", symbol),
                ),
            )
        )


def _collect(
    image: Image,
    current: CodeGraph,
    graph: CodeGraph,
    options: BinaryImportOptions,
    objdump: Tool,
    symbolizer: Tool | None,
    debug: DebugFile | None,
    mappings: list[tuple[ExplicitMapping, int, int]],
    directory: Path,
    deadline: float,
    path: str,
) -> BinaryArtifact:
    linkage_diagnostics = prepare_linkage(image, objdump, options.limits, deadline)
    scans = _scan_spans(image, options)
    ranges = _ranges(image, scans, path, graph)
    range_index = IntervalIndex(ranges)
    _link_imports(graph, image, ranges, path)
    builder = MappingBuilder()
    diagnostics: list[BinaryDiagnostic] = list(linkage_diagnostics)
    resolver = SourceResolver(current, options.path_mappings)
    for explicit, start, end in mappings:
        covered = []
        for row in ranges:
            left, right = max(row.start, start), min(row.end, end)
            if left < right:
                builder.add(
                    BinarySourceMapping(
                        "", row.id, explicit.source_node_id, left, right, "explicit"
                    )
                )
                covered.append((left, right))
        cursor = start
        for left, right in sorted(covered):
            if left > cursor:
                break
            cursor = max(cursor, right)
        if cursor < end:
            raise ValueError("Explicit source mapping lies outside the imported code scope")
    if image.target.unsupported:
        diagnostics.append(BinaryDiagnostic("unsupported_target", image.target.unsupported))
    elif not image.has_debug and debug is None and not mappings:
        diagnostics.append(
            BinaryDiagnostic(
                "debug_information_missing",
                "No embedded or identity-checked external debug information; "
                "symbol links have no line evidence",
            )
        )
    if symbolizer is None and (image.has_debug or debug):
        diagnostics.append(
            BinaryDiagnostic(
                "symbolizer_missing",
                "Debug information is present but a compatible host symbolizer was not found",
            )
        )
    count = 0
    unowned_code = False
    scanned: list[tuple[int, int]] = []
    aliases: dict[str, set[str]] = {row.id: set(row.symbols) for row in ranges}
    if not image.target.unsupported:
        for span, mode in scans:
            if count >= options.limits.max_instructions or time.monotonic() >= deadline:
                diagnostics.append(
                    BinaryDiagnostic(
                        "import_limit",
                        "Import instruction/time limit reached",
                        span.start,
                        span.end,
                    )
                )
                break
            instructions, gaps, _ = decode(
                image,
                span,
                mode,
                objdump,
                options.limits,
                deadline,
                directory,
                max_instructions=options.limits.max_instructions - count,
            )
            diagnostics.extend(gaps)
            count += len(instructions)
            for instruction in instructions:
                if scanned and scanned[-1][1] == instruction.address:
                    scanned[-1] = (scanned[-1][0], instruction.address + instruction.size)
                else:
                    scanned.append((instruction.address, instruction.address + instruction.size))
            for batch_start in range(0, len(instructions), 4096):
                batch = instructions[batch_start : batch_start + 4096]
                frames = {}
                # PE CodeView paths are not followed implicitly: PDB data must
                # first pass the GUID/age check in prepare_debug.
                if symbolizer and (image.format != "PE" or debug or image.has_debug):
                    frames, gaps = symbolize(
                        symbolizer,
                        image,
                        [item.address for item in batch],
                        debug,
                        options.limits,
                        deadline,
                    )
                    diagnostics.extend(gaps)
                for instruction in batch:
                    owners = range_index.containing(
                        instruction.address, instruction.address + instruction.size
                    )
                    stack = frames.get(instruction.address, ())
                    explicit_here = any(
                        start < instruction.address + instruction.size and instruction.address < end
                        for _, start, end in mappings
                    )
                    for owner in owners:
                        _add_call(graph, image, owner, instruction, range_index, path)
                        if (
                            stack
                            and graph.nodes[owner.node_id].kind == NodeKind.FUNCTION
                            and stack[-1].function not in ("", "??")
                        ):
                            aliases[owner.id].add(stack[-1].function)
                        padding = instruction.mnemonic.startswith("nop") or (
                            image.target.architecture in ("i386", "x86_64")
                            and instruction.bytes == "6690"
                        )
                        if (
                            graph.nodes[owner.node_id].kind == NodeKind.BINARY_REGION
                            and owner.provenance != "linker_resolver"
                            and not padding
                        ):
                            unowned_code = True
                        for depth, frame in enumerate(stack):
                            if frame.line <= 0 or frame.file in ("", "??"):
                                continue
                            source_id = (
                                None if explicit_here else resolver.location(frame.file, frame.line)
                            )
                            builder.add(
                                BinarySourceMapping(
                                    "",
                                    owner.id,
                                    source_id,
                                    instruction.address,
                                    instruction.address + instruction.size,
                                    "debug",
                                    frame.file,
                                    frame.line,
                                    frame.column or None,
                                    frame.function,
                                    depth,
                                )
                            )
    if unowned_code or not image.functions:
        diagnostics.append(
            BinaryDiagnostic(
                "function_boundaries_unknown",
                "Some executable ranges have no established function boundaries",
            )
        )
    rows = builder.finish()
    mapped = {
        row.range_id for row in rows if row.source_node_id is not None and row.method != "explicit"
    }
    mapped_sources: dict[str, set[str | None]] = {}
    for mapping in rows:
        if mapping.method != "explicit":
            mapped_sources.setdefault(mapping.range_id, set()).add(mapping.source_node_id)
    for row in ranges:
        if graph.nodes[row.node_id].kind != NodeKind.FUNCTION or row.provenance == "import_stub":
            continue
        # Each physical alias is independent evidence. A debug frame for one
        # folded function must not suppress its siblings. Ambiguous individual
        # names still resolve to nothing, and explicit mappings retain priority.
        candidates = {
            source_id
            for name in row.symbols
            if (source_id := resolver.symbol((name,), macho=image.format == "Mach-O")) is not None
        }
        if row.id not in mapped:
            source_id = resolver.symbol(
                tuple(sorted(aliases[row.id])), macho=image.format == "Mach-O"
            )
            if source_id is not None:
                candidates.add(source_id)
        elif len(row.symbols) < 2:
            candidates.clear()
        already = mapped_sources.get(row.id, set())
        for source_id in sorted(candidates - already):
            parts = [(row.start, row.end)]
            for _, start, end in mappings:
                parts = [
                    (a, b)
                    for left, right in parts
                    for a, b in ((left, min(right, start)), (max(left, end), right))
                    if a < b
                ]
            for start, end in parts:
                builder.add(BinarySourceMapping("", row.id, source_id, start, end, "symbol"))
    rows = builder.finish()
    graph.binary_ranges.update(
        (row.id, replace(row, symbols=tuple(sorted(aliases[row.id])))) for row in ranges
    )
    graph.binary_source_mappings.update((row.id, row) for row in rows)
    correspondence_keys = set()
    for mapping in rows:
        if mapping.source_node_id is not None:
            row = graph.binary_ranges[mapping.range_id]
            key = (mapping.source_node_id, row.node_id, row.id, mapping.method)
            if key in correspondence_keys:
                continue
            correspondence_keys.add(key)
            graph.edges.append(
                CodeEdge(
                    mapping.source_node_id,
                    row.node_id,
                    EdgeKind.CORRESPONDS_TO,
                    EdgeConfidence.INFERRED,
                    attributes=(
                        ("artifact", image.id),
                        ("range_id", row.id),
                        ("method", mapping.method),
                    ),
                )
            )
    graph.subgraphs[f"binary:{image.id}"] = {row.node_id for row in ranges}
    versions = [("objdump", objdump.version)]
    if symbolizer:
        versions.append(("symbolizer", symbolizer.version))
    diagnostics = list(dict.fromkeys(diagnostics))
    return BinaryArtifact(
        id=image.id,
        path=path,
        sha256=image.sha256,
        format=image.format,
        architecture=image.target.architecture,
        bits=image.target.bits,
        byte_order=image.target.byte_order,
        image_base=image.image_base,
        slice_offset=image.slice_offset,
        slice_size=image.slice_size,
        slice_index=image.slice_index,
        cpu_subtype=image.cpu_subtype,
        abi=image.target.abi,
        mode=image.target.mode,
        build_id=image.build_id,
        uuid=image.uuid,
        debug_file=debug.path if debug else None,
        debug_sha256=debug.sha256 if debug else None,
        backend=objdump.family,
        objdump=objdump.path,
        symbolizer=symbolizer.path if symbolizer else None,
        tool_versions=tuple(versions),
        scanned_ranges=tuple(scanned),
        complete=not diagnostics,
        diagnostics=tuple(diagnostics),
        limits=options.limits,
    )


def augment_binary_file(
    store: GraphStore, path: str, *, options: BinaryImportOptions | None = None
) -> dict[str, Any]:
    """Stage every slice and evidence record before committing any graph changes."""
    options = options or BinaryImportOptions()
    deadline = time.monotonic() + options.limits.import_timeout
    original = str(Path(path).expanduser().resolve(strict=True))
    current = store._graph  # noqa: SLF001
    graph = CodeGraph()
    with tempfile.TemporaryDirectory(prefix="trailmark-binary-") as temp:
        directory = Path(temp)
        container = directory / "container"
        digest = snapshot(original, container, deadline)
        mappings = explicit_mappings(options.mapping_file, digest, current)
        images = load_images(container, digest, directory, options.slice_index)
        if (
            any(
                mapping.slice_index is not None
                and mapping.slice_index not in {image.slice_index for image in images}
                for mapping in mappings
            )
            and options.slice_index is None
        ):
            raise ValueError("Explicit binary mapping names a nonexistent slice")
        for image in images:
            objdump = select_objdump(options, deadline, image.target.architecture, image.format)
            debug = (
                prepare_debug(image, options.debug_file, directory, deadline)
                if options.debug_file
                else None
            )
            if debug and not debug.pdb:
                include_debug_symbols(image, debug.local_path)
            symbolizer = select_symbolizer(
                options,
                objdump,
                deadline,
                needs_llvm=image.format == "Mach-O" or bool(debug and debug.pdb),
            )
            explicit = _explicit_for_image(image, mappings, images)
            artifact = _collect(
                image,
                current,
                graph,
                options,
                objdump,
                symbolizer,
                debug,
                explicit,
                directory,
                deadline,
                original,
            )
            graph.binary_artifacts[artifact.id] = artifact
        if options.strict and any(
            not artifact.complete for artifact in graph.binary_artifacts.values()
        ):
            messages = "; ".join(
                diagnostic.message
                for artifact in graph.binary_artifacts.values()
                for diagnostic in artifact.diagnostics
            )
            raise ValueError(f"Incomplete native binary import: {messages}")
        # Recheck the source after tools have consumed the stable snapshot.
        if (
            snapshot(
                original,
                directory / "recheck",
                max(deadline, time.monotonic() + options.limits.tool_timeout),
            )
            != digest
        ):
            raise ValueError("Binary input changed during inspection")
    store.add_binary_graph(graph)
    diagnostics = [
        {"artifact": artifact.id, **asdict(diagnostic)}
        for artifact in graph.binary_artifacts.values()
        for diagnostic in artifact.diagnostics
    ]
    return {
        "artifacts": sorted(graph.binary_artifacts),
        "binary_nodes": sum(node.origin == NodeOrigin.BINARY for node in graph.nodes.values()),
        "ranges": len(graph.binary_ranges),
        "source_mappings": len(graph.binary_source_mappings),
        "call_edges": sum(edge.kind == EdgeKind.CALLS for edge in graph.edges),
        "correspondences": sum(edge.kind == EdgeKind.CORRESPONDS_TO for edge in graph.edges),
        "complete": all(artifact.complete for artifact in graph.binary_artifacts.values()),
        "diagnostics": diagnostics,
    }


def _restore_functions(image: Image, ranges: list[BinaryRange], store: GraphStore) -> None:
    """Reconstruct missing debug-only entries across all persisted ranges."""
    restored: dict[int, Function] = {}
    for row in ranges:
        node = store.unit(row.node_id)
        attributes = dict(node.attributes) if node else {}
        address = attributes.get("address")
        if not isinstance(address, int):
            continue
        previous = restored.get(address)
        restored[address] = Function(
            address,
            max(row.end - address, previous.size if previous else 0),
            tuple(sorted(set(row.symbols) | set(previous.names if previous else ()))),
            row.provenance,
            str(attributes.get("import_symbol") or ""),
        )
    known = {function.address: function for function in image.functions}
    for address, function in restored.items():
        if address not in known or function.provenance in ("import_stub", "linker_resolver"):
            known[address] = function
    image.functions = sorted(known.values(), key=lambda function: function.address)


def disassemble_binary(
    store: GraphStore,
    artifact_id: str,
    start: int,
    end: int,
    *,
    address_kind: str = "va",
    max_instructions: int = 256,
) -> BinaryDisassembly:
    """Revalidate bytes and decode from an established range boundary."""
    if (
        not isinstance(max_instructions, int)
        or isinstance(max_instructions, bool)
        or max_instructions <= 0
    ):
        raise ValueError("max_instructions must be a positive integer")
    selection = BinarySelection(start, end, address_kind)
    artifact = store.binary_artifact(artifact_id)
    if artifact is None:
        return BinaryDisassembly(artifact_id, start, end)
    deadline = time.monotonic() + artifact.limits.import_timeout
    with tempfile.TemporaryDirectory(prefix="trailmark-disassembly-") as temp:
        directory = Path(temp)
        container = directory / "container"
        if snapshot(artifact.path, container, deadline) != artifact.sha256:
            raise ValueError("Binary artifact content changed since ingestion")
        image = load_images(container, artifact.sha256, directory, artifact.slice_index)[0]
        start, end = image.selection(selection)
        ranges = sorted(
            store.binary_ranges_for_artifact(artifact_id), key=lambda row: (row.start, row.end)
        )
        selected = [row for row in ranges if row.start < end and start < row.end]
        cursor = start
        for row in selected:
            if row.start > cursor:
                raise ValueError(
                    "Disassembly request includes bytes outside the imported code scope"
                )
            cursor = max(cursor, row.end)
        if not selected or cursor < end:
            raise ValueError("Disassembly request is outside the imported code scope")
        tool = identify_tool(artifact.objdump, artifact.limits, deadline)
        _restore_functions(image, ranges, store)
        instructions: dict[int, BinaryInstruction] = {}
        diagnostics = []
        truncated = False
        for row in selected:
            if len(instructions) >= max_instructions:
                truncated = True
                break
            span = Span(row.start, row.end, row.file_offset, row.section)
            decoded, gaps, limited = decode(
                image,
                span,
                row.mode,
                tool,
                artifact.limits,
                deadline,
                directory,
                max_instructions=artifact.limits.max_instructions,
            )
            diagnostics.extend(gaps)
            truncated |= limited
            if row.start <= start < row.end and not any(item.address == start for item in decoded):
                if not gaps:
                    raise ValueError("Disassembly start is not an instruction boundary")
                diagnostics.append(
                    BinaryDiagnostic(
                        "instruction_boundary",
                        "Cannot establish the requested start as an instruction boundary",
                        start,
                        end,
                    )
                )
            for instruction in decoded:
                if instruction.address < end < instruction.address + instruction.size:
                    diagnostics.append(
                        BinaryDiagnostic(
                            "instruction_boundary",
                            "Disassembly end is not an instruction boundary",
                            instruction.address,
                            end,
                        )
                    )
                    continue
                if start <= instruction.address < end:
                    if len(instructions) >= max_instructions:
                        truncated = True
                        break
                    instructions[instruction.address] = instruction
        if truncated:
            diagnostics.append(
                BinaryDiagnostic("instruction_limit", "Disassembly result was truncated")
            )
        returned_end = max(
            (item.address + item.size for item in instructions.values()), default=start
        )
        source = tuple(
            mapping
            for mapping in store.binary_mappings_for_artifact(artifact_id)
            if mapping.start < returned_end and start < mapping.end
        )
        return BinaryDisassembly(
            artifact_id,
            start,
            end,
            tuple(instructions[key] for key in sorted(instructions)),
            source,
            truncated,
            tuple(dict.fromkeys(diagnostics)),
        )
