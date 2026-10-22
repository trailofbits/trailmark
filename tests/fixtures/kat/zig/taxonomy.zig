const std = @import("std");
const Mode = enum { read, write };
const Packet = union(enum) { number: u32, bytes: []const u8 };
const Server = struct {
    pub fn handle(self: *Server, n: u32) !u32 {
        if (n > 0) { return helper(n); }
        return 0;
    }
};
fn helper(n: u32) u32 { return n; }
pub fn main() void { _ = helper(1); }
export fn process(n: u32) u32 { return helper(n); }
extern "c" fn external_fn(n: u32) u32;
