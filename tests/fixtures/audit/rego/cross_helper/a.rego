package crosshelper
import rego.v1
allow if { helper(input.user) }
