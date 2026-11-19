module Bounds {
  predicate Within(x: int) { 0 <= x <= 255 }
  function Clamp(x: int): int { if x < 0 then 0 else if x > 255 then 255 else x }
  lemma ClampWithin(x: int) ensures Within(Clamp(x)) {}
  lemma {:axiom} Trusted(x: int) ensures Within(x)
  method Normalize(x: int) returns (y: int) ensures Within(y) {
    ClampWithin(x);
    y := Clamp(x);
    assert Within(y);
  }
  method Apply(Clamp: int -> int, x: int) returns (y: int) { y := Clamp(x); }
}
