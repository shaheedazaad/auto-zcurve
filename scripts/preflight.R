required <- c(
  "dplyr",
  "jsonlite",
  "knitr",
  "purrr",
  "readr",
  "rmarkdown",
  "tibble",
  "yaml",
  "zcurve"
)

missing <- required[!vapply(required, requireNamespace, logical(1), quietly = TRUE)]

quote_json <- function(x) {
  paste0('"', gsub('"', '\\"', x, fixed = TRUE), '"')
}

missing_json <- paste(vapply(missing, quote_json, character(1)), collapse = ",")
cat(sprintf('{"ok":%s,"missing":[%s]}', if (length(missing) == 0) "true" else "false", missing_json))
cat("\n")

if (length(missing)) {
  quit(status = 1)
}
