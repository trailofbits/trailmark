fn host() {}
verus! {
    spec fn model(x: u64) -> bool { x > 0 }
    proof fn lemma() { host(); host(); }
    fn checked(x: u64) -> (r: u64)
        requires x < 100,
        ensures (r) > (x),
    {
        if model(x) { lemma(); }
        x + 1
    }
}
mod inner {
    fn helper() {}
    verus! { pub fn checked() { helper(); } }
}
verus! { fn last() { checked(1); } }
