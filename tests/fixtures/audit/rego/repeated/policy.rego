package repeated
import rego.v1

default allow := false
allow if { first(input.user); input.active }
allow if { second(input.user) }
first(user) if { user == "alice" }
second(user) if { user == "bob" }
