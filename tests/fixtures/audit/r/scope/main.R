helper <- function() "global"
main <- function() {
  helper()
  helper <- function() "local"
  helper()
}
main()
