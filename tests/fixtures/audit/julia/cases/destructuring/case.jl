helper(x) = x
main((helper, item)) = helper(item)
properties((; helper)) = helper(1)
closure = ((helper, item),) -> helper(item)
