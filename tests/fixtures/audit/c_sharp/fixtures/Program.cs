using Microsoft.AspNetCore.Builder;
var app = WebApplication.CreateBuilder().Build();
app.MapGet("/method", Handlers.Route);
app.MapPost("/lambda", (string input) => Handlers.Sink(input));
public static class Handlers {
    public static string Route(string input) => Sink(input);
    public static string Sink(string input) => input;
}
