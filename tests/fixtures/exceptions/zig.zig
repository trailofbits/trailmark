fn f() !void {
    defer cleanup();
    errdefer cleanup();
    try helper();
    helper() catch |err| cleanup();
    return error.Bad;
}
