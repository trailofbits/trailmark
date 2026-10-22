const Actions = struct { fn helper() void {} };
fn labeled_switch(initial: u8) void {
    state: switch (initial) {
        0 => continue :state 1,
        else => Actions.helper(),
    }
}
fn named_cc() callconv(.c) void {}
