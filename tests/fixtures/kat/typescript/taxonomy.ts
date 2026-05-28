// Comprehensive TypeScript feature taxonomy fixture.
// Exercises functions, classes, interfaces, enums, generics, optional
// params, type aliases, throws, async, branches, and call edges.

import { readFile } from "fs/promises";

export const CONSTANT: number = 42;

export type Pair<A, B> = { first: A; second: B };

export interface Greeter {
    greet(name: string): string;
}

export enum Status {
    Pending = "pending",
    Done = "done",
}

export function add(a: number, b: number): number {
    return a + b;
}

export function branchy(value: number, mode: string): number {
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

export const square = (n: number): number => n * n;

export async function fetchAsync(url: string): Promise<string> {
    return url;
}

export class Animal {
    public name: string;
    public species: string = "unknown";

    constructor(name: string) {
        this.name = name;
    }

    describe(): string {
        return `${this.name} the ${this.species}`;
    }
}

export class Dog extends Animal implements Greeter {
    public breed: string | null;

    constructor(name: string, breed: string | null = null) {
        super(name);
        this.species = "dog";
        this.breed = breed;
    }

    bark(loud: boolean = false): string {
        if (loud) {
            throw new Error("too loud");
        }
        return `${this.name}: woof`;
    }

    greet(name: string): string {
        return `${this.name} greets ${name}`;
    }
}

export function useAnimal(d: Dog): string {
    return d.bark(false);
}
