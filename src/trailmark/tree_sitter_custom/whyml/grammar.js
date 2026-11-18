// Independently authored WhyML source subset. Unsupported forms carry coverage diagnostics.
// SPDX-License-Identifier: Apache-2.0
// Syntax reference: https://why3.org/doc/syntaxref.html
const comma = (rule) => seq(rule, repeat(seq(",", rule)));
module.exports = grammar({
  name: "whyml",
  word: ($) => $.identifier,
  reserved: {
    global: ($) => [
      "abstract",
      "absurd",
      "alias",
      "any",
      "at",
      "break",
      "by",
      "check",
      "coinductive",
      "continue",
      "diverges",
      "epsilon",
      "exn",
      "export",
      "for",
      "fun",
      "function",
      "ghost",
      "goal",
      "if",
      "import",
      "in",
      "inductive",
      "invariant",
      "label",
      "lemma",
      "let",
      "match",
      "meta",
      "module",
      "mutable",
      "not",
      "old",
      "partial",
      "predicate",
      "private",
      "pure",
      "raise",
      "raises",
      "reads",
      "rec",
      "requires",
      "returns",
      "scope",
      "so",
      "then",
      "theory",
      "to",
      "try",
      "type",
      "use",
      "val",
      "variant",
      "while",
      "with",
      "writes",
    ],
  },
  extras: ($) => [/\s/, $.comment, $.attribute],
  externals: ($) => [$.comment],
  conflicts: ($) => [],
  rules: {
    source_file: ($) => repeat($.module_declaration),
    module_declaration: ($) =>
      seq(
        choice("module", "theory"),
        field("name", $.constructor_identifier),
        repeat($._declaration),
        "end",
      ),
    _declaration: ($) =>
      choice(
        $.scope_declaration,
        $.use_declaration,
        $.clone_declaration,
        $.type_declaration,
        $.logic_declaration,
        $.let_declaration,
        $.val_declaration,
        $.proposition,
        $.exception_declaration,
        $.unsupported_keyword,
      ),
    scope_declaration: ($) =>
      seq(
        "scope",
        optional(choice("import", "export")),
        field("name", $.constructor_identifier),
        repeat($._declaration),
        "end",
      ),
    use_declaration: ($) =>
      seq(
        "use",
        optional(choice("import", "export")),
        field(
          "module",
          choice($.qualified_identifier, $.constructor_identifier),
        ),
        optional(seq("as", field("alias", $.constructor_identifier))),
      ),
    clone_declaration: ($) =>
      seq(
        "clone",
        optional(choice("import", "export")),
        field(
          "module",
          choice($.qualified_identifier, $.constructor_identifier),
        ),
        optional(seq("as", field("alias", $.constructor_identifier))),
        optional(seq("with", comma($.substitution))),
      ),
    substitution: ($) =>
      seq(
        optional(
          choice(
            "type",
            "function",
            "predicate",
            "val",
            "axiom",
            "lemma",
            "goal",
          ),
        ),
        choice($.identifier, $.constructor_identifier, $.qualified_identifier),
        "=",
        choice($._type, $.constructor_identifier, "*"),
      ),
    type_declaration: ($) =>
      seq(
        "type",
        field("name", $.identifier),
        repeat($.type_variable),
        optional(seq("=", choice($.record_type, $.variant_type, $._type))),
        repeat($.contract),
      ),
    record_type: ($) =>
      seq(
        "{",
        $.field_declaration,
        repeat(seq(";", $.field_declaration)),
        optional(";"),
        "}",
      ),
    field_declaration: ($) =>
      seq(
        repeat(choice("mutable", "ghost")),
        field("name", $.identifier),
        ":",
        field("type", $._type),
      ),
    variant_type: ($) =>
      seq(
        optional("|"),
        $.constructor_declaration,
        repeat(seq("|", $.constructor_declaration)),
      ),
    constructor_declaration: ($) =>
      seq(field("name", $.constructor_identifier), repeat($._type_atom)),
    logic_declaration: ($) =>
      seq(
        choice("function", "predicate", "constant"),
        field("name", $.identifier),
        repeat(field("parameter", $.binder)),
        optional(seq(":", field("type", $._type))),
        optional(seq("=", field("body", $._expression))),
      ),
    let_declaration: ($) =>
      seq(
        "let",
        optional("rec"),
        repeat(choice("ghost", "lemma", "function", "predicate")),
        field("name", $.identifier),
        repeat(field("parameter", choice($.binder, $.identifier, $.unit))),
        optional(seq(":", field("type", $._type))),
        repeat($.contract),
        "=",
        field("body", $._expression),
      ),
    val_declaration: ($) =>
      seq(
        "val",
        repeat(choice("ghost", "function", "predicate")),
        field("name", $.identifier),
        repeat(field("parameter", choice($.binder, $.unit))),
        optional(seq(":", field("type", $._type))),
        repeat($.contract),
      ),
    proposition: ($) =>
      seq(
        choice("axiom", "lemma", "goal"),
        field("name", choice($.identifier, $.constructor_identifier)),
        ":",
        field("body", $._expression),
      ),
    exception_declaration: ($) =>
      seq(
        "exception",
        field("name", $.constructor_identifier),
        optional($._type),
      ),
    binder: ($) =>
      seq(
        "(",
        optional("ghost"),
        repeat1(field("name", $.identifier)),
        ":",
        field("type", $._type),
        ")",
      ),
    contract: ($) =>
      seq(
        choice(
          "requires",
          "ensures",
          "invariant",
          "variant",
          "reads",
          "writes",
          "raises",
          "returns",
        ),
        "{",
        optional(field("body", $._expression)),
        "}",
      ),
    _type: ($) => choice($._type_atom, $.type_application, $.arrow_type),
    _type_atom: ($) =>
      choice(
        $.identifier,
        $.qualified_identifier,
        $.type_variable,
        $.unit,
        seq("(", comma($._type), ")"),
      ),
    type_application: ($) =>
      prec.left(2, seq($._type_atom, repeat1($._type_atom))),
    arrow_type: ($) => prec.right(1, seq($._type, "->", $._type)),
    _expression: ($) =>
      choice(
        $._atom,
        $.application,
        $.binary_expression,
        $.unary_expression,
        $.if_expression,
        $.let_expression,
        $.lambda_expression,
        $.quantifier,
        $.match_expression,
        $.block_expression,
        $.while_expression,
        $.for_expression,
        $.assert_expression,
        $.ghost_expression,
        $.unsupported_keyword,
      ),
    _atom: ($) =>
      choice(
        $.identifier,
        $.qualified_identifier,
        $.constructor_identifier,
        $.number,
        $.string,
        $.boolean,
        $.unit,
        $.parenthesized_expression,
      ),
    unit: ($) => "()",
    parenthesized_expression: ($) => seq("(", comma($._expression), ")"),
    application: ($) =>
      prec.left(
        12,
        seq(field("function", $._atom), repeat1(field("argument", $._atom))),
      ),
    binary_expression: ($) =>
      choice(
        prec.right(1, seq($._expression, ";", $._expression)),
        prec.right(2, seq($._expression, choice("<-", ":="), $._expression)),
        prec.right(3, seq($._expression, choice("->", "<->"), $._expression)),
        prec.left(4, seq($._expression, choice("\\/", "||"), $._expression)),
        prec.left(5, seq($._expression, choice("/\\", "&&"), $._expression)),
        prec.left(
          6,
          seq(
            $._expression,
            choice("=", "<>", "<", ">", "<=", ">="),
            $._expression,
          ),
        ),
        prec.left(8, seq($._expression, choice("+", "-"), $._expression)),
        prec.left(9, seq($._expression, choice("*", "/", "%"), $._expression)),
      ),
    unary_expression: ($) =>
      prec(11, seq(choice("not", "-", "!", "old"), $._expression)),
    if_expression: ($) =>
      prec.right(
        seq(
          "if",
          $._expression,
          "then",
          $._expression,
          optional(seq("else", $._expression)),
        ),
      ),
    let_expression: ($) =>
      prec.right(
        seq(
          "let",
          repeat(choice("rec", "ghost", "ref")),
          field("name", $.identifier),
          repeat(field("parameter", $.binder)),
          optional(seq(":", $._type)),
          "=",
          field("value", $._expression),
          "in",
          field("body", $._expression),
        ),
      ),
    lambda_expression: ($) =>
      prec.right(
        seq(
          "fun",
          repeat1(field("parameter", choice($.identifier, $.binder))),
          "->",
          field("body", $._expression),
        ),
      ),
    quantifier: ($) =>
      prec.right(
        seq(
          choice("forall", "exists"),
          comma($.quantifier_binder),
          ".",
          field("body", $._expression),
        ),
      ),
    quantifier_binder: ($) =>
      seq(repeat1(field("name", $.identifier)), optional(seq(":", $._type))),
    match_expression: ($) =>
      seq("match", $._expression, "with", repeat1($.match_case), "end"),
    match_case: ($) => seq("|", field("pattern", $.pattern), "->", field("body", $._expression)),
    pattern: ($) =>
      choice(
        field("name", $.identifier),
        "_",
        $.constructor_pattern,
        seq("(", comma($.pattern), ")"),
      ),
    constructor_pattern: ($) =>
      prec.right(seq($.constructor_identifier, repeat($.pattern))),
    block_expression: ($) => seq("begin", $._expression, "end"),
    while_expression: ($) =>
      seq(
        "while",
        $._expression,
        "do",
        repeat($.contract),
        field("body", $._expression),
        "done",
      ),
    for_expression: ($) =>
      seq(
        "for",
        field("name", $.identifier),
        "=",
        $._expression,
        choice("to", "downto"),
        $._expression,
        "do",
        repeat($.contract),
        field("body", $._expression),
        "done",
      ),
    assert_expression: ($) =>
      seq(choice("assert", "assume", "check"), "{", $._expression, "}"),
    ghost_expression: ($) => prec.right(seq("ghost", $._expression)),
    unsupported_keyword: ($) =>
      choice(
        "abstract",
        "absurd",
        "alias",
        "any",
        "at",
        "break",
        "by",
        "coinductive",
        "continue",
        "diverges",
        "epsilon",
        "exn",
        "inductive",
        "label",
        "meta",
        "partial",
        "private",
        "pure",
        "raise",
        "so",
        "try",
      ),
    identifier: ($) => /[a-z_][A-Za-z0-9_']*/,
    constructor_identifier: ($) => /[A-Z][A-Za-z0-9_']*/,
    qualified_identifier: ($) =>
      /[A-Za-z_][A-Za-z0-9_']*(\.[A-Za-z_][A-Za-z0-9_']*)+/,
    type_variable: ($) => /'[a-z][A-Za-z0-9_']*/,
    number: ($) => /[0-9][0-9_]*(\.[0-9]+)?/,
    boolean: ($) => choice("true", "false"),
    string: ($) => token(seq('"', repeat(choice(/[^"\\]/, /\\./)), '"')),
    attribute: ($) => token(seq("[@", /[^\]\n]*/, "]")),
  },
});
