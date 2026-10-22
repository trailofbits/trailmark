void sink() {}
void outer() {
    auto never_called = [] { if (true) { sink(); } };
    struct Local {
        static void run() { if (true) { sink(); } }
    };
}
class Outer {
public:
    class Nested { public: void run() { sink(); } };
    template<class T> T identity(T x) { return x; }
};
