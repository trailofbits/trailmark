helper <- function() "original"
replacement <- function() "replacement"
base::assign("helper", replacement)
main <- function() helper()
main()
