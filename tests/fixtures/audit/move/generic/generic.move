module 0x42::generic {
    fun id<T>(x: T): T { return x; }
    public fun run() {
        id(1);
        id<u64>(1);
    }
}
