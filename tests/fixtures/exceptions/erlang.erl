-module(app).
f() ->
    try throw(bad)
    catch throw:X -> observe(), erlang:error(X)
    after cleanup()
    end.
