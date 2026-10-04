source(file.path("R", "utils.R"))
source(file.path("R", "schema.R"))
source(file.path("R", "report.R"))

# CPU detection degrades through platform probes without requiring a shell.
none <- function(name, default) default
info <- detect_zcurve_workers(function(...) stop("unavailable"), function(...) "6", none)
stopifnot(identical(info$workers, 5L), identical(info$source, "getconf _NPROCESSORS_ONLN"))
info <- detect_zcurve_workers(function(...) NA, function(...) stop("missing"), function(name, default) if (name == "NUMBER_OF_PROCESSORS") "4" else default)
stopifnot(identical(info$workers, 3L), identical(info$source, "NUMBER_OF_PROCESSORS"))
info <- detect_zcurve_workers(function(...) NA, function(...) "invalid", none)
stopifnot(identical(info$workers, 1L), identical(info$source, "safe default"))
for (value in list(NULL, "", "bad", NA, 0, -1, Inf)) stopifnot(is.na(positive_integer(value)))
stopifnot(identical(positive_integer("3"), 3L))

# Worker probes always clean up a created cluster, including validation failure.
stopifnot(!probe_zcurve_workers(1)$available)
for (fail_validation in c(FALSE, TRUE)) {
  stopped <- FALSE
  result <- probe_zcurve_workers(2, make_cluster = function(workers) "cluster", validate_cluster = function(cluster) { if (fail_validation) stop("cannot load zcurve") }, stop_cluster = function(cluster) { stopped <<- TRUE })
  stopifnot(stopped, identical(result$available, !fail_validation))
  if (fail_validation) stopifnot(identical(result$message, "cannot load zcurve"))
}
result <- probe_zcurve_workers(2, make_cluster = function(workers) stop("cannot open connection"), stop_cluster = function(...) stop("must not stop absent cluster"))
stopifnot(!result$available, identical(result$message, "cannot open connection"))
stopifnot(!is_parallel_worker_error(list()), !is_parallel_worker_error(simpleError("invalid data")), is_parallel_worker_error(simpleError("Error in unserialize(node)")))

# Option failures lead to sequential execution; ordinary fit errors are retained.
for (workers in list(NA, 1L, 3L)) {
  calls <- list()
  result <- fit_zcurve_with_parallel_fallback(list(), bootstrap = 2L,
    core_info = list(workers = workers, detected_cores = 4, source = "test"),
    fit_function = function(data, bootstrap, parallel) { calls[[length(calls) + 1]] <<- parallel; stop("invalid data") },
    get_option = function(...) stop("no option"),
    set_option = function(...) stop("option unavailable"))
  stopifnot(identical(calls, list(FALSE)), inherits(result$fit, "error"), identical(conditionMessage(result$fit), "invalid data"))
  stopifnot(result$execution$requested_workers <= 2L, identical(result$execution$workers, 1L))
}
result <- fit_zcurve_with_parallel_fallback(list(), core_info = list(workers = 3, detected_cores = 4, source = "test"),
  worker_probe = function(...) list(available = TRUE), fit_function = function(...) stop("invalid data"),
  get_option = function(...) 8L, set_option = function(...) invisible(NULL))
stopifnot(identical(result$execution$mode, "parallel"), identical(conditionMessage(result$fit), "invalid data"))
stopifnot(grepl("unavailable.$", zcurve_execution_message("sequential_fallback", 2, list(), NULL)))
cat("R report execution tests passed\n")
