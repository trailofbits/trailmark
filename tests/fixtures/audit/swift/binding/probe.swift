func secretSink() {}
func decoySink() {}
func helper() { decoySink() }
struct Service {
    func audit() { helper() }
    func helper() { secretSink() }
}
func shadowed(helper: () -> Void) { helper() }
