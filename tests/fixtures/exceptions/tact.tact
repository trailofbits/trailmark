fun f() {
    try { throw(1); }
    catch (error) { observe(); }
}
