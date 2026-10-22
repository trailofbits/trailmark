int object(void) { return 2; }
struct object { int member; };
int call_object(void) { return object(); }
