script;
fn helper() -> u64 { 1 }
fn main() -> u64 {
    let mut index = 0;
    while index < 4 {
        index += helper();
    }
    index
}
