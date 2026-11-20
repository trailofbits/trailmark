# Visual Basic .NET grammar

Source: https://github.com/CodeAnt-AI/tree-sitter-vb-dotnet

Pinned revision: `cfca210ce8fdcb5245bd9cd5c47ce0a21a8488d5`

Upstream's `grammar.js` declares MIT licensing and credits CodeAnt AI. Its
`package.json` also declares MIT. The pinned tree has no standalone license file;
the original attribution and license declaration remain in `grammar.js`.

Trailmark retains the upstream grammar and corrects these source forms:

- Class inheritance and implementation clauses follow the header terminator.
- Generic type argument lists include their `(Of ...)` parentheses.
- Generic type parameter declarations also include their `(Of ...)` parentheses.
- Import statements retain explicit type or namespace aliases.
- Primitive type names can receive static member access in expressions.
- Local array declarations retain their empty or explicit bounds.
- `Using resource As New Type(...)` has an explicit construction expression.
- Constructor arguments do not become array type suffixes.
- `REM` comments require a keyword boundary and do not consume `Remaining`.
- Interpolated literal text takes precedence over comment trivia.

The grammar name is `vb` to use Trailmark's shared native binding. Generated C
sources remain build artifacts; the pinned tree-sitter CLI generates them for
source distributions and wheels.
