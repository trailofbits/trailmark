type Foo { value: Int }
fn positive(d: Data) -> Bool { if d is Foo { d.value > 0 } else { False } }
