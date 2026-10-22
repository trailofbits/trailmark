use foreign/checks.{verify as imported}
fn helper(n: Int) -> Bool { if n > 0 { True } else { False } }
fn second(n: Int, m: Int) -> Int { n + m }
fn direct() { helper(1) }
fn pipeline(x: Int) { x |> second(1) |> second(2) }
fn parameter(helper: fn(Int) -> Bool) { helper(1) }
fn binding(x) { expect Some(helper) = x helper(1) }
fn imported_call() { imported(1) }
fn module_call() { checks.verify(1) }
fn nested() { let f = fn(n: Int) { if n > 0 { helper(n) } else { False } } True }
validator payments(owner: ByteArray) {
  mint(r, p, t) { helper(r) }
  spend(d, r, p, t) { helper(r) }
  withdraw(r, p, t) { helper(r) }
  publish(r, p, t) { helper(r) }
  vote(r, p, t) { helper(r) }
  propose(r, p, t) { helper(r) }
  else(_) { fail }
}
test unit() { payments.mint(#"", 1, #"", todo) }
