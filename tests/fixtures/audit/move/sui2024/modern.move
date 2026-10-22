module demo::modern;

public struct Item has key { id: sui::object::UID }

public fun make(ctx: &mut sui::tx_context::TxContext): Item {
    Item { id: sui::object::new(ctx) }
}
