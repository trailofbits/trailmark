class App {
    void f() throws Declared {
        try { throw new Bad(); }
        catch (Bad | Other error) { observe(); throw error; }
        finally { cleanup(); }
    }
}
