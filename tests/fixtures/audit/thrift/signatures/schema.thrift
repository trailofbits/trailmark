namespace py audit.signatures

typedef string Token
enum Status { OK = 0, FAILED = 1 }
exception Failure { 1: string reason }
union Identity { 1: string email; 2: i64 account }
struct Request {
  1: required Token token
  2: optional list<Identity> identities
  3: i32 retries = 3
}
service Base {
  bool ping()
}
service Auth extends Base {
  Request login(1: Request request) throws (1: Failure failure)
  oneway void notify(1: string message)
  list<Request> batch(1: map<string, list<Request>> requests)
}
