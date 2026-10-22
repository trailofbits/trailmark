#![allow(dead_code)]
fn sensitive() {}
fn harmless() {}
fn parameter(sensitive: fn()) { sensitive(); }
fn generic<F: Fn()>(sensitive: F) { sensitive(); }
fn closure() { let sensitive = || {}; sensitive(); }
fn binding() { let sensitive = harmless; sensitive(); }
fn block() {
    sensitive();
    { let sensitive = || {}; sensitive(); }
    sensitive();
}
