sub helper { return 1; }
sub main {
    helper();
    helper 1;
    helper;
    &helper();
    &helper;
    goto &helper;
}
