"""Real, nonexecuted linked-image evidence across the supported target union."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from tests.binary_oracle import aarch64_branches, inspect_image, merged_ranges
from trailmark.models import BinaryImportOptions, BinaryLimits, BinarySelection
from trailmark.query.api import QueryEngine

pytestmark = pytest.mark.binary_tools
ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests/fixtures/binary"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text())
ELF_IMAGES = [row for row in MANIFEST["images"] if row["format"] == "ELF"]
BACKENDS = (
    (os.environ["TRAILMARK_BINARY_BACKEND"],)
    if os.environ.get("TRAILMARK_BINARY_BACKEND")
    else ("llvm", "gnu")
)


def _required_or_skip(message: str) -> None:
    if os.environ.get("TRAILMARK_REQUIRE_BINARY_TOOLS"):
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture(scope="module", autouse=True)
def binary_dependency() -> None:
    if importlib.util.find_spec("lief") is None:
        _required_or_skip("Install trailmark[binary] to run native image integration tests")


def _tool(family: str, kind: str) -> str:
    configured = os.environ.get(f"TRAILMARK_{family.upper()}_{kind.upper()}")
    candidates = ([configured] if configured else []) + (
        [str(Path(os.environ["TRAILMARK_LLVM_OBJDUMP"]).parent / f"llvm-{kind}")] + [f"llvm-{kind}"]
        if family == "llvm" and os.environ.get("TRAILMARK_LLVM_OBJDUMP")
        else [f"llvm-{kind}"]
        if family == "llvm"
        else ["gobjdump", "objdump"]
        if kind == "objdump"
        else ["gaddr2line", "addr2line"]
    )
    for candidate in candidates:
        path = shutil.which(candidate)
        if path:
            version = subprocess.run([path, "--version"], capture_output=True, check=True).stdout  # noqa: S603
            if (family == "llvm" and b"LLVM" in version) or (family == "gnu" and b"GNU" in version):
                return path
    _required_or_skip(f"Missing {family} {kind}; set TRAILMARK_{family.upper()}_{kind.upper()}")
    raise AssertionError("unreachable")


@pytest.fixture(params=BACKENDS)
def options(request: pytest.FixtureRequest) -> BinaryImportOptions:
    family = request.param
    return BinaryImportOptions(
        backend=family,
        objdump=_tool(family, "objdump"),
        symbolizer=_tool(family, "symbolizer"),
        path_mappings=(("/trailmark-binary-fixture", str(ROOT)),),
    )


def source_engine() -> QueryEngine:
    return QueryEngine.from_directory(str(FIXTURES), language="c", detect_entrypoints_=False)


def test_fixture_manifest_hashes() -> None:
    assert len(ELF_IMAGES) >= 16
    for filename, digest in MANIFEST["sha256"].items():
        assert hashlib.sha256((FIXTURES / filename).read_bytes()).hexdigest() == digest, filename


def test_folded_functions_keep_each_source_alias(options: BinaryImportOptions) -> None:
    engine = source_engine()
    report = engine.augment_binary_file(str(FIXTURES / "elf-folded"), options=options)
    # LLD fills the inter-function alignment gap with int3. Retain its unknown
    # ownership instead of treating arbitrary trap bytes as proven padding.
    assert {d["code"] for d in report["diagnostics"]} <= {"function_boundaries_unknown"}
    left, right = engine.binary_ranges("folded_left"), engine.binary_ranges("folded_right")
    assert len(left) == len(right) == 1
    assert left[0]["node_id"] == right[0]["node_id"]
    assert {"folded_left", "folded_right"} <= set(left[0]["symbols"])
    node = engine._store.unit(left[0]["node_id"])
    assert node is not None and node.name.startswith("sub_")
    at = engine.source_at_address(left[0]["artifact_id"], left[0]["start"])
    assert {m["source_node_id"] for m in at if m["source_node_id"]} == {
        "folded:folded_left",
        "folded:folded_right",
    }
    assert all(
        e.confidence.value == "inferred"
        for e in engine._store._graph.edges
        if e.kind.value == "corresponds_to"
    )
    assert engine.binary_artifacts()[0]["image_base"] != 0
    for row in left + right:
        assert row["start"] != row["rva"]
        assert engine.source_at_address(
            row["artifact_id"], row["start"]
        ) == engine.source_at_address(row["artifact_id"], row["rva"], address_kind="rva")


def test_folded_aliases_respect_explicit_priority_and_ambiguity(
    options: BinaryImportOptions, tmp_path: Path
) -> None:
    image = FIXTURES / "elf-folded"
    oracle = inspect_image(image, _tool("llvm", "readobj"), _tool("llvm", "nm"))
    (address,) = oracle.symbols["folded_left"]
    mapping = tmp_path / "mapping.json"
    mapping.write_text(
        json.dumps(
            {
                "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                "mappings": [
                    {"source_node_id": "folded:folded_left", "start": address, "end": address + 4}
                ],
            }
        )
    )
    engine = source_engine()
    engine.augment_binary_file(str(image), options=replace(options, mapping_file=str(mapping)))
    assert engine.binary_ranges("folded_left")
    assert not engine.binary_ranges("folded_right")
    # Without debug lines, both aliases must receive the same uncovered bytes
    # even when one is also named by an interior explicit mapping.
    stripped = tmp_path / "folded-no-debug"
    subprocess.run(  # noqa: S603 -- Strip only the checked-in fixture with the selected LLVM tool.
        [_tool("llvm", "strip"), "--strip-debug", "-o", str(stripped), str(image)], check=True
    )
    mapping.write_text(
        json.dumps(
            {
                "sha256": hashlib.sha256(stripped.read_bytes()).hexdigest(),
                "mappings": [
                    {
                        "source_node_id": "folded:folded_left",
                        "start": address + 1,
                        "end": address + 2,
                    }
                ],
            }
        )
    )
    partial = source_engine()
    partial.augment_binary_file(str(stripped), options=replace(options, mapping_file=str(mapping)))
    artifact = partial.binary_artifacts()[0]["id"]
    for point in (address, address + 2, address + 3):
        assert {m["source_node_id"] for m in partial.source_at_address(artifact, point)} == {
            "folded:folded_left",
            "folded:folded_right",
        }
    assert {m["source_node_id"] for m in partial.source_at_address(artifact, address + 1)} == {
        "folded:folded_left"
    }
    for name in ("a", "b"):
        (tmp_path / f"{name}.c").write_text("int folded_left(int v) { return v; }")
    (tmp_path / "right.c").write_text("int folded_right(int v) { return v; }")
    engine = QueryEngine.from_directory(str(tmp_path), language="c", detect_entrypoints_=False)
    engine.augment_binary_file(str(image), options=options)
    assert not engine.binary_ranges("folded_left")
    assert engine.binary_ranges("folded_right")


def test_nonzero_base_and_fat_offsets_match_independent_metadata(
    options: BinaryImportOptions,
) -> None:
    readobj, nm = _tool("llvm", "readobj"), _tool("llvm", "nm")
    oracle = inspect_image(FIXTURES / "elf-folded", readobj, nm)
    engine = source_engine()
    engine.augment_binary_file(str(FIXTURES / "elf-folded"), options=options)
    artifact = engine.binary_artifacts()[0]
    assert artifact["image_base"] == oracle.image_base != 0
    assert merged_ranges(artifact["scanned_ranges"]) == merged_ranges(
        [(s.start, s.end) for s in oracle.sections]
    )
    for address in oracle.symbols["folded_left"]:
        assert engine.source_at_address(artifact["id"], address) == engine.source_at_address(
            artifact["id"], address - oracle.image_base, address_kind="rva"
        )
        assert engine.source_at_address(artifact["id"], address) == engine.source_at_address(
            artifact["id"], oracle.offset(address), address_kind="file_offset"
        )


def test_fat_offsets_match_thin_image_metadata() -> None:
    readobj, nm = _tool("llvm", "readobj"), _tool("llvm", "nm")
    engine = source_engine()
    engine.augment_binary_file(
        str(FIXTURES / "macho-universal"),
        options=BinaryImportOptions(objdump=_tool("llvm", "objdump")),
    )
    payload = (FIXTURES / "macho-universal").read_bytes()
    for artifact in engine.binary_artifacts():
        thin = "macho-arm64" if artifact["architecture"] == "aarch64" else "macho-x86_64"
        oracle = inspect_image(FIXTURES / thin, readobj, nm)
        _, _, offset, _, _ = struct.unpack_from(">IIIII", payload, 8 + artifact["slice_index"] * 20)
        assert artifact["image_base"] == oracle.image_base != 0
        assert artifact["slice_offset"] == offset
        row = engine.binary_ranges("helper", artifact=artifact["id"])[0]
        assert row["start"] == oracle.symbols["_helper"][0]
        assert row["file_offset"] == oracle.offset(row["start"]) + offset
        assert engine.source_at_address(artifact["id"], row["start"]) == engine.source_at_address(
            artifact["id"], row["file_offset"], address_kind="file_offset"
        )


def test_gcc_hot_cold_fragment_transfer_is_not_a_tail_call() -> None:
    image = FIXTURES / "macho-cold.dylib"
    oracle = inspect_image(image, _tool("llvm", "readobj"), _tool("llvm", "nm"))
    (hot,), (cold,) = oracle.symbols["_cold_split"], oracle.symbols["_cold_split.cold"]
    engine = source_engine()
    engine.augment_binary_file(
        str(image), options=BinaryImportOptions(backend="llvm", objdump=_tool("llvm", "objdump"))
    )
    artifact = engine.binary_artifacts()[0]["id"]
    instructions = engine.disassemble_binary(artifact, hot, cold)["instructions"]
    transfers = [i for i in instructions if i["target"] == cold or i["mnemonic"] == "b"]
    assert len(transfers) == 1
    assert aarch64_branches(image, oracle, hot, cold) == {(transfers[0]["address"], cold)}
    assert transfers[0]["call_kind"] is None
    assert not any(dict(e.attributes).get("kind") == "tail" for e in engine._store._graph.edges)


@pytest.mark.parametrize(
    "options,filename",
    [
        (backend, filename)
        for backend in BACKENDS
        for filename in ("elf-linkage.so", "macho-linkage.dylib")
        if backend == "llvm" or filename.startswith("elf")
    ],
    indirect=["options"],
)
def test_linker_entries_and_inferred_tail_transfers_are_distinct(
    options: BinaryImportOptions, filename: str
) -> None:
    if filename.startswith("macho"):
        options = replace(options, debug_file=str(FIXTURES / "macho-linkage.dsym"))
    engine = source_engine()
    report = engine.augment_binary_file(str(FIXTURES / filename), options=options)
    assert report["complete"], report["diagnostics"]
    graph = engine._store._graph
    stubs = [n for n in graph.nodes.values() if dict(n.attributes).get("import_symbol")]
    first = next(
        n for n in stubs if str(dict(n.attributes)["import_symbol"]).lstrip("_") == "imported_one"
    )
    second = next(
        n for n in stubs if str(dict(n.attributes)["import_symbol"]).lstrip("_") == "imported_two"
    )
    assert first.id != second.id
    assert first.name.endswith(("@plt", "@stub"))
    assert any(
        e.source_id == first.id
        and e.confidence.value == "uncertain"
        and dict(e.attributes).get("kind") == "linkage"
        for e in graph.edges
    )
    tail = engine.binary_ranges("linked_tail")[0]
    edges = [
        e
        for e in graph.edges
        if e.source_id == tail["node_id"] and dict(e.attributes).get("kind") == "tail"
    ]
    assert len(edges) == 1 and edges[0].confidence.value == "inferred"
    assert "linked_helper" in graph.nodes[edges[0].target_id].name
    instructions = engine.disassemble_binary(tail["artifact_id"], tail["start"], tail["end"])[
        "instructions"
    ]
    assert any(i["call_kind"] == "tail" for i in instructions)


@pytest.mark.parametrize("profile", ELF_IMAGES, ids=lambda row: row["file"])
def test_all_architectures_link_source_calls_and_instruction_bytes(
    options: BinaryImportOptions,
    profile: dict[str, str],
) -> None:
    engine = source_engine()
    image = FIXTURES / profile["file"]
    report = engine.augment_binary_file(str(image), options=options)
    artifact = engine.binary_artifacts()[0]
    assert artifact["architecture"] == profile["architecture"]
    assert artifact["format"] == "ELF"
    assert report["correspondences"] >= 3
    assert not {item["code"] for item in report["diagnostics"]} & {
        "tool_error",
        "unsupported_target",
        "symbolization_error",
        "symbolization_output",
    }
    if profile["file"] == "elf-mipsn32":
        assert artifact["bits"] == 32
        assert artifact["abi"] == "n32"
    entry = engine.binary_ranges("entry")
    assert entry
    row = entry[0]
    binary_id = row["node_id"]
    assert any(
        node["name"] == "helper" and node.get("origin") == "binary"
        for node in engine.callees_of(binary_id)
    )
    result = engine.disassemble_binary(artifact["id"], row["start"], row["end"])
    assert result["instructions"]
    data = image.read_bytes()
    for instruction in result["instructions"]:
        offset, size = instruction["file_offset"], instruction["size"]
        assert data[offset : offset + size].hex() == instruction["bytes"]
        assert instruction["rva"] == instruction["address"] - artifact["image_base"]
    dispatch = engine.binary_ranges("dispatch")[0]
    assert engine.unresolved_calls(dispatch["node_id"])
    # A source-to-binary correspondence does not become a call edge.
    assert all(not node["id"].startswith("bin.") for node in engine.callees_of("entry"))


def test_bidirectional_inline_lines_and_half_open_addresses(options: BinaryImportOptions) -> None:
    engine = source_engine()
    engine.augment_binary_file(str(FIXTURES / "elf-x86_64"), options=options)
    rows = engine.binary_ranges("inline_helper", line=6)
    assert rows
    for row in rows:
        at = engine.source_at_address(row["artifact_id"], row["start"])
        assert any(item["source_node_id"].endswith(":inline_helper") for item in at)
        assert any(item["inline_depth"] > 0 for item in at)
        ids = {item["id"] for item in at}
        assert {
            item["id"]
            for item in engine.source_at_address(row["artifact_id"], row["rva"], address_kind="rva")
        } == ids
        assert {
            item["id"]
            for item in engine.source_at_address(
                row["artifact_id"], row["file_offset"], address_kind="file_offset"
            )
        } == ids
        for mapping in at:
            assert mapping["id"] not in {
                item["id"] for item in engine.source_at_address(row["artifact_id"], mapping["end"])
            }
    assert engine.binary_ranges("missing") == []
    assert engine.source_at_address("missing", 0) == []
    assert engine.disassemble_binary("missing", 0, 1)["instructions"] == ()


def test_reimport_is_idempotent_and_export_contains_evidence(options: BinaryImportOptions) -> None:
    engine = source_engine()
    image = str(FIXTURES / "elf-x86_64")
    engine.augment_binary_file(image, options=options)
    before = engine.to_json()
    engine.augment_binary_file(image, options=options)
    assert engine.to_json() == before
    exported = json.loads(before)
    assert exported["binary_artifacts"]
    assert exported["binary_ranges"]
    assert exported["binary_source_mappings"]


def test_query_limits_and_instruction_boundaries(options: BinaryImportOptions) -> None:
    engine = source_engine()
    engine.augment_binary_file(str(FIXTURES / "elf-x86_64"), options=options)
    row = engine.binary_ranges("entry")[0]
    full = engine.disassemble_binary(row["artifact_id"], row["start"], row["end"])
    limited = engine.disassemble_binary(
        row["artifact_id"], row["start"], row["end"], max_instructions=1
    )
    assert len(limited["instructions"]) == 1
    assert limited["truncated"]
    call = next(item for item in full["instructions"] if item["call_kind"] == "direct")
    with pytest.raises(ValueError, match="instruction boundary"):
        engine.disassemble_binary(row["artifact_id"], call["address"] + 1, row["end"])
    partial = engine.disassemble_binary(row["artifact_id"], row["start"], call["address"] + 2)
    assert any(item["code"] == "instruction_boundary" for item in partial["diagnostics"])


def test_changed_artifact_is_rejected(options: BinaryImportOptions, tmp_path: Path) -> None:
    image = tmp_path / "image"
    shutil.copyfile(FIXTURES / "elf-x86_64", image)
    engine = source_engine()
    engine.augment_binary_file(str(image), options=options)
    row = engine.binary_ranges("entry")[0]
    with image.open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="content changed"):
        engine.disassemble_binary(row["artifact_id"], row["start"], row["end"])


def test_stripped_debug_companion_and_identity_failure(options: BinaryImportOptions) -> None:
    engine = source_engine()
    image = str(FIXTURES / "elf-x86_64-stripped")
    report = engine.augment_binary_file(
        image, options=replace(options, debug_file=str(FIXTURES / "elf-x86_64.debug"))
    )
    assert report["correspondences"] >= 3
    assert report["complete"], report["diagnostics"]
    assert engine.binary_ranges("entry")
    before = engine.to_json()
    with pytest.raises(ValueError, match="build ID"):
        engine.augment_binary_file(
            image, options=replace(options, debug_file=str(FIXTURES / "elf-arm"))
        )
    assert engine.to_json() == before


def test_debuglink_checksum_fallback(options: BinaryImportOptions, tmp_path: Path) -> None:
    engine = source_engine()
    image = str(FIXTURES / "elf-x86_64-debuglink")
    engine.augment_binary_file(
        image, options=replace(options, debug_file=str(FIXTURES / "elf-x86_64.debug"))
    )
    assert engine.binary_ranges("entry")
    mismatch = tmp_path / "elf-x86_64.debug"
    mismatch.write_bytes((FIXTURES / mismatch.name).read_bytes() + b"changed")
    before = engine.to_json()
    with pytest.raises(ValueError, match="checksum"):
        engine.augment_binary_file(image, options=replace(options, debug_file=str(mismatch)))
    assert engine.to_json() == before


def test_verified_debug_can_add_function_ownership_to_a_selected_stripped_region(
    options: BinaryImportOptions,
) -> None:
    reference = source_engine()
    reference.augment_binary_file(str(FIXTURES / "elf-x86_64"), options=options)
    row = reference.binary_ranges("entry")[0]
    selected = replace(options, ranges=(BinarySelection(row["start"], row["end"]),))
    image = str(FIXTURES / "elf-x86_64-stripped")
    engine = source_engine()
    engine.augment_binary_file(image, options=selected)
    engine.augment_binary_file(
        image, options=replace(selected, debug_file=str(FIXTURES / "elf-x86_64.debug"))
    )
    mapped = engine.binary_ranges("entry")
    assert mapped
    assert all(item["node_id"].split(":")[-1].startswith("sub_") for item in mapped)
    graph = engine._store._graph  # noqa: SLF001
    assert len(graph.binary_ranges) == 2
    assert len({item.node_id for item in graph.binary_ranges.values()}) == 2


@pytest.mark.parametrize("filename", ["elf-x86_64-stripped", "elf-x86_64-sectionless"])
def test_explicit_file_offset_mapping_of_stripped_regions(
    options: BinaryImportOptions,
    tmp_path: Path,
    filename: str,
) -> None:
    engine = source_engine()
    reference = source_engine()
    reference.augment_binary_file(str(FIXTURES / "elf-x86_64"), options=options)
    row = reference.binary_ranges("entry")[0]
    source_id = engine._store.find_node_id("entry")  # noqa: SLF001
    image = FIXTURES / filename
    mapping = tmp_path / "mapping.json"
    mapping.write_text(
        json.dumps(
            {
                "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                "mappings": [
                    {
                        "source_node_id": source_id,
                        "start": row["file_offset"],
                        "end": row["file_offset"] + row["end"] - row["start"],
                        "address_kind": "file_offset",
                    }
                ],
            }
        )
    )
    configured = replace(
        options, mapping_file=str(mapping), ranges=(BinarySelection(row["start"], row["end"]),)
    )
    report = engine.augment_binary_file(str(image), options=configured)
    assert report["correspondences"] == 1
    mapped = engine.binary_ranges("entry")[0]
    assert (mapped["start"], mapped["end"]) == (row["start"], row["end"])
    assert mapped["source_mappings"][0]["method"] == "explicit"
    assert engine.disassemble_binary(mapped["artifact_id"], mapped["start"], mapped["end"])[
        "instructions"
    ]
    before = engine.to_json()
    mapping.write_text(json.dumps({"sha256": "0" * 64, "mappings": []}))
    with pytest.raises(ValueError, match="SHA-256"):
        engine.augment_binary_file(str(image), options=configured)
    assert engine.to_json() == before


def test_incomplete_import_is_atomic_in_strict_mode(options: BinaryImportOptions) -> None:
    engine = source_engine()
    before = engine.to_json()
    configured = replace(options, strict=True, limits=BinaryLimits(max_instructions=1))
    with pytest.raises(ValueError, match="Incomplete"):
        engine.augment_binary_file(str(FIXTURES / "elf-x86_64"), options=configured)
    assert engine.to_json() == before
    report = engine.augment_binary_file(
        str(FIXTURES / "elf-x86_64"), options=replace(configured, strict=False)
    )
    assert not report["complete"]
    assert any(item["code"] == "instruction_limit" for item in report["diagnostics"])


@pytest.mark.parametrize(
    "filename,debug_file",
    [
        ("macho-arm64", "macho-arm64.dsym"),
        ("macho-x86_64", "macho-x86_64.dsym"),
        ("pe-x86_64.exe", "pe-x86_64.pdb"),
    ],
)
def test_macho_and_pe_debug_identity_and_source_ranges(filename: str, debug_file: str) -> None:
    engine = source_engine()
    options = BinaryImportOptions(
        objdump=_tool("llvm", "objdump"),
        symbolizer=_tool("llvm", "symbolizer"),
        debug_file=str(FIXTURES / debug_file),
        path_mappings=(("/trailmark-binary-fixture", str(ROOT)),),
    )
    report = engine.augment_binary_file(str(FIXTURES / filename), options=options)
    assert report["complete"], report["diagnostics"]
    assert report["correspondences"] >= 3
    row = engine.binary_ranges("entry")[0]
    assert engine.disassemble_binary(row["artifact_id"], row["start"], row["end"])["instructions"]
    assert any(item["method"] == "debug" for item in row["source_mappings"])
    if filename.startswith("macho"):
        assert row["start"] > row["rva"]
        other = "macho-x86_64.dsym" if "arm64" in filename else "macho-arm64.dsym"
        before = engine.to_json()
        with pytest.raises(ValueError, match="UUID"):
            engine.augment_binary_file(
                str(FIXTURES / filename), options=replace(options, debug_file=str(FIXTURES / other))
            )
        assert engine.to_json() == before


def test_universal_slices_and_container_offsets() -> None:
    engine = source_engine()
    options = BinaryImportOptions(
        objdump=_tool("llvm", "objdump"), symbolizer=_tool("llvm", "symbolizer")
    )
    report = engine.augment_binary_file(str(FIXTURES / "macho-universal"), options=options)
    artifacts = engine.binary_artifacts()
    assert len(report["artifacts"]) == 2
    assert len({row["id"] for row in artifacts}) == 2
    assert len({row["uuid"] for row in artifacts}) == 2
    for artifact in artifacts:
        row = engine.binary_ranges("entry", artifact=artifact["id"])[0]
        assert row["file_offset"] >= artifact["slice_offset"]
        result = engine.disassemble_binary(artifact["id"], row["start"], row["end"])
        for instruction in result["instructions"]:
            data = (FIXTURES / "macho-universal").read_bytes()
            offset = instruction["file_offset"]
            assert data[offset : offset + instruction["size"]].hex() == instruction["bytes"]


def test_unsupported_mips_mode_does_not_guess(options: BinaryImportOptions, tmp_path: Path) -> None:
    payload = bytearray((FIXTURES / "elf-mipsel").read_bytes())
    flags = struct.unpack_from("<I", payload, 36)[0]
    struct.pack_into("<I", payload, 36, flags | 0x02000000)
    image = tmp_path / "micromips"
    image.write_bytes(payload)
    engine = source_engine()
    report = engine.augment_binary_file(str(image), options=options)
    assert not report["complete"]
    assert any(item["code"] == "unsupported_target" for item in report["diagnostics"])
    assert not engine.binary_artifacts()[0]["scanned_ranges"]


def test_binary_cli_json_and_augment(options: BinaryImportOptions) -> None:
    assert options.objdump and options.symbolizer
    common = [
        "--objdump",
        options.objdump,
        "--symbolizer",
        options.symbolizer,
        "--path-map",
        f"/trailmark-binary-fixture={ROOT}",
    ]
    result = subprocess.run(  # noqa: S603 -- Invoke the installed CLI on inert fixtures.
        [
            sys.executable,
            "-c",
            "from trailmark.cli import main; main()",
            "binary",
            str(FIXTURES / "elf-arm"),
            "--source-root",
            str(FIXTURES),
            "--language",
            "c",
            "--function",
            "entry",
            "--disassemble",
            "--json",
            *common,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["report"]["complete"]
    assert data["disassembly"][0]["instructions"]
    assert data["graph"]["binary_artifacts"]
    result = subprocess.run(  # noqa: S603 -- Invoke the installed CLI on inert fixtures.
        [
            sys.executable,
            "-c",
            "from trailmark.cli import main; main()",
            "augment",
            str(FIXTURES),
            "-l",
            "c",
            "--binary",
            str(FIXTURES / "elf-arm"),
            "--json",
            *common,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["binary_artifacts"]
    assert "Binary" in result.stderr


def test_mixed_arm_thumb_instruction_modes(options: BinaryImportOptions) -> None:
    engine = source_engine()
    report = engine.augment_binary_file(str(FIXTURES / "elf-mixed-arm"), options=options)
    assert report["complete"], report["diagnostics"]
    for function, mode in (("arm_caller", "arm"), ("thumb_callee", "thumb")):
        row = engine.binary_ranges(function)[0]
        assert row["mode"] == mode
        result = engine.disassemble_binary(row["artifact_id"], row["start"], row["end"])
        assert result["instructions"]
        assert all(item["mode"] == mode for item in result["instructions"])
    caller = engine.binary_ranges("arm_caller")[0]["node_id"]
    assert any(node["name"] == "thumb_callee" for node in engine.callees_of(caller))


def test_powerpc64_descriptor_addresses_are_code_addresses(options: BinaryImportOptions) -> None:
    engine = source_engine()
    engine.augment_binary_file(str(FIXTURES / "elf-ppc64-descriptors"), options=options)
    row = engine.binary_ranges("descriptor_entry")[0]
    assert row["provenance"] == "function_descriptor"
    result = engine.disassemble_binary(row["artifact_id"], row["start"], row["end"])
    assert result["instructions"][0]["mnemonic"] == "mflr"
    assert any(node["name"] == "descriptor_helper" for node in engine.callees_of(row["node_id"]))


def test_shared_library_and_discontiguous_selections(options: BinaryImportOptions) -> None:
    engine = source_engine()
    engine.augment_binary_file(str(FIXTURES / "elf-x86_64.so"), options=options)
    assert engine.binary_ranges("helper")
    reference = source_engine()
    reference.augment_binary_file(str(FIXTURES / "elf-x86_64"), options=options)
    row = reference.binary_ranges("entry")[0]
    decoded = reference.disassemble_binary(row["artifact_id"], row["start"], row["end"])
    first, last = decoded["instructions"][0], decoded["instructions"][-1]
    selected = (
        BinarySelection(first["address"], first["address"] + first["size"]),
        BinarySelection(last["address"], last["address"] + last["size"]),
    )
    engine = source_engine()
    engine.augment_binary_file(
        str(FIXTURES / "elf-x86_64"), options=replace(options, ranges=selected)
    )
    native_id = engine.binary_ranges("entry")[0]["node_id"]
    assert len(engine.binary_ranges(native_id)) == 2
    with pytest.raises(ValueError, match="scope"):
        engine.disassemble_binary(row["artifact_id"], first["address"], last["address"])


def test_same_cpu_universal_slices_are_selected_exactly() -> None:
    options = BinaryImportOptions(objdump=_tool("llvm", "objdump"))
    engine = source_engine()
    engine.augment_binary_file(str(FIXTURES / "macho-same-cpu"), options=options)
    artifacts = engine.binary_artifacts()
    assert len(artifacts) == 2
    assert len({item["cpu_subtype"] for item in artifacts}) == 2
    assert {item["architecture"] for item in artifacts} == {"x86_64"}
    selected = source_engine()
    selected.augment_binary_file(
        str(FIXTURES / "macho-same-cpu"), options=replace(options, slice_index=1)
    )
    artifact = selected.binary_artifacts()[0]
    assert artifact["slice_index"] == 1
    row = selected.binary_ranges("entry")[0]
    result = selected.disassemble_binary(artifact["id"], row["start"], row["end"])
    assert result["instructions"]
    assert all(item["file_offset"] >= artifact["slice_offset"] for item in result["instructions"])


def test_universal_cpu_metadata_mismatch_is_atomic(tmp_path: Path) -> None:
    payload = bytearray((FIXTURES / "macho-same-cpu").read_bytes())
    struct.pack_into(">I", payload, 12, 99)
    image = tmp_path / "mismatch"
    image.write_bytes(payload)
    engine = source_engine()
    before = engine.to_json()
    with pytest.raises(ValueError, match="CPU/subtype"):
        engine.augment_binary_file(str(image))
    assert engine.to_json() == before


def test_pdb_guid_age_mismatch_is_atomic(tmp_path: Path) -> None:
    import uuid

    from trailmark.analysis.native_binary.debug import pdb_identity

    original = FIXTURES / "pe-x86_64.pdb"
    payload = bytearray(original.read_bytes())
    guid, _ = pdb_identity(original)
    offset = payload.index(uuid.UUID(guid).bytes_le)
    payload[offset] ^= 0x80
    mismatch = tmp_path / "mismatch.pdb"
    mismatch.write_bytes(payload)
    options = BinaryImportOptions(objdump=_tool("llvm", "objdump"), debug_file=str(mismatch))
    engine = source_engine()
    before = engine.to_json()
    with pytest.raises(ValueError, match="GUID/age"):
        engine.augment_binary_file(str(FIXTURES / "pe-x86_64.exe"), options=options)
    assert engine.to_json() == before


@pytest.mark.parametrize("contents", [b"not an executable", b"\x7fELF", b"!<arch>\n"])
def test_malformed_and_excluded_containers_are_atomic(
    options: BinaryImportOptions, tmp_path: Path, contents: bytes
) -> None:
    image = tmp_path / "invalid"
    image.write_bytes(contents)
    engine = source_engine()
    before = engine.to_json()
    with pytest.raises(ValueError):
        engine.augment_binary_file(str(image), options=options)
    assert engine.to_json() == before
