module 0x42::grouped {
    use 0x43::library::{Self, transfer as imported_transfer};
    public fun run() { imported_transfer(); library::transfer(); }
}
