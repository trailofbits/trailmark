-module(caller).
-export([static_missing/0, static_known/0, dynamic/2, dynamic_fun/1, imported/1]).
-import(lists, [reverse/1]).
static_missing() -> absent:hit().
static_known() -> local_target:hit().
dynamic(M, F) -> M:hit(), local_target:F().
dynamic_fun(F) -> F().
imported(X) -> reverse(X).
