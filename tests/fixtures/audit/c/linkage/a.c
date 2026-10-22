static int hidden(void) { return 7; }
int external_callback(void) { return 11; }
int own(void) { return hidden(); }
