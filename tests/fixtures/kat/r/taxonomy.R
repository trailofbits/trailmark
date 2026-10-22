library(utils)
helper <- function(x, y = 1, ...) { if (x > 0) x + y else 0 }
main <- function() { helper(1); pkg::helper(2) }
lapply(xs, function(x) helper(x))
1 |> helper()
