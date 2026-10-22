fn inner_action() void {}
const Server = struct { pub fn run(self: Server) void { _ = self; } };
fn dynamic(helper: *const fn() void, receiver: Server) void {
    helper();
    receiver.run();
}
fn captured(callbacks: []const *const fn() void) void {
    for (callbacks) |helper| { helper(); }
}
fn local() void { const helper = @import("dep.zig").run; _ = helper(1); }
fn Factory(comptime T: type) type {
    return struct {
        pub fn run(value: T) T { if (true) { inner_action(); } return value; }
    };
}
fn instantiate() void { _ = Factory(u32).run(1); }
comptime { inner_action(); }
test "not extracted" { inner_action(); }
