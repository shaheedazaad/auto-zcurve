source(file.path("R", "utils.R"))
source(file.path("R", "schema.R"))
source(file.path("R", "report.R"))
config <- list(meta_data = list(), effects = list())

# Parse supported notation and compare against R's distribution functions.
cases <- list(
  list(" t(20) = -2.5 ", "t", 2 * stats::pt(2.5, 20, lower.tail = FALSE)),
  list("F(2, 30)=4.2", "f", stats::pf(4.2, 2, 30, lower.tail = FALSE)),
  list("χ²(3)=8.2", "chi_square", stats::pchisq(8.2, 3, lower.tail = FALSE)),
  list("chi-square(3)=8.2", "chi_square", stats::pchisq(8.2, 3, lower.tail = FALSE)),
  list("z=-1.96", "z", 2 * stats::pnorm(1.96, lower.tail = FALSE)),
  list("r(20)=0.4", "r", 2 * stats::pt(0.4 * sqrt(20 / (1 - 0.4^2)), 20, lower.tail = FALSE)),
  list("p=.05", "p", 0.05)
)
for (case in cases) {
  parsed <- parse_reported_statistic(case[[1]])
  stopifnot(identical(parsed$type, case[[2]]), isTRUE(all.equal(computed_p_from_statistic(parsed), case[[3]])))
  if (parsed$type %in% c("t", "z", "r")) {
    stopifnot(isTRUE(all.equal(computed_p_from_statistic(parsed, TRUE), case[[3]] / 2)))
  }
}
for (input in list(NULL, NA_character_, "", "  ", "unsupported")) stopifnot(is.null(parse_reported_statistic(input)))
for (comparator in c("<", "<=", ">", ">=")) {
  parsed <- parse_reported_statistic(paste0("p", comparator, ".05"))
  stopifnot(identical(parsed$comparator, comparator), is.na(computed_p_from_statistic(parsed)))
}
for (parsed in list(NULL, list(type = "unsupported"), list(type = "t", df1 = NA_real_, value = 2), list(type = "f", df1 = 1, df2 = NA_real_, value = 2), list(type = "chi_square", df1 = 2, value = NA_real_), list(type = "z", value = NA_real_), list(type = "r", df1 = 20, value = 1))) {
  stopifnot(is.na(computed_p_from_statistic(parsed)))
}
stopifnot(is.na(zcurve_input_from_parsed_statistic(NULL)), is.na(zcurve_input_from_parsed_statistic(list())), is.na(zcurve_input_from_parsed_statistic(list(type = "r", df1 = 20, value = 1))))
stopifnot(grepl("^p=", zcurve_input_from_parsed_statistic(parse_reported_statistic("r(20)=0.4"))))
stopifnot(identical(zcurve_input_from_parsed_statistic(parse_reported_statistic("p<.05")), "p<.05"))

# Role mappings, fallback columns, and source IDs remain aligned across rows.
input <- data.frame(reported_statistic = c("t(20)=2", "unsupported", "", ""), p_value = c(0.9, 0.04, NA, NA), z_value = c(NA, NA, 2, NA), one_sided = c(FALSE, TRUE, FALSE, FALSE))
stopifnot(identical(build_analysis_input(input, config), c("t(20)=2", "p=0.04", "z=2", NA_character_)))
stopifnot(is.null(lookup_field(list(), "missing")), identical(lookup_field(list(custom = "mapped"), "custom"), "mapped"))
clusters <- build_zcurve_cluster_id(data.frame(doi = c(" DOI ", "", NA), source_name = c("a.pdf", "b.pdf", "")), config)
stopifnot(identical(clusters, c("DOI", "b.pdf", "row-3")))
stopifnot(identical(build_zcurve_cluster_id(data.frame(other = 1:2), config), c("row-1", "row-2")))

# Eligibility requires a boolean true when the schema defines it.
legacy <- normalize_effect_eligibility(data.frame(p_value = c(.01, .02)), config)
stopifnot(all(legacy$eligible), all(grepl("Legacy project", legacy$eligibility_explanation)))
explicit_config <- list(meta_data = list(), effects = list(include = list(role = "eligible"), reason = list(role = "eligibility_explanation")))
explicit <- normalize_effect_eligibility(data.frame(include = c(TRUE, FALSE, NA), reason = c("Valid test", "", NA)), explicit_config)
stopifnot(identical(explicit$eligible, c(TRUE, FALSE, FALSE)), identical(explicit$eligibility_explanation[[1]], "Valid test"), all(grepl("No eligibility explanation", explicit$eligibility_explanation[2:3])))
stopifnot(!any(normalize_effect_eligibility(data.frame(include = c("true", "yes")), explicit_config)$eligible))
stopifnot(!any(normalize_effect_eligibility(data.frame(other = 1:2), explicit_config)$eligible))
stopifnot(identical(run_zcurve_analysis(data.frame(), config)$status, "error"))
excluded <- run_zcurve_analysis(data.frame(include = FALSE, reason = "Not relevant", p_value = .01), explicit_config)
stopifnot(identical(excluded$status, "error"), grepl("Not relevant", excluded$disclosure_table$zcurve_exclusion_reason))

# Missing statistics remain unchecked; inconsistencies must retain warning status.
stopifnot(identical(validate_statistic_row(list(), config)$statistic_validation_status, "not_checked"))
stopifnot(identical(validate_statistic_row(list(reported_statistic = "p=.03", p_value = .03, significant = TRUE), config)$statistic_validation_status, "ok"))
for (row in list(
  list(reported_statistic = "invalid"),
  list(reported_statistic = "p=1.2"),
  list(reported_statistic = "p=.01", p_value = .2),
  list(reported_statistic = "p<.05", p_value = .05),
  list(reported_statistic = "p<=.05", p_value = .06),
  list(reported_statistic = "p>.05", p_value = .05),
  list(reported_statistic = "p>=.05", p_value = .04),
  list(reported_statistic = "z=2", z_value = 3),
  list(reported_statistic = "z=2", significant = FALSE),
  list(p_value = 1.2),
  list(z_value = Inf)
)) stopifnot(identical(validate_statistic_row(row, config)$statistic_validation_status, "warning"))
stopifnot(identical(validate_statistic_row(list(reported_statistic = NA_character_, p_value = .03), config)$statistic_validation_status, "ok"))
stopifnot(nrow(validate_extracted_statistics(data.frame(), config)) == 0)
stopifnot(nrow(validate_extracted_statistics(data.frame(p_value = c(.01, .02)), config)) == 2)
cat("R report statistic tests passed\n")
