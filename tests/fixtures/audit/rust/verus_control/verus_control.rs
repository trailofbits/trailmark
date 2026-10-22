fn host() {}
verus! {
    spec fn twice(x: int) -> int { x + x }
    proof fn lemma(x: int)
        requires x >= 0,
        ensures twice(x) >= x,
    { assert(twice(x) >= x); }
    struct V;
    impl V { fn run() { host(); } }
    fn entry() { V::run(); host(); }
    mod inner { proof fn nested() {} }
}
