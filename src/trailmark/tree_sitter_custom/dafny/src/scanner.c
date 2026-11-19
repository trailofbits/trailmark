#include "tree_sitter/parser.h"
#include <string.h>
#include <stdbool.h>
#include <stdio.h>

// ALL tokens starting with '<', '>', '|', '^', '&' handled by scanner.
enum TokenType {
  GENERIC_CLOSE,    // >  (single, for nested >>)
  GENERIC_OPEN,     // <  that starts a generic instantiation
  LESS_THAN,        // <  relational
  LESS_EQUAL,       // <=
  EXPLIES,          // <==
  EQUIV_LEFT,       // <==>
  SHIFT_LEFT,       // <<
  ARROW_LEFT,       // <- (quantifier domain)
  BAR_OPEN,         // |  opening cardinality |expr|
  BAR_CLOSE,        // |  closing cardinality |expr|
  BAR,              // |  bitwise/guard/datatype/pattern
  BAR_BAR,          // || logical OR
  CARET,            // ^  bitwise XOR
  AMPERSAND,        // &  bitwise AND
  AMP_AMP,          // && logical AND
};

// ---- Helpers ----

static bool is_ident_start(int32_t c) {
  return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_' || c == '?' || c == '\'';
}

static bool is_ident_char(int32_t c) {
  return is_ident_start(c) || (c >= '0' && c <= '9');
}

static void skip_ws(TSLexer *lexer) {
  while (lexer->lookahead == ' ' || lexer->lookahead == '\t' ||
         lexer->lookahead == '\n' || lexer->lookahead == '\r') {
    lexer->advance(lexer, true);
  }
}

static int read_ident(TSLexer *lexer, char *buf, int max_len) {
  int len = 0;
  if (!is_ident_start(lexer->lookahead)) return 0;
  while (is_ident_char(lexer->lookahead) && len < max_len - 1) {
    buf[len++] = (char)lexer->lookahead;
    lexer->advance(lexer, false);
  }
  buf[len] = '\0';
  return len;
}

static bool is_type_keyword(const char *w) {
  return strcmp(w,"int")==0 || strcmp(w,"nat")==0 || strcmp(w,"real")==0 ||
         strcmp(w,"bool")==0 || strcmp(w,"char")==0 || strcmp(w,"string")==0 ||
         strcmp(w,"object")==0 || strcmp(w,"ORDINAL")==0 ||
         strcmp(w,"set")==0 || strcmp(w,"iset")==0 || strcmp(w,"seq")==0 ||
         strcmp(w,"map")==0 || strcmp(w,"imap")==0 || strcmp(w,"multiset")==0;
}

// ---- Type scanner for IsGenericInstantiation ----

static bool scan_type(TSLexer *lexer, unsigned depth);

static bool scan_generic_args(TSLexer *lexer, unsigned depth) {
  skip_ws(lexer);
  if (!scan_type(lexer, depth + 1)) return false;
  while (true) {
    skip_ws(lexer);
    if (lexer->lookahead == ',') { lexer->advance(lexer, false); skip_ws(lexer); if (!scan_type(lexer, depth + 1)) return false; }
    else break;
  }
  skip_ws(lexer);
  return lexer->lookahead == '>' ? (lexer->advance(lexer, false), true) : false;
}

static bool scan_type(TSLexer *lexer, unsigned depth) {
  // Bound speculative type lookahead independently of the process C stack.
  if (depth >= 256) return false;
  skip_ws(lexer);
  if (lexer->lookahead == '(') {
    lexer->advance(lexer, false); skip_ws(lexer);
    if (lexer->lookahead == ')') { lexer->advance(lexer, false); return true; }
    if (!scan_type(lexer, depth + 1)) return false;
    skip_ws(lexer);
    while (lexer->lookahead == ',') {
      lexer->advance(lexer, false); skip_ws(lexer);
      if (!scan_type(lexer, depth + 1)) return false;
      skip_ws(lexer);
    }
    return lexer->lookahead == ')' ? (lexer->advance(lexer, false), true) : false;
  }
  if (is_ident_start(lexer->lookahead)) {
    char buf[64];
    read_ident(lexer, buf, sizeof(buf));
    skip_ws(lexer);
    while (lexer->lookahead == '.') {
      lexer->advance(lexer, false); skip_ws(lexer);
      if (is_ident_start(lexer->lookahead)) { read_ident(lexer, buf, sizeof(buf)); skip_ws(lexer); }
      else break;
    }
    if (lexer->lookahead == '<') {
      lexer->advance(lexer, false);
      if (!scan_generic_args(lexer, depth + 1)) return false;
      skip_ws(lexer);
    }
    if (lexer->lookahead == '?') { lexer->advance(lexer, false); skip_ws(lexer); }
    if (lexer->lookahead == '-') {
      lexer->advance(lexer, false);
      if (lexer->lookahead == '>') { lexer->advance(lexer, false); return scan_type(lexer, depth + 1); }
      if (lexer->lookahead == '-') { lexer->advance(lexer, false);
        if (lexer->lookahead == '>') { lexer->advance(lexer, false); return scan_type(lexer, depth + 1); }
      }
    }
    if (lexer->lookahead == '~') {
      lexer->advance(lexer, false);
      if (lexer->lookahead == '>') { lexer->advance(lexer, false); return scan_type(lexer, depth + 1); }
    }
    return true;
  }
  return false;
}

static bool is_generic_instantiation(TSLexer *lexer) {
  skip_ws(lexer);
  if (!scan_type(lexer, 0)) return false;
  while (true) {
    skip_ws(lexer);
    if (lexer->lookahead == ',') { lexer->advance(lexer, false); skip_ws(lexer); if (!scan_type(lexer, 0)) return false; }
    else break;
  }
  skip_ws(lexer);
  return lexer->lookahead == '>';
}

// ---- IsCardinality: same-line | counting with comprehension awareness ----
// After consuming '|', scan the rest of the line counting single '|' tokens
// (ignoring '||'). If we find an odd number, this was a cardinality open.
// If even, it's bitwise/guard. Also tracks comprehension keywords to
// understand that |set x | guard :: body| has the guard | as internal.
//
// Returns true if this | opens a cardinality expression.

static bool is_cardinality_expression(TSLexer *lexer) {
  int single_bars = 0;       // count of single | on rest of line
  int paren = 0, bracket = 0, brace = 0;
  int comprehension_guards = 0;  // expected guards from set/iset/forall/exists

  while (true) {
    // Skip spaces/tabs (not newlines)
    while (lexer->lookahead == ' ' || lexer->lookahead == '\t')
      lexer->advance(lexer, false);

    int32_t c = lexer->lookahead;

    // Stop at newline, EOF
    if (c == 0 || c == '\n' || c == '\r') break;

    // Brackets
    if (c == '(') { paren++; lexer->advance(lexer,false); continue; }
    if (c == ')') { if (paren>0) paren--; lexer->advance(lexer,false); continue; }
    if (c == '[') { bracket++; lexer->advance(lexer,false); continue; }
    if (c == ']') { if (bracket>0) bracket--; lexer->advance(lexer,false); continue; }
    if (c == '{') { brace++; lexer->advance(lexer,false); continue; }
    if (c == '}') { if (brace>0) brace--; lexer->advance(lexer,false); continue; }

    // Semicolons at depth 0 end the scan
    if (paren==0 && bracket==0 && brace==0 && c == ';') break;

    // '|' counting
    if (c == '|') {
      lexer->advance(lexer, false);
      if (lexer->lookahead == '|') {
        lexer->advance(lexer, false); // skip ||
        continue;
      }
      single_bars++;
      continue;
    }

    // Track comprehension keywords at depth 0
    if (is_ident_start(c) && paren==0 && bracket==0 && brace==0) {
      char buf[16]; int len = 0;
      while (is_ident_char(lexer->lookahead) && len < 15) {
        buf[len++] = (char)lexer->lookahead;
        lexer->advance(lexer, false);
      }
      buf[len] = '\0';
      if (strcmp(buf,"set")==0 || strcmp(buf,"iset")==0 ||
          strcmp(buf,"forall")==0 || strcmp(buf,"exists")==0) {
        comprehension_guards++;
      }
      continue;
    }

    // Skip strings
    if (c == '"') {
      lexer->advance(lexer,false);
      while (lexer->lookahead!='"' && lexer->lookahead!='\n' && lexer->lookahead!=0) {
        if (lexer->lookahead=='\\') lexer->advance(lexer,false);
        lexer->advance(lexer,false);
      }
      if (lexer->lookahead=='"') lexer->advance(lexer,false);
      continue;
    }

    lexer->advance(lexer, false);
  }

  // Subtract comprehension guards — each adds one | that isn't a cardinality delimiter
  int effective_bars = single_bars - comprehension_guards;

  // If odd number of effective |'s remain, this | opens a cardinality
  // (the last one on the line is our matching close)
  return effective_bars > 0 && (effective_bars % 2 == 1);
}

// ---- Scanner state ----
// Bar state stack: each entry is what the next | means at that nesting level
// 1 = expect close, 2 = expect guard (then close)
#define MAX_BAR_DEPTH 16
typedef struct {
  uint8_t depth;
  uint8_t stack[MAX_BAR_DEPTH]; // each: 1=expect_close, 2=expect_guard
} ScannerState;

void *tree_sitter_dafny_external_scanner_create() {
  return calloc(1, sizeof(ScannerState));
}
void tree_sitter_dafny_external_scanner_destroy(void *p) { free(p); }
unsigned tree_sitter_dafny_external_scanner_serialize(void *p, char *b) {
  ScannerState *s = (ScannerState*)p;
  b[0] = s->depth;
  for (int i = 0; i < s->depth && i < MAX_BAR_DEPTH; i++) b[i+1] = s->stack[i];
  return 1 + s->depth;
}
void tree_sitter_dafny_external_scanner_deserialize(void *p, const char *b, unsigned l) {
  ScannerState *s = (ScannerState*)p;
  s->depth = l > 0 ? b[0] : 0;
  if (s->depth > MAX_BAR_DEPTH) s->depth = MAX_BAR_DEPTH;
  for (int i = 0; i < s->depth; i++) s->stack[i] = (i+1 < (int)l) ? b[i+1] : 1;
}

// ---- Main scanner ----

bool tree_sitter_dafny_external_scanner_scan(void *payload, TSLexer *lexer, const bool *valid_symbols) {
  ScannerState *state = (ScannerState*)payload;

  // No error recovery guard — tokens are external-only, must always handle

  // ---- Handle '>' ----
  if (valid_symbols[GENERIC_CLOSE]) {
    skip_ws(lexer);
    if (lexer->lookahead == '>') {
      lexer->advance(lexer,false); lexer->result_symbol = GENERIC_CLOSE;
      lexer->mark_end(lexer); return true;
    }
  }

  // ---- Handle '<' ----
  if (lexer->lookahead == '<' &&
      (valid_symbols[GENERIC_OPEN] || valid_symbols[LESS_THAN] ||
       valid_symbols[LESS_EQUAL] || valid_symbols[EXPLIES] ||
       valid_symbols[EQUIV_LEFT] || valid_symbols[SHIFT_LEFT] ||
       valid_symbols[ARROW_LEFT])) {
    lexer->advance(lexer,false); lexer->mark_end(lexer);
    if (lexer->lookahead == '<') {
      if (valid_symbols[SHIFT_LEFT]) {
        lexer->advance(lexer,false); lexer->mark_end(lexer);
        lexer->result_symbol = SHIFT_LEFT; return true; }
    } else if (lexer->lookahead == '-') {
      lexer->advance(lexer,false);
      if (lexer->lookahead!='-'&&lexer->lookahead!='>'&&valid_symbols[ARROW_LEFT]) {
        lexer->mark_end(lexer); lexer->result_symbol = ARROW_LEFT; return true; }
      if (valid_symbols[LESS_THAN]) { lexer->result_symbol=LESS_THAN; return true; }
    } else if (lexer->lookahead == '=') {
      lexer->advance(lexer,false); lexer->mark_end(lexer);
      if (lexer->lookahead=='=') { lexer->advance(lexer,false);
        if (lexer->lookahead=='>') { lexer->advance(lexer,false); lexer->mark_end(lexer);
          if (valid_symbols[EQUIV_LEFT]) { lexer->result_symbol=EQUIV_LEFT; return true; }
        } else { lexer->mark_end(lexer);
          if (valid_symbols[EXPLIES]) { lexer->result_symbol=EXPLIES; return true; } } }
      if (valid_symbols[LESS_EQUAL]) { lexer->result_symbol=LESS_EQUAL; return true; }
    } else {
      if (valid_symbols[GENERIC_OPEN] && is_generic_instantiation(lexer)) {
        lexer->result_symbol = GENERIC_OPEN; return true; }
    }
    if (valid_symbols[LESS_THAN]) { lexer->result_symbol=LESS_THAN; return true; }
    if (valid_symbols[GENERIC_OPEN]) { lexer->result_symbol=GENERIC_OPEN; return true; }
    return false;
  }

  // ---- Handle '|' — all | tokens are external ----
  if (lexer->lookahead == '|' &&
      (valid_symbols[BAR_OPEN]||valid_symbols[BAR_CLOSE]||
       valid_symbols[BAR]||valid_symbols[BAR_BAR])) {
    lexer->advance(lexer,false); lexer->mark_end(lexer);

    // || — always consume both chars
    if (lexer->lookahead == '|') {
      lexer->advance(lexer,false); lexer->mark_end(lexer);
      lexer->result_symbol = BAR_BAR; return true;
    }

    // ATG-inspired allowBitwiseOps logic:
    // Inside cardinality (depth > 0), | as bitwise is DISABLED.
    // Only BAR_CLOSE (cardinality close) and BAR (quantifier guard) are possible.
    //
    // Outside cardinality (depth == 0), | routes by valid_symbols:
    // BAR_OPEN at primary_expression level, BAR at bitvector_factor level.

    if (state->depth > 0) {
      // INSIDE cardinality — allowBitwiseOps is false
      uint8_t top = state->stack[state->depth - 1];

      if (top == 2) {
        // Expecting comprehension guard — return BAR, transition to expect close
        state->stack[state->depth - 1] = 1;
        lexer->result_symbol = BAR;
        return true;
      }

      // State says expect close OR no specific state — close the cardinality
      if (valid_symbols[BAR_CLOSE]) {
        state->depth--;
        lexer->result_symbol = BAR_CLOSE;
        return true;
      }

      // BAR_CLOSE not valid but we're inside cardinality — return BAR (guard)
      lexer->result_symbol = BAR;
      return true;
    }

    // OUTSIDE cardinality — check for comprehension keyword
    // Only try BAR_OPEN when BAR is NOT also valid — this means we're
    // at a true expression-start position where only cardinality makes sense
    if (valid_symbols[BAR_OPEN] && !valid_symbols[BAR]) {
      // At expression-start position — open cardinality
      // Peek for comprehension keyword to set guard state
      bool has_comprehension = false;
      while (lexer->lookahead == ' ' || lexer->lookahead == '\t')
        lexer->advance(lexer, false);
      if (is_ident_start(lexer->lookahead)) {
        char buf[16]; int len = 0;
        while (is_ident_char(lexer->lookahead) && len < 15) {
          buf[len++] = (char)lexer->lookahead;
          lexer->advance(lexer, false);
        }
        buf[len] = '\0';
        has_comprehension = (strcmp(buf,"set")==0 || strcmp(buf,"iset")==0 ||
                             strcmp(buf,"forall")==0 || strcmp(buf,"exists")==0);
      }
      if (has_comprehension) {
        // |set/iset/forall/exists — definitely cardinality of comprehension
        if (state->depth < MAX_BAR_DEPTH) {
          state->stack[state->depth++] = 2;  // expect guard then close
        }
        lexer->result_symbol = BAR_OPEN;
        return true;
      }
      // Not a comprehension keyword — fall through to BAR (GLR handles simple cardinality)
    }

    // BAR at bitvector_factor level (bitwise OR, guard, separator)
    if (valid_symbols[BAR]) {
      lexer->result_symbol = BAR;
      return true;
    }

    // Fallbacks
    if (valid_symbols[BAR_CLOSE]) {
      if (state->depth > 0) state->depth--;
      lexer->result_symbol = BAR_CLOSE;
      return true;
    }
    lexer->result_symbol = BAR;
    return true;
  }

  // ---- Handle '^' ----
  if (lexer->lookahead == '^' && valid_symbols[CARET]) {
    lexer->advance(lexer,false); lexer->mark_end(lexer);
    lexer->result_symbol = CARET; return true;
  }

  // ---- Handle '&' and '&&' ----
  if (lexer->lookahead == '&' && (valid_symbols[AMPERSAND] || valid_symbols[AMP_AMP])) {
    lexer->advance(lexer,false); lexer->mark_end(lexer);
    if (lexer->lookahead == '&') {
      lexer->advance(lexer,false); lexer->mark_end(lexer);
      lexer->result_symbol = AMP_AMP; return true;
    }
    lexer->result_symbol = AMPERSAND; return true;
  }

  return false;
}
