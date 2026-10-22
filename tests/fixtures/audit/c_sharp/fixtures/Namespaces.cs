namespace First {
public static class Same {
    public static void Run() { FirstOnly(); }
    public static void FirstOnly() { }
}
}
namespace Second {
public static class Same {
    public static void Run() { SecondOnly(); }
    public static void SecondOnly() { }
}
}
