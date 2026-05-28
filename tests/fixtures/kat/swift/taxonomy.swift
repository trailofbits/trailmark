// Comprehensive Swift feature taxonomy fixture.

import Foundation

let CONSTANT: Int = 42

protocol Greeter {
    func greet(_ name: String) -> String
}

enum Status {
    case pending
    case done(String)
}

func add(_ a: Int, _ b: Int) -> Int {
    return a + b
}

func branchy(value: Int, mode: String) throws -> Int {
    var total = 0
    if value > 0 {
        total += value
    } else if value < 0 {
        total -= value
    }
    for i in 0..<value {
        if i % 2 == 0 {
            total += i
        }
    }
    while total > 100 {
        total /= 2
    }
    guard let parsed = Int(mode) else {
        throw NSError(domain: "taxonomy", code: 1)
    }
    return parsed + total
}

class Animal {
    var name: String
    var species: String = "unknown"

    init(name: String) {
        self.name = name
    }

    func describe() -> String {
        return "\(name) the \(species)"
    }
}

class Dog: Animal, Greeter {
    var breed: String?

    init(name: String, breed: String? = nil) {
        self.breed = breed
        super.init(name: name)
        self.species = "dog"
    }

    func bark(loud: Bool = false) throws -> String {
        if loud {
            throw NSError(domain: "taxonomy", code: 2)
        }
        return "\(name): woof"
    }

    func greet(_ other: String) -> String {
        return "\(name) greets \(other)"
    }
}

func useAnimal(_ d: Dog) throws -> String {
    return try d.bark(loud: false)
}
