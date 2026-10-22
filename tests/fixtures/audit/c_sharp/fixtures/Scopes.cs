using System;
namespace Audit.Scopes {
public static class Dispatch {
    public static void Send() { }
    public static void Sink() { }
    public static void ByParameter(Action Send) { Send(); }
    public static void ByLocal() { Action Send = () => { }; Send(); }
    public static void LocalShadow() { void Send() { } Send(); }
    public static void Deferred() {
        Action later = () => { if (DateTime.Now.Year > 0) Sink(); };
        void Never() { Sink(); }
    }
}
}
