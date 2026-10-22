#include "api.h"
int helper(int value) { return value; }
int exported(int value) { return helper(value); }
__attribute__((visibility("default"))) int direct_export(int value) { return helper(value); }
static int internal(void) { return 1; }
int ordinary(void) { return internal(); }
int main(void) { return exported(1); }
