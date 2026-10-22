#!/usr/bin/env bash
helper() { printf 'original\n'; }
main() {
    \eval 'helper() { printf "replacement\n"; }'
    helper
}
main
