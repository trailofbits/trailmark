/* SPDX-License-Identifier: Apache-2.0 */
/* Exercise ARM/Thumb interworking without executing the image. */
__attribute__((target("thumb"), noinline)) int thumb_callee(int value) {
    return value + 7;
}
__attribute__((target("arm"), noinline)) int arm_caller(int value) {
    return thumb_callee(value) + 3;
}
