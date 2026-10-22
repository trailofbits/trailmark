module 0x42::callback {
    fun handler(x: u64): u64 { return x; }
    inline fun run(handler: |u64|u64): u64 { handler(1) }
}
