/* Exercise the shared scanner against a real TSLexer interface. */
#include <assert.h>
#include <stddef.h>
#include <string.h>
#define TRAILMARK_LANGUAGE tree_sitter_control
#include "nested_comment.h"

typedef struct {
    TSLexer lexer;
    const unsigned char *cursor;
} Input;
static void advance(TSLexer *lexer, bool skip) {
    Input *input = (Input *)lexer;
    lexer->lookahead = *++input->cursor;
}
static bool eof(const TSLexer *lexer) { return lexer->lookahead == 0; }
static void mark_end(TSLexer *lexer) {}
static void check(const char *text, bool expected, const char *rest) {
    Input input = {.lexer = {.lookahead = (unsigned char)*text, .advance = advance,
                            .eof = eof, .mark_end = mark_end},
                   .cursor = (const unsigned char *)text};
    const bool valid[] = {true};
    assert(tree_sitter_control_external_scanner_scan(NULL, &input.lexer, valid) == expected);
    if (expected) assert(strcmp((const char *)input.cursor, rest) == 0);
}
int main(void) {
    assert(tree_sitter_control_external_scanner_serialize(NULL, NULL) == 0);
#ifdef TRAILMARK_SLASH_TEST
    check(" /* outer /* nested */ text */tail", true, "tail");
    check("/* unfinished /* nested */", false, "");
#else
    check(" (* outer (* nested *) text *)tail", true, "tail");
    check("(* Unicode Ī/ į* 你好 *)tail", true, "tail");
    check("(* unfinished (* nested *)", false, "");
    check("(*) operator", false, "");
#endif
    check("ordinary source", false, "");
    return 0;
}
