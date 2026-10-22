message(0x12345678) Ping {
    value: Int as uint32;
}

struct Data {
    value: Int;
}

trait Policy {
    virtual fun allowed(value: Int): Bool {
        return value > 0;
    }

    receive("policy") {
        if (self.allowed(1)) {
            audit();
        }
    }
}

fun audit() {}

contract Wallet with Policy {
    value: Int;

    init(value: Int) {
        self.value = value;
    }

    get fun current(): Int {
        return self.value;
    }

    fun internalOnly(): Int {
        return self.value;
    }

    receive(msg: Ping) {
        self.value = msg.value;
    }

    receive("ping") {
        self.internalOnly();
    }

    external(msg: Slice) {
        audit();
    }

    bounced(msg: bounced<Ping>) {
        audit();
    }
}
