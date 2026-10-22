# Installation

```bash
uv tool install trailmark        # the CLI, in its own environment
uv add trailmark                 # the library, in a uv-managed project
```

Trailmark requires Python &ge; 3.12. CI tests Python 3.12 through 3.15.
Wheel and source-distribution smoke tests run on Python 3.13 and 3.15 on
Linux and macOS, including the bundled native grammars.

## Grammars

Trailmark uses three sources of tree-sitter grammars.

**`tree-sitter-language-pack`** provides most languages. Trailmark requires
`tree-sitter-language-pack` 1.22.1 or newer. The package downloads each grammar
the first time it is used and caches it. Root-owned grammar caches are
supported when they are not group- or other-writable, which lets a system-wide
install share one read-only cache.

**`tree-sitter-sql`** provides the SQL grammar. It ships as a wheel dependency
and does not use the language-pack cache.

**Bundled grammars** for Circom, Miden Assembly, Move, and Verus ship as C sources
inside Trailmark. See [below](#bundled-grammars).

### Offline and TLS-inspected networks

Grammar downloads use the platform certificate store and honor `HTTPS_PROXY`.
If the target machine cannot download grammars, populate the cache on a
machine with the same platform and copy it over:

```bash
python -c "import tree_sitter_language_pack as p; p.download_all()"
```

Then copy the resulting `tree-sitter-language-pack` cache directory to the same
location on the target machine.

## Bundled grammars

The bundled Circom, Miden Assembly, Move, and Verus grammars are compiled the first
time a file needs them. This requires a C compiler (`cc`), Python development
headers, and write access to the installed grammar directories. Rust files
without `verus!` blocks never trigger compilation.

If the Verus grammar cannot be built or loaded, Trailmark logs a warning, skips
Verus blocks, records a structured parse diagnostic, and continues parsing ordinary Rust.
Strict parsing rejects that diagnostic. A compiler failure's warning
includes the exit status and stderr.

Compiled bindings are cached under a key derived from the compiled source
contents, Python ABI, platform, and compiler options. When any of those change,
Trailmark builds a new binding. Completed builds are published atomically, so
concurrent processes do not observe a partial build.

## Nix

```bash
nix run github:trailofbits/trailmark -- analyze --summary path/to/project
nix develop github:trailofbits/trailmark    # shell with trailmark and uv
```

The Nix package is built from `uv.lock`, precompiles the bundled grammars for
the read-only Nix store, and uses a root-owned language-pack grammar cache.

## From a git checkout

A checkout keeps the Circom, Move, and Verus grammar definitions and generates
their C parsers during the build with Tree-sitter CLI 0.27.1, which needs Node.js.
Circom and Move use ABI 14; Verus uses ABI 15:

```bash
npm ci                     # installs the pinned Tree-sitter CLI
uv sync --all-groups
uv run trailmark --version
```

Before `uv sync` or `uv build`, run `npm ci` with npm install scripts enabled;
the Tree-sitter CLI package downloads its binary from an install script. If
your npm configuration disables scripts, run
`npm rebuild tree-sitter-cli --ignore-scripts=false` after `npm ci`.

The generated parser and headers are gitignored. Editable `uv` installs rerun
the build hook to restore missing generated files, and reuse existing output
when inputs are unchanged. Wheels and source distributions include the
generated sources, so installing published packages needs neither Node.js nor
the Tree-sitter CLI.
