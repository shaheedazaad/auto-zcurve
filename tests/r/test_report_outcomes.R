source(file.path("R", "utils.R"))
source(file.path("R", "schema.R"))
source(file.path("R", "report.R"))

with_namespace_function <- function(package, name, replacement, code) {
  namespace <- asNamespace(package)
  original <- get(name, envir = namespace)
  unlockBinding(name, namespace)
  assign(name, replacement, envir = namespace)
  lockBinding(name, namespace)
  on.exit({
    unlockBinding(name, namespace)
    assign(name, original, envir = namespace)
    lockBinding(name, namespace)
  })
  force(code)
}

stopifnot(identical(map_parsed_inputs_to_rows(NULL, NULL, c("z=2"), 1L), list(precise = integer(), precise_positions = integer(), censored = integer(), censored_positions = integer())))
stopifnot(identical(replace_fixed_text("unchanged", "", "replacement"), "unchanged"))
requested <- detect_zcurve_workers(environment = function(name, default) "7", detect_cores = function(...) stop("must not detect"))
stopifnot(identical(requested, list(workers = 7L, detected_cores = 7L, source = "AUTO_ZCURVE_ZCURVE_CORES")))

# Execute the default worker initialization locally to check its dependency load.
with_namespace_function("parallel", "clusterEvalQ", function(cl, expr) {
  stopifnot(identical(cl, "test-cluster"))
  list(eval(substitute(expr), envir = new.env(parent = parent.frame())))
}, {
  stopped <- FALSE
  result <- probe_zcurve_workers(2L, make_cluster = function(...) "test-cluster", stop_cluster = function(...) { stopped <<- TRUE })
  stopifnot(result$available, stopped)
})

config <- list(meta_data = list(), effects = list())
effects <- data.frame(source_name = "a.pdf", reported_statistic = "z=2.4")
with_namespace_function("zcurve", "zcurve_data", function(...) stop("Decoder failed"), {
  result <- run_zcurve_analysis(effects, config)
  stopifnot(identical(result$status, "error"), identical(result$message, "Decoder failed"), nrow(result$disclosure_table) == 1L)
})

# Extreme tail probabilities that underflow must remain disclosed, not fitted.
result <- run_zcurve_analysis(data.frame(source_name = "extreme.pdf", reported_statistic = "chi(5151)=15536.35"), config)
stopifnot(identical(result$status, "error"), grepl("No finite z-values", result$message), !result$disclosure_table$usable_for_zcurve, length(result$warnings) > 0)

# Use a deterministic fit with a real summary method to verify report assembly.
original_fit <- fit_zcurve_with_parallel_fallback
fixture_fit <- stats::lm(y ~ x, data = data.frame(x = 1:5, y = c(2, 5, 5, 9, 10)))
execution <- list(mode = "sequential", workers = 1L)
fit_zcurve_with_parallel_fallback <- function(parsed) {
  stopifnot(nrow(parsed$precise) + nrow(parsed$censored) == 1L)
  list(fit = fixture_fit, execution = execution)
}
result <- run_zcurve_analysis(effects, config)
fit_zcurve_with_parallel_fallback <- original_fit
stopifnot(identical(result$status, "ok"), is.null(result$message), identical(result$execution, execution), identical(result$fit, fixture_fit))
stopifnot(identical(names(result$metrics), c("metric", "Estimate")), identical(result$metrics$metric, c("(Intercept)", "x")))
stopifnot(isTRUE(all.equal(result$metrics$Estimate, unname(stats::coef(fixture_fit)))), result$disclosure_table$usable_for_zcurve)
cat("R report outcome tests passed\n")
