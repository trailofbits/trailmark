function f(): void {
    try { throw new Bad(); }
    catch (error: unknown) { observe(); throw error; }
    finally { cleanup(); }
}
