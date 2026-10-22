include "common.thrift"
namespace py audit.client
service Auth extends common.Base {
  common.Request login(1: common.Request request) throws (1: common.Failure failure)
}
