<?php
// Comprehensive PHP feature taxonomy fixture.

namespace Taxonomy;

const CONSTANT = 42;

interface Greeter {
    public function greet(string $name): string;
}

enum Status: string {
    case Pending = 'pending';
    case Done = 'done';
}

function add(int $a, int $b): int {
    return $a + $b;
}

function branchy(int $value, string $mode): int {
    $total = 0;
    if ($value > 0) {
        $total += $value;
    } elseif ($value < 0) {
        $total -= $value;
    }
    for ($i = 0; $i < $value; $i++) {
        if ($i % 2 === 0) {
            $total += $i;
        }
    }
    while ($total > 100) {
        $total = intdiv($total, 2);
    }
    try {
        return intval($mode) + $total;
    } catch (\Exception $e) {
        throw new \RuntimeException("bad mode");
    }
}

class Animal {
    public string $name;
    public string $species = 'unknown';

    public function __construct(string $name) {
        $this->name = $name;
    }

    public function describe(): string {
        return "{$this->name} the {$this->species}";
    }
}

class Dog extends Animal implements Greeter {
    public ?string $breed;

    public function __construct(string $name, ?string $breed = null) {
        parent::__construct($name);
        $this->species = 'dog';
        $this->breed = $breed;
    }

    public function bark(bool $loud = false): string {
        if ($loud) {
            throw new \RuntimeException("too loud");
        }
        return "{$this->name}: woof";
    }

    public function greet(string $name): string {
        return "{$this->name} greets {$name}";
    }
}

function use_animal(Dog $d): string {
    return $d->bark(false);
}
