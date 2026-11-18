---- MODULE Counter ----
EXTENDS Naturals
VARIABLE n
CONSTANT Limit
Init == n = 0
Next == n' = n + 1
Inv == n <= Limit
Spec == Init /\ [][Next]_n
====
