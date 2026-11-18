/**
 * @file Rocq (formerly Coq) grammar for tree-sitter
 * @author Aru Zdh <aruzdh@gmail.com>
 * @license MIT
 */

/// <reference types="tree-sitter-cli/dsl" />
// @ts-check

module.exports = grammar({
  name: "rocq",

  word: $ => $.ident,
  externals: $ => [$.comment],

  extras: $ => [
    /\s/,
    $.comment
  ],

  conflicts: $ => [
    [$.generic_tactic_body],
    [$.match_pattern, $.list_literal],
    [$.binder],

    [$.fixannot, $._qualid],
    [$.assert_tactic, $._qualid],

    [$._term100, $.term_cast],
  ],

  precedences: $ => [
    [
      'scope',
      'application',
      'negation',
      'list_ops',
      'multiplicative',
      'additive',
      'comparison',

      'conjunction',
      'disjunction',
      'equivalence',

      'custom',
      'arrow_term',
      'quantifier_term',
      'binder',
      'type'
    ],
    [
      'tactic_match',
      'tactic_application',
      'tactic_or',
      'tactic_sequence'
    ],
  ],

  rules: {
    // ~~~~~~~~~~~~~ Top-level structure ~~~~~~~~~~~~
    source_file: $ => repeat($.sentence),

    sentence: $ => choice(
      seq(
        optional($.fail),
        optional($.attributes),
        optional($.selector),
        choice(
          $._vernacular_command,
          $.ltac_definition,
          $._ltac_expr,
        ),
        choice(".", "...")
      ),
      $.proof_block,
      $.bullet
    ),

    fail: $ => "Fail",

    attributes: $ => choice(
      seq(
        repeat1($._modern_attribute_block),
        repeat($.legacy_attr)
      ),
      repeat1($.legacy_attr)
    ),

    _modern_attribute_block: $ => seq(
      "#",
      "[",
      optional(seq(
        $.attribute,
        repeat(seq(",", $.attribute))
      )),
      "]"
    ),

    attribute: $ => seq($.ident, optional($.attr_value)),

    attr_value: $ => choice(
      seq("=", $.string),
      seq("=", $._qualid),
      seq(
        "(",
        $.attribute,
        repeat(seq(",", $.attribute)),
        ")"
      ),
    ),

    legacy_attr: $ => choice(
      choice("Local", "Global"),
      choice("Polymorphic", "Monomorphic"),
      choice("Cumulative", "NonCumulative"),
      "Private",
      "Program"
    ),

    selector: $ => seq(
      choice($.goal_selector, "all", "!", "par"),
      ":",
    ),

    goal_selector: $ => seq(
      $.range_selector,
      repeat(seq(",", $.range_selector))
    ),

    range_selector: $ => choice(
      $._natural,
      seq("[", $._qualid, "]"),
      seq(field("start", $._natural), "-", field("end", $._natural)),
    ),

    // ~~~~~~~~~~ Veracular command ~~~~~~~~~~~~~~~

    _vernacular_command: $ =>
      choice(
        $.require_command,
        $.import_command,
        $.export_command,
        $.create_hintdb_command,
        $.create_rewrite_hintdb_command,
        $.hint_command,
        $.evaluation_command,
        $.extraction_command,
        $.extract_command,
        $.set_command,
        $.unset_command,
        $.add_command,
        $.remove_command,
        $.test_command,
        $.show_proof_command,
        $.coercion_command,
        $.declare_command,
        $.open_scope_command,
        $.close_scope_command,

        $.theorem_command,
        $.definition_command,
        $.fixpoint_command,
        $.inductive_command,
        $.assumption_command,
        $.arguments_command,
        $.section_command,
        $.module_command,
        $.end_command,
        $.reserved_notation_command,
        $.notation_command,

        $.proof_command,
        $.qed_command,
        $.save_command,
        $.defined_command,
        $.admitted_command,
        $.abort_command,
      ),

    require_command: $ => seq(
      optional(seq("From", field("dirpath", $._qualid))),
      "Require",
      optional(seq(
        choice("Import", "Export"),
        optional($.import_categories)
      )),
      repeat1($.filtered_import)
    ),

    import_command: $ => seq(
      "Import",
      optional($.import_categories),
      repeat1($.filtered_import)
    ),

    export_command: $ => seq(
      "Export",
      optional($.import_categories),
      repeat1($.filtered_import)
    ),

    neg_selection: $ => "-",

    import_categories: $ => seq(
      optional($.neg_selection),
      "(",
      field("category", $._qualid),
      repeat(seq(",", field("category", $._qualid))),
      ")"
    ),

    filtered_import: $ => seq(
      field("module", $._qualid),
      optional(seq(
        "(",
        $.filtered_import_item,
        repeat(seq(",", $.filtered_import_item)),
        ")"
      ))
    ),

    filtered_import_item: $ => seq(
      field("name", $._qualid),
      optional($.import_wildcard)
    ),

    import_wildcard: $ => "(..)",

    create_hintdb_command: $ => seq(
      "Create",
      "HintDb",
      field("name", $.ident),
      optional($.discriminated_db)
    ),

    create_rewrite_hintdb_command: $ => seq(
      "Create",
      "Rewrite",
      "HintDb",
      field("name", $.ident),
    ),

    discriminated_db: $ => "discriminated",

    hint_command: $ => seq(
      "Hint",
      choice(
        $.hint_extern,
        seq(
          choice("Resolve", "Rewrite", "Constructors", "Unfold", "Immediate", "Transparent", "Opaque"),
          repeat1(field("name", $._qualid)),
          optional(seq(":", field("database", $.ident))))
      )),

    hint_extern: $ => seq(
      "Extern",
      field("cost", $._natural),
      field("pattern", optional($._one_pattern)),
      "=>",
      field("tactic", $._ltac_expr),

      optional(seq(
        ":",
        repeat1(field("database", $.ident))
      ))
    ),

    _one_pattern: $ => $._one_term,

    evaluation_command: $ => seq(
      choice("Check", "Compute", "Print", "Search", "Locate"),
      $._term,
    ),

    extraction_command: $ => choice(
      seq("Extraction", $._qualid),
      seq("Recursive", "Extraction", repeat1($._qualid)),
      seq("Extraction", $.string, repeat1($._qualid)),
      seq("Extraction", "Library", $.ident),
      seq("Recursive", "Extraction", "Library", $.ident),
      seq("Separete", "Extraction", repeat1($._qualid)),
      seq("Extraction", "TestCompile", repeat1($._qualid)),
      seq("Extraction", "Language", $.language),
      seq("Extraction", "Optimize"),
      seq("Extraction", "Conservative", "Types"),
      seq("Extraction", "KeepSingleton"),
      seq("Extraction", "AutoInline"),
      seq("Extraction", choice("Inline", "NoInline"), repeat1($._qualid)),
      seq("Extraction", "Implicit", $._qualid, "[", repeat(choice($.ident, $.integer)), "]"),
      seq("Extraction", "SafeImplicits"),
      seq("Extraction", "AccessOpaque"),
      seq("Extraction", "Blacklist", repeat1($.ident)),
      seq("Extraction", "File", "Comment", $.string),
      seq("Extraction", "Flag", $._natural),
      seq("Extraction", "TypeExpand"),
      seq("Extraction", "Output", "Directory", $.string),
    ),

    extract_command: $ => choice(
      seq("Extract", "Constant", $._qualid, repeat($.string), "=>", choice($.ident, $.string)),
      seq("Extract", "Inlined", "Constant", $._qualid, "=>", choice($.ident, $.string)),
      seq("Extract", "Inductive", $._qualid, "=>", choice($.ident, $.string),
        "[", repeat(choice($.ident, $.string)), "]", optional($.string)),
      seq("Extract", "Foreign", "Constant", $._qualid, "=>", $.string),
      seq("Extract", "Callback", optional($.string), $._qualid),
    ),

    language: $ => choice("OCaml", "Haskell", "Scheme", "JSON"),

    set_command: $ =>
      seq("Set", $.setting_name, optional(choice($.integer, $.string))),

    unset_command: $ =>
      seq("Unset", $.setting_name),

    add_command: $ =>
      seq("Add", $.setting_name, repeat1(choice($._qualid, $.string))),

    remove_command: $ =>
      seq("Remove", $.setting_name, repeat1(choice($._qualid, $.string))),

    test_command: $ =>
      seq("Test", $.setting_name, optional(seq("for", repeat1(choice($._qualid, $.string))))),

    setting_name: $ => prec.right(repeat1($.ident)),

    show_proof_command: $ => seq(
      "Show", "Proof", optional(seq("Diffs", optional("removed")))
    ),

    coercion_command: $ => seq(
      "Coercion",
      field("name", $.reference),
      optional(seq(
        ":", $.coercion_class, ">->", $.coercion_class))
    ),

    coercion_class: $ => choice("Funclass", "Sortclass", $.reference),

    declare_command: $ => choice(
      $._declare_scope_command,
      $._declare_custom_entry_command
    ),

    _declare_scope_command: $ => seq(
      "Declare", "Scope", field("name", $.ident)
    ),

    _declare_custom_entry_command: $ => seq(
      "Declare", "Custom", "Entry", field("name", $.ident)
    ),

    open_scope_command: $ => seq(
      "Open", "Scope", field("name", $.ident)
    ),

    close_scope_command: $ => seq(
      "Close", "Scope", field("name", $.ident)
    ),

    theorem_command: $ => seq(
      choice("Theorem", "Lemma", "Fact", "Remark", "Corollary", "Proposition", "Property"),
      $.ident_decl,
      repeat($.binder),
      ":",
      $.type,
      repeat(seq(
        "with",
        $.ident_decl,
        repeat($.binder),
        ":",
        $.type
      ))
    ),

    definition_command: $ => seq(
      choice("Definition", "Example"),
      $.ident_decl,
      $._def_body
    ),

    _def_body: $ => choice(
      seq(
        repeat($.binder),
        optional(seq(":", $.type)),
        ":=",
        field("body", $._term)
      ),
      seq(
        repeat($.binder),
        ":",
        $.type
      )
    ),

    fixpoint_command: $ => seq(
      "Fixpoint",
      $._fix_definition,
      repeat(seq("with", $._fix_definition)),
    ),

    _fix_definition: $ => seq(
      $.ident_decl,
      repeat($.binder),
      optional($.fixannot),
      optional(seq(":", $.type)),
      optional(seq(":=", field("body", $._term))),
      optional($.decl_notations)
    ),

    fixannot: $ => choice(
      seq("{", "struct", $.ident, "}"),
      seq("{", "wf", $._one_term, $.ident, "}"),
      seq("{", "measure", $._one_term, optional($.ident), optional($._one_term), "}"),
    ),

    decl_notations: $ => seq(
      "where", $.notation_declaration,
      repeat(seq("and", $.notation_declaration))
    ),

    inductive_command: $ => seq(
      "Inductive",
      $.inductive_definition,
      repeat(seq("with", $.inductive_definition)),
    ),

    inductive_definition: $ => seq(
      field("name", $.ident),
      repeat($.binder),
      optional(seq("|", repeat($.binder))),
      optional(seq(":", $.type)),
      ":=",
      optional($.constructor_block),
      optional($.decl_notations)
    ),

    constructor_block: $ => seq(
      optional("|"),
      $.constructor,
      repeat(seq("|", $.constructor))
    ),

    constructor: $ => seq(
      repeat($._constructor_attr),
      field("name", $.ident),
      repeat($.binder),
      optional($.of_type_inst)
    ),

    of_type_inst: $ => seq(
      choice(":", ":>", "::", "::>"),
      $.type
    ),

    _constructor_attr: $ =>
      seq(
        "#",
        "[",
        $.attribute,
        repeat(seq(",", $.attribute)),
        "]"),

    assumption_command: $ => seq(
      choice(
        "Axiom", "Axioms",
        "Conjecture", "Conjectures",
        "Parameter", "Parameters",
        "Hypothesis", "Hypotheses",
        "Variable", "Variables",
      ),

      optional(seq(
        "Inline",
        optional(seq("(", $._natural, ")"))
      )),

      choice(
        $.assumpt,
        repeat1(seq("(", $.assumpt, ")"))
      )
    ),

    assumpt: $ => seq(repeat1($.ident_decl), $.of_type),

    of_type: $ => seq(choice(":", ":>"), $.type),

    arguments_command: $ => seq(
      "Arguments",
      $.reference,

      repeat(choice(
        $.wildcard,
        $.ident,
        seq("(", repeat1($.ident), ")"),
        seq("{", repeat1($.ident), "}"),
        seq("[", repeat1($.ident), "]"),
        "/",
        "&"
      )),

      optional(seq(
        ",",
        repeat1(choice(
          $.ident,
          seq("[", repeat1($.ident), "]"),
          seq("{", repeat1($.ident), "}")
        ))
      )),

      // optional(seq(
      //   ":",
      //   repeat1($._args_modifier_placeholder)
      // ))
    ),

    // _args_modifier_placeholder: $ => "",

    section_command: $ => seq("Section", $.ident),

    module_command: $ => seq("Module", optional(choice("Import", "Export")), field("name", $.ident), optional(seq(":=", field("value", $._qualid)))),

    end_command: $ => seq("End", $.ident),

    reserved_notation_command: $ => seq(
      "Reserved",
      "Notation",
      field("notation", $.string),
      optional($.syntax_modifier_list)
    ),

    notation_command: $ => seq(
      "Notation",
      $.notation_declaration
    ),

    notation_declaration: $ => seq(
      field("notation", $.string),
      ":=",
      field("definition", $._one_term),
      optional($.syntax_modifier_list),
      optional($.notation_scope)
    ),

    notation_scope: $ => seq(
      ":",
      field("scope_name", $.ident)
    ),

    syntax_modifier_list: $ => seq(
      "(",
      $.syntax_modifier,
      repeat(seq(",", $.syntax_modifier)),
      optional(","),
      ")"
    ),

    syntax_modifier: $ => choice(
      $.level_modifier,
      $.custom_entry_modifier,
      $.associativity_modifier,
      $.parsing_modifier,
      $.printing_modifier,
      $.format_modifier,
      $.plural_variable_modifier,
      $.singular_variable_modifier
    ),

    level_modifier: $ => $.at_level_num,

    custom_entry_modifier: $ => seq(
      "in",
      "custom",
      $._qualid,
      optional($.at_level_num)
    ),

    associativity_modifier: $ => seq(
      choice("left", "right", "no"),
      "associativity"
    ),

    parsing_modifier: $ => seq(
      "only",
      "parsing"
    ),

    printing_modifier: $ => seq(
      "only",
      "printing"
    ),

    format_modifier: $ => seq(
      "format",
      $.string
    ),

    plural_variable_modifier: $ => seq(
      $.ident,
      repeat1(seq(",", $.ident)),
      choice($.at_level, seq("in", "scope", $.ident))
    ),

    singular_variable_modifier: $ => seq(
      $.ident,
      choice(
        seq("in", "scope", $.ident),
        seq("at", $.level, optional($.binder_interp)),
        $.explicit_subentry,
        $.binder_interp
      )
    ),

    explicit_subentry: $ => choice(
      "ident",
      "name",
      "global",
      "bigint",
      seq("strict", "pattern", optional($.at_level_num)),
      "binder",
      seq("closed", "binder"),
      seq("constr", optional($.at_level), optional($.binder_interp)),
      seq("custom", $.ident, optional($.at_level), optional($.binder_interp)),
      seq("pattern", optional($.at_level_num))
    ),

    binder_interp: $ => seq(
      "as",
      choice("ident", "name", "pattern", seq("strict", "pattern"))
    ),

    level: $ => choice(
      seq("level", $._natural),
      seq("next", "level"),
    ),

    at_level_num: $ => seq("at", "level", $._natural),

    at_level: $ => seq("at", $.level),

    proof_command: $ => seq(
      "Proof",
      optional(
        choice(
          seq(
            "using", $.section_var_expr,
            optional(seq("with", $._ltac_expr))
          ),
          seq(
            "with", $._ltac_expr,
            optional(seq("using", $.section_var_expr))
          )
        )
      )
    ),

    section_var_expr: $ => choice(
      seq($.expr_leaf, repeat1($.expr_leaf)),

      seq(
        optional("-"),
        $.expr_ops
      )
    ),

    expr_ops: $ => choice(
      seq($.expr_atomic, "-", $.expr_atomic),
      seq($.expr_atomic, "+", $.expr_atomic),
      $.expr_atomic
    ),

    expr_atomic: $ => choice(
      $.expr_leaf,
      seq("(", ")"),
      seq("(", $.section_var_expr, ")", optional("*"))
    ),

    expr_leaf: $ => choice(
      seq($.ident, optional("*")),
      seq("Type", optional("*")),
      "All"
    ),

    qed_command: $ => "Qed",

    admitted_command: $ => "Admitted",

    save_command: $ => seq("Save", $.ident),

    defined_command: $ => seq("Defined", optional($.ident)),

    abort_command: $ => seq("Abort", optional("All")),

    proof_block: $ => seq(
      "{",
      repeat(
        $.sentence,
      ),
      "}"
    ),

    bullet: $ => /[+*-]+/,

    // ~~~~~~~~~~~~~~~ Terms ~~~~~~~~~~~~~~~~~~

    _term: $ => choice(
      $._term100,
    ),

    _term100: $ => choice(
      $.term_cast,
      $._term99,
    ),

    term_cast: $ => prec.right('type', seq(
      $._term99,
      choice(":", "<:", "<<:", ":>"),
      $.type
    )),

    _term99: $ => choice(
      $._term10,
    ),

    _term10: $ => choice(
      $.application,
      $.lambda_function,
      $.quantifier_term,
      $.let_expression,
      $.fix_expression,
      $.if_expression,
      $._one_term,

      $.arrow_term,
      $._infix_operation,
      $.imp_evaluation_operation,
    ),

    _one_term: $ => choice(
      seq("@", $._qualid),
      $._term1
    ),

    _term1: $ => choice(
      $.scoped_term,
      $._term0,
    ),

    _term0: $ => seq(
      optional("'"), // For ImpParser Chapter
      choice(
        $._qualid_annotated,
        $.number,
        $.string,
        $.match_expression,
        $.parenthesized_term,
        $.metavariable,

        // Custom
        $.list_literal,
        $.recursive_notation_term,
        $.custom_notation_block,
      ),
    ),


    double_dot: $ => "..",

    recursive_notation_term: $ => seq(
      $.double_dot,
      $._term0,
      $.double_dot,
    ),

    parenthesized_term: $ => seq(
      "(",
      $._term,

      optional(choice(
        repeat1(seq(",", $._term)),
        repeat1(seq(";", $._term))
      )),

      optional(choice(",", ";")),
      ")"
    ),

    list_literal: $ => seq(
      "[",
      optional(seq(
        $._term,
        repeat(seq(";", $._term)),
        optional(";")
      )),
      "]"
    ),

    scoped_term: $ => prec.left('scope', seq(
      $._term1,
      '%',
      optional("_"),
      $.ident
    )),

    application: $ => prec.left('application', seq(
      $._term, repeat1($._arg)
    )),

    _arg: $ => choice(
      seq("(", field("argument_label", $.ident), ":=", $._term, ")"),
      seq("(", $._natural, ":=", $._term, ")"),
      $._term1,
    ),

    lambda_function: $ => prec('binder', seq(
      "fun",
      $.open_binders,
      "=>",
      $._term,
    )),

    quantifier_term: $ => prec.right('quantifier_term', seq(
      choice("forall", "exists"),
      $.open_binders,
      ",",
      $.type
    )),

    let_expression: $ => choice(
      $._let_expression_term,
      $._destructuring_let_name_list,
      $._destructuring_let_apos
    ),

    _let_expression_term: $ => prec.right('binder',
      seq(
        "let",
        field("name", $._name),
        repeat($.binder),
        optional(seq(":", $.type)),
        ":=",
        field("value", $._term),
        "in",
        field("body", $._term),
      )
    ),

    _destructuring_let_name_list: $ => prec.right('binder', seq(
      "let",
      field("pattern", $.let_expression_name_list),
      optional($.term_as_clause),
      optional($.term_return_clause),
      ":=",
      field("value", $._term),
      "in",
      field("body", $._term),
    )),

    _destructuring_let_apos: $ => prec.right('binder', seq(
      "let",
      field("pattern", $.destructuring_pattern),
      optional($.term_in_pattern),
      ":=",
      field("value", $._term),
      optional($.term_return_clause),
      "in",
      field("body", $._term),
    )),

    let_expression_name_list: $ => seq(
      "(",
      optional(seq($._name, repeat(seq(",", $._name)))),
      ")"
    ),

    match_expression: $ => seq(
      "match",
      $.case_item,
      repeat(seq(",", $.case_item)),
      optional($.term_return_clause),
      "with",
      optional(choice(
        seq(
          $.match_case,
          repeat(seq("|", $.match_case))
        ),
        repeat1(seq("|", $.match_case)),
      )),
      "end"
    ),

    case_item: $ => seq(
      $._term100,
      optional($.term_as_clause),
      optional($.term_in_pattern),
    ),

    match_case: $ => seq(
      $.pattern_option,
      repeat(seq("|", $.pattern_option)),
      "=>",
      field("body", $._term)
    ),

    pattern_option: $ => seq(
      field("pattern", $._pattern),
      repeat(seq(",", field("pattern", $._pattern))),
    ),

    if_expression: $ => prec.right('binder', seq(
      "if",
      field("condition", $._term),
      optional($.term_as_clause),
      optional($.term_return_clause),
      "then",
      field("then", $._term),
      "else",
      field("else", $._term),
    )),

    fix_expression: $ => prec.right(seq(
      "fix",
      $.fix_decl,
      optional(seq(
        repeat1(seq("with", $.fix_decl)),
        "for",
        $.ident
      )),
    )),

    fix_decl: $ => prec.right(seq(
      field("name", $.ident),
      repeat($.binder),
      optional($.fixannot),
      optional(seq(":", $.type)),
      ":=",
      field("body", $._term)
    )),

    // ~~~~~~~~~~ Term Clauses
    term_as_clause: $ => seq(
      "as", field("as_name", $._name)
    ),

    term_return_clause: $ => seq(
      "return", field("as_return", $._term100)
    ),

    term_in_pattern: $ => prec.left(seq(
      "in",
      field("in_pattern", $._pattern)
    )),

    _infix_operation: $ => choice(
      $.negation_operation,
      $.cons_operation,
      $.app_operation,
      $.multiplicative_operation,
      $.additive_operation,
      $.comparison_operation,
      $.logical_conjunction,
      $.logical_disjunction,
      $.logical_equivalence,
      $.custom_operation
    ),

    negation_operation: $ => prec.left('negation', seq(
      "~",
      $._term
    )),

    cons_operation: $ => prec.right('list_ops', seq(
      $._term,
      "::",
      $._term
    )),

    app_operation: $ => prec.right('list_ops', seq(
      $._term,
      "++",
      $._term
    )),

    multiplicative_operation: $ => prec.left('multiplicative', seq(
      $._term,
      choice("*", "/"),
      $._term
    )),

    additive_operation: $ => prec.left('additive', seq(
      $._term,
      choice("+", "-"),
      $._term
    )),

    comparison_operation: $ => prec.left('comparison', seq(
      $._term,
      choice("=", "<>", "<", "<=", ">", ">=", "=?", "<>?", "<?", "<=?", ">?", ">=?"),
      $._term
    )),

    logical_conjunction: $ => prec.right('conjunction', seq(
      $._term,
      "/\\",
      $._term
    )),

    logical_disjunction: $ => prec.right('disjunction', seq(
      $._term,
      "\\/",
      $._term
    )),

    logical_equivalence: $ => prec.left('equivalence', seq(
      $._term,
      "<->",
      $._term
    )),

    custom_operation: $ => prec.left('custom', seq(
      $._term,
      alias(
        choice(
          // any combination, but a dot
          /[\-!#$%&*+\/<=>?@^|~;]+/,
          // any combination with a dot, but using at least one non-dot char
          /[\-!#$%&*+.\/<=>?@^|~;]*[\-!#$%&*+\/<=>?@^|~;][\-!#$%&*+.\/<=>?@^|~;]*/,
          /\.\./,
          /\.\.\.\.+/
        )
        , $.custom_operator),
      $._term
    )),

    arrow_term: $ => prec.right('arrow_term', seq(
      field("antecedent", $._term),
      "->",
      field("consequent", $._term)
    )),

    // ~~~~~~~~~~~~~~~~ Patterns ~~~~~~~~~~~~~~~~

    _pattern: $ => prec.right(choice(
      $._pattern10,
      seq($._pattern10, ":", $._term),

      // Custom
      $.infix_pattern,
      $.custom_notation_block,
    )),

    infix_pattern: $ => prec.right('list_ops', seq(
      $._pattern,
      choice("::", "++"),
      $._pattern,
    )),

    _pattern10: $ => prec.right(choice(
      seq($._pattern10, "as", $._name),
      $.pattern_application,
      seq("@", $._qualid, repeat($._pattern1)),
      $._pattern1 // To go down
    )),

    pattern_application: $ => prec.left('application', seq(
      $._pattern10,
      repeat1($._pattern1)
    )),

    _pattern1: $ => choice(
      seq($._pattern1, "%", optional("_"), $.ident),
      $._pattern0
    ),

    _pattern0: $ => choice(
      $._qualid,
      $.wildcard,
      seq("{", "|", repeat(seq($._qualid, ":=", $._pattern)), "|", "}"),
      $.parenthesized_pattern,
      $.number,
      $.string,

      // Custom
      $.list_pattern
    ),

    parenthesized_pattern: $ => seq(
      "(",
      $._pattern,
      repeat(seq(choice("|", ","), $._pattern)),
      ")"
    ),

    list_pattern: $ => seq(
      "[",
      optional(seq(
        $._pattern,
        repeat(seq(";", $._pattern)),
      )),
      "]"
    ),

    destructuring_pattern: $ => prec.right(seq(
      "'", $._pattern
    )),

    // ~~~~~~~~~~~~~~~~ Binders and Type ~~~~~~~~~~~~~~~~
    type: $ => prec.right($._term),

    _name: $ => choice($.wildcard, $._qualid),

    binder: $ => choice(
      $._name,
      seq("(", repeat($._name), ":", $.type, ")"),
      seq("(", $._name, optional(seq(":", $.type)), ":=", $._term, ")"),
      $.implicit_binders,
      seq("(", $._name, ":", $.type, "|", $._term, ")"),
      seq($.destructuring_pattern),
    ),

    implicit_binders: $ => choice(
      seq("{", repeat1($._name), optional(seq(":", $.type)), "}"),
      seq("[", repeat1($._name), optional(seq(":", $.type)), "]")
    ),

    open_binders: $ => seq(
      repeat1($.binder),
      optional(seq(":", $.type))
    ),

    // ~~~~~~~~~~~~~~~ Tactics ~~~~~~~~~~~~~~~~~~~

    ltac_definition: $ => seq(
      "Ltac",
      field("name", $.ident),
      repeat($.ident),
      ":=",
      field("body", $._ltac_expr)
    ),

    _ltac_expr: $ => choice(
      $.parenthesized_tactic,

      $.tactic_sequence,
      $.tactic_branch,
      $.tactic_invocation,

      $.tactic_or,
      $.match_goal,
    ),

    parenthesized_tactic: $ => seq(
      "(",
      $._ltac_expr,
      ")"
    ),

    tactic_sequence: $ => prec.left('tactic_sequence', seq(
      $._ltac_expr,
      ";",
      $._ltac_expr
    )),

    tactic_branch: $ => seq(
      $._ltac_expr,
      ";",
      "[",
      optional($._for_each_goal),
      "]"
    ),

    _for_each_goal: $ => $._goal_tactics,

    _goal_tactics: $ => prec.left('tactic_sequence', choice(
      $.goal_branch,
      seq(
        optional($.goal_branch),
        repeat1(seq("|", optional($.goal_branch)))
      )
    )),

    goal_branch: $ => choice(
      seq($._ltac_expr, optional($.double_dot)),
      $.double_dot
    ),

    tactic_invocation: $ => choice(
      $.intros_tactic,
      $.assert_tactic,
      $.rewrite_tactic,
      $.tactical,
      $.generic_tactic
    ),

    intros_tactic: $ => seq(
      choice("intro", "intros"),
      repeat($.intro_pattern)
    ),

    assert_tactic: $ => prec.right(seq(
      "assert",
      choice(
        seq(
          "(",
          $.ident,
          ":",
          $.type,
          ")",
          optional($.by_clause)
        ),
        seq(
          "(",
          $.ident,
          ":=",
          $._term,
          ")"
        ),
        seq(
          $._one_type,
          optional($.as_clause),
          optional($.by_clause)
        )
      )
    )),

    _one_type: $ => $._one_term,

    rewrite_tactic: $ => prec.left('tactic_application', seq(
      "rewrite",
      $.oriented_rewriter,
      repeat(seq(",", $.oriented_rewriter)),
      optional($.in_clause),
      optional($.by_clause)
    )),

    oriented_rewriter: $ => seq(
      optional(choice("<-", "->")),
      optional($._natural),
      optional(choice("?", "!")),
      $.atomic_term_with_bindings
    ),

    atomic_term_with_bindings: $ => prec.right(seq(
      $._one_term, optional(seq("with", $.bindings))
    )),

    tactical: $ => prec('tactic_application', seq(
      choice("repeat", "try"),
      $._ltac_expr
    )),

    generic_tactic: $ => prec.left('tactic_application', seq(
      field("name", $.ident),
      optional($.generic_tactic_body),
      repeat(seq(",", $.generic_tactic_body)),
      optional(",")
    )),

    generic_tactic_body: $ => choice(
      seq(
        repeat1($._tactic_arg),
        repeat($._tactic_clause)
      ),
      repeat1($._tactic_clause)
    ),

    _tactic_clause: $ => choice(
      $.in_clause,
      $.as_clause,
      $.eqn_clause,
      $.by_clause,
      $.using_clause,
    ),

    _tactic_arg: $ => choice(
      $.atomic_term_with_bindings,
    ),

    tactic_or: $ => prec.left('tactic_or', seq(
      $._ltac_expr,
      choice("+", "||"),
      $._ltac_expr
    )),

    match_goal: $ => seq(
      choice("match", "lazymatch", "multimatch"),
      optional("reverse"),
      "goal",
      "with",
      choice(
        repeat1(seq("|", $.match_goal_case)),
        seq(
          $.match_goal_case,
          repeat(seq("|", $.match_goal_case))
        ),
        optional("|")
      ),
      "end"
    ),

    match_goal_case: $ => seq(
      $.goal_pattern,
      "=>",
      $._ltac_expr
    ),

    goal_pattern: $ => choice(
      $._goal_pattern_body,
      seq("[", $._goal_pattern_body, "]"),
      "_"
    ),

    _goal_pattern_body: $ => seq(
      optional(seq($.match_hyp, repeat(seq(",", $.match_hyp)))),
      "|-",
      field("goal", $.match_pattern)
    ),

    match_hyp: $ => seq(
      $._name,
      choice(
        seq(":", $.match_pattern),
        seq(":=", $.match_pattern),
        seq(":=", "[", $.match_pattern, "]", ":", $.match_pattern),
      )
    ),

    // ~~~~~~~~~~~~~~~~ Tactic Patterns ~~~~~~~~~~~~~~~~

    match_pattern: $ => choice(
      $._term,
      seq("context", optional($.ident), "[", $._term, "]")
    ),

    intro_pattern: $ => choice(
      $.ident,
      $.wildcard,
      "?",
      "->",
      "<-",
      $.bracketed_intro_pattern
    ),

    bracketed_intro_pattern: $ => seq(
      "[",
      repeat(choice(
        $.ident,
        "?",
        $.wildcard,
        "|",
        "&",
        $.bracketed_intro_pattern
      )),
      "]"
    ),

    // ~~~~~~~~~~~~~ Tactic Clauses ~~~~~~~~~~~~~~~

    in_clause: $ => prec.right(seq(
      "in",
      choice(
        seq($.ident, repeat(seq(",", $.ident))),
        "*")
    )),

    as_clause: $ => seq(
      "as",
      repeat1($.intro_pattern)
    ),

    by_clause: $ => prec.right(seq(
      "by",
      $._ltac_expr
    )),

    bindings: $ => prec.right('binder', choice(
      repeat1($._one_term),
      repeat1(seq(
        "(",
        field("name", choice($.ident, $._natural)),
        ":=",
        field("value", $._term),
        ")"
      ))
    )),

    eqn_clause: $ => seq("eqn", ":", $.ident),

    using_clause: $ => prec.right(seq(
      "using",
      $._one_term,
      repeat(seq(",", $._one_term))
    )),

    // ~~~~~~~~~~~~~ Lexical tokens ~~~~~~~~~~~~~~~

    _qualid: $ => choice(
      $.ident,
      $.dotted_qualid
    ),

    ident: $ => /[\p{L}_][\p{L}_\p{N}']*/u,

    ident_decl: $ => seq(
      $.ident,
    ),


    metavariable: $ => token(seq(
      "?",
      choice(
        /[a-zA-Z_][a-zA-Z0-9_']*/,
        /[0-9]+/
      )
    )),

    number: $ => token(choice(
      seq(
        optional('-'),
        /[0-9][0-9_]*/,
        optional(seq('.', /[0-9_]+/)),
        optional(seq(/[eE]/, optional(/[+-]/), /[0-9][0-9_]*/))
      ),
      seq(
        optional('-'),
        /0[xX][0-9a-fA-F][0-9a-fA-F_]*/,
        optional(seq('.', /[0-9a-fA-F_]+/)),
        optional(seq(/[pP]/, optional(/[+-]/), /[0-9][0-9_]*/))
      )
    )),

    integer: $ => $._bigint,

    _bigint: $ => seq(optional("-"), $._bignat),

    _natural: $ => $._bignat,

    _bignat: $ => choice($.decnat, $.hexnat),

    decnat: $ => /[0-9][0-9_]*/,

    digit: $ => /[0-9]/,

    hexnat: $ => /0[xX][0-9a-fA-F][0-9a-fA-F_]*/,

    hexdigit: $ => /[0-9a-fA-F]/,

    dotted_qualid: $ => token(seq(
      /[\p{L}_][\p{L}_\p{N}']*/u,
      repeat(seq('.', /[\p{L}_][\p{L}_\p{N}']*/u))
    )),

    _qualid_annotated: $ => seq(
      $._qualid,
    ),

    reference: $ => choice(
      $._qualid,
      seq($.string, optional(seq("%", $.ident)))
    ),

    string: $ => seq(
      '"',
      repeat(choice(
        /[^"]/,
        '""'
      )),
      '"'
    ),

    wildcard: $ => "_",

    // Rules for Software Foundations Imp Chapters
    custom_notation_block: $ => choice(
      seq('<{', repeat($._dsl_token), '}>'),
      seq('[|', repeat($._dsl_token), '|]'),
      seq('{|', repeat($._dsl_token), '|}')
    ),

    _dsl_token: $ => choice(
      $.ident,
      $.number,
      $.string,
      /[+\-*/&=<>!~|%^:;]+/,
      seq('(', repeat($._dsl_token), ')'),
      seq('[', repeat($._dsl_token), ']'),
      seq('{', repeat($._dsl_token), '}')
    ),


    imp_evaluation_operation: $ => prec.left('custom', seq(
      field("initial_state", $._term),
      '=[',
      $.imp_program,
      ']=>',
      field("final_state", $._term),
    )),

    imp_program: $ => repeat1($._imp_token),

    _imp_token: $ => choice(
      $.ident,
      $.number,
      ":=",
      /[+\-*/&=<>!~|%;]+/,
      seq("(", repeat($._imp_token), ")"),
      seq("[", repeat($._imp_token), "]"),
      seq("{", repeat($._imp_token), "}")
    ),
  }
});
