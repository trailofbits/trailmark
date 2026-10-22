struct Handler
    scale::Int
end
struct ShortHandler end
leaf(x) = x
function (h::Handler)(x)
    if x > 0
        leaf(x)
    else
        leaf(0)
    end
end
(h::ShortHandler)(x) = leaf(x)
main() = Handler(1)(2)
