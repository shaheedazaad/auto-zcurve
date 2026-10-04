# Exercise the standalone dependency probe without changing installed packages.
for (missing_packages in list(character(), c("yaml", "zcurve"))) {
  probe <- new.env(parent = baseenv())
  requested <- character()
  exit_status <- 0L
  probe$requireNamespace <- function(package, quietly) {
    stopifnot(quietly)
    requested <<- c(requested, package)
    !(package %in% missing_packages)
  }
  probe$quit <- function(status) { exit_status <<- status }
  output <- capture.output(sys.source("scripts/preflight.R", envir = probe))
  payload <- jsonlite::fromJSON(paste(output, collapse = "\n"))
  missing <- as.character(unlist(payload$missing, use.names = FALSE))
  stopifnot(identical(payload$ok, !length(missing_packages)), identical(missing, missing_packages))
  stopifnot(identical(exit_status, if (length(missing_packages)) 1 else 0L))
  stopifnot(identical(requested, c("dplyr", "jsonlite", "knitr", "purrr", "readr", "rmarkdown", "tibble", "yaml", "zcurve")))
}
cat("R dependency probe tests passed\n")
