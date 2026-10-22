pragma circom 2.1.0;
template Check() {
    signal input in;
    signal output out;
    in * (in - 1) === 0;
    out <== in;
}
template Root() {
    signal input in;
    _ <== Check()(in);
}
component main = Root();
