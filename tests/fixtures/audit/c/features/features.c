typedef int (*Callback)(int);
typedef struct { int x; } Point;
union Value { int i; double d; };
int callee(int x) { return x; }
const char *pointer_return(const char *restrict text, int values[static 4], int (*cb)(int), Callback alias) {
    (void)values;
    (void)cb;
    (void)alias;
    Point p = {.x = callee(3)};
    return p.x ? text : "";
}
#define WRAP(x) callee(x)
int macro_user(void) { return WRAP(1); }
#ifdef FEATURE
int feature_on(void) { return callee(1); }
#else
int feature_off(void) { return callee(2); }
#endif
