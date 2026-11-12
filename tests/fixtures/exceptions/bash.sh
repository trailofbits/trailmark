f() {
    trap cleanup ERR
    false || observe
}
