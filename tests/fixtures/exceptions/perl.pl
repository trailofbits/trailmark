sub f {
    eval { die "bad"; };
    observe();
}
