fn helper(x: Int, y: Int) -> Int { x + y }
fn dormant() -> Bool { let _callback = helper(_, 1) True }
fn invoked() -> Int { let callback = helper(_, 1) callback(2) }
fn piped() -> Int { 2 |> helper(_, 1) }
fn passed() -> Bool { receives(helper) }
fn receives(_callback: fn(Int, Int) -> Int) -> Bool { True }
