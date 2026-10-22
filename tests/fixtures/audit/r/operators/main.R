`%send%` <- function(x, y) helper(x)
helper <- function(x) x
main <- function() {
  1 %send% 2
  `%send%`(1, 2)
}
main()
