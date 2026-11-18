const BUILT_INS = [
  'diffie-hellman',
  'hashing',
  'symmetric-encryption',
  'asymmetric-encryption',
  'signing',
  'bilinear-pairing',
  'xor',
  'multiset',
  'revealing-signing'
]

module.exports = grammar({
  name: 'tamarin',

  extras: $ => [/\s|\\\r?\n/, $.multiline_comment, $.comment],

  externals: $ => [
    $.multiline_comment
  ],

  rules: {
    source_file: $ => $.security_protocol_theory,

    security_protocol_theory: $ => seq(
      'theory',
      $.ident,
      'begin',
      optional($.body),
      'end'
    ),

    unsupported_directive: $ => seq(
      choice('#ifdef', '#ifndef', '#else', '#endif', '#define', 'predicates:', 'macros:', 'process:'),
      optional(token(/[^\r\n]+/))
    ),

    ident: $ => /[a-zA-Z0-9]\w*?/,

    body: $ => repeat1(
      choice(
        $._signature_spec,
        $.global_heuristic,
        $.rule,
        $.restriction,
        $.lemma,
        $.formal_comment,
        $.tactic,
        $.unsupported_directive
      )
    ),


    _signature_spec: $ => choice(
      $.functions,
      $.equations,
      $.built_ins
    ),

    functions: $ => seq(
      'functions',
      ':',
      $.function_sym,
      repeat(
        seq(
          ',',
          $.function_sym
        )
      ),
      //optional(',')
    ),

    function_sym: $ => seq(
      $.ident,
      '/',
      $.arity,
      optional('[private]')
    ),

    arity: $ => /\d+?/,


    equations: $ => seq(
      'equations',
      ':',
      $.equation,
      repeat(
        seq(
          ',',
          $.equation
        )
      ),
      //optional(',')
    ),

    equation: $ => seq(
      $.term,
      '=',
      $.term
    ),


    built_ins: $ => seq(
      'builtins',
      ':',
      $.built_in,
      repeat(
        seq(
          ',',
          $.built_in
        )
      ),
      //optional(',')
    ),


    built_in: $ => choice(
      ...BUILT_INS
    ),


    global_heuristic: $ => seq(
      'heuristic',
      ':',
      repeat1($.goal_ranking)
    ),

    goal_ranking: $ => choice(
      $.standard_goal_ranking,
      $.oracle_goal_ranking
    ),


    standard_goal_ranking: $ => choice(
      'C',
      'I',
      'P',
      'S',
      'c',
      'i',
      'p',
      's'
    ),

    oracle_goal_ranking: $ => choice(
      seq(
        'o',
        '"',
        /[^"]*/,
        '"'
      ),
      seq(
        'O',
        '"',
        /[^"]*/,
        '"'
      )
    ),

    tactic: $ => seq(
      'tactic',
      ':',
      $.ident,
      optional($.presort),
      choice(
        seq(
          repeat1($.prio),
          repeat($.deprio)
        ),
        seq(
          repeat($.prio),
          repeat1($.deprio)
        )
      )
    ),

    presort: $ => seq(
      'presort',
      ':',
      $.standard_goal_ranking
    ),

    prio: $ => seq(
      'prio',
      ':',
      optional(
        seq(
          '{',
          $.post_ranking,
          '}'
        )
      ),
      repeat1($.function)
    ),

    deprio: $ => seq(
      'deprio',
      ':',
      optional(
        seq(
          '{',
          $.post_ranking,
          '}'
        )
      ),
      repeat1($.function)
    ),

    post_ranking: $ => choice(
      'smallest',
      'id'
    ),

    function: $ => seq(
      $.and_function,
      repeat(
        seq(
          '|',
          optional(
            $.and_function
          )
        )
      )
    ),

    and_function: $ => seq(
      $.not_function,
      repeat(
        seq(
          '&',
          optional(
            $.not_function
          )
        )
      )
    ),

    not_function: $ => seq(
      optional('not'),
      $.function_name,
      repeat(
        seq(
          '"',
          $.param,
          '"'
        )
      )
    ),

    param: $ => /[^"]*/,

    function_name: $ => choice(
      'regex',
      'isFactName',
      'isInFactTerms',
      'dhreNoise',
      'defaultNoise',
      'reasonableNoncesNoise',
      'nonAbsurdGoal'
    ),

    rule: $ => seq(
      $.simple_rule,
      optional($.variants)
    ),

    diff_rull: $ => seq(
      $.simple_rule,
      optional(
        seq(
          'left',
          $.rule,
          'right',
          $.rule
        )
      )
    ),

    simple_rule: $ => seq(
      'rule',
      optional($.modulo),
      $.ident,
      optional($.rule_attrs),
      ':',
      optional($.let_block),
      '[',
      optional($.facts),
      ']',
      choice(
        '-->',
        seq(
          '--[',
          optional($.facts),
          ']->'
        )
      ),
      '[',
      optional($.facts),
      ']'
    ),

    variants: $ => seq(
      'variants',
      $.simple_rule,
      repeat(
        seq(
          ',',
          $.simple_rule
        )
      )
    ),

    modulo: $ => seq(
      '(',
      'modulo',
      choice(
        'E',
        'AC'
      ),
      ')'
    ),

    rule_attrs: $ => seq(
      '[',
      $.rule_attr,
      repeat(
        seq(
          ',',
          $.rule_attr
        )
      ),
      optional(','),
      ']'
    ),

    rule_attr: $ => seq(
      choice(
        'color=',
        'colour='
      ),
      $.hexcolor
    ),

    let_block: $ => seq(
      'let',
      repeat1(
        seq(
          $.msg_var,
          '=',
          $._msetterm
        )
      ),
      'in'
    ),

    msg_var: $ => prec.left(4, seq(
      $.ident,
      optional(
        seq(
          '.',
          $.natural
        )
      ),
      optional(
        seq(
          '.',
          'msg'
        )
      )
    )),

    restriction: $ => seq(
      'restriction',
      $.ident,
      optional($.restriction_attrs),
      ':',
      '"',
      $.formula,
      '"'
    ),

    restriction_attrs: $ => seq(
      '[',
      choice(
        'left',
        'right'
      ),
      ']'
    ),

    lemma: $ => seq(
      'lemma',
      optional($.modulo),
      $.ident,
      optional($.lemma_attrs),
      ':',
      optional($.trace_quantifier),
      '"',
      $.formula,
      '"',
      optional($.proof_skeleton)
    ),

    lemma_attrs: $ => seq(
      '[',
      $.lemma_attr,
      repeat(
        seq(
          ',',
          $.lemma_attr
        )
      ),
      optional(','),
      ']'
    ),

    lemma_attr: $ => choice(
      'sources',
      'reuse',
      'use_induction',
      seq(
        'hide_lemma=', $.ident
      ),
      seq(
        'heuristic=', repeat1($.goal_ranking)
      ),
      'left',
      'right'
    ),

    trace_quantifier: $ => choice(
      'all-trace',
      'exists-trace'
    ),

    proof_skeleton: $ => choice(
      'SOLVED',
      seq('by', $.proof_method),
      seq($.proof_method, $.proof_skeleton),
      seq(
        $.proof_method,
        'case',
        $.ident,
        $.proof_skeleton,
        repeat(
          seq(
            'next',
            'case',
            $.ident,
            $.proof_skeleton
          )
        ),
        'qed'
      ),
    ),

    proof_method: $ => choice(
      'sorry',
      'simplify',
      seq(
        'solve',
        '(',
        $.goal,
        ')'
      ),
      'contradiction',
      'induction'
    ),

    goal: $ => prec.left(2, choice(
      seq(
        $.fact,
        '▶',
        $.natural_subscr,
        $.node_var
      ),
      seq(
        $.fact,
        '@',
        $.node_var
      ),
      seq(
        '(',
        $.node_var,
        ',',
        $.natural,
        ')',
        '~~>',
        '(',
        $.node_var,
        ',',
        $.natural,
        ')'
      ),
      seq(
        $.formula,
        repeat(
          seq(
            '∥',
            $.formula
          )
        )
      ),
      seq(
        'splitEqs',
        '(',
        $.natural,
        ')'
      )
    )),

    node_var: $ => prec.left(3, choice(
      seq(
        optional('#'),
        $.ident,
        optional(
          seq(
            '.',
            $.natural
          )
        )
      ),
      seq(
        $.ident,
        optional(
          seq(
            '.',
            $.natural
          )
        ),
        ':',
        'node'
      )
    )),

    natural: $ => /\d+?/,

    natural_subscr: $ => repeat1(
      choice(
        '₀',
        '₁',
        '₂',
        '₃',
        '₄',
        '₅',
        '₆',
        '₇',
        '₈',
        '₉'
      )
    ),

    formal_comment: $ => seq(
      $.ident,
      '{*',
      /[^\*]*/,
      /*repeat(
        $.ident
      ),*/
      '*}'
    ),

    tuple_term: $ => seq(
      '<',
      $._msetterm,
      repeat(
        seq(
          ',',
          $._msetterm
        )
      ),
      '>'
    ),

    _msetterm: $ => seq(
      $._xorterm,
      repeat(
        seq(
          alias('+', $.builtin_operator),
          $._xorterm
        )
      )
    ),

    _xorterm: $ => seq(
      $._multterm,
      repeat(
        seq(
          choice(
            alias('XOR', $.builtin_operator),
            alias('⊕', $.builtin_operator)
          ),
          $._multterm
        )
      )
    ),


    _multterm: $ => seq(
      $._expterm,
      repeat(
        seq(
          alias('*', $.builtin_operator),
          $._expterm
        )
      )
    ),

    _expterm: $ => seq(
      $.term,
      repeat(
        seq(
          alias('^', $.builtin_operator),
          $.term
        )
      )
    ),

    term: $ => choice(
      $.tuple_term,
      $.app,
      $.nested,
      $.literal
    ),

    //nullary_fun: $ => '<all-nullary-functions-defined-up-to-here>',

    /*binary_app: $ => seq(
      $.binary_fun,
      '{',
      $.tuple_term,
      '}',
      $.term
    ),*/

    //binary_fun: $ => '<all-binary-functions-defined-up-to-here>',
    //nullary_fun: $ => $._fun,

    nested: $ => seq(
      '(',
      $._msetterm,
      ')'
    ),

    // Include Tamarin's encryption/signature shorthand f{message}key.
    app: $ => choice(
      prec.left(2, seq($.ident, '(', optional(seq($._msetterm, repeat(seq(',', $._msetterm)))), ')')),
      prec.right(3, seq($.ident, '{', $._msetterm, repeat(seq(',', $._msetterm)), '}', $.term))
    ),

    //nary_fun: $ => '<all-nary-functions-defined-up-to-here>',

    literal: $ => choice(
      seq(
        '\'',
        $.ident,
        '\''
      ),
      seq(
        '~\'',
        $.ident,
        '\''
      ),
      $.nonnode_var
    ),

    nonnode_var: $ => prec.left(2, choice(
      seq(
        optional('$'),
        $.ident,
        optional(
          seq(
            '.',
            $.natural
          )
        )
      ),
      seq(
        $.ident,
        optional(
          seq(
            '.',
            $.natural
          )
        ),
        ':',
        'pub'
      ),
      seq(
        optional('~'),
        $.ident,
        optional(
          seq(
            '.',
            $.natural
          )
        )
      ),
      seq(
        $.ident,
        optional(
          seq(
            '.',
            $.natural
          )
        ),
        ':',
        'fresh'
      ),
      $.msg_var
    )),

    facts: $ => seq(
      $.fact,
      repeat(
        seq(
          ',',
          $.fact
        )
      )
    ),

    builtin_facts: $ => choice(
      'In',
      'Out',
      'Fr'
    ),


    fact: $ => seq(
      optional('!'),
      choice(
        $.builtin_facts,
        $.ident,
        alias("F", $.ident),
        alias("T", $.ident)
      ),
      '(',
      optional(
        seq(
          $._msetterm,
          repeat(
            seq(
              ',',
              $._msetterm
            )
          )
        )
      ),
      ')',
      optional($.fact_annotes)
    ),

    fact_annotes: $ => seq(
      '[',
      $.fact_annote,
      repeat(
        seq(
          ',',
          $.fact_annote
        )
      ),
      ']'
    ),

    fact_annote: $ => choice(
      '+',
      '-',
      'no_precomp'
    ),

    formula: $ => prec.left(2, seq(
      $.imp,
      optional(
        seq(
          choice(
            '<=>',
            '⇔'
          ),
          $.imp
        )
      )
    )),

    imp: $ => prec.left(2, seq(
      $.disjunction,
      optional(
        seq(
          choice(
            '==>',
            '⇒'
          ),
          $.imp
        )
      )
    )),

    disjunction: $ => prec.left(2, seq(
      $.conjunction,
      repeat(
        seq(
          choice(
            '|',
            '∨'
          ),
          $.conjunction
        )
      )
    )),


    conjunction: $ => prec.left(2, seq(
      $.negation,
      repeat(
        seq(
          choice(
            '&',
            '∧'
          ),
          $.negation
        )
      )
    )),

    negation: $ => seq(
      optional(
        choice(
          'not',
          '¬'
        )
      ),
      $.atom
    ),

    atom: $ => choice(
      '⊥',
      'F',
      '⊤',
      'T',
      seq(
        '(',
        $.formula,
        ')'
      ),
      seq(
        'last',
        '(',
        $.node_var,
        ')'
      ),
      seq(
        $.fact,
        '@',
        $.node_var
      ),
      seq(
        $.node_var,
        '<',
        $.node_var
      ),
      seq(
        $._msetterm,
        '=',
        $._msetterm
      ),
      seq(
        $.node_var,
        '=',
        $.node_var
      ),
      seq(
        $.quantifier,
        repeat1($.lvar),
        '.',
        $.formula
      )
    ),

    lvar: $ => choice(
      $.node_var,
      $.nonnode_var
    ),

    quantifier: $ => choice(
      'Ex',
      '∃',
      'All',
      '∀'
    ),

    hexcolor: $ => /#[0-9a-f]{6}/,


    comment: $ => token(
      seq('//', /(\\(.|\r?\n)|[^\\\n])*/)
    ),

  }
});
