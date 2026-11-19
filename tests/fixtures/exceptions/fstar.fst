module Errors
exception Failure
let stop () = raise Failure
