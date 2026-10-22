#!/usr/bin/env bash
helper() { :; }
main() {
    "unset" -f helper
    helper
}
