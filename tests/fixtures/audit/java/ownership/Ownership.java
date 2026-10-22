class Ownership {
    static int hits;
    static void sink() { hits++; }
    static Runnable deferred(boolean flag) {
        return () -> { if (flag) sink(); };
    }
    static void local(boolean flag) {
        class Hidden { void run() { if (flag) sink(); } }
    }
    static Runnable anonymous(boolean flag) {
        return new Runnable() { public void run() { if (flag) sink(); } };
    }
    public static void main(String[] args) {
        deferred(true);
        local(true);
        anonymous(true);
        System.out.println(hits);
    }
}
