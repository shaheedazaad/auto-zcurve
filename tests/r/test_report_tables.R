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
stopifnot(identical(table$extraction_status, rep("ok", 5)), all(is.na(table$extraction_review_reasons)))

# needs_review extractions are analysed but marked; projects saved before the
# status existed (no review_reasons) and other statuses keep their old handling.
review_results <- list(
  list(status = "needs_review", file_name = "repaired.pdf", review_reasons = list("JSON was repaired", "finish reason: MAX_TOKENS"), data = list(meta_data = list(title = "Repaired"), effects = list(list(p = .05)))),
  list(status = "needs_review", file_name = "unexplained.pdf", data = list(meta_data = list(title = "Unexplained"), effects = list(list(p = .06)))),
  list(status = "ok", file_name = "clean.pdf", review_reasons = list(), data = list(meta_data = list(title = "Clean"), effects = list(list(p = .07)))),
  list(file_name = "statusless.pdf", data = list(effects = list(list(p = .08)))),
  list(status = c("ok", "ok"), file_name = "malformed.pdf", data = list(effects = list(list(p = .09))))
)
stopifnot(identical(vapply(review_results, is_extracted_result, logical(1)), c(TRUE, TRUE, TRUE, FALSE, FALSE)))
review_table <- flatten_results(review_results, config)
stopifnot(identical(review_table$source_name, c("repaired.pdf", "unexplained.pdf", "clean.pdf")))
stopifnot(identical(review_table$p, c(.05, .06, .07)))
stopifnot(identical(review_table$extraction_status, c("needs_review", "needs_review", "ok")))
stopifnot(identical(review_table$extraction_review_reasons, c("JSON was repaired; finish reason: MAX_TOKENS", "No reason was recorded.", NA_character_)))
stopifnot(identical(build_reference_table(review_results, config)$citation, c("Repaired", "Unexplained", "Clean")))

path <- tempfile(fileext = ".txt")
writeLines("Use {{reported_statistic_field}}, {{eligible_field}}, {{eligibility_explanation_field}}", path)
prompt_config <- list(meta_data = list(), effects = list(stat = list(role = "reported_statistic"), keep = list(role = "eligible"), reason = list(role = "eligibility_explanation")))
stopifnot(identical(build_system_prompt(prompt_config, path), "Use stat, keep, reason"))
stopifnot(identical(build_system_prompt(empty_config, path), "Use reported_statistic, eligible, eligibility_explanation"))
unlink(path)
cat("R report table tests passed\n")
