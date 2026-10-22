use aiken/fuzz
fn identity(x: Int) -> Int { x }
fn sampler(size: Int) { fuzz.int() }
bench identity_cost(x: Int via sampler) { identity(x) }
