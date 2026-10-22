fn @"helper"() void {}
const @"Server" = struct { pub fn @"run"() void {} };
pub fn @"main"() void {
    helper();
    Server.run();
    @"helper"();
    @"Server".@"run"();
}
