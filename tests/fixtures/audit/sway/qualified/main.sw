script;
fn helper() -> u64 { 1 }
fn main() -> u64 {
    unrelated::helper()
}
