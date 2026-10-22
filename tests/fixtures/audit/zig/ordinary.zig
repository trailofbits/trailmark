const dep = @import("dep.zig");
const Server = struct {
    pub fn run(value: u32) u32 { return helper(value); }
    const Inner = struct {
        pub fn run() u32 { return Server.run(1); }
    };
};
fn helper(value: u32) u32 { return value; }
pub fn main() void {
    _ = Server.run(1);
    _ = Server.Inner.run();
    _ = Server . run(2);
    _ = helper(3);
    _ = dep.run(4);
    _ = @sizeOf(u32);
    _ = @call(.auto, helper, .{5});
}
export fn api(value: u32) u32 { return helper(value); }
extern "c" fn named(value: u32) u32;
extern "c" fn unnamed(u32, [*]const u8, usize) c_int;
extern "c" fn variadic(format: [*:0]const u8, ...) c_int;
fn ffi_calls() void { _ = unnamed(1, "x", 1); _ = variadic("x"); }
