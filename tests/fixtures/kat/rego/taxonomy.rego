package example.auth

import future.keywords.if

allow if {
    helper(input.action)
}

deny[msg] if {
    msg := "no"
}

helper(action) if {
    action == "read"
}
