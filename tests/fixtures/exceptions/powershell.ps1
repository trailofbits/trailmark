function f {
    try { throw "bad" }
    catch { observe }
    finally { cleanup }
}
