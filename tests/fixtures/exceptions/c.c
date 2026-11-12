#include <setjmp.h>
jmp_buf env;
void f(void) {
    if (setjmp(env)) observe();
    else longjmp(env, 1);
}
