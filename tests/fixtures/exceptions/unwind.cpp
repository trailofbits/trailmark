volatile int cleaned = 0;

extern "C" void cleanup() {
    ++cleaned;
}

extern "C" void may_throw() {
    throw 7;
}

struct Guard {
    ~Guard() { cleanup(); }
};

extern "C" int guarded() {
    try {
        Guard guard;
        may_throw();
        return 0;
    } catch (int) {
        return 1;
    }
}

int main() {
    return guarded();
}
