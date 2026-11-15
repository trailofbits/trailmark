import Std

namespace Example

/-- A coordinate pair. -/
structure Point where
  x : Nat
  y : Nat

class Sized (α : Type) where
  size : α → Nat

inductive Color where
  | red
  | blue

/-- Increment a natural number. -/
def inc (n : Nat) : Nat := n + 1

def choose (n : Nat) : Nat := if n > 0 then inc n else 0

def apply {α : Type} (f : α → α) (x : α) : α := f x

theorem reflexive (n : Nat) : n = n := by rfl

end Example

def main : IO Unit := IO.println (Example.choose 1)
