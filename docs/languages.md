# Language notes

The [language table](../README.md#supported-languages) lists what each parser
extracts. This page covers the languages whose behavior needs more
explanation. Rules shared by every language, such as node IDs, overloads,
header handling, and cross-file linking, are in [Graph model](graph-model.md).

- [Bash](#bash)
- [Rust and Verus](#rust-and-verus)
- [Zig](#zig)
- [Solidity persistent state](#solidity-persistent-state)
- [Aiken](#aiken)
- [Julia](call-resolution.md#julia)
- [TypeScript](#typescript)
- [Perl](#perl)
- [Lua](#lua)
- [R](#r)
- [SQL](#sql)
- [PowerShell](#powershell)
- [C, C++, and Objective-C headers](#c-c-and-objective-c-headers)
- [VB.NET](#vbnet)

## Bash

The `.sh` and `.bash` frontend extracts both function syntaxes, command calls
(including pipelines, command substitutions and subshells), branches, and literal
`source`/`.` dependencies. A synthetic `file:<script>` function owns top-level
commands. Positional arguments do not become invented formal parameters.
Unique top-level functions declared before a script call resolve as `inferred`;
function bodies can also refer to those declarations. Conditional, nested or
repeated definitions, aliases, dynamic commands, external programs, path calls,
and cross-file names stay uncertain proxies. Sourcing or `eval` disables local
inference throughout that file because it can change command bindings. This
also applies through `builtin` and `command`; `command -v/-V` only inspects names.
Literal source dependencies are omitted when a same-file function or alias
shadows the source command. Function redirects and non-brace function bodies
retain their calls and branches under the function.
`command helper` records a call to the command builtin, not to a shell function.
No shell code is executed. `main` follows the generic entrypoint rule; script
execution needs an entrypoint override. Extensionless scripts need explicit
single-file parsing. Bash syntax errors warn that the graph may be incomplete.

## Rust and Verus

### Scopes and node IDs

- Items in inline modules have qualified IDs, for example `file.inner:function`.
- Methods keep their impl or trait owner.
- Functions defined inside another function keep the enclosing function in
  their ID, for example `file:outer.inner`.
- Structs, enums, traits, and inline modules defined inside a function do too:
  `file:outer.Guard.drop`, `file:outer.local.helper`. Their bodies have their
  own calls and complexity.
- When the same local name is declared in different blocks, each declaration
  gets a byte-offset suffix so the nodes stay distinct. Editing code before such
  a declaration changes its ID, so `trailmark diff` can report it as removed and
  added.

### What resolves

- Calls to local helper functions, and relative imports such as `use super::*`,
  resolve to declarations in the same file.
- Bare calls search function and module scopes, and respect block scope.
- Explicit calls to known local types and modules resolve within the file.
  Associated items use paths such as `Type::method`.
- Dotted calls resolve only when the receiver names a known struct or enum.
  Other value receivers, including `self.method()`, remain unresolved.
- Implicit destructor calls and receiver type inference are not modeled.

These rules apply to ordinary Rust as well as Verus blocks. For the patterns
that typically stay unresolved, see [Rust limits](call-resolution.md#rust-limits).

### Verus

The Rust parser extracts ordinary, `spec`, and `proof` functions with bodies
from `verus! { ... }` blocks, including qualified invocations such as
`vstd::prelude::verus!`.

- The Verus grammar supports only brace-delimited blocks. `verus!(...)` and
  `verus![...]` are skipped with a warning.
- Each block is parsed independently. Blocks with syntax errors produce a
  warning with the file and block location.
- The Verus grammar is compiled on first use. If it cannot be built or loaded,
  Trailmark logs a warning, skips Verus blocks, and continues parsing the rest
  of the file as ordinary Rust. See [Installation](installation.md#bundled-grammars).

### Not extracted

- Trailmark does not expand arbitrary macros. Literal Verus invocations inside
  opaque macros or function bodies are skipped with a warning, and uninvoked
  `macro_rules!` definitions are not expanded.
- Function signatures without bodies are not extracted.
- Calls in `requires` and `ensures` clauses are not extracted.

## Zig

The `.zig` frontend extracts functions (including extern declarations), named
structs, enums and unions, nested container methods, signatures, branches,
and literal `@import` dependencies. IDs include the container, for example
`file:Server.handle`. `main` uses the generic CLI entrypoint rule; `export fn`
is an untrusted API entrypoint. `pub fn` alone is not an external entrypoint.

Bare calls and explicit calls through named local containers resolve within the
file. Calls through parameters, local variables, instance receivers, imported
modules, function pointers, and builtins remain uncertain. Local bindings and
captures conservatively shadow declarations throughout the enclosing function.
Comptime execution, generated/anonymous types, test blocks, and container methods
declared inside function bodies are not analyzed. Zig syntax errors produce a
warning; the resulting graph may be incomplete.

## Solidity persistent state

The Solidity parser records which functions read and write contract storage.

```python
engine = QueryEngine.from_directory("./contracts", language="solidity")
engine.state_read_by("Token.transfer")     # state variables transfer reads
engine.state_written_by("Token.transfer")  # state variables transfer writes
engine.readers_of("Token.balances")        # functions that read balances
engine.writers_of("Token.balances")        # functions that write balances
```

**Nodes.** Each persistent storage declaration in a contract becomes a
`state_variable` node, with an ID such as `token:Token.balances`, a
`declared_type` attribute, and a `contains` edge from its contract. Constants,
immutables, and transient variables are excluded.

**Edges.** Functions, constructors, and modifiers get direct `reads` and
`writes` edges to the declarations they access, with a source location for each
occurrence. Repeated accesses are separate edges in the JSON export. The four
query methods return distinct nodes, ordered by ID, and `[]` for unknown names.

| Syntax | Recorded as |
| --- | --- |
| Assignment, `delete x` | write |
| Compound assignment (`x += 1`), increment, decrement | read and write |
| Struct member, mapping entry, array element | access to the owning state declaration; index expressions are read separately |
| `.push()` and `.pop()` on dynamic arrays and `bytes` | read and write, including through nested mapping/array paths and same-file struct members whose type is known |
| A library method that happens to be named `push` or `pop` | no storage write |
| Call options such as `{value: v, salt: s}` | the labels are not state accesses; the expressions `v` and `s` are analyzed |

Parameters, named returns, and local variables respect lexical scope, so a local
that shadows a state variable is not a state access. Initializer expressions
read enclosing bindings before the new local binding takes effect.

**Limits.** This is syntax-based, declaration-level dependency information, not
an EVM storage-slot or mapping-key analysis.

- Only state declared in the same contract is resolved. Inherited state,
  storage aliases, library effects, inline assembly, `delegatecall`, and
  external calls are not followed.
- Declaration initializers and compiler-generated public getters are not
  modeled.
- Modifier bodies own their accesses. The effects of a modifier are not
  attributed to the functions that use it, and the absence of a modifier edge
  does not establish the absence of access control.
- State edges do not change the default call traversals.
- An absent edge is not proof that a function cannot access storage.
- Other language parsers do not yet emit state-access edges.

## Aiken

The `.ak` frontend represents validators as contracts and their handlers as
methods, for example `file:payments.spend`. All six purposes (`mint`, `spend`,
`withdraw`, `publish`, `vote`, `propose`) and explicit `else` fallbacks are
untrusted API entrypoints. Helper functions and tests are extracted without
being promoted to entrypoints. Custom records, enums, type aliases (represented
as structs with `aiken_type_kind=alias`), and import dependencies are retained.

Direct calls and pipeline stages resolve to known lexical functions or explicit
same-file validator handlers. Parameters and pattern bindings conservatively
shadow names throughout a function. Anonymous functions get separate nodes
with byte-offset IDs; their calls and branches belong to those nodes. Editing
earlier source can change these IDs. Passing a function as an argument does not
create a call edge to it. Imported/aliased calls and higher-order dispatch stay
uncertain; Trailmark does not evaluate Plutus scripts or infer callback targets.
Aiken syntax errors produce a warning and can leave an incomplete graph.

## TypeScript

TypeScript resolves direct calls and straightforward receivers assigned with
`new ConcreteClass()`. Interface dispatch through manifests, computed property
names, dependency-injection containers, and other dynamic mechanisms is
best-effort and often stays unresolved.

## Perl

The Perl 5 frontend (`.pl`, `.pm`) extracts packages, named subroutine bodies,
anonymous subs, signatures/defaults, branches, and literal `use`, `require` and
`do FILE` dependencies. Prototypes are retained as `perl_prototype` metadata,
not mistaken for formal parameters. Forward declarations do not create duplicate
implementations. Package names qualify IDs (`file:Example.helper`); anonymous and
repeated definitions have byte-offset suffixes. `file:<script>` owns top-level
execution, while anonymous subs own their bodies.

Unique same-file package functions resolve as `inferred`, including explicit
package-qualified calls. Lexical subs are retained but calls through their
bindings stay uncertain; their names conservatively shadow package functions
throughout the enclosing function. Coderefs, receiver methods, imported aliases,
AUTOLOAD and cross-file names remain uncertain proxies. Typeglob assignment,
subroutine undefinition and `eval` disable local inference throughout the file.
No Perl or generated assembly is executed or analyzed as generated output;
heredoc contents are data. Script execution needs an entrypoint override;
`main` uses the generic rule. Syntax errors warn that the graph may be incomplete.
Some valid Perl regex syntax exceeds the packaged grammar, including a pattern
in OpenSSL's `arm-xlate.pl`; those warnings indicate a source coverage gap.

## Lua

The `.lua` frontend extracts local and global function declarations, functions
assigned to named variables or table fields, anonymous functions, parameters
(including implicit `self` for colon methods), branches, and literal `require`
dependencies. A synthetic `file:<chunk>` function owns top-level execution.
Table methods use IDs such as `file:M.handle`; nested functions include their
enclosing function. Repeated names and anonymous functions gain byte-offset
suffixes, which can change when earlier source is edited.

Calls to unreassigned local functions resolve with `certain` confidence.
Parameters, lexical blocks, loop variables, and declaration order affect name
lookup; a local binding is not visible in its own initializer. Local function
declarations do support recursion. Any reassignment makes calls through that
binding uncertain throughout its scope. Calls to a unique same-file global
function are `inferred`; global mutation can prevent that inference.

Table/receiver dispatch, callbacks, aliases, metatables, `_ENV` behavior, and
cross-file global/import binding are not resolved. These calls remain uncertain
proxies even when a function elsewhere has the same name. A shadowed `require`
does not create an import. Named `main` functions follow the generic entrypoint
rule; chunk execution and host callbacks require entrypoint overrides. Lua
syntax errors produce a warning and can leave an incomplete graph.

## R

The R frontend detects `.r` and `.R` through case-insensitive extension matching.
It extracts functions assigned with `<-`, `=`, or `->`, nested closures, anonymous
functions (including shorthand syntax), defaults/variadics, branches, explicit
calls/native-pipe call stages, and literal `library`, `require` and `source`
dependencies. `file:<script>` owns top-level execution. Anonymous and repeated
names gain byte-offset suffixes; nested functions retain their enclosing owner.

Unique unreassigned lexical functions resolve as `inferred`. Parameters,
assignments and loop variables conservatively shadow names throughout their
function environment; top-level calls also respect declaration order. Namespace
and object dispatch, aliases, S3/S4 methods, callbacks, cross-file bindings and
nonstandard evaluation stay uncertain. Superassignment, dynamic assignment,
source loading and environment evaluation disable inference throughout the file.
A dynamic `character.only=TRUE` package argument is not a literal dependency.
Promises and runtime environments are not evaluated. Script execution needs an
entrypoint override; `main` follows the generic rule. Syntax errors produce an
incomplete-graph warning.

## SQL

SQL support is PostgreSQL-oriented. It extracts schemas, tables, views,
functions, and procedures, plus `corresponds_to` edges from routines and views
to the objects they reference. It is not a SQL dialect validator and does not
analyze query semantics. The SQL grammar ships as the `tree-sitter-sql` wheel
dependency, so it does not use the shared grammar cache.

## PowerShell

The `.ps1`/`.psm1` frontend extracts functions, filters, classes and methods,
header and `param` parameters, type annotations/defaults, branches, literal
`Import-Module`/`using module` dependencies, and command/pipeline calls.
`file:<script>` owns top-level execution; anonymous script blocks own their
bodies with byte-offset IDs. Repeated declarations also gain offset suffixes.
Unique lexical function names resolve case-insensitively as `inferred`, respecting
script declaration order. Scoped names, receiver/static method dispatch, aliases,
call operators and callbacks remain uncertain proxies. Alias/function-provider
mutation, imports, dot-sourcing and expression evaluation disable local inference
throughout the file. Dynamic scoping, module exports and session state are not
evaluated, and manifests (`.psd1`) are not auto-detected. Script execution and host
callbacks need entrypoint overrides; exact lowercase `main` uses the generic
rule. Syntax errors warn that the graph may be incomplete.

## C, C++, and Objective-C headers

C and Objective-C both claim `.h`. When both are selected, headers are parsed
once with the Objective-C grammar, which also retains C declarations. The C++
parser handles `.hpp`, `.hh`, and `.hxx` but not `.h`. See
[Directory parsing](graph-model.md#directory-parsing) for extension matching
rules.

The C parser also recovers definitions inside optional C++ namespace wrappers
whose opening and closing braces use matching positive `#ifdef` guards. Original
syntax diagnostics remain visible; arbitrary C++ syntax is not interpreted as C.
Whitespace and trailing comments on the separating `#endif` are accepted.
Alternative `#elif`/`extern "C"` linkage arms are left intact with their diagnostics.
Physical-body recovery respects line-spliced comments and literals with LF or CRLF.
It also recognizes spliced block-macro names and comments before directives.
Recovery retains potentially active C23 `#elifdef` and `#elifndef` arms and native
`__try`/`__finally` blocks.
C functions returning function pointers keep their own parameters and callback shadows.

The C and C++ parsers recover functions inside preprocessor conditional blocks
and tree-sitter error nodes, so code that does not parse cleanly as a whole
still yields its functions.

## VB.NET

The `vb` frontend targets Visual Basic .NET (`.vb`), not VB6 or VBA. It extracts
namespaces, modules, classes/structures/interfaces/enums, methods, constructors,
property accessor bodies, lambdas, parameters/defaults/types, return types,
branches and imports. IDs include containers (`file:Demo.App.Main`); overloads
are distinct nodes with source-position suffixes. Lambdas own their bodies and
have byte-offset IDs.

Unique same-container calls and explicit calls through known local modules or
Shared methods resolve case-insensitively as `inferred`. Parameter/local shadows,
overloads, unknown receivers, delegates, aliases, inheritance and cross-file
calls stay uncertain; the compiler's overload/type resolution is not reproduced.
Calls are extracted from explicit invocations and bare call statements; implicit
property access and compiler-generated calls are not modeled.

Explicit Main methods are CLI entrypoints when they belong to a Module or are
Shared, accept no parameters or one String() parameter, and return nothing or
Integer. Instance Main methods are excluded, including lowercase `main`.
Generated application-framework entrypoints require overrides. The packaged
grammar has gaps, including generic method syntax; syntax errors produce an
incomplete-graph warning even for some valid VB.NET programs.

## Schema frontends and FunC/Sway declarations

Proto retains nested messages/enums, map fields and oneof members; IDs include
lexical owners, and public imports retain the actual filename. GraphQL honors
explicit schema root mappings and SDL extension fields in single-file and
directory parses. List/non-null wrappers remain recursive TypeRefs so named
leaf types retain dependency edges. Thrift IDs use source-file identity instead
of the first target-language namespace; namespaces remain metadata. Container
arguments and declared throws are represented as type references.

FunC dot/tilde method variants retain specialized declarations and bare fallback
semantics. Function-valued parameters and locals block unrelated same-name
binding. Getters use `method_id` metadata and `run_ticktock` is a reserved
entrypoint; a `get_` prefix alone no longer makes a private function callable.
Sway ABI entrypoints use declaration metadata, including implemented methods;
generic impl methods retain their own type containers. Implemented ABI signatures
are retained with a `#declaration` suffix alongside concrete method bodies.

See [Parser coverage and declaration identity](parser-coverage.md) for callable
ownership, conservative binding, schema identity changes and pinned grammar limits.
