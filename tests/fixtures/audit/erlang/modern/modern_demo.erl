-module(modern_demo).
-export([maps/1, binaries/1, lists/1, maybe_value/1, zip/2, strict/1]).
maps(M) -> M#{key => helper(maps:get(key, M, 0))}.
binaries(B) -> << <<(helper(X))>> || <<X>> <= B >>.
lists(L) -> [helper(X) || X <- L, X > 0].
maybe_value(X) -> maybe {ok, V} ?= decode(X), helper(V) else error -> failed end.
zip(L1, L2) -> [{helper(X), Y} || X <- L1 && Y <- L2].
strict(L) -> [helper(X) || X <:- L].
helper(X) -> X.
decode(X) -> {ok, X}.
