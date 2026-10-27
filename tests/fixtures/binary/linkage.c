/* SPDX-License-Identifier: Apache-2.0 */
extern int imported_one(int value);
extern int imported_two(int value);
__attribute__((noinline)) int linked_helper(int value) { return value + 3; }
int linked_tail(int value) { return linked_helper(value); }
int linked_entry(int value) {
    return imported_one(value) + imported_two(value) + linked_tail(value);
}
