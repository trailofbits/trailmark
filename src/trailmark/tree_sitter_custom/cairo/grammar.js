// Upstream utils.js, mechanically wrapped for a self-contained build.
const cairo_utils = (() => {
const PREC = {
  PAREN: -1,
  ASSIGN: 1,
  LOGICAL_OR: 3,
  LOGICAL_AND: 4,
  EQUALITY: 7,
  ADDITIVE: 8,
  MULTIPLICATIVE: 9,
  SHIFT: 10,
  BITAND: 11,
  BITXOR: 12,
  BITOR: 13,
  UNARY: 14,
  POWER: 15,
  CALL: 16,
  MEMBER: 17,
};

/**
 * Creates a rule to optionally match one or more of the rules separated
 * by a comma, optionally ending with a comma
 *
 * @param {Rule} rule
 *
 * @return {ChoiceRule}
 *
 */
function optionalCommaSep(rule) {
  return optional(seq(sep1(rule, ','), optional(',')));
}


/**
 * Creates a rule to optionally match one or more of the rules separated
 * by a comma
 *
 * @param {Rule} rule
 *
 * @return {ChoiceRule}
 *
 */
function commaSep(rule) {
  return optional(seq(sep1(rule, ','), optional(',')));
}

/**
 * Creates a rule to match one or more of the rules separated by a comma
 *
 * @param {Rule} rule
 *
 * @return {SeqRule}
 *
 */
function commaSep1(rule) {
  return sep1(rule, ',');
}

/**
* Creates a rule to match one or more of the rules separated by the separator
*
* @param {Rule} rule
* @param {string|Rule} separator - The separator to use.
*
* @return {SeqRule}
*
*/
function sep1(rule, separator) {
  return seq(rule, repeat(seq(separator, rule)));
}

/**
* Creates a rule to match zero or more of the rules separated by the separator
*
* @param {Rule} rule
* @param {string|Rule} separator - The separator to use.
*
* @return {SeqRule}
*
*/
function optionalSep1(rule, separator) {
  return optional(sep1(rule, separator));
}

return {
  PREC,
  optionalCommaSep,
  commaSep,
  commaSep1,
  sep1,
  optionalSep1,
};

})();

// Upstream cairo_0.js, mechanically wrapped for a self-contained build.
const cairo_0_grammar = (() => {
// deno-lint-ignore-file ban-ts-comment
/* eslint-disable arrow-parens */
/* eslint-disable camelcase */
/* eslint-disable-next-line spaced-comment */
/// <reference types="tree-sitter-cli/dsl" />
// @ts-check

const {PREC, optionalCommaSep, commaSep, commaSep1, sep1} = cairo_utils;

return {
  // ╭──────────────────╮
  // │ Cairo 0.x Parser │
  // ╰──────────────────╯
  _cairo_0_statement: $ => choice(
    alias($._cairo_0_import_statement, $.import_statement),
    $.type_definition,
    $.builtin_directive,
    $.lang_directive,
    alias($._cairo_0_decorated_definition, $.decorated_definition),
    alias($._cairo_0_namespace_definition, $.namespace_definition),
    alias($._cairo_0_struct_definition, $.struct_definition),
    alias($._cairo_0_function_definition, $.function_definition),
    alias($._cairo_0_expression_statement, $.expression_statement),
    $.alloc_locals,
    alias($._cairo_0_assert_statement, $.assert_statement),
    alias($._cairo_0_static_assert_statement, $.static_assert_statement),
    alias($._cairo_0_let_binding, $.let_binding),
    $.const_var_declaration,
    $.local_var_declaration,
    $.temp_var_declaration,
    $.instruction,
    $.hint,
    $.label,
    alias($._cairo_0_attribute_statement, $.attribute_statement),
    alias($._cairo_0_if_statement, $.if_statement),
    alias($._cairo_0_with_statement, $.with_statement),
    alias($._cairo_0_return_statement, $.return_statement),
  ),

  _cairo_0_import_statement: $ => seq(
    'from',
    field('module_name', $.dotted_name),
    'import',
    choice(
      $._import_list,
      seq('(', $._import_list, ')'),
    ),
  ),

  _import_list: $ => prec.right(seq(
    commaSep1(field('name', choice(
      $.dotted_name,
      $.aliased_import,
    ))),
    optional(','),
  )),

  aliased_import: $ => seq(
    field('name', $.dotted_name),
    'as',
    field('alias', $.identifier),
  ),

  type_definition: $ => seq(
    'using', $.identifier, '=', alias($._cairo_0_type, $.type), $._separator,
  ),

  builtin_directive: $ => prec.right(seq(
    '%builtins', repeat1($.identifier),
  )),

  lang_directive: $ => seq(
    '%lang', $.identifier,
  ),

  _cairo_0_decorated_definition: $ => seq(
    repeat1($.decorator),
    field('definition', choice(
      alias($._cairo_0_namespace_definition, $.namespace_definition),
      alias($._cairo_0_struct_definition, $.struct_definition),
      alias($._cairo_0_function_definition, $.function_definition),
    )),
  ),

  decorator: $ => seq(
    '@',
    $.identifier,
  ),

  _cairo_0_namespace_definition: $ => seq(
    'namespace', $.identifier, '{',
    repeat($._cairo_0_statement),
    '}',
  ),

  _cairo_0_struct_definition: $ => seq(
    'struct', $.identifier, '{',
    optionalCommaSep($.typed_identifier),
    '}',
  ),

  _cairo_0_function_definition: $ => seq(
    'func',
    $.identifier,
    optional($.implicit_arguments),
    $.arguments,
    optional(seq(
      '->',
      alias(
        $._cairo_0_type,
        $.return_type,
      ),
    )),
    choice(
      seq('{', repeat($._cairo_0_statement), '}'),
      seq(':', repeat($._cairo_0_statement), 'end'),
    ),
  ),

  implicit_arguments: $ => seq(
    '{',
    optionalCommaSep($.typed_identifier),
    '}',
  ),

  arguments: $ => seq(
    '(',
    commaSep($.typed_identifier),
    optional(','),
    ')',
  ),

  _cairo_0_expression_statement: $ => seq($._cairo_0_expression, $._separator),

  alloc_locals: $ => seq('alloc_locals', $._separator),

  _cairo_0_assert_statement: $ => seq(
    'assert', $._cairo_0_expression, '=', $._cairo_0_expression, $._separator,
  ),

  _cairo_0_static_assert_statement: $ => seq(
    'static_assert', $._cairo_0_expression, '==', $._cairo_0_expression, $._separator,
  ),

  _cairo_0_let_binding: $ => seq(
    'let',
    field('left', $._ref_binding),
    '=',
    field('right', choice(
      $.call_instruction,
      $._cairo_0_expression,
    )),
    $._separator,
  ),

  const_var_declaration: $ => seq(
    'const', $.identifier, '=', $._cairo_0_expression, $._separator,
  ),

  local_var_declaration: $ => seq(
    'local', $.typed_identifier, optional(seq('=', $._cairo_0_expression)), $._separator,
  ),

  temp_var_declaration: $ => seq(
    'tempvar', $.typed_identifier, optional(seq('=', $._cairo_0_expression)), $._separator,
  ),

  instruction: $ => choice(
    seq($._instruction_body, $._separator),
    seq(
      $._instruction_body, ',', 'ap', '++', $._separator,
    ),
  ),

  _instruction_body: $ => choice(
    $.inst_assert_eq,
    $.inst_jmp_rel,
    $.inst_jmp_abs,
    $.inst_jmp_to_label,
    $.inst_jnz,
    $.inst_jnz_to_label,
    $.call_instruction,
    $.inst_ret,
    $.inst_add_ap,
    $.inst_data_word,
  ),

  inst_assert_eq: $ => seq(
    $._cairo_0_expression, '=', $._cairo_0_expression,
  ),

  inst_jmp_rel: $ => seq(
    'jmp', 'rel', $._cairo_0_expression,
  ),

  inst_jmp_abs: $ => seq(
    'jmp', 'abs', $._cairo_0_expression,
  ),

  inst_jmp_to_label: $ => seq(
    'jmp', $.identifier,
  ),

  inst_jnz: $ => prec(1, seq(
    'jmp', 'rel', $._cairo_0_expression, 'if', $._cairo_0_expression, '!=', $.number,
  )),

  inst_jnz_to_label: $ => prec(1, seq(
    'jmp', $.identifier, 'if', $._cairo_0_expression, '!=', $.number,
  )),

  inst_ret: _ => 'ret',

  inst_add_ap: $ => seq(
    'ap', '+=', $._cairo_0_expression,
  ),

  inst_data_word: $ => seq(
    'dw', $._cairo_0_expression,
  ),

  label: $ => seq($.identifier, ':', $._cairo_0_statement),

  _cairo_0_attribute_statement: $ => seq(
    'with_attr',
    $.identifier,
    optional(seq('(', repeat($.string), ')')),
    '{',
    repeat($._cairo_0_statement),
    '}',
  ),

  _cairo_0_if_statement: $ => seq(
    'if',
    '(',
    $._cairo_0_expression,
    ')',
    '{',
    repeat($._cairo_0_statement),
    optional(seq('}', 'else', '{', repeat($._cairo_0_statement))),
    '}',
  ),

  _cairo_0_with_statement: $ => seq(
    'with', commaSep1($.identifier), '{',
    repeat($._cairo_0_statement),
    '}',
  ),

  _cairo_0_return_statement: $ => seq(
    'return', $._cairo_0_expression, $._separator,
  ),

  _non_identifier_type: $ => choice(
    alias($._cairo_0_builtin_type, $.builtin_type),
    alias($._cairo_0_pointer_type, $.pointer_type),
    alias($._cairo_0_tuple_type, $.tuple_type),
    $.hint,
  ),

  _cairo_0_builtin_type: _ => choice('felt', 'codeoffset'),

  _cairo_0_pointer_type: $ => seq(choice($._non_identifier_type, $.identifier), choice('*', '**')),

  _cairo_0_tuple_type: $ => seq('(', commaSep($._cairo_0_type), ')'),

  _cairo_0_type: $ => choice(
    $._non_identifier_type,
    $.named_type,
    $.identifier,
  ),

  named_type: $ => prec.right(1, seq(
    $.identifier,
    optional(seq(':', alias($._cairo_0_type, $.type))),
  )),

  _cairo_0_expression: $ => choice(
    $.unary_expression,
    $.binary_expression,
    $.assignment_expression,
    $.subscript_expression,
    $.member_expression,
    $.cast_expression,
    $.tuple_expression,
    $.identifier,
    $.number,
    $.short_string,
    $.hint_expression,
    $.register,
    alias($._cairo_0_call_expression, $.call_expression),
    $.deref_expression,
    $.cast_expression,
  ),

  unary_expression: $ => prec.left(PREC.UNARY,
    seq(
      field('operator', choice('&', '-', 'new')),
      field('operand', $._cairo_0_expression),
    ),
  ),

  binary_expression: $ => {
    const table = [
      [prec.left, '+', PREC.ADDITIVE],
      [prec.left, '-', PREC.ADDITIVE],
      [prec.left, '*', PREC.MULTIPLICATIVE],
      [prec.left, '/', PREC.MULTIPLICATIVE],
      [prec.left, 'and', PREC.LOGICAL_AND],
      [prec.right, '**', PREC.POWER],
      [prec.left, '==', PREC.EQUALITY],
      [prec.left, '!=', PREC.EQUALITY],
    ];

    // @ts-ignore
    return choice(...table.map(([fn, operator, precedence]) => fn(precedence, seq(
      field('left', $._cairo_0_expression),
      // @ts-ignore
      field('operator', operator),
      field('right', $._cairo_0_expression),
    ))));
  },

  assignment_expression: $ => prec.right(PREC.ASSIGN, seq(
    field('left', $._cairo_0_expression),
    field('operator', '='),
    field('right', $._cairo_0_expression),
  )),

  string: _ => /"(.*?)"/,

  short_string: _ => /'(.*?)'/,

  hint_expression: $ => seq('nondet', $.hint),

  hint: $ => seq(
    '%{',
    optional($.python_code),
    '%}',
  ),

  python_code: $ => repeat1($.code_line),

  register: _ => choice(
    'ap',
    'fp',
  ),

  deref_expression: $ => seq(
    '[',
    $._cairo_0_expression,
    ']',
  ),

  subscript_expression: $ => prec(PREC.MEMBER, seq(
    $._cairo_0_expression, '[', $._cairo_0_expression, ']',
  )),

  member_expression: $ => prec(PREC.MEMBER, seq(
    $._cairo_0_expression, '.', $.identifier,
  )),

  cast_expression: $ => seq(
    'cast', '(', $._cairo_0_expression, ',', alias($._cairo_0_type, $.type), ')',
  ),

  tuple_expression: $ => prec(PREC.CALL, seq(
    '(',
    optionalCommaSep($._cairo_0_expression),
    ')',
  )),

  _cairo_0_call_expression: $ => prec(PREC.CALL, seq(
    field('function', $._cairo_0_expression),
    optional(seq(
      '{',
      commaSep($.assignment_expression),
      optional(','),
      '}',
    )),
    $.tuple_expression,
  )),

  _ref_binding: $ => choice(
    $.typed_identifier,
    seq('(', commaSep($.typed_identifier), ')'),
  ),

  call_instruction: $ => choice(
    seq('call', 'rel', $._cairo_0_expression),
    seq('call', 'abs', $._cairo_0_expression),
    seq('call', $.identifier),
  ),

  typed_identifier: $ => seq(
    optional('local'),
    $.identifier,
    optional(seq(':', alias($._cairo_0_type, $.type))),
  ),

  dotted_name: $ => sep1($.identifier, '.'),

  identifier: _ => /[a-zA-Z_][a-zA-Z_0-9]*/,

  number: _ => {
    const hex_literal = /0x[a-f|A-F|0-9]+/;

    const decimal_literal = /\d+/;

    return token(choice(
      hex_literal,
      decimal_literal,
    ));
  },

  _separator: _ => choice(';', '\n'),

  comment: _ => token(seq('//', /.*/)),
};

})();

// Upstream cairo_1.js, mechanically wrapped for a self-contained build.
const cairo_1_grammar = (() => {
// deno-lint-ignore-file ban-ts-comment
/* eslint-disable arrow-parens */
/* eslint-disable camelcase */
/* eslint-disable-next-line spaced-comment */
/// <reference types="tree-sitter-cli/dsl" />
// @ts-check

const {PREC, commaSep, commaSep1, sep1} = cairo_utils;

const numeric_types = [
  'u8',
  'i8',
  'u16',
  'i16',
  'u32',
  'i32',
  'u64',
  'i64',
  'u128',
  'i128',
  'u256',
  'i256',
  'isize',
  'usize',
  'f32',
  'f64',
  'felt252',
];

const primitive_types = numeric_types.concat(['bool', 'str', 'char']);

return {
  _cairo_1_statement: $ => choice(
    $.use_declaration,
    $.extern_type_statement,
    $.extern_function_statement,
    $.let_declaration,
    $.attribute_item,
    $.impl_item,
    $.trait_item,
    $.mod_item,
    $.struct_item,
    $.enum_item,
    $.const_item,
    $.type_item,
    alias($._cairo_1_function_definition, $.function_definition),
    alias($._cairo_1_function_signature, $.function_signature),
  ),

  use_declaration: $ => seq(
    // optional($.visibility_modifier),
    'use',
    field('argument', $._use_clause),
    ';',
  ),

  extern_type_statement: $ => seq(
    'extern',
    'type',
    field('name', $.identifier),
    optional($.type_parameters),
    ';',
  ),

  _use_clause: $ => choice(
    $._path,
    $.use_as_clause,
    $.use_list,
    $.scoped_use_list,
    // $.use_wildcard,
  ),

  scoped_use_list: $ => seq(
    field('path', optional($._path)),
    '::',
    field('list', $.use_list),
  ),

  use_list: $ => seq(
    '{',
    sep1($._use_clause, ','),
    optional(','),
    '}',
  ),

  use_as_clause: $ => seq(
    field('path', $._path),
    'as',
    field('alias', $.identifier),
  ),

  extern_function_statement: $ => seq(
    'extern',
    'fn',
    $.identifier,
    optional($.type_parameters),
    $._function_signature,
    ';',
  ),

  attribute_item: $ => seq('#', '[', $._path, optional($.attribute_arguments), ']'),
  attribute_arguments: $ => seq('(', commaSep(seq(optional(seq($.identifier, ':' )), $._cairo_1_expression)), ')'),

  mod_item: $ => seq(
    // optional($.visibility_modifier),
    'mod',
    field('name', $.identifier),
    choice(
      ';',
      field('body', $.declaration_list),
    ),
  ),

  struct_item: $ => seq(
    // optional($.visibility_modifier),
    'struct',
    field('name', $._cairo_1_type_identifier),
    field('type_parameters', optional($.type_parameters)),
    choice(
      seq(
        // optional($.where_clause),
        field('body', $.field_declaration_list),
      ),
      // seq(
      // field('body', $.ordered_field_declaration_list),
      // optional($.where_clause),
      // ';',
      // ),
      ';',
    ),
  ),

  enum_item: $ => seq(
    // optional($.visibility_modifier),
    'enum',
    field('name', $._cairo_1_type_identifier),
    field('type_parameters', optional($.type_parameters)),
    // optional($.where_clause),
    field('body', $.enum_variant_list),
  ),

  enum_variant_list: $ => seq(
    '{',
    commaSep(seq(repeat($.attribute_item), $.enum_variant)),
    optional(','),
    '}',
  ),

  enum_variant: $ => seq(
    // optional($.visibility_modifier),
    field('name', $.identifier),
    ':',
    field('body', optional(choice(
      // $.field_declaration_list,
      // $.ordered_field_declaration_list,
      $._cairo_1_type,
    ))),
    optional(seq(
      '=',
      field('value', $._cairo_1_expression),
    )),
  ),


  field_declaration_list: $ => seq(
    '{',
    commaSep(seq(repeat($.attribute_item), $.field_declaration)),
    optional(','),
    '}',
  ),

  field_declaration: $ => seq(
    // optional($.visibility_modifier),
    field('name', $._cairo_1_field_identifier),
    ':',
    field('type', $._cairo_1_type),
  ),

  ordered_field_declaration_list: $ => seq(
    '(',
    commaSep(seq(
      repeat($.attribute_item),
      // optional($.visibility_modifier),
      field('type', $._cairo_1_type),
    )),
    optional(','),
    ')',
  ),

  const_item: $ => seq(
    // optional($.visibility_modifier),
    'const',
    field('name', $.identifier),
    ':',
    field('type', $._cairo_1_type),
    optional(
      seq(
        '=',
        field('value', $._cairo_1_expression),
      ),
    ),
    ';',
  ),

  type_item: $ => seq(
    // optional($.visibility_modifier),
    'type',
    field('name', $._cairo_1_type_identifier),
    field('type_parameters', optional($.type_parameters)),
    '=',
    field('type', $._cairo_1_type),
    ';',
  ),


  block: $ => seq(
    '{',
    repeat(choice($._cairo_1_statement, alias($._cairo_1_expression_statement, $.expression_statement))),
    '}',
  ),

  _cairo_1_function_definition: $ => seq(
    'fn',
    $.identifier,
    optional($.type_parameters),
    $._function_signature,
    $.block,
  ),

  _function_signature: $ => seq(
    '(',
    commaSep($.parameter),
    ')',
    optional(seq('->', field('returns', $._cairo_1_type))),
    optional(seq('implicits', '(', commaSep($.identifier), ')')),
    optional('nopanic'),
  ),

  _cairo_1_function_signature: $ => seq(
    'fn',
    $.identifier,
    optional($.type_parameters),
    '(',
    commaSep($.parameter),
    ')',
    optional(seq('->', field('returns', $._cairo_1_type))),
    // optional(seq('implicits', '(', commaSep($.identifier), ')')),
    optional('nopanic'),
    ';',
  ),

  parameter: $ => seq(
    optional(choice('ref', 'mut')),
    choice($.identifier, $.self),
    ':',
    $._cairo_1_type,
  ),

  impl_item: $ => seq(
    'impl',
    $._cairo_1_type,
    'of',
    $._cairo_1_type,
    choice(
      $.block,
      ';',
    ),
  ),

  trait_item: $ => seq(
    // optional($.visibility_modifier),
    // optional('unsafe'),
    'trait',
    field('name', $._cairo_1_type_identifier),
    field('type_parameters', optional($.type_parameters)),
    field('bounds', optional($.trait_bounds)),
    // optional($.where_clause),
    choice(field('body', $.declaration_list), ';'),
  ),

  let_declaration: $ => seq(
    'let',
    optional('mut'),
    field('pattern', $._pattern),
    optional(seq(
      ':',
      field('type', $._cairo_1_type),
    )),
    optional(seq(
      '=',
      field('value', $._cairo_1_expression),
    )),
    // optional(seq(
    //   'else',
    //   field('alternative', $.block),
    // )),
    ';',
  ),

  declaration_list: $ => seq(
    '{',
    repeat($._cairo_1_statement),
    '}',
  ),

  _cairo_1_type: $ => choice(
    $._cairo_1_type_identifier,
    $.scoped_type_identifier,
    $.at_type,
    $.generic_type,
    alias($._cairo_1_tuple_type, $.tuple_type),
    $.abstract_type,
    alias(choice(...primitive_types), $.primitive_type),
    $.unit_type,
  ),

  // TODO: what?
  at_type: $ => seq(
    '@',
    $._cairo_1_type,
  ),

  _cairo_1_generic_function: $ => prec(1, seq(
    field('function', choice(
      $.identifier,
      $.scoped_identifier,
      alias($._cairo_1_field_expression, $.field_expression),
    )),
    '::',
    alias($._cairo_1_type_arguments, $.type_arguments),
  )),

  generic_type: $ => prec(1, seq(
    field('type', choice(
      $._cairo_1_type_identifier,
      $.scoped_type_identifier,
    )),
    optional('::'),
    alias($._cairo_1_type_arguments, $.type_arguments),
  )),

  generic_type_with_turbofish: $ => seq(
    field('type', choice(
      $._cairo_1_type_identifier,
      $.scoped_identifier,
    )),
    '::',
    alias($._cairo_1_type_arguments, $.type_arguments),
  ),

  _cairo_1_type_arguments: $ => seq(
    token(prec(1, '<')),
    sep1(
      choice(
        $._cairo_1_type,
        // $.type_binding,
        // $.lifetime,
        $._literal,
        $.block,
      ),
      ',',
    ),
    optional(','),
    '>',
  ),

  _cairo_1_tuple_type: $ => seq(
    '(',
    commaSep1($._cairo_1_type),
    optional(','),
    ')',
  ),

  unit_type: _ => seq('(', ')'),

  abstract_type: $ => seq(
    'impl',
    field('trait', choice(
      $._cairo_1_type_identifier,
      $.scoped_type_identifier,
      $.generic_type,
      // $.function_type,
    )),
    $.trait_bounds,
  ),

  _cairo_1_expression_statement: $ => seq(
    $._cairo_1_expression,
    optional(';'),
  ),

  _cairo_1_expression: $ => (choice(
    $.identifier,
    $._keyword_identifier,
    $.scoped_identifier,
    alias($._cairo_1_short_string, $.short_string),
    $.self,
    $._literal,
    $.boolean,
    alias(choice(...primitive_types), $.identifier),
    alias($._cairo_1_call_expression, $.call_expression),
    alias($._cairo_1_field_expression, $.field_expression),
    alias($._cairo_1_unit_expression, $.unit_expression),
    alias($._cairo_1_parenthesized_expression, $.parenthesized_expression),
    alias($._cairo_1_tuple_expression, $.tuple_expression),
    alias($._cairo_1_unary_expression, $.unary_expression),
    alias($._cairo_1_binary_expression, $.binary_expression),
    alias($._cairo_1_assignment_expression, $.assignment_expression),
    alias($._cairo_1_compound_assignment_expression, $.compound_assignment_expression),
    alias($._cairo_1_struct_expression, $.struct_expression),
    alias($._cairo_1_try_expression, $.try_expression),
    alias($._cairo_1_return_expression, $.return_expression),
    alias($._cairo_1_loop_expression, $.loop_expression),
    alias($._cairo_1_generic_function, $.generic_function),
    alias($._cairo_1_at_expression, $.at_expression),
    alias($._cairo_1_index_expression, $.index_expression),
    $._cairo_1_expression_ending_with_block,
  )),

  _cairo_1_expression_ending_with_block: $ => choice(
    // $.unsafe_block,
    // $.async_block,
    $.block,
    alias($._cairo_1_if_expression, $.if_expression),
    $.match_expression,
    // $.while_expression,
    // $.loop_expression,
    // $.for_expression,
    // $.const_block,
  ),

  _cairo_1_return_expression: $ => choice(
    prec.left(seq('return', $._cairo_1_expression)),
    prec(-1, 'return'),
  ),

  _cairo_1_if_expression: $ => prec.right(seq(
    'if',
    field('condition', $._cairo_1_expression),
    field('consequence', $.block),
    optional(field('alternative', alias($._cairo_1_else_clause, $.else_clause))),
  )),

  _cairo_1_else_clause: $ => seq('else', choice($.block, alias($._cairo_1_if_expression, $.if_expression))),

  match_expression: $ => seq(
    'match',
    field('value', $._cairo_1_expression),
    field('body', $.match_block),
  ),

  match_block: $ => seq(
    '{',
    optional(seq(
      repeat($.match_arm),
      alias($.last_match_arm, $.match_arm),
    )),
    '}',
  ),

  match_arm: $ => seq(
    repeat($.attribute_item),
    field('pattern', $.match_pattern),
    '=>',
    choice(
      seq(field('value', $._cairo_1_expression), ','),
      field('value', prec(1, $._cairo_1_expression_ending_with_block)),
    ),
  ),

  last_match_arm: $ => seq(
    repeat($.attribute_item),
    field('pattern', $.match_pattern),
    '=>',
    field('value', $._cairo_1_expression),
    optional(','),

  ),

  match_pattern: $ => seq(
    $._pattern,
    optional(seq('if', field('condition', $._cairo_1_expression))),
  ),

  _cairo_1_loop_expression: $ => seq(
    // optional(seq($.loop_label, ':')),
    'loop',
    field('body', $.block),
  ),

  _cairo_1_at_expression: $ => prec.right(seq(
    '@',
    $._cairo_1_expression,
  )),

  _pattern: $ => choice(
    $._literal_pattern,
    alias(choice(...primitive_types), $.identifier),
    $.identifier,
    $.scoped_identifier,
    $.tuple_pattern,
    $.tuple_struct_pattern,
    $.struct_pattern,
    // $._reserved_identifier,
    // $.ref_pattern,
    // $.slice_pattern,
    // $.captured_pattern,
    // $.reference_pattern,
    // $.remaining_field_pattern,
    // $.mut_pattern,
    // $.range_pattern,
    // $.or_pattern,
    // $.const_block,
    // $.macro_invocation,
    '_',
  ),

  tuple_pattern: $ => seq(
    '(',
    commaSep($._pattern),
    optional(','),
    ')',
  ),

  tuple_struct_pattern: $ => seq(
    field('type', choice(
      $.identifier,
      $.scoped_identifier,
    )),
    '(',
    commaSep($._pattern),
    optional(','),
    ')',
  ),

  struct_pattern: $ => seq(
    field('type', choice(
      $._cairo_1_type_identifier,
      $.scoped_type_identifier,
      alias(choice(...primitive_types), $.primitive_type),
    )),
    '{',
    // commaSep(choice($.field_pattern, $.remaining_field_pattern)),
    commaSep($.field_pattern),
    optional(','),
    '}',
  ),

  field_pattern: $ => seq(
    optional('ref'),
    optional('mut'),
    choice(
      field('name', alias($.identifier, $.shorthand_field_identifier)),
      seq(
        field('name', $._cairo_1_field_identifier),
        ':',
        field('pattern', $._pattern),
      ),
    ),
  ),

  remaining_field_pattern: _ => '..',

  _cairo_1_unary_expression: $ => prec(PREC.UNARY, seq(
    choice('-', '*', '!', '~'),
    $._cairo_1_expression,
  )),

  _cairo_1_binary_expression: $ => {
    const table = [
      [PREC.LOGICAL_AND, '&&'],
      [PREC.LOGICAL_OR, '||'],
      [PREC.BITAND, '&'],
      [PREC.BITOR, '|'],
      [PREC.BITXOR, '^'],
      [PREC.EQUALITY, choice('==', '!=', '<', '<=', '>', '>=')],
      [PREC.SHIFT, choice('<<', '>>')],
      [PREC.ADDITIVE, choice('+', '-')],
      [PREC.MULTIPLICATIVE, choice('*', '/', '%')],
    ];

    // @ts-ignore
    return choice(...table.map(([precedence, operator]) => prec.left(precedence, seq(
      field('left', $._cairo_1_expression),
      // @ts-ignore
      field('operator', operator),
      field('right', $._cairo_1_expression),
    ))));
  },

  _cairo_1_assignment_expression: $ => prec.left(PREC.ASSIGN, seq(
    field('left', $._cairo_1_expression),
    '=',
    field('right', $._cairo_1_expression),
  )),

  _cairo_1_compound_assignment_expression: $ => prec.left(PREC.ASSIGN, seq(
    field('left', $._cairo_1_expression),
    field('operator', choice('+=', '-=', '*=', '/=', '%=', '&=', '|=', '^=', '<<=', '>>=')),
    field('right', $._cairo_1_expression),
  )),

  _cairo_1_try_expression: $ => seq(
    $._cairo_1_expression,
    '?',
  ),

  _cairo_1_call_expression: $ => prec(PREC.CALL, seq(
    field('function', $._cairo_1_expression),
    '(',
    commaSep(seq(
      optional('ref'),
      optional(seq(
        optional(field('name', $.identifier)),
        ':',
      )),
      $._cairo_1_expression,
    )),
    ')',
  )),

  _cairo_1_index_expression: $ => prec(PREC.CALL, seq(
    $._cairo_1_expression, '[', $._cairo_1_expression, ']',
  )),

  _cairo_1_field_expression: $ => prec(PREC.MEMBER, seq(
    field('value', $._cairo_1_expression),
    '.',
    field('field', $._cairo_1_field_identifier),
  )),

  _cairo_1_parenthesized_expression: $ => seq(
    '(',
    $._cairo_1_expression,
    ')',
  ),

  _cairo_1_tuple_expression: $ => seq(
    '(',
    repeat($.attribute_item),
    seq($._cairo_1_expression, ','),
    repeat(seq($._cairo_1_expression, ',')),
    optional($._cairo_1_expression),
    ')',
  ),

  _cairo_1_unit_expression: _ => seq('(', ')'),

  _cairo_1_struct_expression: $ => seq(
    field('name', choice(
      $._cairo_1_type_identifier,
      alias(choice(...primitive_types), $.primitive_type),
      // alias($.scoped_type_identifier_in_expression_position, $.scoped_type_identifier),
      // $.generic_type_with_turbofish,
    )),
    field('body', $.field_initializer_list),
  ),

  field_initializer_list: $ => seq(
    '{',
    commaSep(choice(
      $.shorthand_field_initializer,
      $.field_initializer,
      $.base_field_initializer,
    )),
    optional(','),
    '}',
  ),

  shorthand_field_initializer: $ => seq(
    repeat($.attribute_item),
    $.identifier,
  ),

  field_initializer: $ => seq(
    repeat($.attribute_item),
    field('name', $._cairo_1_field_identifier),
    ':',
    field('value', $._cairo_1_expression),
  ),

  base_field_initializer: $ => seq(
    '..',
    $._cairo_1_expression,
  ),

  trait_bounds: $ => seq(
    ':',
    // sep1('+', choice(
    //   $._type,
    //   $.lifetime,
    //   $.higher_ranked_trait_bound,
    //   $.removed_trait_bound,
    // )),
    $._cairo_1_type,
  ),

  type_parameters: $ => prec(1, seq(
    '<',
    sep1(
      choice(
        // $.lifetime,
        // $.metavariable,
        $._cairo_1_type_identifier,
        $.constrained_type_parameter,
        $.abstract_type,
        // $.optional_type_parameter,
        // $.const_parameter,
      ),
      ',',
    ),
    optional(','),
    '>',
  )),

  constrained_type_parameter: $ => seq(
    // field('left', choice($.lifetime, $._type_identifier)),
    optional('const'),
    field('left', $._cairo_1_type_identifier),
    field('bounds', $.trait_bounds),
  ),

  _literal: $ => choice(
    $.number,
  ),

  _literal_pattern: $ => choice(
    $.number,
  ),

  _cairo_1_short_string: _ => token(seq(
    /'(.*?)'/,
    optional(seq('_', choice(...numeric_types))),
  )),

  number: _ => token(seq(
    optional('-'),
    choice(
      /[0-9][0-9_]*/,
      /0x[0-9a-fA-F_]+/,
      /0b[01_]+/,
      /0o[0-7_]+/,
    ),
    optional(choice(...numeric_types)),
  )),

  namespace_name: $ => sep1($.identifier, '::'),

  scoped_identifier: $ => prec(1, seq(
    field('path', optional(choice(
      $._path,
      alias($.generic_type_with_turbofish, $.generic_type),
    ))),
    '::',
    field('name', $.identifier),
  )),

  scoped_type_identifier: $ => seq(
    field('path', optional(choice(
      $._path,
      // alias($.generic_type_with_turbofish, $.generic_type),
      // $.bracketed_type,
      $.generic_type,
    ))),
    '::',
    field('name', $._cairo_1_type_identifier),
  ),


  _path: $ => choice(
    $.self,
    alias(choice(...primitive_types), $.identifier),
    // prec(-1, $._keyword_identifier),
    // alias(choice(...primitive_types), $.identifier),
    // $.metavariable,
    // $.super,
    // $.crate,
    $.identifier,
    $.scoped_identifier,
    // $._reserved_identifier,
  ),

  _cairo_1_type_identifier: $ => alias($.identifier, $.type_identifier),
  _cairo_1_field_identifier: $ => alias($.identifier, $.field_identifier),

  _keyword_identifier: $ => prec(-3, alias(choice(
    ...primitive_types,
  ), $.identifier)),

  boolean: _ => choice('true', 'false'),

  self: _ => 'self',
  // super: $ => 'super',
  // crate: $ => 'crate',
};

})();
/**
 * @file Cairo grammar for tree-sitter
 * @author Amaan Qureshi <amaanq12@gmail.com>
 * @author Blaž Hrastnik <blaz@mxxn.io>
 * @author Scott Piriou
 * @license MIT
 */

// deno-lint-ignore-file ban-ts-comment
/* eslint-disable arrow-parens */
/* eslint-disable camelcase */
/* eslint-disable-next-line spaced-comment */
/// <reference types="tree-sitter-cli/dsl" />
// @ts-check

'use strict';

const {PREC, sep1} = cairo_utils;

// Based on https://github.com/starkware-libs/cairo-lang/blob/master/src/starkware/cairo/lang/compiler/cairo.ebnf
module.exports = grammar({
  name: 'cairo',

  conflicts: $ => [
    [$.typed_identifier, $._pattern],
    [$._cairo_1_expression, $._cairo_1_struct_expression],
    [$.tuple_pattern, $._ref_binding],
    [$._cairo_0_struct_definition, $.field_declaration_list],
    [$._cairo_0_tuple_type, $.unit_type],
  ],

  externals: $ => [
    '%{',
    $.code_line,
    $._failure,
  ],

  extras: $ => [
    $.comment,
    /\s/,
  ],

  inline: $ => [
    $._path,
    $._cairo_1_type_identifier,
    $._cairo_1_field_identifier,
    $._cairo_1_expression_ending_with_block,
    $._instruction_body,
    $._keyword_identifier,
  ],

  supertypes: $ => [
    $._instruction_body,
    $._cairo_0_statement,
    $._cairo_1_expression,
    $._pattern,
  ],

  word: $ => $.identifier,

  rules: {
    program: $ => optional(choice(
      $.cairo_0_file,
      $.cairo_1_file,
    )),

    cairo_0_file: $ => repeat1($._cairo_0_statement),
    cairo_1_file: $ => repeat1($._cairo_1_statement),

    ...Object.fromEntries(
      Object.entries(cairo_0_grammar).filter(([key]) => key !== 'PREC'),
    ),

    ...Object.fromEntries(
      Object.entries(cairo_1_grammar).filter(([key]) => key !== 'PREC'),
    ),

    // Cairo impl heads declare generic parameters, including anonymous impls.
    impl_item: $ => seq(
      'impl',
      field('name', $._cairo_1_type_identifier),
      field('type_parameters', optional($.type_parameters)),
      'of',
      field('trait', $._cairo_1_type),
      choice($.block, ';'),
    ),

    type_parameters: $ => prec(1, seq(
      '<',
      sep1(choice(
        $._cairo_1_type_identifier,
        $.constrained_type_parameter,
        $.abstract_type,
        $.anonymous_impl_parameter,
      ), ','),
      optional(','),
      '>',
    )),

    anonymous_impl_parameter: $ => seq('+', $._cairo_1_type),
  },
});

module.exports.PREC = PREC;
