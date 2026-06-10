include "common.thrift"

namespace py example.auth

struct LoginRequest {
  1: string token
}

service AuthService {
  bool login(1: LoginRequest req)
}
