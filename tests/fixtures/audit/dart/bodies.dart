void bodySink() {}
void arrow() => bodySink();
Future<void> asyncArrow() async => bodySink();
Future<void> asyncBlock() async { bodySink(); }
class C {
  void arrow() => bodySink();
  void block() { bodySink(); }
}
extension Extra on String {
  void extra() { bodySink(); }
}
enum Status {
  ready;
  void run() { bodySink(); }
}
@pragma('vm:entry-point')
void nativeCallback(String data) { bodySink(); }
void main() { arrow(); }
