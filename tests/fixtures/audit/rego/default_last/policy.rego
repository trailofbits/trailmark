package defaultlast
import rego.v1

allow if { helper(input.user) }
default allow := false
helper(user) if { user == "alice" }
