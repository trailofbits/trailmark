contract;

abi Wallet {
    fn deposit(amount: u64);
}

struct Balance {
    amount: u64
}

impl Wallet for Contract {
    fn deposit(amount: u64) {
        helper(amount);
    }
}

pub fn helper(amount: u64) -> u64 {
    if amount > 0 { amount } else { 0 }
}
