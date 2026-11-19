module Taxonomy

let positive (x:int) : prop = x >= 0
let identity (x:int) : Tot int = x
let proof (x:nat) : Lemma (positive (identity x)) = ()
let run (x:int) : Tot int = identity x
let apply (identity: int -> Tot int) (x:int) : Tot int = identity x
assume val trusted : x:int -> Lemma (positive x)
