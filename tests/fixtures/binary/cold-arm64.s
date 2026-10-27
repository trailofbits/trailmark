// SPDX-License-Identifier: Apache-2.0
// GCC 16.2.0 -O2 -fno-exceptions -freorder-blocks-and-partition -S cold.c.
// Instruction/label snapshot; debug and SDK-version directives removed.
.text
.globl _cold_split
.p2align 2
_cold_split:
    mov w1, 9999
    cmp w0, w1
    beq Lcold_prologue
    add w0, w0, 1
    ret
Lcold_prologue:
    stp x29, x30, [sp, -16]!
    mov x29, sp
    b Lcold_body
.section __TEXT,__text_cold,regular,pure_instructions
_cold_split.cold:
Lcold_body:
    bl _never_return
