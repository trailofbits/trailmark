record Receipt(String input) {
    Receipt { if (input == null) throw new IllegalArgumentException(); }
    String normalized() { return input.trim(); }
}
class Declarations {
    static class Nested { void work() { } }
    void overloaded(int value) { intSink(); }
    void overloaded(String value) { stringSink(); }
    void intSink() { }
    void stringSink() { }
    void plain() { intSink(); }
    void explicitThis() { this.intSink(); }
    void receiver(Declarations target) { target.intSink(); }
    void ambiguous() { overloaded(1); }
    Runnable reference() { return this::intSink; }
}
