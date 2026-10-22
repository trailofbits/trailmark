fun check(): Int {
    return 1;
}

fun onlyGlobal(): Int {
    return 2;
}

contract Wallet {
    init() {}

    fun check(): Int {
        return 3;
    }

    fun toString(): String {
        return "local";
    }

    receive("run") {
        check();
        self.check();
        onlyGlobal();
        let value: Int = 7;
        value.toString();
    }
}
