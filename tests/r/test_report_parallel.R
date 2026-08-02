source(file.path("R", "utils.R"))
source(file.path("R", "report.R"))

regression_inputs <- c(
  "chi(1) = 6.74",
  "z = 3.63",
  "F(2, 240) = 0.69",
  "p=0.000000114807",
  "t(206)=2.48",
  "chi(5151)=15536.35"
)
regression_parsed <- zcurve::zcurve_data(
  regression_inputs,
  id = seq_along(regression_inputs)
)
regression_map <- map_parsed_inputs_to_rows(
  regression_parsed$precise$input,
  regression_parsed$censored$input,
  regression_inputs,
  seq_along(regression_inputs)
)
stopifnot(
  identical(regression_map$precise, seq_along(regression_inputs)),
  identical(regression_map$precise_positions, seq_along(regression_inputs))
)

regression_disclosure <- data.frame(
  reported_statistic = regression_inputs,
  usable_for_zcurve = FALSE,
  analysis_p = NA_real_,
  analysis_z = NA_real_,
  zcurve_exclusion_reason = NA_character_
)
regression_prepared <- prepare_zcurve_results(
  regression_parsed,
  regression_map,
  regression_disclosure
)
expected_p <- c(
  stats::pchisq(6.74, df = 1, lower.tail = FALSE),
  2 * stats::pnorm(3.63, lower.tail = FALSE),
  stats::pf(0.69, df1 = 2, df2 = 240, lower.tail = FALSE),
  0.000000114807,
  2 * stats::pt(2.48, df = 206, lower.tail = FALSE)
)
stopifnot(
  isTRUE(all.equal(
    regression_prepared$disclosure_table$analysis_p[1:5],
    expected_p,
    tolerance = 1e-12
  )),
  all(is.finite(regression_prepared$disclosure_table$analysis_z[1:5])),
  all(regression_prepared$disclosure_table$usable_for_zcurve[1:5]),
  identical(regression_prepared$disclosure_table$analysis_p[[6]], 0),
  is.na(regression_prepared$disclosure_table$analysis_z[[6]]),
  !regression_prepared$disclosure_table$usable_for_zcurve[[6]],
  nzchar(regression_prepared$disclosure_table$zcurve_exclusion_reason[[6]]),
  length(regression_prepared$warnings) == 1,
  nrow(regression_prepared$parsed$precise) == 5
)

unmatched_parsed <- structure(
  list(
    precise = data.frame(
      input = c("unsupported=1", "z=3.63"),
      p = c(0, expected_p[[2]]),
      id = c(1, 2)
    ),
    censored = data.frame(
      input = character(0),
      p.rep = numeric(0),
      id = numeric(0)
    )
  ),
  class = "zcurve_data"
)
unmatched_map <- map_parsed_inputs_to_rows(
  unmatched_parsed$precise$input,
  unmatched_parsed$censored$input,
  c("not-supported=1", "z=3.63"),
  1:2
)
unmatched_disclosure <- data.frame(
  usable_for_zcurve = rep(FALSE, 2),
  analysis_p = rep(NA_real_, 2),
  analysis_z = rep(NA_real_, 2),
  zcurve_exclusion_reason = rep(NA_character_, 2)
)
unmatched_prepared <- prepare_zcurve_results(
  unmatched_parsed,
  unmatched_map,
  unmatched_disclosure
)
stopifnot(
  identical(unmatched_map$precise, 2L),
  identical(unmatched_map$precise_positions, 2L),
  isTRUE(all.equal(unmatched_prepared$disclosure_table$analysis_p[[2]], expected_p[[2]])),
  is.na(unmatched_prepared$disclosure_table$analysis_p[[1]]),
  nrow(unmatched_prepared$parsed$precise) == 1
)

boundary_parsed <- zcurve::zcurve_data("p=1", id = 1)
boundary_map <- map_parsed_inputs_to_rows(
  boundary_parsed$precise$input,
  boundary_parsed$censored$input,
  "p=1",
  1
)
boundary_disclosure <- data.frame(
  usable_for_zcurve = FALSE,
  analysis_p = NA_real_,
  analysis_z = NA_real_,
  zcurve_exclusion_reason = NA_character_
)
boundary_prepared <- prepare_zcurve_results(
  boundary_parsed,
  boundary_map,
  boundary_disclosure
)
stopifnot(
  identical(boundary_prepared$disclosure_table$analysis_p[[1]], 1),
  identical(boundary_prepared$disclosure_table$analysis_z[[1]], 0),
  !boundary_prepared$disclosure_table$usable_for_zcurve[[1]],
  grepl("outside [0, 1]", boundary_prepared$disclosure_table$zcurve_exclusion_reason[[1]], fixed = TRUE),
  nrow(boundary_prepared$parsed$censored) == 0,
  any(grepl("p-value bounds", boundary_prepared$warnings, fixed = TRUE))
)

fallback_cores <- detect_zcurve_workers(
  detect_cores = function(logical = TRUE) NA_integer_,
  system_command = function(command, args, stdout, stderr) "8",
  environment = function(name, unset = "") unset
)
stopifnot(
  identical(fallback_cores$workers, 7L),
  identical(fallback_cores$detected_cores, 8L),
  identical(fallback_cores$source, "getconf _NPROCESSORS_ONLN")
)

parallel_calls <- logical(0)
parallel_result <- fit_zcurve_with_parallel_fallback(
  parsed = list(),
  bootstrap = 1000,
  core_info = list(workers = 3L, detected_cores = 4L, source = "test"),
  worker_probe = function(workers) list(available = TRUE, message = NULL),
  fit_function = function(data, bootstrap, parallel) {
    parallel_calls <<- c(parallel_calls, parallel)
    list(ok = TRUE)
  },
  get_option = function(name) 1L,
  set_option = function(...) invisible(NULL)
)
stopifnot(
  identical(parallel_calls, TRUE),
  identical(parallel_result$execution$mode, "parallel"),
  identical(parallel_result$execution$workers, 3L)
)

fallback_calls <- logical(0)
fallback_result <- fit_zcurve_with_parallel_fallback(
  parsed = list(),
  bootstrap = 1000,
  core_info = list(workers = 3L, detected_cores = 4L, source = "test"),
  worker_probe = function(workers) list(available = TRUE, message = NULL),
  fit_function = function(data, bootstrap, parallel) {
    fallback_calls <<- c(fallback_calls, parallel)
    if (parallel) {
      stop("creation of server socket failed: port cannot be opened")
    }
    list(ok = TRUE)
  },
  get_option = function(name) NA_integer_,
  set_option = function(...) invisible(NULL)
)
stopifnot(
  identical(fallback_calls, c(TRUE, FALSE)),
  identical(fallback_result$execution$mode, "sequential_fallback"),
  grepl("server socket", fallback_result$execution$message, fixed = TRUE)
)

probe_calls <- logical(0)
probe_result <- fit_zcurve_with_parallel_fallback(
  parsed = list(),
  bootstrap = 1000,
  core_info = list(workers = 3L, detected_cores = 4L, source = "test"),
  worker_probe = function(workers) {
    list(available = FALSE, message = "local sockets blocked")
  },
  fit_function = function(data, bootstrap, parallel) {
    probe_calls <<- c(probe_calls, parallel)
    list(ok = TRUE)
  },
  get_option = function(name) NA_integer_,
  set_option = function(...) invisible(NULL)
)
stopifnot(
  identical(probe_calls, FALSE),
  identical(probe_result$execution$mode, "sequential_fallback"),
  grepl("local sockets blocked", probe_result$execution$message, fixed = TRUE)
)
