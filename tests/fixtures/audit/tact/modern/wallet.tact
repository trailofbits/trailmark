contract Wallet(value: Int as uint32) {
    receive("inc") {
        self.value += 1;
    }

    get fun current(): Int {
        return self.value;
    }
}

fun optional(value: Int?, values: map<Int, Int>): Int? {
    if (value != null) {
        return value;
    }
    return null;
}

asm fun add(a: Int, b: Int): Int { ADD }
