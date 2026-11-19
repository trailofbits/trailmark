/// <reference types="tree-sitter-cli/dsl" />
// @ts-check

// Dafny tree-sitter grammar v2 — designed around generics disambiguation.
//
// Key insight: call_expression, generic_expression, and binary '<' all share
// prec.left(PREC.RELATIONAL). This forces GLR to fork at '<', and
// prec.dynamic(10) on the generic path wins the merge when the content
// is a valid type list.

const PREC = {
  ARROW: 0,            // ~>, -->, ->
  SEMI_EXPR: 1,        // S;E (statement-in-expression)
  DECREASES_TO: 2,     // decreases to
  EQUIV: 3,            // <==>
  IMPLIES: 4,          // ==> (right-assoc)
  EXPLIES: 5,          // <== (left-assoc)
  BITWISE: 6,          // |, &, ^ (low for cardinality disambiguation)
  LOGICAL: 7,          // &&, || (same precedence in Dafny)
  RELATIONAL: 8,       // ==, !=, <, >, <=, >=, in, !in, !!
  SHIFT: 9,            // <<, >>
  ADD: 10,             // +, -
  MUL: 11,             // *, /, %
  AS_IS: 12,           // as, is
  UNARY: 13,           // !, -, ~
  PRIMARY: 14,         // suffix, application
};

module.exports = grammar({
  name: 'dafny',

  extras: $ => [
    /\s/,
    $.line_comment,
    $.block_comment,
  ],

  externals: $ => [
    $._generic_close,   // >  (single, for nested >>)
    $._generic_open,    // <  that starts generic instantiation
    $._less_than,       // <  relational
    $._less_equal,      // <=
    $._explies,         // <==
    $._equiv,           // <==>
    $._shift_left,      // <<
    $._arrow_left,      // <- (quantifier domain)
    $._bar_open,        // |  cardinality open
    $._bar_close,       // |  cardinality close
    $._bar,             // |  bitwise/guard/datatype/pattern
    $._bar_bar,         // || logical OR
    $._caret,           // ^  bitwise XOR
    $._ampersand,       // &  bitwise AND
    $._amp_amp,         // && logical AND
  ],

  word: $ => $.identifier,

  conflicts: $ => [
    // Chain self-conflicts
    [$.equiv_expression],
    [$.implies_explies_expression],
    [$.implies_expression],
    [$.implies_explies_expression, $.implies_expression],
    [$.logical_expression],
    [$.relational_expression],
    [$.shift_term],
    [$.term],
    [$.factor],
    [$.bitvector_factor],
    [$.as_expression],
    [$.as_expression, $.unary_expression],
    // Generic disambiguation
    [$.call_expression, $.relational_expression],
    [$.generic_expression, $.relational_expression],
    [$.call_expression, $.generic_expression, $.relational_expression],
    // Name vs type context
    [$.name_segment, $.qualified_name],
    [$.name_segment, $.named_type],
    [$.qualified_name, $.named_type],
    // Tuple
    [$.tuple_type],
    [$.parenthesized_expression, $.tuple_expression],
    // Statement vs expression
    [$._expression, $.assign_statement],
    [$.assign_statement, $.expression_statement],
    [$.assign_statement, $._stmt_expression],
    [$.expression_statement, $._stmt_expression],
    // Quantifiers and lambdas
    [$.quantifier_expression],
    [$.quantifier_domain],
    [$.quantifier_var],
    [$.forall_expression, $.forall_statement],
    [$.name_segment, $.lambda_expression],
    [$.formal, $.case_pattern],
    // Let/var
    [$.var_decl_statement, $.let_expression],
    [$.var_decl_statement, $.let_or_fail_expression],
    [$.var_decl_statement, $.let_expression, $.let_or_fail_expression],
    [$.var_decl_statement, $.case_pattern, $.local_var_decl],
    [$.local_var_decl, $.case_pattern],
    [$.let_or_fail_expression, $.bare_let_or_fail_expression],
    [$.let_expression],
    [$.let_or_fail_expression],
    [$.rhs, $.let_expression],
    [$.rhs, $.let_or_fail_expression],
    // Match
    [$.match_statement, $.match_expression],
    [$.case_stmt_clause, $.case_expr_clause],
    [$.case_expr_clause, $._stmt_expression],
    [$.assign_statement, $.case_expr_clause],
    [$.expression_statement, $.case_expr_clause],
    // Block vs set display
    [$.block_statement, $.set_display],
    [$.if_alternative_block, $.set_display],
    // Attribute
    [$.attribute],
    [$.attribute_arg, $.actual_binding],
    // Cardinality GLR fallback
    [$.cardinality_expression, $.bitvector_factor],
    [$.set_comprehension, $.generic_expression],
    [$.set_comprehension, $.call_expression],
    [$.quantifier_expression, $.generic_expression],
    [$.quantifier_expression, $.call_expression],
    [$.map_comprehension, $.generic_expression],
    [$.forall_expression, $.generic_expression],
    // Backtick
    [$.frame_expression, $.backtick_expression],
    // If case
    [$.if_statement],
    // Labeled
    [$.labeled_statement],
    // Module export
    [$.module_export],
    // Tuple expression vs lambda/case_pattern
    [$.tuple_expression, $.lambda_expression],
    [$.tuple_expression, $.case_pattern],
    [$.name_segment, $.case_pattern],
    // Bare bind
    [$.bare_bind_statement, $.bare_let_or_fail_expression],
    // Qualified member expression
    [$.qualified_member_expression, $.name_segment],
    [$.qualified_member_expression, $.named_type],
    [$.qualified_member_expression, $.suffix_expression],
    // fn_ variants
    [$.fn_let_expression, $.var_decl_statement],
    [$.fn_let_expression, $.let_expression],
    [$.fn_let_expression, $.let_or_fail_expression],
    [$.fn_let_expression, $.var_decl_statement, $.let_expression],
    [$.fn_let_expression, $.var_decl_statement, $.let_or_fail_expression],
    [$.fn_let_expression, $.var_decl_statement, $.let_expression, $.let_or_fail_expression],
    [$.fn_let_expression, $.case_pattern],
    [$.fn_let_expression, $.local_var_decl, $.case_pattern],
    [$.fn_let_expression, $.var_decl_statement, $.case_pattern, $.local_var_decl],
    [$._function_body_expression, $._stmt_expression],
    [$.fn_match_expression, $.match_expression],
    [$.fn_match_expression, $.match_statement],
    [$.fn_case_expr_clause, $.case_expr_clause],
    [$.fn_case_expr_clause, $.case_stmt_clause],
    [$.fn_case_expr_clause, $.case_expr_clause, $.case_stmt_clause],
    [$.fn_case_expr_clause, $._stmt_expression],
    [$._function_body_expression, $.case_expr_clause],
    // Lambda params
    [$.case_pattern, $.lambda_expression],
    // Generic + call + qualified_member
    [$.call_expression, $.generic_expression, $.qualified_member_expression],
    [$.call_expression, $.qualified_member_expression],
    [$.generic_expression, $.qualified_member_expression],
    // Negative literal vs unary minus
    [$.literal_expression],
    [$._guard, $.name_segment],
    [$.quantifier_expression, $.forall_expression],
    [$.tuple_type, $.tuple_expression],
    [$.qualified_name, $.name_segment, $.case_pattern],
    [$._function_body_expression, $.if_expression],
    [$._function_body_expression, $.let_expression],
    [$.tuple_type, $.tuple_expression, $.case_pattern],
    [$._tuple_element, $.case_pattern],
  ],

  rules: {
    source_file: $ => seq(
      repeat($.include_directive),
      repeat($._top_decl),
    ),

    // ========== Comments ==========

    line_comment: _ => token(seq('//', /[^\n]*/)),
    block_comment: _ => token(seq('/*', /(\*[^/]|[^*])*/, '*/')),

    // ========== Include ==========

    include_directive: $ => seq('include', $.string_literal),

    // ========== Top-level declarations ==========

    _top_decl: $ => seq(
      repeat($.attribute),
      repeat($.decl_modifier),
      choice(
        $.module_definition,
        $.module_import,
        $.module_export,
        $.class_decl,
        $.trait_decl,
        $.datatype_decl,
        $.newtype_decl,
        $.synonym_type_decl,
        $.iterator_decl,
        $.method_decl,
        $.function_decl,
        $.field_decl,
        $.constant_field_decl,
      ),
    ),

    decl_modifier: _ => choice('abstract', 'replaceable', 'ghost', 'static', 'opaque'),

    // ========== Attributes ==========

    attribute: $ => choice(
      seq('{:', $.identifier, repeat(seq(optional(','), $._expression)), '}'),
      seq('@', $.identifier, optional(seq('(', commaSep($.attribute_arg), ')'))),
    ),

    attribute_arg: $ => seq(optional(seq($.identifier, ':=')), $._expression),

    // ========== Modules ==========

    module_definition: $ => seq(
      'module', repeat($.attribute), field('name', $.qualified_name),
      optional(choice($.refines_clause, $.replaces_clause)), optional($.module_body),
    ),

    refines_clause: $ => seq('refines', $.qualified_name),
    replaces_clause: $ => seq('replaces', $.qualified_name),

    module_body: $ => seq('{', repeat($._top_decl), '}'),

    module_import: $ => seq(
      'import', repeat($.attribute), optional('opened'),
      choice(
        seq(field('name', $.identifier), '=', $._import_path),
        seq(field('name', $.identifier), ':', $._import_path),
        $._import_path,
      ),
    ),

    _import_path: $ => seq($.qualified_name, optional(choice(
      seq('`', $.identifier),
      seq('`', '{', commaSep1($.identifier), '}'),
    ))),

    module_export: $ => seq(
      'export', repeat($.attribute), optional(field('name', $.identifier)),
      optional('...'),
      repeat(choice(
        seq('provides', commaSep1($.export_id)),
        seq('reveals', commaSep1($.export_id)),
        seq('extends', commaSep1($.export_id)),
      )),
    ),

    export_id: $ => choice($.qualified_name, '*'),

    qualified_name: $ => prec.left(seq($.identifier, repeat(seq('.', $.identifier)))),

    // ========== Classes ==========

    class_decl: $ => seq(
      'class', repeat($.attribute), field('name', $.identifier),
      optional($.generic_parameters), optional($.extends_clause),
      optional('...'),
      '{', repeat($.class_member), '}',
    ),

    extends_clause: $ => seq('extends', commaSep1($._type)),

    class_member: $ => seq(
      repeat($.attribute), repeat($.decl_modifier),
      choice($.method_decl, $.function_decl, $.field_decl, $.constant_field_decl, $.constructor_decl),
    ),

    // ========== Traits ==========

    trait_decl: $ => seq(
      'trait', repeat($.attribute), field('name', $.identifier),
      optional($.generic_parameters), optional($.extends_clause),
      optional('...'),
      '{', repeat($.class_member), '}',
    ),

    // ========== Datatypes ==========

    datatype_decl: $ => seq(
      choice('datatype', 'codatatype'), repeat($.attribute),
      field('name', $.identifier), optional($.generic_parameters),
      optional($.extends_clause),
      choice(
        seq('=', optional($._bar), $.datatype_ctor, repeat(seq($._bar, $.datatype_ctor))),
        '...',
      ),
      optional(seq('{', repeat($.class_member), '}')),
    ),

    datatype_ctor: $ => seq(
      repeat($.attribute), optional('ghost'),
      field('name', $.identifier),
      optional(seq('(', commaSep($.datatype_formal), ')')),
    ),

    datatype_formal: $ => choice(
      seq(repeat($.attribute), choice(seq(optional('ghost'), optional('nameonly')), seq('nameonly', 'ghost')),
        field('name', choice($.identifier, $.integer_literal)), ':', $._type,
        optional(seq(':=', $._expression))),
      seq(repeat($.attribute), optional('ghost'), $._type),
    ),

    // ========== Newtypes ==========

    newtype_decl: $ => prec.right(seq(
      'newtype', repeat($.attribute), field('name', $.identifier),
      optional($.generic_parameters), optional($.extends_clause),
      choice(
        seq('=', $._type, optional(seq($._bar, $._expression, optional($.witness_clause))), optional($.witness_clause)),
        seq('=', $.identifier, optional(seq(':', $._type)), $._bar, $._expression, optional($.witness_clause)),
        '...',
      ),
      optional(seq('{', repeat($.class_member), '}')),
    )),

    witness_clause: $ => choice(
      seq('witness', choice('*', $._expression)),
      seq(token(seq('ghost', /\s+/, 'witness')), $._expression),
    ),

    // ========== Synonym types ==========

    synonym_type_decl: $ => prec.right(seq(
      'type', repeat($.attribute), field('name', $.identifier),
      optional($.type_parameter_characteristics),
      optional($.generic_parameters), optional($.extends_clause),
      choice(
        seq('=', $._type),
        seq('=', $.identifier, optional(seq(':', $._type)), $._bar, $._expression, optional($.witness_clause)),
        seq(),
      ),
      optional(seq('{', repeat($.class_member), '}')),
    )),

    // ========== Iterators ==========

    iterator_decl: $ => seq(
      'iterator', repeat($.attribute), field('name', $.identifier),
      optional($.generic_parameters), $.formals,
      optional(seq('yields', $.formals)),
      repeat(choice(
        $.requires_clause, $.yield_requires_clause,
        $.ensures_clause, $.yield_ensures_clause,
        $.modifies_clause, $.reads_clause, $.decreases_clause, $.invariant_clause,
      )),
      optional($.block_statement),
    ),

    yield_requires_clause: $ => seq('yield', 'requires', $._expression, optional(';')),
    yield_ensures_clause: $ => seq('yield', 'ensures', $._expression, optional(';')),

    // ========== Methods ==========

    method_decl: $ => seq(
      choice('method', 'lemma', 'colemma', 'greatest', 'least', 'twostate', 'inductive'),
      optional(choice('lemma', 'method')),
      repeat($.attribute),
      optional(field('name', choice($.identifier, $.integer_literal))),
      choice(
        seq(optional($.generic_parameters), optional($.ktype), $.formals,
          optional(seq('returns', $.formals)), repeat($._method_spec), optional($.block_statement)),
        seq('...', repeat($._method_spec), optional($.block_statement)),
      ),
    ),

    ktype: $ => seq('[', choice('nat', 'ORDINAL'), ']'),

    _method_spec: $ => choice(
      $.requires_clause, $.ensures_clause, $.modifies_clause, $.reads_clause, $.decreases_clause,
    ),

    // ========== Constructors ==========

    constructor_decl: $ => seq(
      'constructor', repeat($.attribute),
      optional(field('name', $.identifier)),
      choice(
        seq(optional($.generic_parameters), $.formals, repeat($._method_spec), optional($.block_statement)),
        seq('...', optional($.block_statement)),
      ),
    ),

    // ========== Functions ==========

    function_decl: $ => seq(
      choice(
        'function', 'predicate',
        seq('least', 'predicate'), seq('greatest', 'predicate'),
        seq('twostate', choice('function', 'predicate')),
        seq('inductive', 'predicate'), 'copredicate',
      ),
      optional('method'), repeat($.attribute),
      optional(field('name', choice($.identifier, $.integer_literal))),
      choice(
        seq(optional($.generic_parameters), optional($.ktype), $.formals,
          optional(choice(seq(':', $._type), seq(':', '(', $.identifier, ':', $._type, ')'))),
          repeat($._function_spec), optional($.function_body)),
        seq('...', repeat($._function_spec), optional($.function_body)),
      ),
    ),

    function_body: $ => seq(
      '{', $._function_body_expression, '}',
      optional(seq('by', 'method', $.block_statement)),
    ),

    _function_body_expression: $ => choice(
      $._stmt_expression,
      $.ghost_call_chain,
      $.fn_assert_expression, $.fn_assume_expression, $.fn_reveal_expression,
      $.fn_expect_expression, $.fn_calc_expression, $.fn_let_expression,
      $.fn_if_expression, $.fn_match_expression,
    ),

    ghost_call_chain: $ => prec.right(PREC.SEMI_EXPR, seq(
      $._expression, ';', $._function_body_expression,
    )),

    fn_assert_expression: $ => prec.right(PREC.SEMI_EXPR, seq(
      'assert', repeat($.attribute), $._expression,
      choice(seq('by', $.block_statement), ';'), $._function_body_expression)),
    fn_assume_expression: $ => prec.right(PREC.SEMI_EXPR, seq(
      'assume', repeat($.attribute), $._expression, ';', $._function_body_expression)),
    fn_reveal_expression: $ => prec.right(PREC.SEMI_EXPR, seq(
      choice('reveal', 'hide'), choice('*', commaSep1($._reveal_target)),
      ';', $._function_body_expression)),
    fn_expect_expression: $ => prec.right(PREC.SEMI_EXPR, seq(
      'expect', $._expression, optional(seq(',', $._expression)),
      ';', $._function_body_expression)),
    fn_calc_expression: $ => prec.right(PREC.SEMI_EXPR, seq(
      'calc', repeat($.attribute), optional($.calc_op),
      '{', repeat($.calc_line), '}', $._function_body_expression)),
    fn_let_expression: $ => seq(
      choice('var', 'ghost', seq('ghost', 'var')), commaSep1($.case_pattern),
      choice(seq(':=', commaSep1($._expression)), seq(':|', $._expression)),
      ';', $._function_body_expression),
    fn_if_expression: $ => prec.right(seq(
      'if', $._expression, 'then', $._function_body_expression,
      'else', $._function_body_expression)),
    fn_match_expression: $ => prec.dynamic(-1, prec.right(seq(
      'match', $._expression, choice(
        seq('{', repeat($.fn_case_expr_clause), '}'),
        repeat1($.fn_case_expr_clause))))),
    fn_case_expr_clause: $ => prec.dynamic(-1, seq(
      'case', repeat($.attribute), $._pattern, '=>', $._function_body_expression)),

    _function_spec: $ => choice(
      $.requires_clause, $.ensures_clause, $.reads_clause, $.decreases_clause,
    ),

    // ========== Fields ==========

    field_decl: $ => seq(
      'var', repeat($.attribute),
      commaSep1(seq(field('name', choice($.identifier, $.integer_literal)), ':', $._type)),
      optional(';'),
    ),

    constant_field_decl: $ => seq(
      'const', repeat($.attribute), field('name', $.identifier),
      optional(seq(':', $._type)), optional(seq(':=', $._stmt_expression)),
      optional(';'),
    ),

    // ========== Specifications ==========

    requires_clause: $ => seq('requires', repeat($.attribute),
      optional(seq(choice($.identifier, $.integer_literal), ':')), $._expression, optional(';')),
    ensures_clause: $ => seq('ensures', repeat($.attribute),
      optional(seq(choice($.identifier, $.integer_literal), ':')), $._expression, optional(';')),
    modifies_clause: $ => seq('modifies', repeat($.attribute), commaSep1($.frame_expression), optional(';')),
    reads_clause: $ => seq('reads', repeat($.attribute), commaSep1($.possible_wild_frame_expression), optional(';')),
    decreases_clause: $ => seq('decreases', repeat($.attribute), choice('*', commaSep1($._expression)), optional(';')),
    invariant_clause: $ => seq('invariant', repeat($.attribute),
      optional(seq(choice($.identifier, $.integer_literal), ':')), $._expression, optional(';')),

    frame_expression: $ => prec.right(choice(
      seq($._expression, optional(seq('`', $.identifier))),
      seq('`', $.identifier),
    )),
    possible_wild_frame_expression: $ => choice('*', '**', seq('(', '*', ')'), $.frame_expression),

    // ========== Generic parameters ==========

    generic_parameters: $ => seq($._generic_open, commaSep1($.type_parameter), $._generic_close),

    type_parameter: $ => prec.left(seq(
      optional($.variance), $.identifier,
      repeat($.type_parameter_characteristics),
      repeat(seq('extends', $._type)),
    )),

    variance: _ => choice('+', '-', '*', '!'),

    type_parameter_characteristics: $ => seq('(', commaSep1(choice('==', '0', '00', '!new')), ')'),

    // ========== Formals ==========

    formals: $ => seq('(', commaSep($.formal), ')'),

    formal: $ => seq(
      optional('ghost'), optional('new'), optional('nameonly'), optional('older'),
      field('name', $.identifier), ':', $._type,
      optional(seq(':=', $._expression)),
    ),

    // ========== Types ==========

    _type: $ => choice(
      $.named_type, $.int_type, $.nat_type, $.real_type, $.bool_type, $.char_type,
      $.string_type, $.object_type, $.bitvector_type, $.float_type, $.ordinal_type,
      $.set_type, $.iset_type, $.multiset_type, $.seq_type, $.map_type, $.imap_type,
      $.array_type, $.arrow_type, $.tuple_type,
    ),

    int_type: _ => 'int', nat_type: _ => 'nat', real_type: _ => 'real',
    bool_type: _ => 'bool', char_type: _ => 'char', string_type: _ => 'string',
    object_type: _ => choice('object', 'object?'),
    bitvector_type: _ => /bv[0-9]+/, float_type: _ => choice('fp32', 'fp64'),
    ordinal_type: _ => 'ORDINAL',

    set_type: $ => prec.dynamic(20, prec.left(seq('set', optional($.generic_instantiation)))),
    iset_type: $ => prec.dynamic(20, prec.left(seq('iset', optional($.generic_instantiation)))),
    multiset_type: $ => prec.dynamic(20, prec.left(seq('multiset', optional($.generic_instantiation)))),
    seq_type: $ => prec.dynamic(20, prec.left(seq('seq', optional($.generic_instantiation)))),
    map_type: $ => prec.dynamic(20, prec.left(seq('map', optional($.generic_instantiation)))),
    imap_type: $ => prec.dynamic(20, prec.left(seq('imap', optional($.generic_instantiation)))),
    array_type: $ => prec.left(seq(/array[0-9]*\??/, optional($.generic_instantiation))),

    arrow_type: $ => prec.right(PREC.ARROW, seq($._type, choice('->', '~>', '-->'), $._type)),
    tuple_type: $ => seq('(', commaSep(seq(optional('ghost'), $._type)), ')'),

    _type_qualified_name: $ => prec.right(PREC.PRIMARY + 1, seq($.identifier, repeat(seq('.', $.identifier)))),
    named_type: $ => prec.right(seq(alias($._type_qualified_name, $.qualified_name), optional($.generic_instantiation), optional('?'))),
    generic_instantiation: $ => seq($._generic_open, commaSep1($._type), $._generic_close),

    // ========== Statements ==========

    block_statement: $ => seq('{', repeat($._statement), '}'),
    opaque_block: $ => seq('opaque', repeat(choice($.ensures_clause, $.modifies_clause)), $.block_statement),

    _statement: $ => choice(
      $.block_statement, $.opaque_block, $.assign_statement, $.var_decl_statement,
      $.if_statement, $.while_statement, $.for_statement, $.match_statement,
      $.assert_statement, $.assume_statement, $.expect_statement,
      $.print_statement, $.return_statement, $.yield_statement,
      $.break_statement, $.continue_statement, $.forall_statement,
      $.modify_statement, $.calc_statement, $.reveal_statement,
      $.labeled_statement, $.bare_bind_statement, $.ellipsis_statement,
      $.expression_statement,
    ),

    var_decl_statement: $ => prec.dynamic(25, seq(
      choice('var', 'ghost', seq('ghost', 'var')), repeat($.attribute),
      choice(commaSep1($.local_var_decl), $.case_pattern),
      optional(choice(
        seq(':=', commaSep1($.rhs)),
        seq(':|', optional('assume'), $._expression),
        seq(':-', optional(seq(choice('expect', 'assert', 'assume'), repeat($.attribute))), commaSep1($.rhs)),
      )),
      choice(seq('by', $.block_statement), ';'),
    )),

    local_var_decl: $ => seq(field('name', $.identifier), optional(seq(':', $._type))),

    rhs: $ => choice(
      seq('new', $._type, repeat(seq('.', $.identifier, optional($.generic_instantiation))),
        optional(seq('(', commaSep($.actual_binding), ')'))),
      seq('new', optional($._type), '[', commaSep($._expression), ']',
        repeat(seq('[', commaSep($._expression), ']')),
        optional(seq('(', $._expression, ')'))),
      seq('*'),
      $._expression,
    ),

    assign_statement: $ => seq(
      commaSep1($._expression),
      choice(
        seq(':=', commaSep1($.rhs)),
        seq(':|', optional('assume'), $._expression),
        seq(':-', optional(seq(choice('expect', 'assert', 'assume'), repeat($.attribute))), commaSep1($.rhs)),
      ),
      choice(seq('by', $.block_statement), ';'),
    ),

    if_statement: $ => prec.right(seq(
      'if', repeat($.attribute),
      choice(
        seq(optional('case'), $._guard, $.block_statement,
          optional(seq('else', choice($.if_statement, $.block_statement)))),
        $.if_alternative_block,
        repeat1($.if_case_clause),
      ),
    )),

    if_case_clause: $ => prec.right(seq('case', repeat($.attribute), $._guard, '=>', repeat($._statement))),

    _guard: $ => choice(
      '*', seq('(', '*', ')'), '...',
      $._expression,
      seq(commaSep1(seq($.identifier, optional(seq(':', $._type)))), repeat($.attribute), ':|', $._expression),
    ),

    if_alternative_block: $ => seq('{',
      repeat(seq('case', repeat($.attribute), $._guard, '=>', repeat($._statement))), '}'),

    while_statement: $ => prec.right(seq(
      'while', repeat($.attribute),
      choice(
        seq(choice($._expression, '*', '...'), repeat($._loop_spec), optional($.block_statement)),
        seq(repeat($._loop_spec), choice($.if_alternative_block, repeat1($.if_case_clause))),
      ),
    )),

    _loop_spec: $ => choice($.invariant_clause, $.decreases_clause, $.modifies_clause),

    for_statement: $ => prec.right(seq(
      'for', $.identifier, optional(seq(':', $._type)), ':=', $._expression,
      choice('to', 'downto'), choice($._expression, '*'),
      repeat($._loop_spec), optional($.block_statement),
    )),

    match_statement: $ => prec.dynamic(1, prec.right(seq(
      'match', repeat($.attribute), $._expression,
      choice(seq('{', repeat($.case_stmt_clause), '}'), repeat($.case_stmt_clause)),
    ))),

    case_stmt_clause: $ => prec.dynamic(1, prec.right(seq(
      'case', repeat($.attribute), $._pattern, '=>', repeat($._statement)))),

    assert_statement: $ => seq('assert', repeat($.attribute),
      choice(
        seq(optional(seq(choice($.identifier, $.integer_literal), ':')), $._expression,
          choice(seq('by', $.block_statement), ';')),
        seq('...', ';'),
      )),

    assume_statement: $ => seq('assume', repeat($.attribute), choice($._expression, '...'), ';'),
    expect_statement: $ => seq('expect', repeat($.attribute), $._expression, optional(seq(',', $._expression)), ';'),
    print_statement: $ => seq('print', commaSep1($._expression), ';'),
    return_statement: $ => seq('return', repeat($.attribute), optional(commaSep1($.rhs)), ';'),
    yield_statement: $ => seq('yield', optional(commaSep1($.rhs)), ';'),
    break_statement: $ => seq('break', repeat($.attribute), optional(choice($.identifier, repeat1('break'))), ';'),
    continue_statement: $ => seq('continue', repeat($.attribute), optional($.identifier), ';'),

    forall_statement: $ => prec.right(seq(
      'forall', choice(seq('(', optional($.quantifier_domain), ')'), optional($.quantifier_domain)),
      repeat($.ensures_clause), optional($.block_statement),
    )),

    modify_statement: $ => seq('modify', repeat($.attribute),
      choice(
        seq(commaSep1($.frame_expression), choice($.block_statement, ';')),
        seq('...', choice($.block_statement, ';')),
      )),

    calc_statement: $ => seq('calc', repeat($.attribute), optional($.calc_op),
      '{', repeat($.calc_line), '}'),

    calc_op: $ => choice(
      seq('==', optional(seq('#', '[', $._expression, ']'))),
      seq('!=', optional(seq('#', '[', $._expression, ']'))),
      $._less_than, '>', $._less_equal, '>=', $._equiv, '==>', $._explies,
    ),

    calc_line: $ => prec.right(seq(
      $._stmt_expression, ';',
      optional($.calc_op), repeat(choice($.block_statement, $.calc_statement)),
    )),

    reveal_statement: $ => seq(
      choice('reveal', 'hide'),
      choice('*', commaSep1($._reveal_target)), ';',
    ),

    _reveal_target: $ => choice($._expression, 'nat', 'int', 'bool', 'char', 'real', 'string', 'ORDINAL'),

    labeled_statement: $ => prec.right(seq(repeat1(seq('label', $.identifier, ':')), $._statement)),
    bare_bind_statement: $ => seq(':-', optional(choice('expect', 'assert', 'assume')), commaSep1($._expression), ';'),
    ellipsis_statement: _ => seq('...', ';'),

    expression_statement: $ => choice(
      seq($._expression, ';'),
      seq($._expression, 'by', $.block_statement),
    ),

    // ========== Patterns ==========

    _pattern: $ => choice($.disjunctive_pattern, $._single_pattern),
    _single_pattern: $ => choice($.constructor_pattern, $.tuple_pattern, $.literal_pattern, $.id_pattern),
    constructor_pattern: $ => seq($.identifier, '(', commaSep($._pattern), ')'),
    tuple_pattern: $ => seq('(', commaSep($._pattern), ')'),
    literal_pattern: $ => choice($.integer_literal, $.real_literal, $.boolean_literal, $.string_literal, $.char_literal,
      seq('-', $.integer_literal), seq('-', $.real_literal)),
    id_pattern: $ => seq(optional('ghost'), $.identifier, optional(seq(':', $._type))),
    disjunctive_pattern: $ => choice(
      seq(optional($._bar), $._single_pattern, repeat1(seq($._bar, $._single_pattern))),
      seq($._bar, $._single_pattern),
    ),

    // ========== Stmt-expression ==========

    _stmt_expression: $ => choice(
      $._expression,
      $.reveal_expression, $.assert_expression, $.assume_expression,
      $.expect_expression, $.calc_expression,
    ),

    // ========== Expressions — EBNF chain structure ==========
    // Precedence is encoded in the chain: equiv < implies < logical < relational
    // < shift < add < mul < bitwise < as < unary < primary
    // Key: bitwise | is ONLY in bitvector_factor, cardinality | is in primary_expression.

    _expression: $ => $.equiv_expression,

    // 17.2.7.2 Equivalence
    equiv_expression: $ => seq(
      $.implies_explies_expression,
      repeat(seq($._equiv, $.implies_explies_expression)),
    ),

    // 17.2.7.3 Implies/Explies
    implies_explies_expression: $ => seq(
      $.logical_expression,
      optional(choice(
        seq('==>', $.implies_expression),
        seq($._explies, $.logical_expression, repeat(seq($._explies, $.logical_expression))),
      )),
    ),

    implies_expression: $ => prec.right(seq(
      $.logical_expression,
      optional(seq('==>', $.implies_expression)),
    )),

    // 17.2.7.4 Logical — left-recursive binary (avoids GLR branch explosion)
    logical_expression: $ => choice(
      $.relational_expression,
      prec.left(PREC.LOGICAL, seq($.logical_expression, $._amp_amp, $.relational_expression)),
      prec.left(PREC.LOGICAL, seq($.logical_expression, $._bar_bar, $.relational_expression)),
      // Prefix && / || (Dafny allows these)
      seq($._amp_amp, $.relational_expression),
      seq($._bar_bar, $.relational_expression),
    ),

    // 17.2.7.5 Relational
    // Left-recursive to avoid GLR branch explosion with chained relationals
    relational_expression: $ => choice(
      $.shift_term,
      prec.left(PREC.RELATIONAL, seq($.relational_expression, $.rel_op, $.shift_term)),
    ),

    rel_op: $ => choice(
      seq('==', optional(seq('#', '[', $._expression, ']'))),
      seq('!=', optional(seq('#', '[', $._expression, ']'))),
      $._less_than, '>', $._less_equal, '>=', 'in', '!in', '!!',
    ),

    // 17.2.7.6 Shift
    shift_term: $ => seq($.term, repeat(seq(choice($._shift_left, '>>'), $.term))),

    // 17.2.7.7 Term (add)
    term: $ => choice($.factor,
      prec.left(PREC.ADD, seq($.term, choice('+', '-'), $.factor))),

    // 17.2.7.8 Factor (mul)
    factor: $ => choice($.bitvector_factor,
      prec.left(PREC.MUL, seq($.factor, choice('*', '/', '%'), $.bitvector_factor))),

    // 17.2.7.9 Bitvector — | is HERE, separate from cardinality
    bitvector_factor: $ => seq($.as_expression, repeat(seq(choice($._bar, $._ampersand, $._caret), $.as_expression))),

    // 17.2.7.10 As/Is
    as_expression: $ => seq($.unary_expression, repeat(seq(choice('as', 'is'), $._type))),

    // 17.2.7.11 Unary
    unary_expression: $ => choice(
      seq('-', $.unary_expression),
      seq('!', $.unary_expression),
      seq('~', $.unary_expression),
      $._primary_expression,
    ),

    // 17.2.7.12 Primary — all non-operator expressions
    _primary_expression: $ => choice(
      $.decreases_to_expression,
      $.call_expression,
      $.generic_expression,
      $.if_expression,
      $.match_expression,
      $.let_expression,
      $.let_or_fail_expression,
      $.bare_let_or_fail_expression,
      $.quantifier_expression,
      $.lambda_expression,
      $.forall_expression,
      $.set_comprehension,
      $.map_comprehension,
      $.set_display,
      $.multiset_display,
      $.seq_display,
      $.map_display,
      $.old_expression,
      $.unchanged_expression,
      $.fresh_expression,
      $.parenthesized_expression,
      $.tuple_expression,
      $.seq_construction,
      $.multiset_construction,
      $.suffix_expression,
      $.name_segment,
      $.qualified_member_expression,
      $.this_expression,
      $.cardinality_expression,
      $.backtick_expression,
      $.literal_expression,
    ),

    decreases_to_expression: $ => prec.left(PREC.DECREASES_TO, seq(
      $._expression, token(seq('decreases', /\s+/, 'to')), $._expression)),

    type_cast_expression: $ => prec.left(PREC.AS_IS, seq(
      $._expression, choice('as', 'is'), $.type_cast_target)),

    type_cast_target: $ => prec.right(PREC.AS_IS, choice(
      seq($.qualified_name, optional($.generic_instantiation), optional('?')),
      seq($._type_cast_base, choice('->', '~>', '-->'), $._type),
      'int', 'nat', 'real', 'bool', 'char', 'string', 'ORDINAL',
      /bv[0-9]+/, 'fp32', 'fp64', 'object', 'object?',
    )),

    _type_cast_base: $ => prec.right(PREC.AS_IS, choice(
      seq($.qualified_name, optional($.generic_instantiation), optional('?')),
      'int', 'nat', 'real', 'bool', 'char', 'string', 'ORDINAL', /bv[0-9]+/, 'object', 'object?',
    )),

    // old unary_expression removed — now part of expression chain above

    // ========== Generic call and generic expression ==========
    // THE KEY: same static prec as binary '<' forces GLR fork.
    // prec.dynamic(10) wins merge when content is valid types.

    call_expression: $ => prec.dynamic(10, prec.left(PREC.RELATIONAL, seq(
      field('function', $._primary_expression),
      field('type_arguments', $.type_arguments),
      '(', commaSep($.actual_binding), ')',
    ))),

    generic_expression: $ => prec.dynamic(10, prec.left(PREC.RELATIONAL, seq(
      $._expression, $.type_arguments,
    ))),

    type_arguments: $ => seq($._generic_open, commaSep1($._type), $._generic_close),

    // ========== Suffix expressions ==========

    suffix_expression: $ => prec.left(PREC.PRIMARY, choice(
      seq(field('object', $._primary_expression), '.', choice(
        seq(field('member', choice($.identifier, $.integer_literal)),
          optional(seq('#', '[', $._expression, ']')),
          optional($.generic_instantiation)),
        seq('(', commaSep1(seq(choice($.identifier, $.integer_literal), ':=', $._expression)), ')'),
      )),
      seq($._primary_expression, '@', $.identifier),
      seq($._primary_expression, '`', $.identifier),
      seq(field('function', $._primary_expression), '(', commaSep($.actual_binding), ')'),
      seq($._primary_expression, '[', choice(
        seq($._expression, ':=', $._expression),
        seq(optional($._expression), '..', optional($._expression)),
        commaSep1($._stmt_expression),
      ), ']'),
    )),

    actual_binding: $ => seq(optional(seq($.identifier, ':=')), $._stmt_expression),

    // Static member access: Type<T>.member
    qualified_member_expression: $ => prec.dynamic(20, prec.left(PREC.PRIMARY, seq(
      $.qualified_name, $.generic_instantiation, '.', choice($.identifier, $.integer_literal),
    ))),

    name_segment: $ => seq(
      $.identifier,
      optional(seq('#', '[', $._expression, ']')),
    ),

    this_expression: _ => 'this',
    parenthesized_expression: $ => seq('(', $._function_body_expression, ')'),
    tuple_expression: $ => seq('(', choice(
      seq(),
      seq('ghost', $._expression),
      seq($._tuple_element, ',', commaSep($._tuple_element)),
    ), ')'),
    _tuple_element: $ => choice(
      seq(optional('ghost'), choice($.identifier, $.integer_literal), ':=', $._expression),
      seq(optional('ghost'), $._expression),
    ),

    if_expression: $ => prec.right(seq('if', $._expression, 'then', $._stmt_expression, 'else', $._stmt_expression)),

    match_expression: $ => prec.dynamic(-1, prec.right(seq(
      'match', $._expression,
      choice(seq('{', repeat($.case_expr_clause), '}'), repeat1($.case_expr_clause)),
    ))),

    case_expr_clause: $ => prec.dynamic(-1, seq('case', repeat($.attribute), $._pattern, '=>', $._expression)),

    let_expression: $ => seq(
      choice('var', 'ghost', seq('ghost', 'var')), commaSep1($.case_pattern),
      choice(seq(':=', commaSep1($._expression)), seq(':|', $._expression)),
      ';', $._stmt_expression,
    ),

    let_or_fail_expression: $ => seq(
      choice('var', 'ghost', seq('ghost', 'var')), $.case_pattern,
      ':-', $._expression, ';', $._stmt_expression,
    ),

    bare_let_or_fail_expression: $ => seq(
      ':-', optional(choice('expect', 'assert', 'assume')),
      $._expression, ';', $._stmt_expression,
    ),

    case_pattern: $ => choice(
      seq($.identifier, optional(seq(':', $._type))),
      seq($.identifier, '(', commaSep($.case_pattern), ')'),
      seq('(', commaSep($.case_pattern), ')'),
    ),

    quantifier_expression: $ => prec.dynamic(20, prec.right(seq(
      choice('forall', 'exists'), $.quantifier_domain, '::', $._stmt_expression))),

    quantifier_domain: $ => commaSep1($.quantifier_var),

    quantifier_var: $ => prec.dynamic(15, prec.right(seq(
      $.identifier,
      optional(choice(
        seq(':', $._type, optional(seq($._arrow_left, $._expression))),
        seq($._arrow_left, $._expression),
      )),
      repeat($.attribute),
      optional(seq($._bar, $._expression)),
    ))),

    lambda_expression: $ => prec.right(seq(
      choice($.identifier, seq('(', commaSep(choice($.formal, $.case_pattern)), ')')),
      repeat(choice($.requires_clause, $.reads_clause)),
      '=>', $._stmt_expression,
    )),

    forall_expression: $ => prec.dynamic(20, prec.right(seq('forall', $.quantifier_domain, '::', $._stmt_expression))),

    set_comprehension: $ => prec.dynamic(20, prec.right(seq(
      choice('set', 'iset'), $.quantifier_domain, optional(seq('::', $._stmt_expression))))),
    map_comprehension: $ => prec.dynamic(20, prec.right(seq(
      choice('map', 'imap'), $.quantifier_domain, '::', $._stmt_expression,
      optional(seq(':=', $._expression))))),

    set_display: $ => seq(choice('{', 'iset{', 'multiset{', seq('multiset', '{')), commaSep($._expression), '}'),
    multiset_display: $ => seq('multiset', '(', $._expression, ')'),
    seq_display: $ => seq('[', commaSep($._expression), ']'),
    map_display: $ => seq(choice('map', 'imap'), '[', commaSep($.map_display_entry), ']'),
    map_display_entry: $ => seq($._expression, ':=', $._expression),

    old_expression: $ => seq('old', optional(seq('@', $.identifier)), '(', $._expression, ')'),
    unchanged_expression: $ => seq('unchanged', optional(seq('@', $.identifier)),
      '(', commaSep1($.frame_expression), ')'),
    fresh_expression: $ => seq('fresh', optional(seq('@', $.identifier)), '(', $._expression, ')'),

    // Cardinality: scanner-confirmed path (BAR_OPEN/BAR_CLOSE) + GLR fallback (BAR)
    cardinality_expression: $ => choice(
      seq($._bar_open, $._expression, $._bar_close),
      prec.dynamic(10, seq($._bar, $._expression, $._bar)),
    ),
    backtick_expression: $ => seq('`', $.identifier),

    seq_construction: $ => prec.left(PREC.PRIMARY, seq('seq', optional($.generic_instantiation), '(', $._expression, ',', $._expression, ')')),
    multiset_construction: $ => prec.left(PREC.PRIMARY, seq('multiset', '(', $._expression, ')')),

    // Stmt-in-expression rules
    reveal_expression: $ => prec.right(PREC.SEMI_EXPR, seq(
      choice('reveal', 'hide'), choice('*', commaSep1($._reveal_target)), ';', $._stmt_expression)),
    assert_expression: $ => prec.right(PREC.SEMI_EXPR, seq(
      'assert', repeat($.attribute), $._expression,
      choice(seq('by', $.block_statement), ';'), $._stmt_expression)),
    assume_expression: $ => prec.right(PREC.SEMI_EXPR, seq('assume', $._expression, ';', $._stmt_expression)),
    expect_expression: $ => prec.right(PREC.SEMI_EXPR, seq(
      'expect', $._expression, optional(seq(',', $._expression)), ';', $._stmt_expression)),
    calc_expression: $ => prec.right(PREC.SEMI_EXPR, seq(
      'calc', repeat($.attribute), optional($.calc_op),
      '{', repeat($.calc_line), '}', $._stmt_expression)),

    // ========== Literals ==========

    literal_expression: $ => choice(
      $.integer_literal, $.real_literal, $.boolean_literal,
      $.char_literal, $.string_literal, $.null_literal,
      seq('-', $.integer_literal), seq('-', $.real_literal),
    ),

    integer_literal: _ => token(choice(/[0-9]([0-9_]*[0-9])?/, /0x[0-9a-fA-F]([0-9a-fA-F_]*[0-9a-fA-F])?/)),
    real_literal: _ => token(choice(
      /[0-9]([0-9_]*[0-9])?\.[0-9]([0-9_]*[0-9])?(e[+-]?[0-9]([0-9_]*[0-9])?)?/,
      /[0-9]([0-9_]*[0-9])?e[+-]?[0-9]([0-9_]*[0-9])?/,
    )),
    boolean_literal: _ => choice('true', 'false'),
    char_literal: _ => seq("'", choice(/[^'\\]/, /\\U\{[0-9a-fA-F]+\}/, /\\u[0-9a-fA-F]{4}/, seq('\\', /./)), "'"),
    string_literal: _ => choice(
      seq('"', repeat(choice(/[^"\\]+/, /\\U\{[0-9a-fA-F]+\}/, /\\u[0-9a-fA-F]{4}/, seq('\\', /./), /\\\n/)), '"'),
      seq('@', '"', repeat(choice(/[^"]+/, '""')), '"'),
    ),
    null_literal: _ => 'null',

    // ========== Identifier ==========

    identifier: _ => /[a-zA-Z_?'][a-zA-Z0-9_?']*/,
  },
});

function commaSep(rule) { return optional(commaSep1(rule)); }
function commaSep1(rule) { return seq(rule, repeat(seq(',', rule))); }
