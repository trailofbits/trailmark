helper <- function(x, y = 1, ...) { if (x > 0) x else y }
(function(x) helper(x)) -> other
main = function(x = helper(1)) {
  inner <- function(z) { if (z) helper(z) else other(z) }
  inner(x)
}
short <- \(x) helper(x)
1 |> helper(x = _)
raw <- r"(hello)"
main()
