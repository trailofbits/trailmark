%% Comprehensive Erlang feature taxonomy fixture.

-module(taxonomy).

-export([add/2, branchy/2, describe/1, bark/2, use_animal/1]).

-define(CONSTANT, 42).

-record(animal, {name, species = "unknown"}).
-record(dog, {base, breed}).

add(A, B) ->
    A + B.

branchy(Value, Mode) when Value > 0 ->
    Total = Value + sum_even(Value),
    Reduced = reduce(Total),
    parse_or_error(Mode, Reduced);
branchy(Value, Mode) when Value < 0 ->
    Total = -Value + sum_even(Value),
    Reduced = reduce(Total),
    parse_or_error(Mode, Reduced);
branchy(_Value, Mode) ->
    parse_or_error(Mode, 0).

sum_even(Value) ->
    lists:sum([I || I <- lists:seq(0, Value - 1), I rem 2 =:= 0]).

reduce(N) when N > 100 ->
    reduce(N div 2);
reduce(N) ->
    N.

parse_or_error(Mode, Total) ->
    case string:to_integer(Mode) of
        {Int, _} when is_integer(Int) -> {ok, Int + Total};
        _ -> {error, "bad mode"}
    end.

new_animal(Name) ->
    #animal{name = Name}.

describe(#animal{name = Name, species = Species}) ->
    Name ++ " the " ++ Species.

new_dog(Name, Breed) ->
    Base = (new_animal(Name))#animal{species = "dog"},
    #dog{base = Base, breed = Breed}.

bark(#dog{base = #animal{name = Name}}, Loud) ->
    case Loud of
        true -> {error, "too loud"};
        false -> {ok, Name ++ ": woof"}
    end.

use_animal(Dog) ->
    bark(Dog, false).
