fn helper(x: Int) -> Int { x }
fn labeled(helper callback: fn(Int) -> Int) -> Int { callback(1) }
fn another(label helper: fn(Int) -> Int) -> Int { helper(1) }
