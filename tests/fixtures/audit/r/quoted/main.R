helper <- function() "never executed"
main <- function() {
  quote(helper())
  expression(helper())
  substitute(helper())
}
main()
