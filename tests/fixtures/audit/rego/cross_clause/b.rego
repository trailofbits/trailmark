package crossclause
import rego.v1
allow if { second(input.user) }
second(user) if { user == "bob" }
