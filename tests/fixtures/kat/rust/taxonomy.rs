//! Comprehensive Rust feature taxonomy fixture.
//! Exercises functions, structs, enums, traits, impls, generics, branches,
//! Result/panic, async, and call edges.

use std::collections::HashMap;

pub const CONSTANT: i32 = 42;

pub trait Greeter {
    fn greet(&self, name: &str) -> String;
}

pub enum Status {
    Pending,
    Done(String),
}

pub fn add(a: i32, b: i32) -> i32 {
    a + b
}

pub fn branchy(value: i32, mode: &str) -> Result<i32, String> {
    let mut total = 0i32;
    if value > 0 {
        total += value;
    } else if value < 0 {
        total -= value;
    }
    for i in 0..value {
        if i % 2 == 0 {
            total += i;
        }
    }
    while total > 100 {
        total /= 2;
    }
    match mode.parse::<i32>() {
        Ok(n) => Ok(n + total),
        Err(_) => Err(String::from("bad mode")),
    }
}

pub async fn fetch_async(url: String) -> String {
    url
}

pub struct Animal {
    pub name: String,
    pub species: String,
}

impl Animal {
    pub fn new(name: String) -> Self {
        Self {
            name,
            species: String::from("unknown"),
        }
    }

    pub fn describe(&self) -> String {
        format!("{} the {}", self.name, self.species)
    }
}

pub struct Dog {
    pub base: Animal,
    pub breed: Option<String>,
}

impl Dog {
    pub fn new(name: String, breed: Option<String>) -> Self {
        let mut base = Animal::new(name);
        base.species = String::from("dog");
        Self { base, breed }
    }

    pub fn bark(&self, loud: bool) -> Result<String, String> {
        if loud {
            return Err(String::from("too loud"));
        }
        Ok(format!("{}: woof", self.base.name))
    }
}

impl Greeter for Dog {
    fn greet(&self, name: &str) -> String {
        format!("{} greets {}", self.base.name, name)
    }
}

pub fn use_animal(d: &Dog) -> Result<String, String> {
    d.bark(false)
}
