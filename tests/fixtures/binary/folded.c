/* SPDX-License-Identifier: Apache-2.0 */
__attribute__((noinline)) int folded_left(int value) { return value + 17; }
__attribute__((noinline)) int folded_right(int value) { return value + 17; }
int folded_entry(int value) { return folded_left(value) + folded_right(value); }
