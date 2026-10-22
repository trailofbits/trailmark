pragma circom 2.1.0;
function inner() { return 2; }
function outer(n) { return n + 1; }
template Child(N) {
    signal input in[N];
    signal output out;
    var sum = 0;
    for (var i = 0; i < N; i++) { sum += in[i]; }
    out <== sum;
}
component main {public [in]} = Child(outer(inner()));
