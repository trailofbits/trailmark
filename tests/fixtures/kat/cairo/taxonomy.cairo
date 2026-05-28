// Comprehensive Cairo feature taxonomy fixture.

use core::starknet::ContractAddress;

const CONSTANT: u128 = 42;

#[derive(Drop)]
struct Animal {
    name: felt252,
    species: felt252,
}

#[derive(Drop)]
struct Dog {
    base: Animal,
    breed: felt252,
}

trait AnimalTrait {
    fn describe(self: @Animal) -> felt252;
}

impl AnimalImpl of AnimalTrait {
    fn describe(self: @Animal) -> felt252 {
        *self.name
    }
}

fn add(a: u128, b: u128) -> u128 {
    a + b
}

fn branchy(value: u128, mode: u128) -> u128 {
    let mut total: u128 = 0;
    if value > 100 {
        total = total + value;
    } else {
        total = total + 1;
    }
    let mut i: u128 = 0;
    loop {
        if i >= value {
            break;
        }
        if i % 2 == 0 {
            total = total + i;
        }
        i = i + 1;
    };
    while total > 100 {
        total = total / 2;
    };
    total + mode
}

fn make_animal(name: felt252) -> Animal {
    Animal { name, species: 'unknown' }
}

fn make_dog(name: felt252, breed: felt252) -> Dog {
    let mut base = make_animal(name);
    base.species = 'dog';
    Dog { base, breed }
}

fn bark(self: @Dog, loud: bool) -> felt252 {
    assert!(!loud, "too loud");
    *self.base.name
}

fn use_animal(d: @Dog) -> felt252 {
    bark(d, false)
}
