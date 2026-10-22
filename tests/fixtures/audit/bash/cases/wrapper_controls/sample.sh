#!/usr/bin/env bash
helper() { :; }
main() { command helper; builtin helper; command -v eval; helper; }
main
