-module(closure_demo).
-export([capture/0, remote_capture/0, deferred/0, immediate/0, factory_call/0, direct/0]).
capture() -> fun sink/0.
remote_capture() -> fun external:sink/0.
deferred() -> fun(X) -> case X of true -> sink(); false -> ok end end.
immediate() -> (fun() -> sink() end)().
factory_call() -> (maker())().
maker() -> fun sink/0.
direct() -> sink().
sink() -> ok.
