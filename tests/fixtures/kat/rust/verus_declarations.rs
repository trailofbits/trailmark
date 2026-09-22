verus! {
    use std::collections::HashMap;
    /// Generic storage.
    #[derive(Clone)]
    pub struct Store<T> { value: T }
    /// Optional value.
    enum Choice<T> { Some(T), None }
    /// Provides a default method.
    trait Check {
        fn valid(&self) -> bool { true }
    }
    impl<T> Check for Store<T> {
        fn valid(&self) -> bool { true }
    }
    impl<T> Store<T> {
        /// Retains the reference type.
        fn get(&self) -> (r: &T) { &self.value }
    }
    proof fn token(tracked value: u64) -> (tracked r: u64) { value }
    fn pair(x: u64) -> (u64, bool) { (x, true) }
    spec fn identity<T: Copy>(value: T) -> (r: T) { value }
    fn values() -> Vec<u64> { Vec::new() }
}
