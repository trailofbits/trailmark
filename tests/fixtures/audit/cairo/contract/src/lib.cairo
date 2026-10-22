#[starknet::interface]
trait ICounter<TContractState> {
    fn read(self: @TContractState) -> u32;
    fn write(ref self: TContractState, value: u32);
}
#[starknet::contract]
mod Counter {
    #[storage]
    struct Storage {}

    #[constructor]
    fn constructor(ref self: ContractState) {}

    #[abi(embed_v0)]
    impl CounterImpl of super::ICounter<ContractState> {
        fn read(self: @ContractState) -> u32 { 1 }
        fn write(ref self: ContractState, value: u32) {}
    }
    #[external(v0)]
    fn legacy_control(self: @ContractState) -> u32 { 2 }

    fn internal_control(self: @ContractState) -> u32 { 3 }

    #[l1_handler]
    fn from_l1(ref self: ContractState, from_address: felt252, value: felt252) {}

    #[abi(per_item)]
    #[generate_trait]
    impl PerItem of PerItemTrait {
        #[external(v0)]
        fn per_item_control(self: @ContractState) -> u32 { 4 }
        fn private_per_item(self: @ContractState) -> u32 { 5 }
    }
}
