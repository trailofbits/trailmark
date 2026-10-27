/* SPDX-License-Identifier: Apache-2.0 */
extern void never_return(void) __attribute__((noreturn, cold));
int cold_split(int value) {
    if (__builtin_expect(value == 9999, 0)) never_return();
    return value + 1;
}
