package controls
import rego.v1
import data.library.helper as ext
import data.library as lib

default allow := false
allow if { local(input.user) }
local(user) if { user == "alice" }
deny contains msg if { msg := sprintf("no %s", [input.user]) }
reasons[msg] := true if { msg := "no" }
violation contains "reason" if { input.bad }
query_result if { approved }
approved if { true }
aliased if { ext(input.user) }
qualified if { lib.helper(input.user) }
