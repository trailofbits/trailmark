pragma circom 2.1.0;
function size(n) { if (n > 0) { return n; } return 1; }
template Multiply(N) {
    signal input x[N];
    signal output out;
    var accum = 0;
    for (var i = 0; i < N; i++) { accum += x[i]; }
    out <== accum;
}
template Wrapper() {
    signal input x[2];
    signal output out;
    out <== Multiply(size(2))(x);
}
component main {public [x]} = Wrapper();
