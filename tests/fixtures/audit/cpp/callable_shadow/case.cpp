void callback() {}
void actual() {}
void parameter(void (*callback)()) { callback(); }
void pointer() { auto callback = &actual; callback(); }
void closure() { auto callback = [] { actual(); }; callback(); }
struct Callable { void operator()() const { actual(); } };
void object() { Callable callback; callback(); }
class Holder {
public:
    void callback() {}
    void method(void (*callback)()) { callback(); }
};
