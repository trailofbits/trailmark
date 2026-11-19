Definition identity (n : nat) : nat := n.
Definition use (n : nat) : nat := identity n.
Axiom trusted : forall n : nat, identity n = n.
Lemma identity_ok (n : nat) : identity n = n.
Proof. reflexivity. Qed.
