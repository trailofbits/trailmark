void f() {
    try { throw new Bad(); }
    on Bad catch (error) { observe(); rethrow; }
    finally { cleanup(); }
}
