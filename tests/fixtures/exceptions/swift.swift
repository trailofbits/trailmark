func f() throws {
    defer { cleanup() }
    do { throw Bad.invalid }
    catch { observe(); throw error }
}
