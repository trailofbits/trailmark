pragma circom 2.1.0;
include "lib/child.circom";
template Root() {
    signal input in[2];
    signal output out;
    component child = Child(width());
    child.in <== in;
    out <== child.out;
}
component main {public [in]} = Child(width());
