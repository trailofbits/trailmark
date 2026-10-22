mod external;
fn helper() -> u32 { 100 }
fn direct_control() -> u32 { helper() }

mod local {
    fn helper() -> u32 { 1 }
    fn caller() -> u32 { helper() }
}

mod first {
    trait Calc { fn value() -> u32; }
    impl SameImpl of Calc {
        fn value() -> u32 { 11 }
    }
    struct Marker { number: u32 }
    enum Outcome { Good: (), Bad: () }
}
mod second {
    trait Calc { fn value() -> u32; }
    impl SameImpl of Calc {
        fn value() -> u32 { 22 }
    }
    struct Marker { number: u32 }
    enum Outcome { Good: (), Bad: () }
}
