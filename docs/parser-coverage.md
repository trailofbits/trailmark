# Parser coverage and declaration identity

Trailmark graphs describe extracted declarations and call sites. File counts
measure inspected source files. They do not establish complete language coverage,
execution, dataflow, exploitability, or dead code. Check `parse_diagnostics()`
when interpreting results; `strict=True` and CLI `--strict` reject incomplete
syntax analysis. Empty and comment-only files remain valid inputs, including
PowerShell and GraphQL inputs whose pinned grammar reports an empty-root error.

## Callable ownership and binding

Python nested `def` and `async def` declarations have owner-qualified IDs such as
`file:factory.hidden`. Their calls, branches and exceptions belong to the nested
function. Nested callable bodies are also reachable through conservative `calls`
edges from their nearest callable owner, with uncertain confidence and `deferred`
metadata. This applies across frontends, including callbacks and methods of local
or anonymous classes. These edges retain possible execution paths; they do not
prove that creating or registering a callable invokes it. Python lambda calls stay
with the enclosing function. Local classes retain separate method owners. Variadic
parameters retain names and annotations; `self` and `cls` are omitted only as
actual method receivers, and retained in free functions and static methods.

JavaScript and TypeScript retain nested functions, arrows, anonymous callbacks,
class-field arrows, object methods, CommonJS handler assignments, and anonymous
default exports. Anonymous IDs contain source positions. Moving a declaration
can change its ID. Registration and acquisition of a callable do not establish
its execution; the uncertain `deferred` edges express that distinction. Object
methods use binding-qualified IDs such as `file:api.init` and `file:admin.get`.
Concise bodies include their root call or conditional. TypeScript
uses the TSX grammar for `.tsx`; interface methods retain signature metadata.
Unknown receiver writes and conditional writes invalidate concrete-constructor
inference; expired block-local declarations do not affect an outer receiver.

Lexical callable parameters and locals prevent binding to unrelated same-name
functions. Such calls remain uncertain proxies with `binding_blocked` metadata,
including after directory linking. This guard does not infer callback values or
reproduce a compiler's full alias, overload, or control-flow analysis. Scope
indexes preserve language declaration order: Rust, Go and Kotlin initializers can
call the previous same-name binding. Python function names bind in their enclosing
scope; methods in module-level and function-local classes do not shadow module
functions or leak their names into an enclosing function. Java variables and parameters
occupy a separate namespace and do not block method calls.

Rust and Verus preserve repeated same-name impl methods with source-offset
suffixes instead of combining their bodies. Ambiguous trait and concrete generic
impl dispatch remains unresolved. Declaration-owned `unsafe(no_mangle)` and
`unsafe(export_name = "symbol")` attributes expose Rust 2024 entrypoints.

Go retains grouped, variadic, function-typed and unnamed parameters. Unnamed
slots use `$argN`; variadic types retain `...`. Explicit single-type generic
calls are distinguished from locally declared generic type conversions.
Unqualified directory call inference stays within the source directory. Calls
inside Go function literals stay with their enclosing named function.

PHP namespace owners appear in declaration IDs, including semicolon namespaces;
namespace separators become dots. Closures and arrows own their calls and body
metadata. First-class `(...)` acquisition contributes no call; argument unpacking
still does. Nullsafe and scoped calls retain sites. Local `self`, named-class and
unambiguous `parent` targets can resolve; late-static dispatch stays uncertain.
Free functions link across files by namespace and name, including fully qualified
references. Unqualified namespace calls retain PHP's global-function fallback.
Executable top-level PHP has a synthetic `<script>` owner. Mixed `.phtml`
templates have a `<template>` owner, preserving direct database calls and their
source ranges without absorbing nested function bodies. Rendering and theme
selection remain registration evidence concerns, separate from parsing.

Perl qualified subroutine names determine symbol ownership while body/default
lookup retains the lexical package. Signature defaults retain calls and nested
anonymous subroutines. Bareword statements are calls only when a preceding
subroutine declaration establishes their classification.

Lua assignments to lexical locals never create global candidates. A custom or
reassigned `_ENV` blocks free-name global inference and unproven `require`
imports, while explicit lexical bindings remain available. R local assignments
respect source order; statically qualified base environment mutators invalidate
inference. Known unshadowed base quoting constructors are opaque to execution
walking. Arbitrary R nonstandard evaluation remains outside this model.

## Schema and security-language frontends

Proto nested declarations use owner-qualified IDs and retain map/oneof fields.
GraphQL schema mappings determine operation roots; extensions retain fields and
recursive list/non-null wrappers retain named type relationships. Thrift uses
source-file IDs, preserves target namespaces as metadata, and retains recursive
container types and declared exceptions. These schema graphs contain no runtime
call edges.

SQL retains overloads and separate relation/routine namespaces. Colliding routine
IDs gain source-position suffixes. Relation matching folds unquoted PostgreSQL
identifiers to lowercase and preserves quoted case. Recovered procedure bodies
exclude comments, strings and later statements. SQL dependencies are
`corresponds_to` edges, not runtime calls; search paths are not evaluated. Replacing
a routine overload removes its previous body dependencies and containment edge.

Cairo module, trait, impl, struct and enum IDs retain their owners. Impl namespaces
are explicit containment nodes. Concrete methods of contract `abi(embed_v0)`
implementations are exposed entrypoints; private helpers remain private. The
pinned Cairo grammar rejects common `assert!(...)` macro statements. They produce
syntax diagnostics and cause strict parsing to fail even for compiler-valid input.

Aiken parameter labels do not replace local binding names. Unapplied captures
such as `helper(_, 1)` do not imply immediate execution; pipeline application
retains its helper call. The pinned grammar does not fully support property
tests, benchmarks, destructured arguments, or soft casts. These constructs can
produce diagnostics and incomplete declarations/calls.

Zig identifier matching handles quoted names and whitespace around dots. Unnamed
extern slots use `$argN`; `...` and a `variadic` attribute preserve variadic
signatures. The pinned grammar rejects some current destructuring and labeled
switch syntax. Calls from a body with syntax recovery remain unresolved, and
strict mode rejects its diagnostics. A complete Zig version range is not claimed.

Miden `exec`, `call`, and `syscall` edges carry opcode metadata. `procref` acquires
a procedure hash and contributes no execution call. Qualified library targets
retain their paths as unresolved proxies when unavailable. `begin` program
blocks and `pub proc` declarations are entrypoints. The bundled grammar does not
cover all recent Miden syntax, including some visibility and branch forms.

Bash command effects use statically decoded words without shell evaluation.
Quoted/escaped mutation builtins invalidate inference. Reserved `time` preserves
the timed call. Named `coproc` compound bodies retain enclosing function spans
and command ownership. Bash 5.3 `${ command; }` and `${| command; }` substitutions
remain grammar limitations and can produce diagnostics.

FunC specialized dot/tilde declarations, bare fallbacks and callable shadowing are
retained. `method_id` identifies getters; `get_` alone does not. Sway ABI exposure
uses declaration metadata; generic impl methods retain their owners, and an
implemented ABI signature remains available with a `#declaration` suffix.

PowerShell recognizes known built-in mutator aliases and module-qualified mutators
when suppressing stale binding inference. Function header defaults and param-block
defaults retain calls; default scriptblocks have separate bodies and signatures.
Arbitrary aliases and session state still require runtime information.

VB.NET accessor units retain property index parameters, getter types and Shared
context. Fields and property parameters shadow type/method names case-insensitively.
Array ranks on parameter names are retained, including `Main(args() As String)`.

Julia destructured arguments bind their lexical leaf names without flattening the
signature. Quoted syntax is data; only interpolation at the current quote level
contributes calls. Long and short callable-struct methods have receiver metadata
and separate bodies; callable-object dispatch remains uncertain. Macro expansion
and grammar recovery can still leave graphs incomplete. Skipped Julia macros emit
`unsupported_syntax` diagnostics and fail strict parsing.

C tag and callable namespaces retain both declarations when names coincide.
Indirect pointer calls remain uncertain. C++ anonymous namespace functions are
excluded from cross-file free-function
fallback. Static functions follow the header and include visibility rules below. Header
export annotations currently need to be repeated on extracted definitions; matching
prototype attributes through preprocessing/includes is not implemented.

C can recover a balanced physical function body around bare block macros, including
a comment between the macro and brace, while
preserving byte offsets and the original syntax diagnostics. Recovered functions
carry `parse_incomplete` and `syntax_recovered` attributes; their calls remain
uncertain. This is source-span recovery, not preprocessing or a model of nonlocal
control flow. Conditional preprocessor groups are allowed when every branch
boundary preserves brace depth and the group closes inside the body. Brace-changing
conditionals and unbalanced bodies are not recovered as one configuration.
Recovery verifies each function independently, so a failed candidate does not
cancel other verified spans. Unrelated remaining syntax errors remain diagnosed.

Single-identifier declaration macros can recover a concrete function signature
from unambiguous definitions in the file or literal quoted headers. Simple token
pasting and object-like wrapper aliases are supported; lookup and expansion are
bounded. Quoted includes must resolve inside the analyzed directory (or the source
file's parent for single-file parsing), including through symlinks. Header trees
and directive indexes are cached only for the current directory operation. The concrete
symbol becomes the callable ID, with `macro_wrapper`, `source_alias`, and
`expansion_alias` attributes preserving its source spelling. Conditional,
redefined, recursive, variadic, or stringifying wrappers can remain symbolic
`WRAPPER(name)` declarations with diagnostics. Calls keep their normal confidence
and are marked `macro_signature_unresolved`. An unexpanded macro cannot establish
injected parameters that shadow a global function; strict analyses should filter
on that attribute. Their calls can still link to real declarations.
Untyped `WRAPPER(name) { ... }` definitions retain that wrapper identity even if
the header is unavailable; argument-only names are not exposed as global aliases.
Unavailable macro definitions cannot establish compiled names. Repeated unknown
declarations retain separate source-position IDs and body ownership.
Ordinary C functions with `void`, primitive types, or object-like parameter-list
macros keep their function names, including with unknown leading specifiers.
Known declaration wrappers take precedence over type-like arguments such as `void`;
aliases still expand, while ambiguous wrapper definitions remain symbolic.
Unambiguous parameter-list macros expand into parameters; conditional definitions
leave parameters unresolved without changing the function's identity.

C callback inference is limited to file-local `static const` objects with direct
designated function initializers. It binds by object and field, records
`callback_table` / `callback_field`, and uses inferred confidence. Mutable or
conditional tables, positional initializers, unknown pointers, and shadowed
receivers remain uncertain. This does not select build configurations or infer
general callback dataflow. Recovered bodies do not infer callback-table targets.
Unresolved wrapper signatures retain normal callback-table inference, with the
same possible injected-parameter limitation. Other parse errors mark the function
`parse_incomplete` but affect confidence only at calls within an ERROR subtree or
with broken descendants. Those calls carry `syntax_error_context`; unrelated
calls and callback-table sites keep their normal binding confidence. Calls on a
callback's return value remain separate indirect sites.

C++ namespaces qualify declaration IDs, including file-local `$anonymous` owners.
Lambdas and local classes own their bodies; lambda capture initializers stay with
the enclosing function. C# namespaces also qualify type IDs; local functions and
lambdas have separate owners. Anonymous `delegate` bodies retain their calls, and
curried C# and Dart lambdas assign each call to the innermost callable. Generic and
conditional-access calls are retained.

Java lambdas and local/anonymous classes own deferred bodies. Explicit construction
and constructor chaining have located edges; unknown or overloaded constructors
remain proxies. Servlet entrypoints require servlet ancestry, an instance method,
a void return and request/response parameters. Unrecognized framework
intermediates such as `FrameworkServlet` do not establish servlet ancestry.

Swift extensions retain methods and attach to an unambiguous extracted type without
replacing its kind or source location. Local functions and closures own their bodies.
Kotlin recursively extracts nested and companion containers. Both prefer known
lexical methods over same-named free functions and preserve uncertain callable values.
Recursive type and callable extraction in JavaScript, TypeScript, C++, C#, Dart,
Java, Objective-C, PHP,
Python, Kotlin and Swift, and Ruby containers and blocks, is limited to 48 extraction
levels. Deeper declarations
produce an `unsupported_syntax` diagnostic. A final per-file recursion guard keeps
directory analysis running and reports any file it cannot extract. Ruby depth
diagnostics identify block nesting. Extremely deep PHP conditional or parenthesized
expressions can still raise `RecursionError` through `parse_file`; the declaration
limit does not cover every expression form.

Objective-C message sends retain complete selectors. A unique `self` selector in
the same implementation can link with inferred confidence; other sends stay
uncertain. Class
methods use `module:Class.+selector` IDs; instance methods retain
`module:Class.selector`. Blocks have separate source-position owners.
Dart constructors use `module:Class.Class` and `module:Class.Class.named` IDs.
Constructor initializers and arrow bodies retain calls, and deferred local bodies
have separate owners. These syntax edges do not imply runtime dispatch inference.

Circom include linking follows literal include paths and their transitive closure.
Same-file calls retain certain confidence; cross-file include bindings are
inferred. Ambiguous included template/function names remain unresolved. Main-component
arguments retain nested calls; anonymous-component `_` discard syntax is supported.
Circom 2.2 bus declarations remain unsupported and produce diagnostics.

Move uses the bundled Move grammar pinned to MystenLabs/sui commit
`f3fd7754ebe506e2a8b1cb4e0ddb45be3e5eb6c1`, with small syntax additions recorded in
`NOTICE`. Tests cover Aptos-style inline callbacks and generic application, multiple
explicit module declarations, `acquires`, `for` ranges, compound assignments,
`package fun`, and Sui 2024-style structs. This is fixture coverage,
not a compiler-version compatibility guarantee. Address blocks remain unsupported
and produce diagnostics. Qualified/address/alias targets retain their symbols;
unknown callback values remain uncertain. `let` bindings take effect after their
initializer and respect block scopes. A syntax error affects calls in that error
context, rather than the entire file. Imports retain both dependency metadata and
`imports` edges. Logical module names include the address, such as `0x1::bank`;
unsupported address-block syntax does not synthesize a logical module node.
Move lambda calls remain with the enclosing function; callback values and macro
expansion do not have complete value-flow coverage.

Tact distinguishes static global calls from `self` member calls. Receiver parameters
and optional/map/bounced signature types are retained. Trait bodies and inherited
receivers are not yet extracted; inherited attack surface is consequently incomplete.

Rego keeps logical rule IDs such as `policy:allow` and separate source-position
clause IDs such as `policy:allow.$clause@file_3_0`. Logical rules have potential
`calls` edges to their clauses, marked `rego_clause`; these represent policy
alternatives, not a runtime evaluation sequence. Calls and branches belong to the
individual clause. Defaults and clauses in different files remain separate.
The pinned grammar rejects nested reference rule heads and some qualified calls;
inline-if call syntax that is split without an ERROR node is explicitly diagnosed.

Ruby namespaces qualify nested type/method IDs. Instance methods use
`file:Class.method`; singleton methods use `file:Class.self.method`. Bare method
reads retain vcall sites while known parameters, local variables, and block
parameters remain values, including destructuring, rescue variables and loop
binders. Bare calls remain eligible for local/inherited method and cross-file
global linking; known local callable values stay uncertain. Rails actions and
Sidekiq `perform` entrypoints require
public instance-method metadata; private and singleton helpers are excluded.
Runtime metaprogramming, reopened class inheritance, and lambda-body coverage remain
limited.

Erlang callable IDs include arity: `module:function/2`. Specs, clauses, export/import
tables and call targets use the same identity. Anonymous fun bodies have distinct
source-position owners; acquiring `fun module:name/arity` adds no invocation edge.
Qualified and imported calls preserve their module, and variable targets remain
uncertain. OTP callback invocation and higher-order target recovery are not inferred.

Haskell local where/let declarations have owner-qualified IDs and separate body
metrics. Function/lambda/case binders prevent unrelated global binding. Parenthesized,
qualified, backtick and operator applications retain sites. `$` remains an ordinary
potentially shadowed operator call; no automatic function application lowering is
claimed. Calls inside Haskell do-bind expressions are retained. Unresolved
operator proxy IDs use readable names such as `$operator.plus` and
`$operator.plus_plus`. Constructor patterns do not create calls. Typeclass default bodies,
applied-type instance bodies, Template Haskell expansion and typeclass dispatch
remain incomplete.

Python follows runtime redefinition order: the last definition keeps its plain ID
unless a type owns that ID; earlier bodies use `@line_column` suffixes. Calls in the
same scope use the latest preceding definition; nested deferred scopes use the final
definition. A later class definition also wins over an earlier function of the
same name; the class retains its base ID. A local function declared inside the
calling function can rebind its parameter. A parameter still shadows a local
function declared in an enclosing function, including in C#, Kotlin and Swift.
Unresolved receiver targets retain the final names of all their enclosing owners.
Only `@overload`, `@typing.overload` and `@typing_extensions.overload` stubs are
skipped, including import aliases of `typing` and `typing_extensions` and
`from typing import overload as ...` aliases. Lambda
parameters, including defaults, shadow outer functions. Kotlin, Swift and Dart
class members shadow module names throughout the class, including before their
declaration, without leaking into outer functions. C and C++ names are visible after
their declarator, including inside
an initializer; Rust, Go and Kotlin bindings become visible after the initializer.

Python directory linking includes all module-level classes and functions and
selects the final module binding for each name in each physical source file.
Historical bodies remain available under their positioned IDs.
A definition inside a conditional branch does not establish a definite binding
after that branch; these calls remain uncertain with `binding_blocked` metadata.
Calls inside the defining branch and after unconditional definitions retain normal
binding confidence. This is conservative syntax analysis rather than a control-flow
proof, including when both branches define different bodies.
Definitions in a `try` body retain normal confidence; definitions in `except` or
`else` clauses remain conditional. Captured Python, Kotlin, Swift and C# parameters
shadow unrelated functions in nested bodies as well as in their declaring callable.

C and C++ internal-linkage functions in headers (`.h`, `.hh`, `.hpp`, `.hxx`) use
include closures to break same-name ties. A single candidate inside the closure,
in either inclusion direction, wins over candidates outside it. An unambiguous
out-of-closure fallback retains inferred confidence and carries
`include_unverified=True`, meaning the resolver could not verify the include path,
not that the caller lacks an include. It is disabled when the caller or its includes declare
a non-static prototype of that name. This also applies in polyglot C-family analysis. Statics
in source files can link when the caller includes the definition or the defining
file includes the caller, directly or transitively. Other source statics stay
unresolved. Quoted includes are checked beside the source and then at the analysis
root, inside the analyzed tree. Traversal uses directive order and breadth-first
search, with include depth capped at 16 and each closure capped at 128 headers.
Compiler `-I` directories are not searched. Results can therefore depend on the
analysis root. Unique-name linking does not select a build configuration and can
pick platform compatibility shims, such as a Windows shim called from POSIX code.
Block-scope function prototypes refer to the external function rather than
shadowing it; function-pointer variables still shadow same-name functions. In C++
block scope, identifier-only initializer arguments such as `Functor cb(cfg)` are
treated as object bindings. A prototype requires `extern` or explicitly typed parameters,
including sized types such as `unsigned`, qualified types, and struct/enum/union types.
An external block-scope prototype also disables unverified header-static fallback
in its own lexical scope, without affecting another function. Such calls carry
`external_prototype` metadata; verified include-supported targets remain eligible.
Invalid block-scope `static` prototypes remain blocked unless a same-file function
definition supplies the target, including when a misparsed macro swallows a prototype.
Java constructor binding also runs after the directory merge. JavaScript and
TypeScript arrows inherit lexical `this`; regular function expressions do not.
Abstract TypeScript classes own their methods. Anonymous classes have positioned
`$class@line_column` owners, and namespace classes have IDs such as
`file:NS.Inner.method`. Local classes and class expressions do not inherit the
enclosing function's `this`. Arrow-valued class fields retain method kind. Module
`const`/`let` arrows and function expressions, including exports, are callable
declarations; noncallable values remain blocked. Duplicate callable bodies retain
their own binding identities. TypeScript signature-only parameters do not introduce
runtime bindings. Local-class heritage, static blocks, computed keys and field
initializers retain calls under the enclosing callable; method bodies keep their
own owners. Repeated TypeScript namespace blocks share a container and resolve
their functions across blocks, including class/function namespace merging and
the legacy `module` spelling. A namespace merged with a function uses a separate
`$namespace` container so its members do not collide with function locals or create
deferred edges from the function. String-named ambient module IDs omit quote
delimiters; declarations without bodies do not create callable nodes. Chained calls retain separate
receiver and method edges at their complete source spans. Local literal `require`
destructuring resolves named exports and aliases, including relative directory
`index` modules; a matching source file takes precedence over a directory.
Parameters shadow outer imports through nested callbacks. Calling a whole-module
require remains an uncertain proxy;
external `node:` module IDs normalize the namespace separator to a dot. Import
resolution is cached per declaration.

Generator expressions and callable initializers wrapped in parentheses, `as`,
`satisfies`, type assertions or non-null assertions retain their variable binding.
Calls in JS/TS default parameter expressions belong to the callable, including
destructured defaults; nested callbacks retain their own body owners. Parameter
decorators follow method decorators: they are not calls from the method body.
Optional TypeScript parameters shadow same-name functions. Named function
expressions retain their internal recursive binding even when the graph ID uses
the variable or property receiving the expression. Getter and
setter bodies keep distinct positioned IDs and each call is extracted once.
Descendants of C# overloads inherit their owner's final positioned ID at every
nesting level. TypeScript receiver assignments are indexed once per body during a
file parse; source order, block visibility and conditional invalidation still apply.
A `do` body runs at least once and does not itself make an assignment conditional.
Local-class field initializers and static blocks do not resolve `this` or `super`
against an enclosing class; heritage expressions and computed keys retain the outer context.

Python, JavaScript, TypeScript, C++, Go and Rust calls through another call's result,
such as `f()()`, retain one
edge to `f` and a separate uncertain indirect edge for the returned callable.
The indirect proxy uses `$returned@line_column_endbyte` and `indirect_call=True`;
its full source span distinguishes it from the producer call. No return-value
type or callback dataflow is inferred.
Parentheses preserve a direct callee's lexical binding. Logical, conditional,
pointer and subscript callees use uncertain `$indirect@line_column_endbyte` proxies;
these callees do not bind to an operand, and Go parenthesized pointer conversions do not
create call edges. Named JS/TS IIFEs target their own function expression.

Ruby parentheses and argument lists always denote method calls, even when a local
variable shares the name. Receiverless variable reads use preceding bindings;
Ruby 3.4's bare implicit `it` parameter is a value inside parameterless blocks and
lambdas, just like numbered parameters.
plain attribute writers do not call the reader method; compound assignments retain
the reader call. Ruby block parameters include splats, double splats, block arguments,
destructuring, optional parameters and keyword parameters. Stabby lambda bodies retain their
calls. Explicit `self` calls do not fall back to unrelated module functions. An
ambiguous inherited method retains an uncertain `{owner}.{name}@ambiguous` target.
Swift extensions of nested types follow their type's final owner. Move module aliases
apply to qualified paths only; member imports may resolve bare calls. Macro callback
parameters retain their `$` prefix. Built-in macros such as `builtin.move:assert!`
are synthetic function nodes at `<builtin>` and count toward summary function
totals. Address-group imports such as `use 0x1::{math, vector}` register each module
alias. Alias-expanded and fully qualified calls both remain uncertain when the
target module is absent; parsing the target allows normal local or cross-file
binding confidence. Import edges name modules rather than grouped source text or `use fun` aliases.
Ruby numbered block/lambda parameters `_1` through `_9` are value reads; explicit
call syntax such as `_1()` remains a method call. Move `Self::name()` resolves
within the current module; a missing member stays uncertain.
Haskell operator proxies consistently use `$operator.<name>`, including composition
and application, without marking ordinary operators as blocked lexical values.
