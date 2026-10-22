func secretSink() {}
func install() -> () -> Void {
    func neverCalled() { secretSink() }
    let deferred = {
        if Bool.random() { secretSink() }
    }
    return deferred
}
