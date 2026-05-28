"""Comprehensive Python feature taxonomy fixture.

Used by the KAT corpus to lock in the parser's canonical output across
functions, classes, methods, decorators, async, generators, control flow,
exceptions, type hints, and imports. Do not edit lightly — every change
will require regenerating the matching .expected.json snapshot.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Optional


CONSTANT = 42


def add(a: int, b: int) -> int:
    """Sum two integers."""
    return a + b


def branchy(value: int, mode: str) -> int:
    """Function with multiple branch types for complexity counting."""
    total = 0
    if value > 0:
        total += value
    elif value < 0:
        total -= value
    for i in range(value):
        if i % 2 == 0:
            total += i
    while total > 100:
        total //= 2
    try:
        return int(mode) + total
    except ValueError:
        raise RuntimeError("bad mode") from None


async def fetch_async(url: str) -> str:
    """An async function — kind should be 'function', not 'method'."""
    return url


def counter(start: int = 0) -> Iterator[int]:
    """A generator function."""
    n = start
    while True:
        yield n
        n += 1


class Animal:
    """Base class with a method and a docstring."""

    species: str = "unknown"

    def __init__(self, name: str) -> None:
        self.name = name

    def describe(self) -> str:
        return f"{self.name} the {self.species}"


class Dog(Animal):
    """Subclass exercising inheritance edge extraction."""

    species = "dog"

    def __init__(self, name: str, breed: Optional[str] = None) -> None:
        super().__init__(name)
        self.breed = breed

    def bark(self, loud: bool = False) -> str:
        if loud:
            raise ValueError("too loud")
        return f"{self.name}: woof"


def use_animal(d: Dog) -> str:
    """Call-edge target test: makes calls to Dog and bark()."""
    msg = d.bark(loud=False)
    return json.dumps({"msg": msg})
