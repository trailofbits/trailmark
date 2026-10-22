package inlineif
import rego.v1
allow if helper(input.user)
helper(user) if { user == "alice" }
