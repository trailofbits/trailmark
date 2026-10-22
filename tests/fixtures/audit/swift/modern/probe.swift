protocol Worker {
    func work(_ input: String) async throws -> String
}
actor Vault {
    func read() async -> String { "ok" }
}
struct Outer {
    struct Inner { func ping() {} }
    init() {}
}
func collect(from input: String, fallback: String = "", transform: (String) -> String) async throws -> String? {
    transform(input)
}
func overloaded(_ input: Int) {}
func overloaded(_ input: String) {}
@main struct App {
    static func main() async { await Vault().read(); overloaded(1) }
}
