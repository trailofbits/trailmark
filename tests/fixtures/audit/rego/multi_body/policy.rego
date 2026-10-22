package multibody
import rego.v1

allow if { primary(input.user) } else if { fallback(input.user) }
result := helper(input.user) if { true }
primary(user) if { user == "alice" }
fallback(user) if { user == "bob" }
helper(user) := user if { true }
