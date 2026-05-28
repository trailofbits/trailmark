// Comprehensive JavaScript feature taxonomy fixture.
// Locks in parser output across functions, classes, methods, arrow functions,
// imports, throws, branches, and call edges. Regenerate snapshot when output
// schema legitimately changes.

import { readFile } from "fs/promises";

export const CONSTANT = 42;

/**
 * Sum two numbers.
 */
export function add(a, b) {
    return a + b;
}

export function branchy(value, mode) {
    let total = 0;
    if (value > 0) {
        total += value;
    } else if (value < 0) {
        total -= value;
    }
    for (let i = 0; i < value; i++) {
        if (i % 2 === 0) {
            total += i;
        }
    }
    while (total > 100) {
        total = Math.floor(total / 2);
    }
    try {
        return parseInt(mode, 10) + total;
    } catch (e) {
        throw new Error("bad mode");
    }
}

export const square = (n) => n * n;

export async function fetchAsync(url) {
    return url;
}

export class Animal {
    constructor(name) {
        this.name = name;
        this.species = "unknown";
    }

    describe() {
        return `${this.name} the ${this.species}`;
    }
}

export class Dog extends Animal {
    constructor(name, breed = null) {
        super(name);
        this.species = "dog";
        this.breed = breed;
    }

    bark(loud = false) {
        if (loud) {
            throw new Error("too loud");
        }
        return `${this.name}: woof`;
    }
}

export function useAnimal(d) {
    return d.bark(false);
}
