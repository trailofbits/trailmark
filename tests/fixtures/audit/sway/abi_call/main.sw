script;
abi Wallet {
    fn deposit(amount: u64);
}
fn main() {
    let caller = abi(Wallet, 0x9299da6c73e6dc03eeabcce242bb347de3f5f56cd1c70926d76526d7ed199b8b);
    caller.deposit { gas: 10000 }(10);
}
