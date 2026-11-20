module.exports = grammar({
  name: 'rego',

  extras: $ => [
    $.comment,
    /[\s\p{Zs}\uFEFF\u2060\u200B]/,
  ],

  word: $ => $.keyword,

  conflicts: $ => [
    [$.membership],
    [$.rule_body, $.assignment_operator],
    [$.rule_body_v1_tail, $.assignment_operator],
    [$.rule_head, $.rule_head_v1],
    [$.rule_head],
  ],

  rules: {
    source_file: $ => optional($.module),

    // module          = package { import } policy
    module: $ =>
      seq(
        $._package,
        repeat($._import),
        optional($.policy),
      ),

    // package         = "package" ref
    _package: $ =>
      seq(
        $.package,
        $.ref,
      ),

    // import          = "import" ref [ "as" var ]
    _import: $ =>
      seq(
        $.import,
        $.ref,
        optional(seq($.as, $.var)),
      ),

    // policy          = { rule }
    policy: $ => repeat1($.rule),

    // rule            = [ "default" ] rule-head { rule-body }
    rule: $ =>
      choice(
        seq(
          optional($.default),
          $.rule_head,
          prec.left(repeat1($.rule_body)),
        ),
        // OPA v1 comp+if rule: `x := 1 if <body>`. The value lives in the
        // head, and the first body is a braced query or a single literal —
        // never a v0 `:= term` body — so a following v0 rule whose head
        // happens to be named `if` cannot be fused in. Dynamic precedence
        // prefers this one-rule parse over "value rule followed by a rule
        // named if".
        prec.dynamic(1, seq(
          optional($.default),
          alias($.rule_head_v1, $.rule_head),
          prec.left(seq(
            alias($.rule_body_v1, $.rule_body),
            repeat(alias($.rule_body_v1_tail, $.rule_body)),
          )),
        )),
      ),

    // rule-head       = var ( rule-head-set | rule-head-obj | rule-head-func | "if" )
    // The head forms share one dynamic precedence: without prec.right on
    // the whole head (which would statically force `var :=` into the v1
    // head and break plain constants), GLR needs an explicit preference
    // for keeping brackets/args/if in the head over demoting them to body
    // literals.
    rule_head: $ =>
      seq(
        $.var,
        optional(prec.dynamic(1, choice(
          // rule-head-set   = ( "contains" term [ "if" ] ) | ( "[" term "]" )
          seq($.contains, $.term, optional($.if)),
          // rule-head-obj   = "[" term "]" [ rule-head-comp ] [ "if" ]
          seq(
            $.open_bracket,
            $.term,
            $.close_bracket,
            optional($.rule_head_comp),
            optional($.if),
          ),
          // rule-head-func  = "(" rule-args ")" [ rule-head-comp ]
          seq(
            seq($.open_paren, $.rule_args, $.close_paren),
            optional($.rule_head_comp),
            optional($.if),
          ),
          // if
          $.if,
        ))),
      ),

    // OPA v1 rule head with the value bound in the head: `x := 1 if`
    rule_head_v1: $ => seq($.var, $.rule_head_comp, $.if),

    // The body of a v1 comp+if rule: braced query or single literal —
    // but a top-level assignment/unification may not have a parenthesized
    // or array-shaped left-hand side. `a := 1` followed by `if(x) := x`
    // (a v0 function named `if`) or `if[x] := 2` (a v0 object rule named
    // `if`) must not fuse into `a := 1 if` with body `(x) := x` /
    // `[x] := 2`: those are exactly the assignment literals whose lhs
    // starts with `(` or `[`, so excluding that shape kills the fused GLR
    // path and the two-rule parse is the only one left standing. Ordinary
    // assignment bodies keep their one-rule parse (`a := 1 if x := 2`,
    // `a := 1 if input.x = 1`), as does everything braced.
    rule_body_v1: $ =>
      choice(
        $._braced_query,
        alias($.literal_v1, $.literal),
      ),

    // literal, minus assignment/unification infix expressions whose lhs
    // is a parenthesized expression or an array. Nested assignments
    // (inside parens, calls, comprehensions) are still reached through
    // the regular expr rules.
    literal_v1: $ =>
      seq(
        choice(
          $.some_decl,
          alias($.expr_v1, $.expr),
          seq($.not, $.expr),
          $._logical_expr_v1,
        ),
        repeat($.with_modifier),
      ),

    // A logical expression opening a v1 comp+if body. Only the leftmost
    // operand needs the restricted hierarchy — same reasoning as
    // expr_infix_v1 — so the right operand is a plain logical operand.
    _logical_expr_v1: $ =>
      choice(
        alias($.logical_and_v1, $.logical_and),
        alias($.logical_or_v1, $.logical_or),
        $.logical_group,
        seq($.not, $.logical_group),
      ),

    logical_and_v1: $ => prec.left(5, seq($._logical_operand_v1, $.and, $._logical_operand)),

    logical_or_v1: $ => prec.left(4, seq($._logical_operand_v1, $.or, $._logical_operand)),

    _logical_operand_v1: $ =>
      choice(
        alias($.logical_and_v1, $.logical_and),
        alias($.logical_or_v1, $.logical_or),
        $._logical_atom_v1,
      ),

    _logical_atom_v1: $ =>
      choice(
        alias($.expr_v1, $.expr),
        $.logical_group,
        seq($.not, choice($.expr, $.logical_group)),
      ),

    expr_v1: $ =>
      prec.left(
        1,
        choice(
          $.term,
          $.expr_call,
          alias($.expr_infix_v1, $.expr_infix),
          $.expr_every,
          $.expr_parens,
          $.expr_unary,
        ),
      ),

    expr_infix_v1: $ =>
      prec.left(
        1,
        choice(
          // Non-assignment operators. The left operand recurses through
          // the restricted hierarchy: assignment binds tighter than the
          // other operators, so in `(x) := x + 1` the `+` is the top
          // operator and `(x) := x` its lhs — with a plain $.expr lhs
          // that would reopen the fusion this rule exists to prevent.
          seq(alias($.expr_v1, $.expr), alias($.infix_operator_v1, $.infix_operator), $.expr),
          // Assignment/unification only with a lhs that cannot be read
          // as the argument list or key of a v0 rule named `if`.
          seq(
            alias($.expr_assign_lhs_v1, $.expr),
            alias($.infix_operator_assign_v1, $.infix_operator),
            $.expr,
          ),
        ),
      ),

    // The lhs shapes admissible for a top-level assignment in a v1
    // unbraced body: everything except parenthesized expressions and
    // array-initial terms (arrays, array comprehensions, memberships,
    // and refs rooted at an array).
    expr_assign_lhs_v1: $ =>
      prec.left(
        1,
        choice(
          alias($.term_assign_lhs_v1, $.term),
          $.expr_call,
          $.expr_unary,
        ),
      ),

    term_assign_lhs_v1: $ =>
      choice(
        alias($.ref_assign_lhs_v1, $.ref),
        $.var,
        $.scalar,
        $.object,
        $.set,
        $.object_compr,
        $.set_compr,
      ),

    ref_assign_lhs_v1: $ =>
      prec.left(
        2,
        seq(
          choice(
            $.var,
            $.object,
            $.set,
            $.object_compr,
            $.set_compr,
            $.expr_call,
          ),
          repeat($.ref_arg),
        ),
      ),

    infix_operator_assign_v1: $ => prec.left(2, $.assignment_operator),

    // Follow-on bodies of a comp+if rule: else clauses (which may carry
    // their own value) and further braced/literal bodies — but never a
    // bare `:= term` value body, since the rule's value already lives in
    // the head. Without this, `a := 1` + `if(x) := x` fused as head
    // `a := 1 if`, first body `(x)`, second body `:= x`.
    rule_body_v1_tail: $ =>
      choice(
        seq(
          $.else,
          optional(
            seq(
              $.assignment_operator,
              $.term,
            ),
          ),
          optional($.if),
          choice(
            $._braced_query,
            $.literal,
            seq(choice($.assignment, $.unification), $.term),
          ),
        ),
        $._braced_query,
        $.literal,
      ),

    infix_operator_v1: $ =>
      choice(
        $.bool_operator,
        $.arith_operator,
        $.bin_operator,
      ),

    // rule-head-comp  = ( ":=" | "=" ) term
    // OPA accepts full expressions as rule values (`f(x) := x + 1 if ...`),
    // not just plain terms; expr-every is body-only and stays out. The
    // dynamic precedence keeps the value in the head when a rule_body
    // value parse would otherwise tie (`q["a"] = 1 { true }`).
    rule_head_comp: $ =>
      prec.dynamic(1, seq(
        $.assignment_operator,
        choice($.term, $.expr_infix, $.expr_call, $.expr_parens, $.expr_unary),
      )),

    // rule-args       = term { "," term }
    rule_args: $ =>
      seq(
        $.term,
        repeat(seq(',', $.term)),
      ),

    // rule-body       = [ "else" [ ( ":=" | "=" ) term ] [ "if" ] ] ( "{" query "}" ) | literal
    rule_body: $ =>
      seq(
        optional(
          seq(
            $.else,
            optional(
              seq(
                $.assignment_operator,
                $.term,
              ),
            ),
            // OPA v1 else clauses: `else := 2 if { ... }` / `else if { ... }`
            optional($.if),
          ),
        ),
        choice(
          $._braced_query,
          $.literal,
          // The bound value of a constant rule: `x := 1` or `x = 1`.
          seq(choice($.assignment, $.unification), $.term),
        ),
      ),

    _braced_query: $ => seq($.open_curly, $.query, $.close_curly),

    // query           = literal { ( ";" | ( [CR] LF ) ) literal }
    query: $ =>
      seq(
        $.literal,
        repeat(
          choice(
            seq(
              choice(';', seq(optional('\r'), '\n')),
              optional($.literal),
            ),
            seq(
              choice(';', seq(optional('\r'), '\n')),
              optional($.with_modifier),
            ),
          ),
        ),
      ),

    // literal         = ( some-decl | expr | "not" expr | logical-expr ) { with-modifier }
    literal: $ =>
      seq(
        choice($.some_decl, $.expr, seq($.not, $.expr), $._logical_expr),
        repeat($.with_modifier),
      ),

    // The `and` / `or` logical operators (OPA future keywords `and` and `or`).
    //
    // These live at the literal level, not inside `expr`: they combine
    // *bodies*, not terms, so `p := a or b`, `f(a or b)` and `a[x or y]` are
    // not valid Rego — an `and`/`or` may only appear where a query literal
    // may. Precedence, tightest binding first:
    //
    //     not > and > or > with
    //
    // Both operators are left-associative, and `and`'s higher precedence is
    // what makes it bind tighter than `or`; the values sit above every other
    // precedence in the grammar so the logical layer resolves on its own.
    // `not` binding tighter than both needs no declaration at all: it prefixes
    // an `expr`, and `and`/`or` are not part of `expr`, so `not x and y` can
    // only be `(not x) and y`.
    //
    // A whole logical expression may also stand alone as a literal, either
    // parenthesized (`(a or b)`) or negated (`not (a or b)`) — the bare form
    // is already covered by logical_and / logical_or.
    _logical_expr: $ =>
      choice(
        $.logical_and,
        $.logical_or,
        $.logical_group,
        seq($.not, $.logical_group),
      ),

    // logical-and     = logical-operand "and" logical-operand
    logical_and: $ => prec.left(5, seq($._logical_operand, $.and, $._logical_operand)),

    // logical-or      = logical-operand "or" logical-operand
    logical_or: $ => prec.left(4, seq($._logical_operand, $.or, $._logical_operand)),

    _logical_operand: $ =>
      choice(
        $.logical_and,
        $.logical_or,
        $._logical_atom,
      ),

    // logical-operand = [ "not" ] ( expr | logical-group )
    //
    // OPA also allows a braced query (`{a; b} and c`). It is left out because a
    // `{`-initial operand is indistinguishable from a set / object /
    // comprehension term, which makes `count({x})` unparseable. Parentheses
    // cover the same ground.
    _logical_atom: $ =>
      choice(
        $.expr,
        $.logical_group,
        seq($.not, choice($.expr, $.logical_group)),
      ),

    // logical-group   = "(" ( logical-and | logical-or | logical-group
    //                       | expr with-modifier { with-modifier } ) ")"
    //
    // Parentheses regroup operands (`(a or b) and c`) and scope a `with` to a
    // single operand (`(a with x as y) and b`) — the latter is required, since a
    // trailing modifier binds to the whole expression, and OPA rejects
    // `a with x as y and b` outright ("`with` modifier is not allowed on
    // operand of `and`"). Requiring an `and`/`or` or a `with` inside is what
    // keeps the group disjoint from the pre-existing expr_parens term — `(a)`
    // and `(not a)` are still an ordinary parenthesized expression, matching
    // OPA, which collapses redundant parentheses around a single operand.
    logical_group: $ =>
      seq(
        $.open_paren,
        choice(
          $.logical_and,
          $.logical_or,
          $.logical_group,
          seq($.expr, repeat1($.with_modifier)),
        ),
        $.close_paren,
      ),

    // with-modifier   = "with" term "as" term
    with_modifier: $ => seq($.with, $.term, $.as, $.term),

    // some-decl       = "some" term { "," term } { "in" expr }
    some_decl: $ =>
      seq(
        $.some,
        $.term,
        repeat(seq(',', $.term)),
        repeat(seq($.in, $.expr)),
      ),

    // expr            = term | expr-call | expr-infix | expr-every | expr-unary
    expr: $ =>
      prec.left(
        1,
        choice(
          $.term,
          $.expr_call,
          $.expr_infix,
          $.expr_every,
          $.expr_parens,
          $.expr_unary,
        ),
      ),

    // expr-parens     = "(" expr ")"
    expr_parens: $ =>
      prec(
        -1,
        seq(
          $.open_paren,
          $.expr,
          $.close_paren,
        ),
      ),

    // expr-call       = var [ "." var ] "(" [ expr { "," expr } ] ")"
    expr_call: $ =>
      seq(
        field('func_name', $.fn_name),
        $.open_paren,
        field('func_arguments', optional($.fn_args)),
        $.close_paren,
      ),

    fn_name: $ => seq($.var, optional(seq('.', $.var))),
    fn_args: $ => seq($.expr, repeat(seq(',', $.expr))),

    // expr-infix = expr infix-operator expr
    expr_infix: $ => prec.left(1, seq($.expr, $.infix_operator, $.expr)),

    // expr-every      = "every" var { "," var } "in" ( term | expr-call | expr-infix ) "{" query "}"
    expr_every: $ =>
      seq(
        $.every,
        $.var,
        repeat(seq(',', $.var)),
        $.in,
        choice($.term, $.expr_call, $.expr_infix),
        $.open_curly,
        $.query,
        $.close_curly,
      ),

    // expr-unary      = "-" expr
    expr_unary: $ => prec.left(-3, seq('-', $.expr)),

    // term            = ref | var | scalar | array | object | set | array-compr | object-compr | set-compr | membership
    term: $ =>
      choice(
        $.ref,
        $.var,
        $.scalar,
        $.array,
        $.object,
        $.set,
        $.array_compr,
        $.object_compr,
        $.set_compr,
        $.membership,
      ),

    // array-compr     = "[" term "|" rule-body "]"
    array_compr: $ =>
      seq(
        $.open_bracket,
        $.term,
        '|',
        $.query,
        $.close_bracket,
      ),

    // set-compr       = "{" term "|" rule-body "}"
    set_compr: $ =>
      seq(
        $.open_curly,
        $.term,
        '|',
        $.query,
        $.close_curly,
      ),

    // object-compr    = "{" object-item "|" rule-body "}"
    object_compr: $ =>
      seq(
        $.open_curly,
        $.object_item,
        '|',
        $.query,
        $.close_curly,
      ),

    // infix-operator  = bool-operator | arith-operator | bin-operator
    infix_operator: $ =>
      choice(
        prec.left(2, $.assignment_operator),
        $.bool_operator,
        $.arith_operator,
        $.bin_operator,
      ),

    // assignment-operator = ":=" | "="
    assignment_operator: $ => choice($.assignment, $.unification),

    // assignment operator
    assignment: $ => ':=',

    // unification operator
    unification: $ => '=',

    // bool-operator   = "==" | "\!=" | "<" | ">" | ">=" | "<="
    bool_operator: $ =>
      choice(
        '==',
        '\!=',
        '<',
        '>',
        '>=',
        '<=',
      ),

    // arith-operator  = "+" | "-" | "*" | "/" | "%"
    arith_operator: $ =>
      choice(
        '+',
        '-',
        '*',
        '/',
        '%',
      ),

    // bin-operator    = "&" | "|"
    bin_operator: $ => choice('&', '|'),

    // ref             = ( var | array | object | set | array-compr | object-compr | set-compr | expr-call ) { ref-arg }
    ref: $ =>
      prec.left(
        2,
        seq(
          choice(
            $.var,
            $.array,
            $.object,
            $.set,
            $.array_compr,
            $.object_compr,
            $.set_compr,
            $.expr_call,
          ),
          repeat($.ref_arg),
        ),
      ),

    // ref-arg         = ref-arg-dot | ref-arg-brack
    ref_arg: $ =>
      choice(
        $.ref_arg_dot,
        $.ref_arg_brack,
      ),

    // ref-arg-brack   = "[" ( scalar | var | array | object | set | "_" ) "]"
    ref_arg_brack: $ =>
      seq(
        $.open_bracket,
        choice(
          $.scalar,
          $.ref,
          $.array,
          $.object,
          $.set,
          '_',
        ),
        $.close_bracket,
      ),

    // ref-arg-dot     = "." var
    ref_arg_dot: $ => prec.left(2, seq('.', $.var)),

    // var             = ( ALPHA | "_" ) { ALPHA | DIGIT | "_" }
    var: $ => /[A-Za-z_]+\w*/,

    // scalar          = string | NUMBER | TRUE | FALSE | NULL
    scalar: $ =>
      choice(
        $.string,
        $.number,
        $.boolean,
        'null',
      ),

    // string          = STRING | raw-string | interpolated-string-double | interpolated-string-raw
    string: $ => choice(
      $.interpolated_string_double,
      $.interpolated_string_raw,
      $.quoted_string,
      $.raw_string,
    ),

    // quoted-string   = '"' { CHAR } '"'
    // String content needs lexical precedence over the comment token:
    // without it, a '#' in the content lets the comment (which runs to end
    // of line) win the longest-match rule, so `"#fff"` lexed as a comment
    // and corrupted the rest of the parse.
    quoted_string: $ => seq(
      '"',
      optional(alias(token.immediate(prec(1, /([^\\"\n]|\\["\\/bfnrt]|\\u[0-9a-fA-F]{4})+/)), 'string_content')),
      '"',
    ),

    // raw-string      = "`" { CHAR-"`" } "`"
    raw_string: $ =>
      seq(
        '`',
        repeat(
          token.immediate(prec(1, /[^`]+/)),
        ),
        '`',
      ),

    // interpolated-string-double = '$"' { string-content | interpolation } '"'
    interpolated_string_double: $ => seq(
      '$"',
      repeat(choice(
        $.interpolation,
        $.interpolation_escape,
        $.string_escape,
        token.immediate(prec(1, /[^\\"\n{]+/)),
      )),
      '"',
    ),

    // interpolated-string-raw = '$`' { string-content | interpolation } '`'
    interpolated_string_raw: $ => seq(
      '$`',
      repeat(choice(
        $.interpolation,
        $.interpolation_escape,
        token.immediate(prec(1, /[^`{]+/)),
      )),
      '`',
    ),

    // interpolation = '{' expr '}'
    interpolation: $ => seq(
      '{',
      $.expr,
      '}',
    ),

    // string-escape = backslash escape sequences
    string_escape: $ => token.immediate(seq(
      '\\',
      choice(
        /["\\/bfnrt]/,
        seq('u', /[0-9a-fA-F]{4}/),
      ),
    )),

    // interpolation-escape = '\{' | '\}'
    interpolation_escape: $ => token.immediate(/\\[{}]/),

    // array           = "[" term { "," term } "]"
    array: $ =>
      seq(
        $.open_bracket,
        $.term,
        repeat(
          seq(',', $.term),
        ),
        optional(','),
        $.close_bracket,
      ),

    // object          = "{" object-item { "," object-item } "}"
    object: $ =>
      seq(
        $.open_curly,
        $.object_item,
        repeat(
          seq(',', $.object_item),
        ),
        optional(','),
        $.close_curly,
      ),

    // object-item     = ( scalar | ref | var ) ":" term
    object_item: $ =>
      seq(
        field('key', choice($.scalar, $.ref, $.var)),
        ':',
        field('value', $.term),
      ),

    // set             = empty-set | non-empty-set
    set: $ => choice($.empty_set, $.non_empty_set),

    // non-empty-set   = "{" term { "," term } "}"
    non_empty_set: $ =>
      seq(
        $.open_curly,
        $.term,
        repeat(
          seq(',', $.term),
        ),
        optional(','),
        $.close_curly,
      ),

    // empty-set       = "set(" ")"
    empty_set: $ => seq('set(', ')'),

    // comment
    comment: $ => token(seq('#', /.*/)),

    // boolean
    boolean: $ => choice('true', 'false'),

    // membership      = term [ "," term ] "in" term
    membership: $ =>
      prec.left(
        -1,
        seq(
          $.term,
          optional(seq(',', $.term)),
          $.in,
          $.term,
        ),
      ),

    // parenthesis
    open_paren: $ => '(',
    close_paren: $ => ')',

    // brackets
    open_bracket: $ => '[',
    close_bracket: $ => ']',

    // curly bracket
    open_curly: $ => '{',
    close_curly: $ => '}',

    // number
    number: $ => /([0-9]*[.])?[0-9]+/,

    // not keyword
    not: $ => 'not',

    // and keyword
    and: $ => 'and',

    // or keyword
    or: $ => 'or',

    // with keyword
    with: $ => 'with',

    // as keyword
    as: $ => 'as',

    // in keyword
    in: $ => 'in',

    // if keyword
    if: $ => 'if',

    // every keyword
    every: $ => 'every',

    // else keyword
    else: $ => 'else',

    // package keyword
    package: $ => 'package',

    // import keyword
    import: $ => 'import',

    // contains keyword
    contains: $ => 'contains',

    // some keyword
    some: $ => 'some',

    // default keyword
    default: $ => 'default',

    // match whole words
    keyword: $ => /[a-z]+/,
  },
});
