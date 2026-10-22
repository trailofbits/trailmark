module Taxonomy
using LinearAlgebra
abstract type Shape end
struct Box{T}
    value::T
end
leaf(x::Int=1)::Int = x
function choose(x::Int; fallback=0)::Int
    if x > 0
        return leaf(x)
    else
        return fallback
    end
end
choose(x::String) = length(x)
callback = x -> leaf(x)
function main()
    for item in 1:3
        choose(item)
    end
    map([1, 2]) do item
        leaf(item)
    end
    callback(1)
end
main()
end
