@interface GetMapping { String value() default ""; }
class InternalStore {
    void doGet(String cacheKey) { }
    static int doPost(int value) { return value; }
    void doDelete() { }
}
class Routes {
    @GetMapping("/value")
    String route() { return "ok"; }
    String helper() { return "ok"; }
}
