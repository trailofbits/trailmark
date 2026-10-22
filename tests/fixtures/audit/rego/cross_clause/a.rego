package crossclause
import rego.v1
allow if { first(input.user); input.active }
first(user) if { user == "alice" }
