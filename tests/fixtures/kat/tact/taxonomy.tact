import "@stdlib/deploy";

struct Ping {
    value: Int as uint32;
}

contract Wallet {
    init(owner: Address) {
        self.reply();
    }

    receive("ping") {
        self.reply();
    }

    external(msg: Ping) {
        reply();
    }

    bounced(msg: bounced<Ping>) {
        reply();
    }

    fun reply() {
        return;
    }
}

fun top() {
}
