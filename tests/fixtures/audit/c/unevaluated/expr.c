int secret(void) { return 99; }
int selected(void) { return 1; }
int rejected(void) { return 2; }
unsigned long only_size(void) { return sizeof(secret()); }
int choice(void) { return _Generic(0, int: selected(), default: rejected()); }
