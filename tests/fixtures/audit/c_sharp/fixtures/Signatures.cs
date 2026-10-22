using System;
using System.Collections.Generic;
namespace Audit.Signatures;
public interface IRun { int Run(); }
public class Syntax<T> : IRun {
    public Syntax() { }
    public int Run() => Sink();
    public static U Identity<U>(U value) => value;
    public static int Sink() => 1;
    public int Generic() => Identity<int>(Sink());
    public void Nullable(Action? action) { action?.Invoke(); }
    public (int, string) Signature(ref int a, out string b, in int c, int d = 3, params string[] e) {
        b = ""; return (a, b);
    }
    public List<string>? Modern(List<string>? value = null) => value;
    public class Nested { public int Work() => Sink(); }
    public int Accessor { get => Sink(); set { Sink(); } }
}
public record Payload(string Value) { public int Read() => Syntax<object>.Sink(); }
public record struct Packet(int Value) { public int Read() => Value; }
public static class Overloads {
    public static void Send(int value) { }
    public static void Send(string value) { }
    public static void Caller() { Send(1); }
}
