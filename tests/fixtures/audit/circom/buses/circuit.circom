pragma circom 2.2.0;
bus Point() {
    signal x;
    signal y;
}
template CopyPoint() {
    input Point() in;
    output Point() out;
    out <== in;
}
component main {public [in]} = CopyPoint();
