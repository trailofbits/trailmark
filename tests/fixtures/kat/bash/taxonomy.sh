#!/usr/bin/env bash
helper() { if [ "$1" = yes ]; then echo ok; fi; }
function main { helper yes | cat; x=$(helper no); }
main "$@"
