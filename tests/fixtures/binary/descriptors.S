/* SPDX-License-Identifier: Apache-2.0 */
/* A linked ELFv1 image whose exported function symbols live in .opd. */
.abiversion 1
.section .opd,"aw",@progbits
.p2align 3
.globl descriptor_entry
.type descriptor_entry,@function
descriptor_entry:
    .quad .Lentry, 0, 0
.size descriptor_entry,24
.globl descriptor_helper
.type descriptor_helper,@function
descriptor_helper:
    .quad .Lhelper, 0, 0
.size descriptor_helper,24
.text
.p2align 2
.Lentry:
    mflr 0
    std 0,16(1)
    stdu 1,-112(1)
    bl .Lhelper
    nop
    addi 1,1,112
    ld 0,16(1)
    mtlr 0
    blr
.Lhelper:
    addi 3,3,1
    blr
