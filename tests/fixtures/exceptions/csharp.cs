class App {
    void f() {
        try { throw new Bad(); }
        catch (Bad error) when (check(error)) { observe(); throw; }
        finally { cleanup(); }
    }
}
