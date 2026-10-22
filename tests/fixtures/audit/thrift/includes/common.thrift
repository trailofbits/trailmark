namespace py audit.common
struct Request { 1: string token }
exception Failure { 1: string reason }
service Base { bool ping() }
