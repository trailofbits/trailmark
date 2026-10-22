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
}
mod second {
    trait Calc { fn value() -> u32; }
    impl SameImpl of Calc {
        fn value() -> u32 { 22 }
    }
}

mod other { pub fn imported_target() -> u32 { 7 } }
use other::imported_target as imported;
fn alias_call() -> u32 { imported() }

mod nested {
    mod child { pub fn buried() -> u32 { 9 } }
    fn nested_call() -> u32 { child::buried() }
}

fn tuple_signature(pair: (u32, u32), array: [u32; 2]) -> (u32, u32) { pair }
fn closure_probe() -> u32 {
    let cb = || { if true { helper() } else { 0 } };
    cb()
}
fn shadow_closure() -> u32 {
    let helper = || 3;
    helper()
}

fn pattern_control(pair: (u32, u32)) -> u32 {
    let (a, b) = pair;
    match a {
        0 => helper(),
        _ => b,
    }
}
fn generic_control<T, +Drop<T>>(value: T) -> T { value }
