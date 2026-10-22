helper <- function() "original"
replacement <- function() "replacement"
assign("helper", replacement)
main <- function() helper()
main()
