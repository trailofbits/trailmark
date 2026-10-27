# Native fixtures

These tiny freestanding images are inspected statically and never executed.
They contain only the adjacent Apache-2.0 source, compiler metadata, and linker
bookkeeping. `manifest.json` records SHA-256 hashes and the compiler version.

Rebuild from the repository root with LLVM/LLD (the checked-in set uses
Homebrew LLVM 23.1.4 and LLD 23.1.4):

```bash
LLVM_BIN=/path/to/llvm/bin LLD_BIN=/path/to/lld/bin \
  python tests/build_binary_fixtures.py
```

The generator requires clang, llvm-objcopy, llvm-strip, llvm-lipo, dsymutil,
ld.lld, ld64.lld, and lld-link. No target sysroots or cross execution are needed.
Repeat builds with the same compiler/linker builds and tool installation paths
produce identical hashes; PDB records retain the linker installation path.
DWARF4 avoids a GNU compatibility warning for clang 23's DWARF5 file tables.

The main profiles cover all 15 requested architecture/mode cases with linked
ELF images. Additional cases include MIPS N32, compressed RISC-V instructions,
mixed ARM/Thumb interworking, a shared library, stripped/debuglink and
section-header-free images, Mach-O dSYM files and universal images, and PE/PDB.
ELF source fixtures use PIE where the target driver enables it. The universal
same-CPU fixture packages x86-64 ALL and Haswell CPU subtypes with identical
baseline instructions to require exact slice selection.

`elf-folded` uses identical-code folding at a nonzero image base. The linkage
images exercise distinct imports, local dynamic bindings, tail transfers, and
linker resolver regions. Their source and all image/debug-file hashes are also
recorded in the manifest. Independent `llvm-readobj`/`llvm-nm` checks validate
section coverage, virtual/RVA translation, and fat-image file offsets.

`descriptors.S` supplies ELFv1 PowerPC64 function descriptors. LLD rejects
ELFv1 inputs, so the generator temporarily sets ELFv2 flags for linking its
ABI-independent absolute data relocations/local code branches, then restores
ELFv1 in the linked image. The exported function symbols point into `.opd`;
calls target the actual code. The ordinary PowerPC64 C profile uses ELFv2.

Dedicated CI runs both GNU and LLVM against the checked-in linked images and
fails if tools, target decoders, or optional dependencies are missing. Local
tests can opt into the same requirement with `TRAILMARK_REQUIRE_BINARY_TOOLS=1`.
`cold-arm64.s` preserves the instruction and symbol layout emitted by GCC
16.2.0 for `cold.c` with `-O2 -fno-exceptions -freorder-blocks-and-partition`.
Debug and SDK-version directives are removed so LLVM can reproducibly assemble
the snapshot without requiring GCC or a macOS SDK. `macho-cold.dylib` retains
both hot and `.cold` entries; their connecting branch is not a tail call.
