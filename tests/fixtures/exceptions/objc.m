void f() {
    @try { @throw error; }
    @catch (Bad *error) { observe(); @throw; }
    @finally { cleanup(); }
}
