helper <- function() 1
caller <- function(helper) { helper(); pkg::helper(); obj$helper() }
changed <- function() { helper <- unknown; helper() }
looper <- function() { for (helper in xs) helper() }
recursive <- function(x) { if (x) recursive(x - 1) }
