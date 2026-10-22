void sink() {}
void parameterShadow(void Function() sink) { sink(); }
void localShadow() {
  void sink() {}
  sink();
}
class Handler {
  void sink() {}
  void implicit() { sink(); }
  void explicit() { this.sink(); }
}
void deferred() {
  final callback = () { if (true) { sink(); } };
}
