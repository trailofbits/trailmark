script;
struct Worker { value: u64 }
impl Worker {
    fn helper(self) -> u64 { self.value }
    fn run(self) -> u64 { self.helper() }
}
fn helper(value: u64) -> u64 { value }
fn main() -> u64 {
    helper(10)
}
