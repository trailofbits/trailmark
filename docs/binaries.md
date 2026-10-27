# Compiled binary evidence

Native ingestion connects source functions and lines to compiled bytes in linked
ELF executables/shared libraries, Mach-O images, and PE images. This feature is
unreleased. Install `trailmark[binary]` and host disassembly/symbolization tools;
the inspected image is never executed.

```bash
uv tool install 'trailmark[binary]'
trailmark binary build/app --source-root . --language auto --disassemble --json
trailmark augment . --language auto --binary build/app --binary build/lib.so --json
```

Native ingestion is explicit; directory parsing does not inspect binaries.
`augment_binary()` remains the existing JSON call-graph importer, with its
existing IDs and no optional dependency. See [augmentation](augmenting.md#binary-call-graphs).
Relocatable objects, static archives, standalone assembly parsing, similarity
matching, and basic-block control-flow graphs are outside this feature's scope.

## Targets and tools

| Architecture | Supported instruction modes and byte order |
| --- | --- |
| i386, x86-64 | little endian |
| ARM | ARM and Thumb, including mixed-mode images; little endian |
| AArch64 | little endian |
| RISC-V32, RISC-V64 | little endian, including compressed instructions |
| PowerPC64 | little and big endian, ELFv1 function descriptors and ELFv2 |
| s390x | big endian |
| MIPS32, MIPS64 | both byte orders; classic MIPS32r2/MIPS64r2, O32/N32/N64 |
| LoongArch64 | little endian |

This is the architecture union documented by
[constant-time-analysis](https://github.com/trailofbits/skills/tree/main/plugins/constant-time-analysis)
and [brotli-checker](https://github.com/trailofbits/brotli-checker/blob/main/docs/usage.md#compiled-decoder-evidence).
Container support also depends on the installed tool build. Unsupported ISA
modes, including microMIPS/MIPS16, produce diagnostics instead of guessed decoding.
ARM mapping symbols and function-symbol Thumb bits establish mode transitions;
explicit selections can supply `mode="arm"` or `mode="thumb"` when needed.
Known ARM data islands are excluded from instruction scans. Executable data that
the decoder cannot distinguish, such as some PowerPC traceback tables, may leave
an import incomplete.

Independent adapters use LLVM objdump with LLVM symbolizer or GNU objdump with
GNU addr2line. Families are detected from capabilities even when the executable
is named simply `objdump`. Automatic selection prefers a compatible LLVM build,
then GNU. Explicit `objdump` and `symbolizer` paths are honored; `backend`
constrains the disassembler family. GNU's available formats/debug information
depend on its installed build. Mach-O and PDB source locations require LLVM
symbolizer; missing capabilities are reported explicitly. See the
[LLVM objdump](https://llvm.org/docs/CommandGuide/llvm-objdump.html),
[LLVM symbolizer](https://llvm.org/docs/CommandGuide/llvm-symbolizer.html), and
[GNU binutils](https://sourceware.org/binutils/docs/binutils/) references.

## Python workflow

```python
from trailmark.models import BinaryImportOptions, BinaryLimits, BinarySelection
from trailmark.query.api import QueryEngine

engine = QueryEngine.from_directory("project", language="auto")
options = BinaryImportOptions(
    backend="auto",
    path_mappings=(("/build/project", "/home/me/project"),),
    limits=BinaryLimits(tool_timeout=30, import_timeout=300),
)
report = engine.augment_binary_file("build/app", options=options)
artifacts = engine.binary_artifacts()
ranges = engine.binary_ranges("verify", artifact=artifacts[0]["id"])
if ranges:
    row = ranges[0]
    source = engine.source_at_address(row["artifact_id"], row["start"])
    assembly = engine.disassemble_binary(row["artifact_id"], row["start"], row["end"])
```

`BinaryImportOptions`, `BinaryLimits`, and `BinarySelection` are frozen.
Options accept `backend`, explicit `objdump`/`symbolizer` paths, `debug_file`,
`mapping_file`, `path_mappings` prefix pairs, `slice_index`, a tuple of `ranges`,
`strict`, and `limits`. Selections contain `start`, `end`, `address_kind`, and
optional ARM `mode`. All supported universal Mach-O slices are inspected by
default; selecting a slice uses its zero-based container index. Tools always
consume the exact temporary thin slice, preserving CPU subtype identity.

The import report contains `artifacts` (IDs), `binary_nodes`, `ranges`,
`source_mappings`, `call_edges`, `correspondences`, `complete`, and `diagnostics`.
Artifact records retain SHA-256 identity, container/slice metadata, ABI and mode,
local debug identity, tool provenance, scanned coverage, and diagnostics.

## Addresses, ranges, and matching

All ranges are half-open: `start <= address < end`. Addresses are link-time
virtual addresses, never an implicit runtime load address. `address_kind="rva"`
adds the artifact's `image_base`; `address_kind="file_offset"` selects file-backed
executable bytes. Universal-image file offsets refer to the whole container,
including the slice offset. Unbacked memory and ambiguous conversions are rejected.

Native function IDs derive from artifact hash, exact slice, and entry address.
`binary_region` nodes represent executable bytes without ordinary function
boundaries, including known linker resolver regions. Functions keep `origin="binary"`. Discontiguous ranges
and overlapping inline mappings remain separate evidence. `binary_ranges()`
returns the actual mapped bytes for a source node, or owned bytes for a binary
node; `line` filters source occurrences. Mangled symbols and demangled aliases
are retained in range `symbols`. Instructions are query results, not graph nodes.
Shared bodies with multiple physical symbol names use a neutral `sub_<address>`
node name and retain every alias. Each unambiguous folded alias can add its own
inferred source correspondence even when debug information describes a sibling.
Explicit mappings take precedence within their byte ranges. Outside those ranges,
folded aliases can each receive symbol evidence, including an alias explicitly
mapped elsewhere in the body. Ambiguous individual names stay unmatched.

Matching uses explicit source-node mappings first, then remapped debug file/line
evidence, then unambiguous symbols or qualified names. Ambiguous candidates remain
unmatched. Raw source locations and inline frames remain available even without
a source-node match. Multiple compiled copies can correspond to one source
function, and several source functions can contribute to the same compiled range.
Each source link is an inferred `corresponds_to` edge with mapping provenance.
Debug paths and symbols do not prove that the current source tree matches the build.

Instruction scans retain compact coalesced source mappings and call sites.
Resolved direct calls connect binary functions; adjacent architectural
PC-relative call pairs are inferred. Indirect and unresolved sites retain
uncertain call evidence. Unconditional immediate branches into a different
established function or import entry are inferred `tail` transfers. Local jumps,
returns, transitions between matching hot/cold symbol fragments, and arbitrary
register data flow do not become inferred tail calls.

Named import entries remain distinct binary functions (`name@plt` or `name@stub`).
Calls retain the actual entry address in `target_address`. Separate `linkage`
edges describe dynamic binding: a unique local definition is inferred, while an
external binding remains an uncertain proxy. Linkage edges carry `stub_address`
instead of an invented instruction call site. Mach-O indirect-symbol metadata
and relocation-backed ELF objdump labels establish entry boundaries. Known
linker resolver regions do not by themselves make an import incomplete;
unrecognized layouts retain unknown regions and diagnostics. A certain direct
edge establishes a compiled destination address, not the source alias selected
by a folded body or the eventual dynamically bound implementation.

Calls are not a complete control-flow reconstruction.
Default traversal still follows only `calls`. Include `corresponds_to` explicitly
in mixed traversal, for example
`connect_subgraphs(source, target, edge_kinds=("calls", "corresponds_to"))`.

## Local debug companions and explicit mappings

Pass `debug_file` (CLI `--debug-file`) for local ELF debug files, dSYM files or
bundles, and PDB files. ELF build IDs or GNU debuglink filename/checksums,
Mach-O UUIDs, and PDB GUID/age must match before symbolization. Companions are
not downloaded. Source-path prefix remapping is explicit and uses the longest
matching path prefix. It does not search for a file by basename.

Stripped images can use a SHA-256-bound JSON mapping file:

```json
{
  "sha256": "<64 lowercase hex characters for the complete artifact>",
  "mappings": [
    {"source_node_id": "crypto:verify", "start": "0x1040", "end": "0x1080",
     "address_kind": "va"}
  ]
}
```

`start` and `end` may be integer JSON values or base-prefixed strings.
`address_kind` is `va` (default), `rva`, or `file_offset`. Universal-image VA/RVA
mappings require `slice_index`; whole-container file offsets can identify the
slice directly. The source node must already exist. Explicit mappings must fit
the imported executable scope. They establish correspondence without establishing
function boundaries or guessing similarity.

## Limits and failure behavior

Defaults are 30 seconds per host-tool invocation, five minutes per import,
64 MiB combined stdout/stderr per invocation, one million scanned instructions,
and 256 instructions per disassembly query. The image is hashed again before
later disassembly; changed content is rejected. Queries require established
instruction boundaries and imported code scope. Results include `instructions`,
`source_mappings`, `truncated`, `complete`, and diagnostics; truncation describes
the requested result, independently of artifact completeness.

Recoverable coverage gaps retain evidence with `complete=false`. Strict mode
rejects incomplete imports atomically. Invalid inputs, identity mismatches, and
collisions leave graph data and indexes unchanged. Repeated identical imports
are idempotent. Reimports retain previously observed gaps as provenance.
Completeness describes the inspected scope and available decoding/debug evidence;
it does not certify source/build equivalence or constant-time behavior.
