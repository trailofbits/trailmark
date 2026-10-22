package qualifieddata
import rego.v1
allow if { data.library.helper(input.user) }
