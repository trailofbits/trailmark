module 0x42::control {
    fun helper() {}
    public fun run(x: u64) {
        if (x > 0) { helper(); };
        while (x > 0) { helper(); };
        loop { break; };
    }
    public fun signature(a: u64, b: &signer): u64 { return a; }
}
