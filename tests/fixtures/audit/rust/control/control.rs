#![allow(dead_code)]
fn leaf() {}
fn id<T>(value: T) -> T { value }
mod inner { pub fn helper() { super::leaf(); } }
use self::inner::helper as alias;
struct S;
trait T { fn declaration(&self); fn defaulted() { leaf(); } }
impl S { fn associated() { leaf(); } }
async fn worker() { leaf(); }
fn ordinary(value: bool) {
    fn local() { leaf(); }
    if value { local(); } else { alias(); }
    S::associated();
    id::<u32>(1);
    let closure = || leaf();
    closure();
    let _future = async { leaf(); worker().await; };
    let _mapped: Vec<_> = [1].iter().map(|x| id(*x)).collect();
    let _opaque = format!("{}", 1);
}
