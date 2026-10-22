class Base {
    Base() { baseSink(); }
    static void baseSink() { }
}
class Construction extends Base {
    Construction() { this(1); }
    Construction(int value) { super(); constructorSink(); }
    static void constructorSink() { }
    static Construction make() { return new Construction(); }
}
