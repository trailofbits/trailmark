/**
 * @file ProVerif's typed pi calculus grammar for tree-sitter
 * @author Marian Buschsieweke <marian.buschsieweke@posteo.net>
 * @license 0BSD
 */

/// <reference types="tree-sitter-cli/dsl" />
// @ts-check

// Helper for what is seq+() and seq() in the appendix
const _seq1 = (separator, rule) => seq(rule, repeat(seq(separator, rule)));
const _seq = (separator, rule) => optional(_seq1(separator, rule));

module.exports = grammar({
  name: "proverif",

  extras: $ => [
    /\s|\\\r?\n/,
    $.comment,
    $.line_comment,
  ],

  conflicts: $ => [
    [$._query],
    [$._typedecl, $.gterm],
    [$._typedecl, $.nidecl],
    [$.gterm],
    [$.gterm, $.gterm_function_call],
    [$.infix, $.equality],
    [$.pattern],
    [$.process],
    [$.process_function_call, $.process_final_item],
    [$.process_get],
    [$.process_item, $.process_replication],
    [$.process_item],
    [$.process_let],
    [$.process_replication, $.process],
    [$.process_replication, $.process_foreach],
    [$.process_replication, $.process_get],
    [$.process_replication, $.process_let],
    [$.process_replication],
    [$.pterm, $.pattern],
    [$.pterm, $.pterm_function_call],
    [$.pterm],
    [$.pterm, $.term_get],
    [$.term_get],
    [$.term],
  ],
  reserved: {
    global: $ => [
      'among',
      'axiom',
      'channel',
      'choice',
      'clauses',
      'const',
      'def',
      'elimtrue',
      'else',
      'equation',
      'equivalence',
      'event',
      'expand',
      'fail',
      'forall',
      'foreach',
      'free',
      'fun',
      'get',
      'if',
      'in',
      'inj-event',
      'insert',
      'lemma',
      'let',
      'letfun',
      'letproba',
      'new',
      'noninterf',
      'noselect',
      'not',
      'nounif',
      'otherwise',
      'out',
      'param',
      'phase',
      'pred',
      'proba',
      'process',
      'proof',
      'public_vars',
      'putbegin',
      'query',
      'reduc',
      'restriction',
      'secret',
      'select',
      'set',
      'suchthat',
      'sync',
      'table',
      'then',
      'type',
      'weaksecret',
      'yield'
    ]
  },
  externals: $ => [$.comment],
  word: $ => $.ident,
  rules: {
    source_file: $ => seq(
      repeat($.decl),
      optional(choice(
        seq('process', $.process),
        seq('equivalence', $.process, $.process)
      ))
    ),

    line_comment: $ => token(seq('#', /.*/)),
    _binding: $ => choice(
      seq('!', $.nat, '=', $.gformat),
      seq($.ident, '=', $.gformat),
    ),
    binding: $ => _seq1(';', $._binding),

    // From Appendix A in the manual
    ident: $ => token(/[A-Za-z_][A-Za-z0-9_'-]*/),
    nat: $ => /\d+/,
    int: $ => /-?\d+/,
    typeid: $ => choice(
      $.ident,
      'channel',
    ),
    option: $ => choice(
      'data',
      'private',
      'typeConverter',
      'memberOptim',
      'precise',
      'nonSat',
      'discardSat',
      'instantiateSat',
      'fullSat',
      'nonVerif',
      'discardVerif',
      'instantiateVerif',
      'fullVerif',
      'induction',
      'noInduction',
      'maxSubset',
      'proveAll',
      'reachability',
      'pv_reachability',
      'real_or_random',
      'pv_real_or_random',
      'bounded',
      'fixed',
      'large',
    ),
    options: $ => seq('[', _seq1(',', $.option), ']'),
    infix: $ => choice(
      '||',
      '&&',
      '=',
      '<>',
      '<=',
      '>=',
      '<',
      '>',
    ),

    // Figure A.1
    term_function_call: $ => seq(
      field('name', $.ident), '(', _seq(',', $.term), ')'
    ),
    term: $ => choice(
      $.ident,
      $.nat,
      seq('(', _seq(',', $.term), ')'),
      $.term_function_call,
      seq($.term, choice('+', '-'), $.nat),
      seq($.nat, '+', $.term),
      seq($.term, $.infix, $.term),
      seq('not', '(', $.term, ')'),
    ),
    pterm_function_call: $ => seq(
      field('name', $.ident), '(', _seq(',', $.pterm), ')'
    ),
    pterm: $ => choice(
      $.ident,
      seq('(', _seq(',', $.pterm), ')'),
      $.pterm_function_call,
      seq('choice', '[', $.pterm, ',', $.pterm, ']'),
      seq($.pterm, choice('+', '-'), $.nat),
      seq($.nat, '+', $.pterm),
      seq($.pterm, $.infix, $.pterm),
      seq('not', '(', $.pterm, ')'),
      seq(
        'new',
        $.ident,
        optional(seq('[', _seq(',', $.ident), ']')),
        ':',
        $.typeid,
        ';',
        $.pterm,
      ),
      seq($.ident, '<-R', $.typeid, ';', $.pterm),
      seq(
        'if',
        $.pterm,
        'then',
        $.pterm,
        optional(seq('else', $.pterm)),
      ),
      seq(
        'let',
        $.pattern,
        '=',
        $.pterm,
        'in',
        $.pterm,
        optional(seq('else', $.pterm)),
      ),
      seq(
        $.ident,
        optional(seq(':', $.typeid)),
        '<-',
        $.pterm,
        ';',
        $.pterm,
      ),
      seq(
        'let',
        $.typedecl,
        'suchthat',
        $.pterm,
        'in',
        $.pterm,
        optional(seq('else', $.pterm)),
      ),
      seq('insert', $.ident, '(', _seq(',', $.pterm), ')'),
      $.term_get,
      seq(
        'event',
        $.ident,
        optional(seq('(', _seq(',', $.pterm), ')')),
        ';',
        $.pterm,
      ),
    ),
    term_get: $ => seq(
        'get', $.ident, '(', _seq(',', $.pattern), ')',
        optional(seq('suchthat', $.pterm)),
        optional($.options),
        'in', $.pterm,
        optional(seq('else', $.pterm)),
      ),
    pattern: $ => choice(
      seq(
        $.ident,
        optional(seq(':', $.typeid)),
      ),
      $.nat,
      seq($.pattern, '+', $.nat),
      seq($.nat, '+', $.pattern),
      seq('(', _seq(',', $.pattern), ')'),
      seq($.ident, '(', _seq(',', $.pattern), ')'),
      seq('=', $.pterm),
    ),
    mayfailterm: $ => choice(
      $.term,
      'fail',
    ),
    _typedecl: $ => seq(_seq1(',', $.ident), ':', $.typeid),
    typedecl: $ => _seq1(',', $._typedecl),
    _failtypedecl: $ => seq(_seq1(',', $.ident), ':', choice($.typeid, 'fail')),
    failtypedecl: $ => _seq1(',', $._failtypedecl),

    // Figure A.2
    type_def: $ => seq(
      'type',
      field('name', $.ident),
      optional($.options),
      '.',
    ),
    function_macro_def: $ => seq(
      'letfun',
      field('name', $.ident),
      optional(seq(
        '(',
        $.typedecl,
        ')',
      )),
      '=',
      $.pterm,
      '.',
    ),
    function_def: $ => seq(
      'fun',
      field('name', $.ident),
      '(',
      _seq(',', $.typeid),
      ')',
      ':',
      $.typeid,
      optional(seq('reduc', $.mayfailreduc)),
      optional($.options),
      '.',
    ),
    /* These statements are CryptoVerif syntax. The ProVerif tool will tolerate
     * those statements to allow sharing code between CryptoVerif and ProVerif,
     * but there body is effectively ignored by ProVerif. */
    decl_cryptoverif: $=> choice(
      seq(
        'proba',
        $.ident,
        /* CryptoVerif-only syntax is ignored in ProVerif */
        optional(seq('(', /[^)]*/, ')')),
        optional($.options),
        '.',
      ),
      seq(
        'letproba',
        $.ident,
        optional(seq('(', $.typedecl, ')')),
        '=',
        /* CryptoVerif-only syntax is ignored in ProVerif */
        /[^\.]*/,
        '.',
      ),
      seq('proof', '{', $.proof, '}'),
      seq(
        'def', $.ident, '(', _seq(',', $.typeid),')',
        '{', $.def, '}',
      ),
      seq('expand', $.ident, '(', _seq(',', $.typeid), ')', '.'),
    ),
    decl_pred: $ => seq(
      'pred',
      field('name', $.ident),
      optional(seq(
        '(',
        _seq(',', $.typeid),
        ')',
      )),
      optional($.options),
      '.',
    ),
    decl_table: $ => seq(
      'table',
      field('name', $.ident),
      '(',
      _seq(',', $.typeid),
      ')',
      '.',
    ),
    decl_process_macro: $ => seq(
      'let', field('name', $.ident),
      optional(seq('(', $.typedecl, ')')),
      '=',
      $.process,
      '.',
    ),
    decl_event: $ => seq(
      'event',
      field('name', $.ident),
      optional(seq(
        '(',
        _seq(',', $.typeid),
        ')',
      )),
      '.',
    ),
    decl_query: $ => seq(
      'query',
      optional(seq($.typedecl, ';')),
      $.query,
      optional($.options),
      '.',
    ),
    decl_channel: $ => seq(
      'channel', _seq1(',', $.ident), '.'
    ),
    decl_free: $ => seq(
      'free', _seq1(',', $.ident), ':', $.typeid,
      optional($.options),
      '.',
    ),
    decl_const: $ => seq(
      'const', _seq1(',', $.ident), ':', $.typeid,
      optional($.options),
      '.',
    ),
    decl: $ => choice(
      $.type_def,
      $.decl_channel,
      $.decl_free,
      $.decl_const,
      $.function_macro_def,
      seq(
        'reduc',
        $.eqlist,
        optional($.options),
        '.',
      ),
      $.function_def,
      seq(
        'equation',
        $.eqlist,
        optional($.options),
        '.',
      ),
      $.decl_pred,
      $.decl_table,
      $.decl_process_macro,
      seq(
        'set',
        $.name,
        '=',
        $.value,
        '.',
      ),
      $.decl_event,
      $.decl_query,
      seq(
        choice('axiom', 'restriction', 'lemma'),
        optional(seq($.typedecl, ';')),
        $.lemma,
        optional($.options),
        '.',
      ),
      seq(
        'noninterf',
        optional(seq($.typedecl, ';')),
        _seq(',', $.nidecl),
        '.',
      ),
      seq(
        'weaksecret',
        $.ident,
        '.',
      ),
      seq(
        'not',
        optional(seq($.typedecl, ';')),
        $.gterm,
        '.',
      ),
      seq(
        choice('select', 'noselect', 'nounif'),
        optional(seq($.typedecl, ';')),
        $.nounifdecl,
        optional(seq('/', $.int)),
        optional(seq('[', _seq1(',', $.nounifoption), ']')),
        '.',
      ),
      seq('elimtrue', optional(seq($.failtypedecl, ';')), $.term, '.'),
      seq('clauses', $.clauses, '.'),
      seq('param', _seq1(',', $.ident), optional($.options), '.',),
      $.decl_cryptoverif,
    ),
    nidecl: $ => seq(
      $.ident,
      optional(seq('among', '(', _seq1(',', $.term), ')'))
    ),
    /* CryptoVerif-only syntax is ignored in ProVerif */
    proof: $ => token(/[^}]*/),
    def: $ => token(/[^}]*/),
    name: $ => $.ident,
    value: $ => choice(
      $.ident,
      $.int,
      $.string,
      'true',
      'false'
    ),
    string: $ => token(seq('"', repeat(choice(/[^"\\]/, /\\./)), '"')),

    // Figure A.3
    equality: $ => choice(
      seq($.term, '=', $.term),
      seq('let', $.ident, '=', $.term, 'in', $.equality),
    ),
    mayfailequality: $ => choice(
      seq(
        $.ident, '(',
        _seq1(',', $.mayfailterm),
        ')',
        '=',
        $.mayfailterm,
      ),
      seq(
        'let',
        $.ident,
        '=',
        $.term,
        'in',
        $.mayfailequality
      ),
    ),
    _eqlist: $ => seq(
      optional(seq('forall', $.typedecl, ';')),
      $.equality,
    ),
    eqlist: $ => _seq1(';', $._eqlist),
    _mayfailreduc: $ => seq(
      optional(seq('forall', $.failtypedecl, ';')),
      $.mayfailequality,
    ),
    mayfailreduc: $ => seq(_seq1('otherwise', $._mayfailreduc)),

    // Figure A.4
    _query: $ => choice(
      seq(
        $.gterm,
        optional(seq('public_vars', _seq1(',', $.ident))),
      ),
      seq(
        'secret',
        $.ident,
        optional(seq('public_vars', _seq1(',', $.ident))),
        optional($.options),
      ),
      seq(
        'putbegin',
        choice('event', 'inj-event'),
        ':',
        _seq1(',', $.ident),
      ),
    ),
    query: $ => _seq1(';', $._query),
    lemma: $ => choice(
      seq($.gterm, optional(seq(';', $.lemma))),
      seq(
        $.gterm,
        'for',
        '{',
        'public_vars',
        _seq1(',', $.ident),
        '}',
        optional(seq(';', $.lemma)),
      ),
      seq(
        $.gterm,
        'for',
        '{',
        'secret',
        $.ident,
        optional(seq('public_vars', _seq1(',', $.ident))),
        '[',
        'real_or_random',
        ']',
        '}',
        optional(seq(';', $.lemma)),
      ),
    ),
    gterm_function_call: $ => seq(
      field('name', $.ident),
      '(',
      _seq(',', $.gterm),
      ')',
      optional(seq('phase', $.nat)),
      optional(seq('@', $.ident)),
    ),
    gterm: $ => choice(
      $.ident,
      seq('(', $.gterm, ')'),
      $.gterm_function_call,
      seq(
        'choice',
        '[',
        $.gterm,
        ',',
        $.gterm,
        ']',
      ),
      seq($.gterm, $.infix, $.gterm),
      seq($.gterm, choice('+', '-'), $.gterm),
      seq($.nat, '+', $.gterm),
      seq(
        choice('event', 'inj-event'),
        '(',
        _seq(',', $.gterm),
        ')',
        optional(seq('@', $.ident)),
      ),
      seq($.gterm, '==>', $.gterm),
      seq($.gterm, ',', $.gterm),
      seq(
        'new',
        $.ident,
        optional(seq('[', $.binding, ']')),
      ),
      seq(
        'let',
        $.ident,
        '=',
        $.gterm,
        'in',
        $.gterm,
      ),
    ),

    // Figure A.6
    nounifdecl: $ => choice(
      seq('let', $.ident, '=', $.gformat, 'in', $.nounifdecl),
      $.ident,
      seq(
        $.gformat_function_call,
          optional(seq('phase', $.nat)),
      ),
    ),
    gformat_function_call: $ => seq(
      field('name', $.ident), '(', _seq(',', $.gformat), ')'
    ),
    gformat: $ => choice(
      $.ident,
      seq('*', $.ident),
      $.gformat_function_call,
      seq('choice', '[', $.gformat, ',', $.gformat, ']'),
      seq('not', '(', _seq(',', $.gformat), ')'),
      seq('(', _seq(',', $.gformat), ')'),
      seq(
        'new',
        $.ident,
        optional(seq(
          '[',
          optional($.binding),
          ']'
        ))
      ),
      seq('let', $.ident, '=', $.gformat, 'in', $.gformat),
    ),
    nounifoption: $ => choice(
      'hypothesis',
      'conclusion',
      'ignoreAFewTimes',
      seq('inductionOn', '=', $.ident),
      seq('inductionOn', '=', '{', _seq1(',', $.ident), '}'),
    ),

    // Figure A.7
    clauses: $ => seq(
      optional(seq('forall', $.failtypedecl, ';')),
      $.clause,
      optional(seq(';', $.clauses)),
    ),
    clause: $ => choice(
      $.term,
      seq($.term, '->', $.term),
      seq($.term, '<->', $.term),
      seq($.term, '<=>', $.term),
    ),

    // Figure A.8
    process_input: $ => seq(
      'in', '(', $.pterm, ',', $.pattern, ')',
      optional($.options),
    ),
    process_output: $ => seq(
      'out', '(', $.pterm, ',', $.pterm, ')',
    ),
    process_new: $ => prec.left(seq(
      'new',
      $.ident,
      optional(seq('[', _seq(',', $.ident), ']')),
      ':',
      $.typeid,
    )),
    process_item: $ => choice(
      seq('!', $.ident, '<=', $.ident),
      seq($.ident, '<-R', $.typeid, optional(seq(';', $.process))),
      seq('if', $.pterm, 'then', $.process, optional(seq('else', $.process))),
      $.process_input,
      $.process_output,
      $.process_new,
      seq($.ident, optional(seq(':', $.typeid)), '<-', $.pterm),
      seq('insert', $.ident, '(', _seq(',', $.pterm), ')'),
      seq(
        'event',
        $.ident,
        optional(seq('(', _seq(',', $.pterm), ')')),
      ),
      seq('phase', $.nat),
      seq(
        'sync',
        $.nat,
        optional(seq('[', $.tag, ']')),
      ),
    ),
    process_function_call: $ => seq(
      field('name', $.ident), '(', _seq(',', $.pterm), ')',
    ),
    process_final_item: $ => choice(
      '0',
      'yield',
      $.process_function_call,
      $.ident,
    ),
    process_replication: $ => choice(
      seq($.process, '|', $.process),
      seq('!', $.process),
    ),
    process_let: $ => choice(
      seq(
        'let', $.pattern, '=', $.pterm,
        optional(seq(
          'in', $.process,
          optional(seq('else', $.process)),
        )),
      ),
      seq(
        'let', $.typedecl, 'suchthat', $.pterm,
        optional($.options),
        optional(seq(
          'in',
          $.process,
          optional(seq('else', $.process)),
        )),
      ),
    ),
    process_foreach: $ => seq(
      'foreach', $.ident, '<=', $.ident, $.process
    ),
    process_get: $ => seq(
      'get',
      $.ident,
      '(',
      _seq(',', $.pattern),
      ')',
      optional(seq('suchthat', $.pterm)),
      optional($.options),
      optional(seq(
        'in',
        $.process,
        optional(seq('else', $.process)),
      )),
    ),
    process: $ => choice(
      seq(
        $.process_item,
        optional(seq(';', $.process))
      ),
      $.process_final_item,
      $.process_let,
      $.process_foreach,
      $.process_get,
      seq('(', $.process, ')'),
      $.process_replication,
    ),

    // Add missing tag (not defined in appedix)
    tag: $ => $.ident,
  }
});
