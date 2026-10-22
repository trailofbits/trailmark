leaf(x) = x
main() = :(leaf(1))
function quoted_block()
    quote
        if condition
            leaf(2)
        end
    end
end
