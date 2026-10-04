# Run from the repository root with covr installed in the active R library.
if (!requireNamespace("covr", quietly = TRUE)) {
  stop("Install the R package 'covr' before running this coverage check.", call. = FALSE)
}
source_files <- list.files("R", pattern = "[.]R$", full.names = TRUE)
test_files <- list.files("tests/r", pattern = "^test_.*[.]R$", full.names = TRUE)
if (!length(source_files) || !length(test_files)) {
  stop("Run this command from the repository root.", call. = FALSE)
}
coverage <- covr::file_coverage(source_files, test_files)
print(coverage)
arguments <- commandArgs(trailingOnly = TRUE)
if (length(arguments)) {
  write.csv(as.data.frame(coverage), arguments[[1]], row.names = FALSE)
}
if (covr::percent_coverage(coverage) < 100) {
  stop("R source coverage is below 100%.", call. = FALSE)
}
