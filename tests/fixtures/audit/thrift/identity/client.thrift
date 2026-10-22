include "a.thrift"
include "b.thrift"
namespace py client
namespace java audit.client
service Auth {
  bool loginA(1: a.Request request)
  bool loginB(1: b.Request request)
}
