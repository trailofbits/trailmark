struct item { int member; };
int item(void) { return 1; }
int use(struct item *p) { return item() + p->member; }
