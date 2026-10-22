#!/usr/bin/env bash
helper() { printf 'helper\n'; }
function main {
    local value=one
    local captured=$(helper)
    helper | cat
    (helper)
    cat <(helper)
    if helper; then :; fi
    until false; do break; done
    select value in one; do helper; break; done
    cat <<'EOF'
$(not_a_command)
EOF
    cat <<EOF
$(helper)
EOF
}
outer() {
    local value=two
    inner() { if true; then printf '%s\n' "$value"; fi; }
    :
}
main "$@"
