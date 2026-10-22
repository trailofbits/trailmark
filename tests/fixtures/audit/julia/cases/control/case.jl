module Demo
struct Box{T}
    value::T
end
leaf(x::Int=1)::Int = x
function work(xs::Int...; fallback=leaf())::Int
    closure = x -> leaf(x)
    map([1]) do item
        if item > 0
            leaf(item)
        end
    end
    closure(fallback)
end
f(x::Int) = x
f(x::String) = x
main() = work(1)
end
