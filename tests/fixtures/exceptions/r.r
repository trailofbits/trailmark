f <- function() {
    on.exit(cleanup())
    tryCatch(stop("bad"), error = function(e) observe(), finally = cleanup())
}
