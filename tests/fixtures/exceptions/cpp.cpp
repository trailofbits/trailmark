void f() {
    try { throw Bad(); }
    catch (const Bad& error) { observe(); throw; }
    catch (...) { cleanup(); }
}
