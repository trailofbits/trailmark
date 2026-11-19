#include "tree_sitter/parser.h"
#include "unicode.h"

// Keep this order identical to both grammars' externals.
enum TokenType {
  BLOCK_COMMENT,
  EXTENSION_CONTENT,
  LANGUAGE_CONTENT,
  ERROR_SENTINEL,
  INTEGER,
  REAL,
  PREFIX_OPERATOR,
  OP0A,
  OP0B,
  OP0C,
  OP0D,
  OP1,
  OP2,
  OP3L,
  OP3R,
  OP4,
  LET_OPERATOR,
  AND_OPERATOR,
  IF_OPERATOR,
  MATCH_OPERATOR,
  FORALL_OPERATOR,
  EXISTS_OPERATOR,
  SEQUENCE_OPERATOR,
  LET_KW,
  AND_KW,
  IF_KW,
  MATCH_KW,
  FORALL_KW,
  EXISTS_KW,
  UNIV_HASH,
  LONG_LEFT_ARROW,
  COLON_EQUALS,
  QMARK_DOT,
  HASH,
  STRING_CONTENT,
  EXTENSION_LANGUAGE,
  IN_FSTAR,
  CONSTRUCTOR_IDENTIFIER,
  PROOF_CONSTRUCTOR,
  EXTERNAL_TOKEN_COUNT,
};

// Fixed lexemes whose shorter prefixes cannot form valid parses are handled
// by the native lexer. Keep classifier values outside the external-symbol array.
enum InternalTokenType {
  MINUS = EXTERNAL_TOKEN_COUNT,
  TILDE,
  EQUALS,
  AMP,
  BAR,
  DOLLAR,
  COLON,
  SEMICOLON,
  QMARK,
  CONJUNCTION,
  DISJUNCTION,
  SUBTYPE,
  EQUALTYPE,
  SUBKIND,
  SQUIGGLY_RARROW,
  RARROW,
  LARROW,
  IFF,
  IMPLIES,
  COLON_COLON,
  PIPE_RIGHT,
  PIPE_LEFT,
  PERCENT_LBRACK,
  BANG_LBRACE,
  SPLICE,
  SPLICET,
};

void *tree_sitter_fstar_external_scanner_create(void) { return NULL; }

void tree_sitter_fstar_external_scanner_destroy(void *payload) {
  (void)payload;
}

unsigned tree_sitter_fstar_external_scanner_serialize(void *payload, char *buffer) {
  (void)payload;
  (void)buffer;
  return 0;
}

void tree_sitter_fstar_external_scanner_deserialize(void *payload,
                                                  const char *buffer,
                                                  unsigned length) {
  (void)payload;
  (void)buffer;
  (void)length;
}

static bool is_whitespace(int32_t c) {
  return c == ' ' || c == '\t' || c == '\v' || c == '\f' || c == '\r' ||
         c == '\n' || c == 0x00A0 || c == 0x1680 ||
         (c >= 0x2000 && c <= 0x200A) || c == 0x2028 || c == 0x2029 ||
         c == 0x202F || c == 0x205F || c == 0x3000 || c == 0xFEFF;
}

static bool is_number_digit(int32_t c, unsigned base) {
  if (c >= '0' && c <= '9') return (unsigned)(c - '0') < base;
  return base == 16 && ((c >= 'a' && c <= 'f') ||
                        (c >= 'A' && c <= 'F'));
}

static bool scan_number(TSLexer *lexer, const bool *valid_symbols) {
  if (!valid_symbols[INTEGER] && !valid_symbols[REAL]) return false;
  if (!is_number_digit(lexer->lookahead, 10)) return false;

  bool initial_zero = lexer->lookahead == '0';
  unsigned base = 10;
  lexer->advance(lexer, false);
  if (initial_zero) {
    switch (lexer->lookahead) {
      case 'x': case 'X': base = 16; break;
      case 'o': case 'O': base = 8; break;
      case 'b': case 'B': base = 2; break;
      default: break;
    }
    if (base != 10) {
      lexer->advance(lexer, false);
      if (!is_number_digit(lexer->lookahead, base)) return false;
    }
  }
  while (is_number_digit(lexer->lookahead, base)) {
    lexer->advance(lexer, false);
  }

  if (base == 10 && lexer->lookahead == '.') {
    // A nonnumeric dot belongs to the following token, e.g. 1.(i).
    lexer->mark_end(lexer);
    lexer->advance(lexer, false);
    if (!is_identifier_continue(lexer->lookahead)) {
      if (!valid_symbols[INTEGER]) return false;
      lexer->result_symbol = INTEGER;
      return true;
    }
    // Digits or identifier characters after the dot make a bad number
    // unless the entire decimal fraction ends in R.
    while (is_number_digit(lexer->lookahead, 10)) {
      lexer->advance(lexer, false);
    }
    if (lexer->lookahead != 'R') return false;
    lexer->advance(lexer, false);
    if (is_identifier_continue(lexer->lookahead)) return false;
    if (!valid_symbols[REAL]) return false;
    lexer->mark_end(lexer);
    lexer->result_symbol = REAL;
    return true;
  }

  if (lexer->lookahead == 'u' || lexer->lookahead == 'U') {
    lexer->advance(lexer, false);
    switch (lexer->lookahead) {
      case 'y': case 's': case 'l': case 'L':
        lexer->advance(lexer, false);
        break;
      default:
        return false;
    }
  } else if (lexer->lookahead == 's') {
    lexer->advance(lexer, false);
    if (lexer->lookahead == 'z') lexer->advance(lexer, false);
  } else if (lexer->lookahead == 'y' || lexer->lookahead == 'z' ||
             lexer->lookahead == 'l' || lexer->lookahead == 'L') {
    lexer->advance(lexer, false);
  }

  if (is_identifier_continue(lexer->lookahead)) return false;
  if (!valid_symbols[INTEGER]) return false;
  lexer->mark_end(lexer);
  lexer->result_symbol = INTEGER;
  return true;
}

/* Helpers is_identifier_continue, is_identifier_start, and is_math_symbol
 * are supplied by the shared scanner. Selection is independent of parser state. */
static bool operator_emit(TSLexer *lexer, const bool *valid_symbols,
                          unsigned symbol) {
  if (symbol >= EXTERNAL_TOKEN_COUNT) return false;
  if (!valid_symbols[symbol]) {
    // A globally longer operator must not fall back to an internal prefix.
    lexer->result_symbol = ERROR_SENTINEL;
    return true;
  }
  lexer->result_symbol = symbol;
  return true;
}

static bool operator_consume(TSLexer *lexer, const char *text) {
  for (; *text; ++text) {
    if (lexer->lookahead != (unsigned char)*text) return false;
    lexer->advance(lexer, false);
  }
  return true;
}

static bool is_operator_rest(int32_t c) {
  switch (c) {
    case '!': case '~': case '?': case '|': case '&': case '=':
    case '<': case '>': case '$': case '@': case '^': case '+':
    case '-': case '*': case '/': case '%': case '.': case ':':
      return true;
    default:
      return false;
  }
}

static bool is_keyword_operator_rest(int32_t c) {
  return is_operator_rest(c) || c == '#' || c == '\\';
}

/* The caller has marked the keyword end. A leading // in the suffix
 * turns even the globally selected keyword-operator back into its keyword. */
static bool scan_keyword_operator_tail(TSLexer *lexer,
                                       const bool *valid_symbols,
                                       unsigned keyword, unsigned operator) {
  bool truncated = false;
  bool has_suffix = false;
  while (is_keyword_operator_rest(lexer->lookahead)) {
    int32_t c = lexer->lookahead;
    lexer->advance(lexer, false);
    if (!truncated) {
      if (c == '/' && lexer->lookahead == '/') {
        truncated = true;
      } else {
        has_suffix = true;
        lexer->mark_end(lexer);
      }
    }
  }
  return operator_emit(lexer, valid_symbols, has_suffix ? operator : keyword);
}

static bool scan_operator_hash(TSLexer *lexer, const bool *valid_symbols) {
  static const char *const headers[] = {
    "show-options", "set-options", "reset-options", "push-options",
    "pop-options", "restart-solver", "print-effects-graph", "check", "eval",
    "lang-",
  };
  unsigned active = (1u << (sizeof(headers) / sizeof(headers[0]))) - 1u;
  unsigned offset = 0;
  lexer->advance(lexer, false);
  lexer->mark_end(lexer);
  while (active) {
    unsigned next = 0;
    for (unsigned i = 0; i < sizeof(headers) / sizeof(headers[0]); ++i) {
      if (!(active & (1u << i))) continue;
      if (!headers[i][offset]) {
        /* Early pragma literals do not require a word boundary. */
        if (i + 1 < sizeof(headers) / sizeof(headers[0]) ||
            is_identifier_start(lexer->lookahead)) return false;
        return operator_emit(lexer, valid_symbols, HASH);
      }
      if (lexer->lookahead == (unsigned char)headers[i][offset]) {
        next |= 1u << i;
      }
    }
    if (!next) break;
    lexer->advance(lexer, false);
    ++offset;
    active = next;
  }
  return operator_emit(lexer, valid_symbols, HASH);
}

static bool scan_operator(TSLexer *lexer, const bool *valid_symbols) {
  int32_t first = lexer->lookahead;
  const char *word = NULL;
  unsigned keyword = 0;
  unsigned keyword_operator = 0;

  switch (first) {
    case 'l': word = "let"; keyword = LET_KW; keyword_operator = LET_OPERATOR; break;
    case 'a': word = "and"; keyword = AND_KW; keyword_operator = AND_OPERATOR; break;
    case 'i': word = "if"; keyword = IF_KW; keyword_operator = IF_OPERATOR; break;
    case 'm': word = "match"; keyword = MATCH_KW; keyword_operator = MATCH_OPERATOR; break;
    case 'f': word = "forall"; keyword = FORALL_KW; keyword_operator = FORALL_OPERATOR; break;
    case 'e': word = "exists"; keyword = EXISTS_KW; keyword_operator = EXISTS_OPERATOR; break;
    case 'u':
      if (!valid_symbols[UNIV_HASH]) return false;
      if (!operator_consume(lexer, "u#")) return false;
      lexer->mark_end(lexer);
      return operator_emit(lexer, valid_symbols, UNIV_HASH);
    case '#':
      return scan_operator_hash(lexer, valid_symbols);
    case ';':
      lexer->advance(lexer, false);
      lexer->mark_end(lexer);
      if (lexer->lookahead == ';') {
        lexer->advance(lexer, false);
        lexer->mark_end(lexer);
        return operator_emit(lexer, valid_symbols, SEQUENCE_OPERATOR);
      }
      return scan_keyword_operator_tail(lexer, valid_symbols,
                                        SEMICOLON, SEQUENCE_OPERATOR);
    case 0x2200: /* ∀ */
    case 0x2203: /* ∃ */
      lexer->advance(lexer, false);
      lexer->mark_end(lexer);
      return scan_keyword_operator_tail(
          lexer, valid_symbols,
          first == 0x2200 ? FORALL_KW : EXISTS_KW,
          first == 0x2200 ? FORALL_OPERATOR : EXISTS_OPERATOR);
    default:
      break;
  }
  if (word) {
    if (!valid_symbols[keyword] && !valid_symbols[keyword_operator]) return false;
    if (!operator_consume(lexer, word) ||
        is_identifier_continue(lexer->lookahead)) return false;
    lexer->mark_end(lexer);
    return scan_keyword_operator_tail(lexer, valid_symbols,
                                      keyword, keyword_operator);
  }

  unsigned symbol;
  switch (first) {
    /* These aliases remain internal identifier/name leaves. */
    case 0x00D7: /* × */
    case 0x22A4: /* ⊤ */
    case 0x22A5: /* ⊥ */
    case 0x2115: /* ℕ */
    case 0x2124: /* ℤ */
    case 0x1D539: /* 𝔹 */
      return false;
    case 0x27F9: symbol = IMPLIES; break;          /* ⟹ */
    case 0x27FA: symbol = IFF; break;              /* ⟺ */
    case 0x2192: symbol = RARROW; break;           /* → */
    case 0x2190: symbol = LARROW; break;           /* ← */
    case 0x27F5: symbol = LONG_LEFT_ARROW; break;  /* ⟵ */
    case 0x219D: symbol = SQUIGGLY_RARROW; break;  /* ↝ */
    case 0x2254: symbol = COLON_EQUALS; break;     /* ≔ */
    case 0x2227: symbol = CONJUNCTION; break;      /* ∧ */
    case 0x2228: symbol = DISJUNCTION; break;      /* ∨ */
    case 0x00AC: symbol = TILDE; break;            /* ¬ */
    case 0x2E2C: symbol = COLON_COLON; break;      /* ⸬ */
    case 0x25B9: symbol = PIPE_RIGHT; break;       /* ▹ */
    case 0x00F7: /* ÷ */
    case 0x2217: symbol = OP3L; break;            /* ∗ */
    case 0x2016: symbol = OP0A; break;            /* ‖ */
    case 0x21D2: /* ⇒ */
    case 0x2265: /* ≥ */
    case 0x2264: /* ≤ */
    case 0x2260: /* ≠ */
    case 0x226A: /* ≪ */
    case 0x25C3: symbol = OP0C; break;            /* ◃ */
    case 0x00B1: /* ± */
    case 0x2201: /* ∁ */
    case 0x2202: /* ∂ */
    case 0x221A: symbol = PREFIX_OPERATOR; break; /* √ */
    default:
      if (first < 0x80) goto ascii_operator;
      if (!valid_symbols[OP4] || !is_math_symbol(first)) return false;
      symbol = OP4;
      break;
  }
  lexer->advance(lexer, false);
  lexer->mark_end(lexer);
  return operator_emit(lexer, valid_symbols, symbol);

ascii_operator:
  if (first == ':') {
    lexer->advance(lexer, false);
    symbol = COLON;
    if (lexer->lookahead == ':' || lexer->lookahead == '=') {
      symbol = lexer->lookahead == ':' ? COLON_COLON : COLON_EQUALS;
      lexer->advance(lexer, false);
    }
    lexer->mark_end(lexer);
    return operator_emit(lexer, valid_symbols, symbol);
  }
  if (first == '\\') {
    if (!operator_consume(lexer, "\\/")) return false;
    lexer->mark_end(lexer);
    return operator_emit(lexer, valid_symbols, DISJUNCTION);
  }

  switch (first) {
    case '!': case '~': case '?': symbol = PREFIX_OPERATOR; break;
    case '|': symbol = OP0A; break;
    case '&': symbol = OP0B; break;
    case '=': case '<': case '>': symbol = OP0C; break;
    case '$': symbol = OP0D; break;
    case '@': case '^': symbol = OP1; break;
    case '+': case '-': symbol = OP2; break;
    case '*': case '/': case '%': symbol = OP3L; break;
    default: return false;
  }

  /* Five means "longer than every competing fixed ASCII operator".
   * Keep selecting on the original run after freezing mark_end at //. */
  char head[4] = {0};
  unsigned length = 0;
  unsigned end_length = 0;
  bool truncated = false;
  while (is_operator_rest(lexer->lookahead)) {
    int32_t c = lexer->lookahead;
    if (length < sizeof(head)) head[length] = (char)c;
    if (length < 5) ++length;
    lexer->advance(lexer, false);
    if (!truncated) {
      if (c == '/' && lexer->lookahead == '/') {
        if (length == 1) {
          if (!operator_consume(lexer, "/ IN F*:")) return false;
          lexer->mark_end(lexer);
          return operator_emit(lexer, valid_symbols, IN_FSTAR);
        }
        truncated = true;
      } else {
        lexer->mark_end(lexer);
        end_length = length;
      }
    }
  }

  if (length == 1) {
    /* These longer fixed tokens leave the generic-rest alphabet. */
    if ((first == '!' && lexer->lookahead == '{') ||
        (first == '%' && lexer->lookahead == '[') ||
        (first == '/' && lexer->lookahead == '\\')) {
      symbol = first == '!' ? BANG_LBRACE :
               first == '%' ? PERCENT_LBRACK : CONJUNCTION;
      lexer->advance(lexer, false);
      lexer->mark_end(lexer);
      return operator_emit(lexer, valid_symbols, symbol);
    }
    if (first == '|' && (lexer->lookahead == ')' ||
                         lexer->lookahead == ']' || lexer->lookahead == '}')) {
      return false; /* Internal lens/list/record closing delimiters. */
    }
    if (first == '%' && lexer->lookahead == 's' &&
        operator_consume(lexer, "splice")) {
      symbol = SPLICE;
      lexer->mark_end(lexer);
      if (operator_consume(lexer, "_t")) {
        symbol = SPLICET;
        lexer->mark_end(lexer);
      }
      return operator_emit(lexer, valid_symbols, symbol);
    }
    switch (first) {
      case '~': symbol = TILDE; break;
      case '-': symbol = MINUS; break;
      case '?': symbol = QMARK; break;
      case '|': symbol = BAR; break;
      case '&': symbol = AMP; break;
      case '=': symbol = EQUALS; break;
      case '$': symbol = DOLLAR; break;
      default: break;
    }
  } else {
    if (first == '*' && head[1] == '*') symbol = OP3R;
    if (first == '|' && length >= 3 && head[1] == '-' && head[2] == '>') {
      symbol = OP4;
    }
    /* Fixed tokens win ties, never a shorter prefix of the original run. */
    if (length == 2) {
      switch (first) {
        case '~': if (head[1] == '>') symbol = SQUIGGLY_RARROW; break;
        case '-': if (head[1] == '>') symbol = RARROW; break;
        case '<':
          switch (head[1]) {
            case ':': symbol = SUBTYPE; break;
            case '@': symbol = SUBKIND; break;
            case '-': symbol = LARROW; break;
            case '|': symbol = PIPE_LEFT; break;
            default: break;
          }
          break;
        case '$': if (head[1] == ':') symbol = EQUALTYPE; break;
        case '?': if (head[1] == '.') symbol = QMARK_DOT; break;
        case '|': if (head[1] == '>') symbol = PIPE_RIGHT; break;
        default: break;
      }
    } else if (length == 3) {
      if (first == '<' && head[1] == '-' && head[2] == '-') {
        symbol = LONG_LEFT_ARROW;
      } else if (first == '=' && head[1] == '=' && head[2] == '>') {
        symbol = IMPLIES;
      }
    } else if (length == 4 && first == '<' && head[1] == '=' &&
               head[2] == '=' && head[3] == '>') {
      symbol = IFF;
    }
  }
  // Universe arithmetic uses a native '+' rather than the generic op2 token.
  if (symbol == OP2 && end_length == 1 && first == '+' && !valid_symbols[OP2])
    return false;
  return operator_emit(lexer, valid_symbols, symbol);
}


static bool scan_string_content(TSLexer *lexer) {
  if (lexer->eof(lexer) || lexer->lookahead == '"') return false;
  if (lexer->lookahead == '\\') {
    lexer->advance(lexer, false);
    lexer->mark_end(lexer);
    switch (lexer->lookahead) {
      case '\\': case '"': case '\'': case 'b': case 'f': case 'n':
      case 't': case 'r': case 'v': case '0': case '\r': case '\n':
      case 0x2028: case 0x2029:
        return false; // A recognized internal escape or line continuation.
      case 'x': case 'u': {
        unsigned count = lexer->lookahead == 'x' ? 2 : 4;
        lexer->advance(lexer, false);
        for (unsigned i = 0; i < count; ++i) {
          if (!is_number_digit(lexer->lookahead, 16)) {
            lexer->result_symbol = STRING_CONTENT;
            return true; // Unknown escapes retain the backslash verbatim.
          }
          lexer->advance(lexer, false);
        }
        return false;
      }
      default:
        lexer->result_symbol = STRING_CONTENT;
        return true;
    }
  }
  do {
    lexer->advance(lexer, false);
  } while (!lexer->eof(lexer) && lexer->lookahead != '"' &&
           lexer->lookahead != '\\');
  lexer->mark_end(lexer);
  lexer->result_symbol = STRING_CONTENT;
  return true;
}

static bool scan_constructor(TSLexer *lexer, const bool *valid_symbols,
                             bool recovering) {
  int32_t first = lexer->lookahead;
  if (first == 0x22a4 || first == 0x22a5) {
    lexer->advance(lexer, false);
    lexer->mark_end(lexer);
    lexer->result_symbol = CONSTRUCTOR_IDENTIFIER;
    return true;
  }
  size_t length = 0;
  bool left = true, right = true;
  do {
    int32_t c = lexer->lookahead;
    left = left && length < 4 && c == "Left"[length];
    right = right && length < 5 && c == "Right"[length];
    ++length;
    lexer->advance(lexer, false);
  } while (is_identifier_continue(lexer->lookahead));
  // These three scalar aliases are IDENTs, not NAMEs.
  if (length == 1 && (first == 0x2115 || first == 0x2124 || first == 0x1d539))
    return false;
  lexer->mark_end(lexer);
  lexer->result_symbol = !recovering && valid_symbols[PROOF_CONSTRUCTOR] &&
                          ((left && length == 4) || (right && length == 5))
                          ? PROOF_CONSTRUCTOR : CONSTRUCTOR_IDENTIFIER;
  // A NAME remains a NAME even where an identifier or shorter selector is expected.
  return true;
}

static bool scan_extension_language(TSLexer *lexer) {
  if (!is_identifier_start(lexer->lookahead)) return false;
  do {
    lexer->advance(lexer, false);
  } while (is_identifier_continue(lexer->lookahead));
  lexer->mark_end(lexer);
  lexer->result_symbol = EXTENSION_LANGUAGE;
  return true;
}

bool tree_sitter_fstar_external_scanner_scan(void *payload, TSLexer *lexer,
                                            const bool *valid_symbols) {
  (void)payload;
  // Recovery must not invent a raw-body, string, tag, or comment context.
  // Ordinary global lexical tokens remain available for resynchronization.
  const bool recovering = valid_symbols[ERROR_SENTINEL];

  // Raw bodies retain whitespace, apparent comments, and apparent strings.
  if (!recovering && valid_symbols[EXTENSION_CONTENT]) {
    bool has_content = false;
    lexer->result_symbol = EXTENSION_CONTENT;
    for (;;) {
      // A fence peek advances the lexer, but must not include the fence.
      lexer->mark_end(lexer);
      if (lexer->eof(lexer)) return has_content;
      if (lexer->lookahead == '`') {
        unsigned ticks = 0;
        do {
          lexer->advance(lexer, false);
          ++ticks;
        } while (ticks < 3 && lexer->lookahead == '`' && !lexer->eof(lexer));
        if (ticks == 3) return has_content;
        // One or two backticks belong to the body, including at EOF.
      } else {
        lexer->advance(lexer, false);
      }
      has_content = true;
    }
  }

  if (!recovering && valid_symbols[LANGUAGE_CONTENT]) {
    if (lexer->eof(lexer)) return false;
    do {
      lexer->advance(lexer, false);
    } while (!lexer->eof(lexer));
    lexer->mark_end(lexer);
    lexer->result_symbol = LANGUAGE_CONTENT;
    return true;
  }
  if (!recovering && valid_symbols[STRING_CONTENT]) return scan_string_content(lexer);
  // Unlike ordinary lexemes, tags cannot be preceded by extras after a header.
  if (!recovering && valid_symbols[EXTENSION_LANGUAGE]) return scan_extension_language(lexer);

  while (is_whitespace(lexer->lookahead) && !lexer->eof(lexer)) {
    lexer->advance(lexer, true);
  }
  if (is_constructor_start(lexer->lookahead) || lexer->lookahead == 0x22a4 ||
      lexer->lookahead == 0x22a5)
    return scan_constructor(lexer, valid_symbols, recovering);
  if (lexer->lookahead >= '0' && lexer->lookahead <= '9') {
    return scan_number(lexer, valid_symbols);
  }
  if (!recovering && lexer->lookahead == '(' && valid_symbols[BLOCK_COMMENT]) {
    lexer->advance(lexer, false);
    if (lexer->lookahead != '*') return false;
    lexer->advance(lexer, false);
    size_t depth = 1;
    while (depth && !lexer->eof(lexer)) {
      int32_t c = lexer->lookahead;
      lexer->advance(lexer, false);
      if (c == '(' && lexer->lookahead == '*') {
        lexer->advance(lexer, false);
        ++depth;
      } else if (c == '*' && lexer->lookahead == ')') {
        lexer->advance(lexer, false);
        --depth;
      }
    }
    // Quotes do not shield delimiters; unfinished comments consume EOF.
    lexer->mark_end(lexer);
    lexer->result_symbol = BLOCK_COMMENT;
    return true;
  }
  return scan_operator(lexer, valid_symbols);
}
