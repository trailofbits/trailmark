/* Shared stateless scanner for nested (* ... *) comments. */
#include <tree_sitter/parser.h>
#include <wctype.h>

#ifndef TRAILMARK_COMMENT_OPEN
#define TRAILMARK_COMMENT_OPEN '('
#define TRAILMARK_COMMENT_CLOSE ')'
#endif
#define TM_JOIN_INNER(a, b) a##b
#define TM_JOIN(a, b) TM_JOIN_INNER(a, b)
#define TM_SCANNER(suffix) TM_JOIN(TRAILMARK_LANGUAGE, suffix)

void *TM_SCANNER(_external_scanner_create)(void) { return NULL; }
void TM_SCANNER(_external_scanner_destroy)(void *payload) {}
unsigned TM_SCANNER(_external_scanner_serialize)(void *payload, char *buffer) { return 0; }
void TM_SCANNER(_external_scanner_deserialize)(void *payload, const char *buffer, unsigned length) {}

bool TM_SCANNER(_external_scanner_scan)(void *payload, TSLexer *lexer, const bool *valid) {
    if (!valid[0]) return false;
    while (iswspace(lexer->lookahead)) lexer->advance(lexer, true);
    if (lexer->lookahead != TRAILMARK_COMMENT_OPEN) return false;
    lexer->advance(lexer, false);
    if (lexer->lookahead != '*') return false;
    lexer->advance(lexer, false);
#ifdef TRAILMARK_COMMENT_OPERATOR
    if (lexer->lookahead == ')') return false;
#endif
    unsigned depth = 1;
    while (!lexer->eof(lexer)) {
        if (lexer->lookahead == TRAILMARK_COMMENT_OPEN) {
            lexer->advance(lexer, false);
            if (lexer->lookahead == '*') {
                lexer->advance(lexer, false);
                depth++;
            }
        } else if (lexer->lookahead == '*') {
            lexer->advance(lexer, false);
            if (lexer->lookahead == TRAILMARK_COMMENT_CLOSE) {
                lexer->advance(lexer, false);
                if (--depth == 0) {
                    lexer->mark_end(lexer);
                    lexer->result_symbol = 0;
                    return true;
                }
            }
        } else {
            lexer->advance(lexer, false);
        }
    }
    return false;
}
