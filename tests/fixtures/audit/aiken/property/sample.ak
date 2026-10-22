use aiken/fuzz
fn identity(x: Int) -> Int { x }
fn my_fuzzer() { fuzz.int() }
test prop(x: Int via my_fuzzer()) { identity(x) == x }
