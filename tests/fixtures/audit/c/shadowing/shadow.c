int target(void) { return 7; }
int actual(void) { return 9; }
int dispatch(int (*target)(void)) { return target(); }
int explicit_indirect(int (*target)(void)) { return (*target)(); }
int local_shadow(void) { int (*target)(void) = actual; return target(); }
int main(void) { return dispatch(actual) + explicit_indirect(actual) + local_shadow(); }
