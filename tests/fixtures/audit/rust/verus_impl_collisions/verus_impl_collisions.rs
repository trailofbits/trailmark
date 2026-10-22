verus! {
fn first_sink() {}
fn second_sink() {}
trait First { fn act(); }
trait Second { fn act(); }
struct Subject;
impl First for Subject { fn act() { first_sink(); } }
impl Second for Subject { fn act() { if true { second_sink(); } } }
fn first_entry() { <Subject as First>::act(); }
fn second_entry() { <Subject as Second>::act(); }
struct Wrapper<T>(T);
impl Wrapper<u8> { fn run() { first_sink(); } }
impl Wrapper<u16> { fn run() { second_sink(); } }
fn generic_entry() { Wrapper::<u8>::run(); Wrapper::<u16>::run(); }
}
