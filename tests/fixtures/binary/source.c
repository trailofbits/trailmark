/* SPDX-License-Identifier: Apache-2.0 */
/* Freestanding fixtures are inspected statically and are never executed. */
volatile int fixture_value;

static inline __attribute__((always_inline)) int inline_helper(int value) {
    return value ^ (value >> 3);
}

__attribute__((noinline)) int helper(int value) {
    return inline_helper(value) + fixture_value;
}

int entry(int value) {
    return helper(value) + 1;
}

int dispatch(int (*callback)(int), int value) {
    return callback(value) + 2;
}
