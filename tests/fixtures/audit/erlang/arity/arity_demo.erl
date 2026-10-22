-module(arity_demo).
-export([run0/0, run1/0, choose/1]).
-spec choose() -> atom().
choose() -> zero_sink().
-spec choose(integer()) -> integer().
choose(X) when is_integer(X) -> one_sink(X).
run0() -> choose().
run1() -> choose(1).
zero_sink() -> zero.
one_sink(X) -> X.
clauses(0) -> zero_sink();
clauses(X) -> one_sink(X).
