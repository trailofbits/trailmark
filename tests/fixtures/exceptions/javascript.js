function f() {
    try { throw new Bad(); }
    catch (error) { observe(); throw error; }
    finally { cleanup(); }
}
