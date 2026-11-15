namespace Geometry

structure Dimensions where
  width : Nat
  height : Nat

def perimeter (d : Dimensions) : Nat :=
  d.width + d.height + d.width + d.height

def widen (d : Dimensions) (extra : Nat) : Dimensions :=
  { d with width := d.width + extra }

end Geometry

def divisibleByTwo (n : Nat) : Prop := n % 2 = 0

theorem two_is_even : divisibleByTwo 2 := by decide

theorem two_still_even : divisibleByTwo 2 := two_is_even
