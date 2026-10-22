#!/usr/bin/env bash
worker() { printf 'worker\n'; }
after() { printf 'after\n'; }
main() {
    coproc JOB { worker; }
    after
    wait "$JOB_PID"
}
main
