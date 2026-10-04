source(file.path("R", "utils.R"))
source(file.path("R", "schema.R"))
source(file.path("R", "report.R"))
config <- list(meta_data = list(title = list(role = "citation"), identifier = list(role = "doi"), link = list(role = "url")), effects = list(p = list(role = "p_value"), tags = list()))
results <- list(
  list(status = "error", file_name = "failed.pdf"),
  list(status = "ok", file_name = "first.pdf", data = list(meta_data = list(title = "First", identifier = "10/example", link = "https://example.org"), effects = list(list(p = .01, tags = c("a", "b")), list(p = .02)))),
  list(status = "ok", file_name = "nested.pdf", data = list(meta_data = list(title = "Nested", effects = list(list(p = .03))))),
  list(status = "ok", file_name = "legacy.pdf", data = list(title = "Legacy", effects = list(list(p = .04)))),
  list(status = "ok", file_name = "empty.pdf", data = list())
)
stopifnot(nrow(flatten_results(list(), config)) == 0)
table <- flatten_results(results, config)
stopifnot(nrow(table) == 5, identical(table$source_name, c("first.pdf", "first.pdf", "nested.pdf", "legacy.pdf", "empty.pdf")))
stopifnot(identical(table$p[1:4], c(.01, .02, .03, .04)), is.na(table$p[[5]]))
stopifnot(identical(table$title[1:4], c("First", "First", "Nested", "Legacy")))
stopifnot(identical(jsonlite::fromJSON(table$tags[[1]]), c("a", "b")), all(is.na(table$tags[-1])))
refs <- build_reference_table(c(results, results[2]), config)
stopifnot(nrow(refs) == 4, identical(refs$citation, c("First", "Nested", "Legacy", NA_character_)), identical(refs$doi[[1]], "10/example"))
stopifnot(nrow(build_reference_table(list(), config)) == 0)
empty_config <- list(meta_data = list(), effects = list())
stopifnot(all(is.na(build_reference_table(results, empty_config)$citation)))

path <- tempfile(fileext = ".txt")
writeLines("Use {{reported_statistic_field}}, {{eligible_field}}, {{eligibility_explanation_field}}", path)
prompt_config <- list(meta_data = list(), effects = list(stat = list(role = "reported_statistic"), keep = list(role = "eligible"), reason = list(role = "eligibility_explanation")))
stopifnot(identical(build_system_prompt(prompt_config, path), "Use stat, keep, reason"))
stopifnot(identical(build_system_prompt(empty_config, path), "Use reported_statistic, eligible, eligibility_explanation"))
unlink(path)
cat("R report table tests passed\n")
