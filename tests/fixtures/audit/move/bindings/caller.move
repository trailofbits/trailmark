module 0x42::caller {
    use 0x43::library;
    use 0x43::library as L;
    fun transfer() {}
    public fun run() {
        transfer();
        library::transfer();
        L::transfer();
        0x43::library::transfer();
    }
}
