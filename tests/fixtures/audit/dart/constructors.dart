void audit() {}
class Permit {
  final String token;
  Permit(this.token);
  Permit.named(this.token) { audit(); }
  factory Permit.create(String token) => Permit(token);
  String describe() => token;
  void send({required String target, int retries = 1}) { audit(); }
}
void main() {
  Permit('a');
  Permit.named('b');
  Permit.create('c');
}
