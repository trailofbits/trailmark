#!/usr/bin/env bash
helper() { printf 'helper\n'; }
main() {
    time helper
    time -p helper | cat
}
main
