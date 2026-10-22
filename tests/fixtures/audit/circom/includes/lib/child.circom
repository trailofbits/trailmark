pragma circom 2.1.0;
function width() { return 2; }
template Child(N) {
    signal input in[N];
    signal output out;
    var sum = 0;
    for (var i = 0; i < N; i++) { sum += in[i]; }
    out <== sum;
}
