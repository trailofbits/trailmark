#!/usr/bin/env bash
helper() { printf 'original\n'; }
main() {
    'source' ./library.bash
    helper
}
main
