contract;
abi Wallet {
    fn deposit(amount: u64);
}
impl Wallet for Contract {
    fn deposit(amount: u64) {
        sink(amount);
    }
}
fn sink(amount: u64) { }
