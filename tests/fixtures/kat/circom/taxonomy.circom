pragma circom 2.0.0;

// Comprehensive Circom feature taxonomy fixture.

template IsZero() {
    signal input in;
    signal output out;

    signal inv;
    inv <-- in != 0 ? 1 / in : 0;
    out <== -in * inv + 1;
    in * out === 0;
}

template Branchy(N) {
    signal input value;
    signal input mode;
    signal output out;

    signal accum[N + 1];
    accum[0] <== 0;
    for (var i = 0; i < N; i++) {
        accum[i + 1] <== accum[i] + value;
    }
    out <== accum[N] + mode;
}

template Adder() {
    signal input a;
    signal input b;
    signal output sum;

    sum <== a + b;
}

template Main() {
    signal input x;
    signal input y;
    signal output result;

    component adder = Adder();
    adder.a <== x;
    adder.b <== y;

    component branchy = Branchy(4);
    branchy.value <== adder.sum;
    branchy.mode <== 1;

    result <== branchy.out;
}

component main = Main();
