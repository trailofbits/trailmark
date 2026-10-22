helper() { printf ok; }
main() { local x=${ helper; }; printf "%s\n" "$x"; }
main
