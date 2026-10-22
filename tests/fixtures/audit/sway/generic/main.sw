library;
struct Wrapper<T> { value: T }
struct Other<T> { value: T }
impl<T> Wrapper<T> {
    fn get(self) -> u64 {
        first_sink()
    }
}
impl<T> Other<T> {
    fn get(self) -> u64 {
        second_sink()
    }
}
fn first_sink() -> u64 { 1 }
fn second_sink() -> u64 { 2 }
