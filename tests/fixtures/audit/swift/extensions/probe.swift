protocol Handler {
    func handle(_ input: String) -> String
}
struct Service: Handler {
    func handle(_ input: String) -> String { input }
}
extension Service {
    func checked(_ input: String) -> String { sanitize(input) }
}
func sanitize(_ input: String) -> String { input }
