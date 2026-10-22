extern "C" {
void block_export() {}
}
extern "C" void direct_export() {}
__attribute__((visibility("default")))
void attribute_export() {}
void ordinary() {}
static void internal() {}
