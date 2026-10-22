contract;
storage {
    balance: u64 = 0,
}
impl Contract {
    #[storage(read, write)]
    fn deposit(amount: u64) {
        storage.balance.write(storage.balance.read() + amount);
    }
}
