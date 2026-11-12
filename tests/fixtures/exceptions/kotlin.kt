fun f() {
    try { throw Bad() }
    catch (error: Bad) { observe(); throw error }
    finally { cleanup() }
}
