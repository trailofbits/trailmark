package main
func f() {
    defer cleanup()
    panic("bad")
}
