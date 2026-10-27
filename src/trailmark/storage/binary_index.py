"""Interval lookup shared by native ingestion and graph queries."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from typing import Protocol


class AddressSpan(Protocol):
    @property
    def start(self) -> int: ...

    @property
    def end(self) -> int: ...


class IntervalIndex[T: AddressSpan]:
    def __init__(self, rows: Iterable[T]) -> None:
        self.rows = sorted(rows, key=lambda row: (row.start, row.end))
        self.starts = [row.start for row in self.rows]
        self.ends: list[int] = []
        maximum = -1
        for row in self.rows:
            maximum = max(maximum, row.end)
            self.ends.append(maximum)

    def overlapping(self, start: int, end: int) -> list[T]:
        left = bisect_right(self.ends, start)
        right = bisect_left(self.starts, end)
        return [row for row in self.rows[left:right] if row.end > start]

    def containing(self, start: int, end: int) -> list[T]:
        return [
            row for row in self.overlapping(start, end) if row.start <= start and end <= row.end
        ]
