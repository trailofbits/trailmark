fn destructure(pair: struct { *const fn() void, u32 }) void {
    const helper, const count = pair;
    _ = count;
    helper();
}
fn control(pair: struct { *const fn() void, u32 }) void {
    const helper = pair[0];
    helper();
}
