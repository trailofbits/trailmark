/* Check speculative type lookahead at its depth and work boundaries. */
#include <assert.h>
#include <stddef.h>
#include "dafny/src/scanner.c"

typedef struct {
    TSLexer lexer;
    const char *cursor;
    size_t advances;
} Input;

static void advance(TSLexer *lexer, bool skip) {
    (void)skip;
    Input *input = (Input *)lexer;
    assert(*input->cursor != '\0');
    input->advances++;
    lexer->lookahead = *++input->cursor;
}

static Input input_for(const char *source) {
    Input input = {
        .lexer = {.lookahead = *source, .advance = advance},
        .cursor = source,
    };
    return input;
}

int main(void) {
    Input below = input_for("int");
    assert(scan_type(&below.lexer, 255));
    assert(below.advances == 3);

    for (unsigned depth = 256; depth <= 257; depth++) {
        Input limited = input_for("int");
        assert(!scan_type(&limited.lexer, depth));
        assert(limited.advances == 0);
    }

    char nested[512 * 3 + 4];
    char *next = nested;
    for (unsigned index = 0; index < 512; index++) {
        *next++ = 'A';
        *next++ = '<';
    }
    memcpy(next, "int", 3);
    next += 3;
    for (unsigned index = 0; index < 512; index++) *next++ = '>';
    *next = '\0';

    Input deep = input_for(nested);
    assert(!scan_type(&deep.lexer, 0));
    /* Each A< advances twice and increases the recursive depth twice. */
    assert(deep.advances == 256);
    return 0;
}
