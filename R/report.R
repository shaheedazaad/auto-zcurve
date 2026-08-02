ZCURVE_BOOTSTRAP_ITERATIONS <- 1000L
ZCURVE_BOOTSTRAP_SEED <- 20260802L

flatten_results <- function(results, config) {
  successful <- purrr::keep(results, ~ identical(.x$status, "ok"))

  if (!length(successful)) {
    return(tibble::tibble())
  }

  meta_fields <- names(config$meta_data)
  effect_fields <- names(config$effects)

  purrr::map_dfr(successful, function(item) {
    study <- item$data$meta_data %||% item$data %||% list()
    effects <- study$effects %||% list()

    if (!length(effects)) {
      effects <- item$data$effects %||% list()
    }

    if (!length(effects)) {
      effects <- list(list())
    }

    purrr::map_dfr(effects, function(effect) {
      row <- list(
        source_name = item$file_name
      )

      for (field_name in meta_fields) {
        row[[field_name]] <- normalize_for_table(study[[field_name]])
      }

      for (field_name in effect_fields) {
        row[[field_name]] <- normalize_for_table(effect[[field_name]])
      }

      tibble::as_tibble(row)
    })
  })
}

lookup_field <- function(lookup, names_to_try, available_names = character(0)) {
  for (name in names_to_try) {
    value <- lookup[[name]] %||% NULL

    if (!is.null(value) && nzchar(value)) {
      return(value)
    }

    if (name %in% available_names) {
      return(name)
    }
  }

  NULL
}

build_analysis_input <- function(effect_table, config) {
  lookup <- build_role_lookup(config)
  out <- rep(NA_character_, nrow(effect_table))
  available_names <- names(effect_table)

  reported_field <- lookup_field(
    lookup$effect,
    c("reported_statistic", "reported_test"),
    available_names
  )
  p_field <- lookup_field(lookup$effect, c("p_value"), available_names)
  z_field <- lookup_field(lookup$effect, c("z_value"), available_names)
  one_sided_field <- lookup_field(list(one_sided = "one_sided"), c("one_sided"), available_names)

  if (!is.null(reported_field) && reported_field %in% names(effect_table)) {
    values <- effect_table[[reported_field]]
    normalized_values <- vapply(
      seq_along(values),
      function(i) {
        parsed <- parse_reported_statistic(values[[i]])

        if (is.null(parsed)) {
          return(NA_character_)
        }

        one_sided <- FALSE

        if (!is.null(one_sided_field) && one_sided_field %in% names(effect_table)) {
          one_sided <- isTRUE(effect_table[[one_sided_field]][[i]])
        }

        zcurve_input_from_parsed_statistic(parsed, one_sided = one_sided)
      },
      character(1)
    )

    idx <- !is.na(normalized_values) & nzchar(trimws(normalized_values))
    out[idx] <- normalized_values[idx]
  }

  if (!is.null(p_field) && p_field %in% names(effect_table)) {
    p_vals <- suppressWarnings(as.numeric(effect_table[[p_field]]))
    idx <- is.na(out) & !is.na(p_vals) & is.finite(p_vals)
    out[idx] <- paste0("p=", vapply(p_vals[idx], format_p_value, character(1)))
  }

  if (!is.null(z_field) && z_field %in% names(effect_table)) {
    z_vals <- suppressWarnings(as.numeric(effect_table[[z_field]]))
    idx <- is.na(out) & !is.na(z_vals) & is.finite(z_vals)
    out[idx] <- paste0("z=", format(signif(z_vals[idx], 6), scientific = FALSE, trim = TRUE))
  }

  out
}

build_zcurve_cluster_id <- function(effect_table, config) {
  available_names <- names(effect_table)
  lookup <- build_role_lookup(config)
  doi_field <- lookup_field(lookup$meta, c("doi"), available_names)

  cluster_id <- rep(NA_character_, nrow(effect_table))

  if (!is.null(doi_field) && doi_field %in% available_names) {
    cluster_id <- trimws(as.character(effect_table[[doi_field]]))
    cluster_id[!nzchar(cluster_id)] <- NA_character_
  }

  if ("source_name" %in% available_names) {
    missing <- is.na(cluster_id) | !nzchar(cluster_id)
    fallback <- trimws(as.character(effect_table$source_name))
    fallback[!nzchar(fallback)] <- NA_character_
    cluster_id[missing] <- fallback[missing]
  }

  missing <- is.na(cluster_id) | !nzchar(cluster_id)
  cluster_id[missing] <- paste0("row-", which(missing))
  cluster_id
}

normalize_zcurve_input_for_match <- function(x) {
  normalized <- tolower(trimws(as.character(x)))
  normalized <- gsub("\u03c7²", "chi", normalized, fixed = TRUE)
  normalized <- gsub("\u03c7", "chi", normalized, fixed = TRUE)
  normalized <- gsub("chi-square", "chisquare", normalized, fixed = TRUE)
  normalized <- gsub("\\s+", "", normalized)
  sub("^(c|chisq|chisquare|x2)\\(", "chi(", normalized)
}

map_parsed_inputs_to_rows <- function(precise_inputs, censored_inputs, analysis_input, valid_ids) {
  precise_inputs <- precise_inputs %||% character(0)
  censored_inputs <- censored_inputs %||% character(0)
  parsed_inputs <- c(precise_inputs, censored_inputs)

  if (!length(parsed_inputs)) {
    return(list(
      precise = integer(0),
      precise_positions = integer(0),
      censored = integer(0),
      censored_positions = integer(0)
    ))
  }

  used <- rep(FALSE, length(valid_ids))
  rows <- integer(length(parsed_inputs))
  normalized_analysis_input <- normalize_zcurve_input_for_match(analysis_input[valid_ids])
  normalized_parsed_inputs <- normalize_zcurve_input_for_match(parsed_inputs)

  for (i in seq_along(parsed_inputs)) {
    candidates <- which(!used & normalized_analysis_input == normalized_parsed_inputs[[i]])

    if (!length(candidates)) {
      rows[[i]] <- NA_integer_
      next
    }

    chosen <- candidates[[1]]
    used[[chosen]] <- TRUE
    rows[[i]] <- valid_ids[[chosen]]
  }

  precise_count <- length(precise_inputs)
  precise_rows <- if (precise_count) rows[seq_len(precise_count)] else integer(0)
  censored_rows <- if (length(rows) > precise_count) rows[(precise_count + 1):length(rows)] else integer(0)
  precise_matched <- !is.na(precise_rows)
  censored_matched <- !is.na(censored_rows)

  list(
    precise = precise_rows[precise_matched],
    precise_positions = which(precise_matched),
    censored = censored_rows[censored_matched],
    censored_positions = which(censored_matched)
  )
}

parse_reported_statistic <- function(x) {
  stat <- safe_character(x)

  if (is.na(stat) || !nzchar(trimws(stat))) {
    return(NULL)
  }

  normalized <- tolower(trimws(stat))
  normalized <- gsub("\u03c7²", "chi", normalized, fixed = TRUE)
  normalized <- gsub("\u03c7", "chi", normalized, fixed = TRUE)
  normalized <- gsub("chi-square", "chisquare", normalized, fixed = TRUE)
  normalized <- gsub("\\s+", "", normalized)

  number <- "([+-]?[0-9]*\\.?[0-9]+)"

  t_match <- regexec(paste0("^t\\(", number, "\\)=", number, "$"), normalized)
  t_parts <- regmatches(normalized, t_match)[[1]]
  if (length(t_parts)) {
    return(list(
      type = "t",
      df1 = suppressWarnings(as.numeric(t_parts[[2]])),
      value = suppressWarnings(as.numeric(t_parts[[3]])),
      comparator = "=",
      raw = stat
    ))
  }

  f_match <- regexec(paste0("^f\\(", number, ",", number, "\\)=", number, "$"), normalized)
  f_parts <- regmatches(normalized, f_match)[[1]]
  if (length(f_parts)) {
    return(list(
      type = "f",
      df1 = suppressWarnings(as.numeric(f_parts[[2]])),
      df2 = suppressWarnings(as.numeric(f_parts[[3]])),
      value = suppressWarnings(as.numeric(f_parts[[4]])),
      comparator = "=",
      raw = stat
    ))
  }

  chi_match <- regexec(paste0("^(chi|chisq|chisquare|x2)\\(", number, "\\)=", number, "$"), normalized)
  chi_parts <- regmatches(normalized, chi_match)[[1]]
  if (length(chi_parts)) {
    return(list(
      type = "chi_square",
      df1 = suppressWarnings(as.numeric(chi_parts[[3]])),
      value = suppressWarnings(as.numeric(chi_parts[[4]])),
      comparator = "=",
      raw = stat
    ))
  }

  z_match <- regexec(paste0("^z=", number, "$"), normalized)
  z_parts <- regmatches(normalized, z_match)[[1]]
  if (length(z_parts)) {
    return(list(
      type = "z",
      value = suppressWarnings(as.numeric(z_parts[[2]])),
      comparator = "=",
      raw = stat
    ))
  }

  r_match <- regexec(paste0("^r\\(", number, "\\)=", number, "$"), normalized)
  r_parts <- regmatches(normalized, r_match)[[1]]
  if (length(r_parts)) {
    return(list(
      type = "r",
      df1 = suppressWarnings(as.numeric(r_parts[[2]])),
      value = suppressWarnings(as.numeric(r_parts[[3]])),
      comparator = "=",
      raw = stat
    ))
  }

  p_match <- regexec(paste0("^p(<=|>=|=|<|>)", number, "$"), normalized)
  p_parts <- regmatches(normalized, p_match)[[1]]
  if (length(p_parts)) {
    return(list(
      type = "p",
      value = suppressWarnings(as.numeric(p_parts[[3]])),
      comparator = p_parts[[2]],
      raw = stat
    ))
  }

  NULL
}

zcurve_input_from_parsed_statistic <- function(parsed_stat, one_sided = FALSE) {
  if (is.null(parsed_stat) || is.null(parsed_stat$type)) {
    return(NA_character_)
  }

  if (parsed_stat$type %in% c("t", "f", "chi_square", "z", "p")) {
    return(parsed_stat$raw %||% NA_character_)
  }

  computed_p <- computed_p_from_statistic(parsed_stat, one_sided = one_sided)

  if (!is.finite(computed_p) || is.na(computed_p) || computed_p < 0 || computed_p > 1) {
    return(NA_character_)
  }

  paste0("p=", format_p_value(computed_p))
}

computed_p_from_statistic <- function(parsed_stat, one_sided = FALSE) {
  if (is.null(parsed_stat)) {
    return(NA_real_)
  }

  if (identical(parsed_stat$type, "t")) {
    if (is.na(parsed_stat$df1) || is.na(parsed_stat$value)) {
      return(NA_real_)
    }

    if (isTRUE(one_sided)) {
      return(stats::pt(abs(parsed_stat$value), df = parsed_stat$df1, lower.tail = FALSE))
    }

    return(2 * stats::pt(abs(parsed_stat$value), df = parsed_stat$df1, lower.tail = FALSE))
  }

  if (identical(parsed_stat$type, "f")) {
    if (is.na(parsed_stat$df1) || is.na(parsed_stat$df2) || is.na(parsed_stat$value)) {
      return(NA_real_)
    }

    return(stats::pf(parsed_stat$value, df1 = parsed_stat$df1, df2 = parsed_stat$df2, lower.tail = FALSE))
  }

  if (identical(parsed_stat$type, "chi_square")) {
    if (is.na(parsed_stat$df1) || is.na(parsed_stat$value)) {
      return(NA_real_)
    }

    return(stats::pchisq(parsed_stat$value, df = parsed_stat$df1, lower.tail = FALSE))
  }

  if (identical(parsed_stat$type, "z")) {
    if (is.na(parsed_stat$value)) {
      return(NA_real_)
    }

    if (isTRUE(one_sided)) {
      return(stats::pnorm(abs(parsed_stat$value), lower.tail = FALSE))
    }

    return(2 * stats::pnorm(abs(parsed_stat$value), lower.tail = FALSE))
  }

  if (identical(parsed_stat$type, "r")) {
    if (is.na(parsed_stat$df1) || is.na(parsed_stat$value) || abs(parsed_stat$value) >= 1) {
      return(NA_real_)
    }

    t_value <- abs(parsed_stat$value) * sqrt(parsed_stat$df1 / (1 - parsed_stat$value^2))

    if (isTRUE(one_sided)) {
      return(stats::pt(t_value, df = parsed_stat$df1, lower.tail = FALSE))
    }

    return(2 * stats::pt(t_value, df = parsed_stat$df1, lower.tail = FALSE))
  }

  if (identical(parsed_stat$type, "p") && identical(parsed_stat$comparator, "=")) {
    return(parsed_stat$value)
  }

  NA_real_
}

validate_statistic_row <- function(row, config) {
  available_names <- names(row)
  lookup <- build_role_lookup(config)

  reported_field <- lookup_field(lookup$effect, c("reported_statistic", "reported_test"), available_names)
  p_field <- lookup_field(lookup$effect, c("p_value"), available_names)
  z_field <- lookup_field(lookup$effect, c("z_value"), available_names)
  one_sided_field <- lookup_field(list(one_sided = "one_sided"), c("one_sided"), available_names)
  significant_field <- lookup_field(list(significant = "significant"), c("significant"), available_names)

  reported_value <- if (!is.null(reported_field)) row[[reported_field]] else NULL
  parsed <- parse_reported_statistic(reported_value)
  one_sided <- if (!is.null(one_sided_field)) isTRUE(row[[one_sided_field]]) else FALSE

  extracted_p <- if (!is.null(p_field)) suppressWarnings(as.numeric(row[[p_field]])) else NA_real_
  extracted_z <- if (!is.null(z_field)) suppressWarnings(as.numeric(row[[z_field]])) else NA_real_
  extracted_significant <- if (!is.null(significant_field)) {
    if (is.logical(row[[significant_field]])) row[[significant_field]] else NA
  } else {
    NA
  }

  status <- "not_checked"
  notes <- character(0)
  computed_p <- computed_p_from_statistic(parsed, one_sided = one_sided)
  parsed_type <- parsed$type %||% NA_character_

  if (!is.na(extracted_p) && (extracted_p < 0 || extracted_p > 1)) {
    status <- "warning"
    notes <- c(notes, "p_value is outside [0, 1].")
  }

  if (!is.na(extracted_z) && !is.finite(extracted_z)) {
    status <- "warning"
    notes <- c(notes, "z_value is not finite.")
  }

  if (!is.null(reported_value) && nzchar(trimws(as.character(reported_value)))) {
    if (is.null(parsed)) {
      status <- "warning"
      notes <- c(notes, "reported_statistic could not be parsed.")
    } else {
      status <- "ok"
    }
  } else if (!is.na(extracted_p) || !is.na(extracted_z)) {
    status <- "ok"
  }

  if (!is.null(parsed) && identical(parsed$type, "p")) {
    if (parsed$value < 0 || parsed$value > 1) {
      status <- "warning"
      notes <- c(notes, "reported_statistic contains a p-value outside [0, 1].")
    }

    if (!is.na(extracted_p)) {
      if (identical(parsed$comparator, "=") && abs(parsed$value - extracted_p) > 0.01) {
        status <- "warning"
        notes <- c(notes, "reported_statistic p-value does not match p_value field.")
      }

      if (identical(parsed$comparator, "<") && !(extracted_p < parsed$value)) {
        status <- "warning"
        notes <- c(notes, "p_value field does not satisfy the reported_statistic inequality.")
      }

      if (identical(parsed$comparator, "<=") && !(extracted_p <= parsed$value)) {
        status <- "warning"
        notes <- c(notes, "p_value field does not satisfy the reported_statistic inequality.")
      }

      if (identical(parsed$comparator, ">") && !(extracted_p > parsed$value)) {
        status <- "warning"
        notes <- c(notes, "p_value field does not satisfy the reported_statistic inequality.")
      }

      if (identical(parsed$comparator, ">=") && !(extracted_p >= parsed$value)) {
        status <- "warning"
        notes <- c(notes, "p_value field does not satisfy the reported_statistic inequality.")
      }
    }
  }

  if (!is.na(computed_p) && !is.na(extracted_p) && abs(computed_p - extracted_p) > 0.01) {
    status <- "warning"
    notes <- c(notes, "Computed p-value from reported_statistic does not match p_value field.")
  }

  if (!is.null(parsed) && identical(parsed$type, "z") && !is.na(extracted_z) && abs(parsed$value - extracted_z) > 0.05) {
    status <- "warning"
    notes <- c(notes, "reported_statistic z-value does not match z_value field.")
  }

  if (!is.na(computed_p) && !is.na(extracted_significant)) {
    expected_significant <- computed_p < 0.05

    if (!identical(isTRUE(extracted_significant), expected_significant)) {
      status <- "warning"
      notes <- c(notes, "significant field does not match the extracted statistic at alpha = 0.05.")
    }
  }

  if (!length(notes) && identical(status, "ok")) {
    notes <- "No obvious inconsistencies detected."
  }

  tibble::tibble(
    statistic_validation_status = status,
    statistic_validation_notes = paste(notes, collapse = " "),
    statistic_validation_type = parsed_type,
    statistic_validation_p = computed_p
  )
}

validate_extracted_statistics <- function(effect_table, config) {
  if (!nrow(effect_table)) {
    return(dplyr::mutate(
      effect_table,
      statistic_validation_status = character(0),
      statistic_validation_notes = character(0),
      statistic_validation_type = character(0),
      statistic_validation_p = numeric(0)
    ))
  }

  validations <- purrr::map_dfr(seq_len(nrow(effect_table)), function(i) {
    validate_statistic_row(as.list(effect_table[i, , drop = FALSE]), config)
  })

  dplyr::bind_cols(effect_table, validations)
}

positive_integer <- function(value) {
  if (is.null(value) || !length(value)) {
    return(NA_integer_)
  }
  parsed <- suppressWarnings(as.integer(value[[1]]))
  if (length(parsed) == 1 && !is.na(parsed) && is.finite(parsed) && parsed > 0) {
    parsed
  } else {
    NA_integer_
  }
}

detect_zcurve_workers <- function(
  detect_cores = parallel::detectCores,
  system_command = system2,
  environment = Sys.getenv
) {
  requested <- positive_integer(environment("AUTO_ZCURVE_ZCURVE_CORES", ""))
  if (!is.na(requested)) {
    return(list(
      workers = requested,
      detected_cores = requested,
      source = "AUTO_ZCURVE_ZCURVE_CORES"
    ))
  }

  detected <- tryCatch(
    positive_integer(detect_cores(logical = TRUE)),
    error = function(e) NA_integer_
  )
  source <- "parallel::detectCores()"

  if (is.na(detected)) {
    detected <- tryCatch(
      positive_integer(system_command(
        "getconf",
        "_NPROCESSORS_ONLN",
        stdout = TRUE,
        stderr = FALSE
      )),
      error = function(e) NA_integer_
    )
    source <- "getconf _NPROCESSORS_ONLN"
  }

  if (is.na(detected)) {
    detected <- positive_integer(environment("NUMBER_OF_PROCESSORS", ""))
    source <- "NUMBER_OF_PROCESSORS"
  }

  if (is.na(detected)) {
    detected <- 1L
    source <- "safe default"
  }

  list(
    workers = max(1L, detected - 1L),
    detected_cores = detected,
    source = source
  )
}

probe_zcurve_workers <- function(
  workers,
  make_cluster = parallel::makePSOCKcluster,
  validate_cluster = function(cluster) {
    parallel::clusterEvalQ(cluster, {
      library("zcurve")
      TRUE
    })
  },
  stop_cluster = parallel::stopCluster
) {
  if (workers <= 1L) {
    return(list(available = FALSE, message = "Only one worker is available."))
  }

  cluster <- NULL
  failure <- NULL
  tryCatch(
    {
      cluster <- make_cluster(workers)
      validate_cluster(cluster)
    },
    error = function(e) {
      failure <<- conditionMessage(e)
    },
    finally = {
      if (!is.null(cluster)) {
        try(stop_cluster(cluster), silent = TRUE)
      }
    }
  )

  list(
    available = is.null(failure),
    message = failure
  )
}

is_parallel_worker_error <- function(error) {
  if (!inherits(error, "error")) {
    return(FALSE)
  }
  message <- tolower(conditionMessage(error))
  patterns <- c(
    "server socket",
    "socket connection",
    "cannot open connection",
    "cannot be opened",
    "all connections are in use",
    "error reading from connection",
    "error in unserialize",
    "node produced errors",
    "na/nan argument"
  )
  any(vapply(patterns, grepl, logical(1), x = message, fixed = TRUE))
}

zcurve_execution_message <- function(mode, workers, core_info, reason = NULL) {
  if (identical(mode, "parallel")) {
    return(sprintf(
      "Bootstrap execution: parallel with %d workers (%d logical CPUs detected via %s).",
      workers,
      core_info$detected_cores,
      core_info$source
    ))
  }

  if (identical(mode, "sequential_fallback")) {
    return(paste0(
      "Bootstrap execution: sequential fallback. Parallel workers were unavailable",
      if (!is.null(reason) && nzchar(reason)) paste0(": ", reason) else "."
    ))
  }

  sprintf(
    "Bootstrap execution: sequential (%d logical CPU detected via %s).",
    core_info$detected_cores,
    core_info$source
  )
}

fit_zcurve_with_parallel_fallback <- function(
  parsed,
  bootstrap = ZCURVE_BOOTSTRAP_ITERATIONS,
  seed = ZCURVE_BOOTSTRAP_SEED,
  core_info = detect_zcurve_workers(),
  worker_probe = probe_zcurve_workers,
  fit_function = zcurve::zcurve_clustered,
  get_option = zcurve::zcurve.get_option,
  set_option = zcurve::zcurve.options
) {
  workers <- positive_integer(core_info$workers)
  if (is.na(workers)) {
    workers <- 1L
  }
  workers <- max(1L, min(workers, as.integer(bootstrap)))
  old_max_cores <- tryCatch(get_option("max_cores"), error = function(e) NULL)
  option_error <- tryCatch(
    {
      set_option(max_cores = workers)
      NULL
    },
    error = function(e) conditionMessage(e)
  )
  if (!is.null(old_max_cores)) {
    on.exit(try(set_option(max_cores = old_max_cores), silent = TRUE), add = TRUE)
  }

  use_parallel <- workers > 1L && is.null(option_error)
  fallback_reason <- option_error
  if (use_parallel) {
    probe <- worker_probe(workers)
    use_parallel <- isTRUE(probe$available)
    fallback_reason <- probe$message %||% NULL
  }

  set.seed(seed)
  fit <- tryCatch(
    fit_function(data = parsed, bootstrap = bootstrap, parallel = use_parallel),
    error = function(e) e
  )

  mode <- if (use_parallel) "parallel" else if (workers > 1L) "sequential_fallback" else "sequential"
  if (use_parallel && is_parallel_worker_error(fit)) {
    fallback_reason <- conditionMessage(fit)
    set.seed(seed)
    fit <- tryCatch(
      fit_function(data = parsed, bootstrap = bootstrap, parallel = FALSE),
      error = function(e) e
    )
    mode <- "sequential_fallback"
  }

  execution <- list(
    mode = mode,
    workers = if (identical(mode, "parallel")) workers else 1L,
    requested_workers = workers,
    detected_cores = core_info$detected_cores,
    core_source = core_info$source,
    bootstrap_iterations = as.integer(bootstrap),
    bootstrap_seed = as.integer(seed),
    message = zcurve_execution_message(mode, workers, core_info, fallback_reason)
  )

  list(fit = fit, execution = execution)
}

finite_z_from_p <- function(p) {
  p <- suppressWarnings(as.numeric(p))
  out <- rep(NA_real_, length(p))
  valid <- !is.na(p) & is.finite(p) & p > 0 & p <= 1
  out[valid] <- stats::qnorm(p[valid] / 2, lower.tail = FALSE)
  out
}

prepare_zcurve_results <- function(parsed, row_map, disclosure_table) {
  precise_rows <- row_map$precise %||% integer(0)
  precise_positions <- row_map$precise_positions %||% integer(0)
  censored_rows <- row_map$censored %||% integer(0)
  censored_positions <- row_map$censored_positions %||% integer(0)

  precise_p <- parsed$precise$p[precise_positions]
  precise_z <- finite_z_from_p(precise_p)
  precise_finite <- !is.na(precise_z) & is.finite(precise_z)

  censored_p <- parsed$censored$p.rep[censored_positions]
  censored_z <- finite_z_from_p(censored_p)
  censored_lb <- parsed$censored$p.lb[censored_positions]
  censored_ub <- parsed$censored$p.ub[censored_positions]
  censored_bounds_valid <-
    !is.na(censored_lb) & is.finite(censored_lb) & censored_lb >= 0 & censored_lb <= 1 &
    !is.na(censored_ub) & is.finite(censored_ub) & censored_ub >= 0 & censored_ub <= 1 &
    censored_lb <= censored_ub
  censored_finite <- !is.na(censored_z) & is.finite(censored_z)
  censored_usable <- censored_finite & censored_bounds_valid

  if (length(precise_rows)) {
    disclosure_table$analysis_p[precise_rows] <- precise_p
    disclosure_table$analysis_z[precise_rows[precise_finite]] <- precise_z[precise_finite]
    disclosure_table$usable_for_zcurve[precise_rows[precise_finite]] <- TRUE
    disclosure_table$zcurve_exclusion_reason[precise_rows[!precise_finite]] <-
      "Excluded because the p-value produced a non-finite z-value."
  }

  if (length(censored_rows)) {
    disclosure_table$analysis_p[censored_rows] <- censored_p
    disclosure_table$analysis_z[censored_rows[censored_finite]] <- censored_z[censored_finite]
    disclosure_table$usable_for_zcurve[censored_rows[censored_usable]] <- TRUE
    disclosure_table$zcurve_exclusion_reason[censored_rows[!censored_finite]] <-
      "Excluded because the representative p-value produced a non-finite z-value."
    disclosure_table$zcurve_exclusion_reason[censored_rows[censored_finite & !censored_bounds_valid]] <-
      "Excluded because the decoded p-value bounds were outside [0, 1] or not finite."
  }

  keep_precise <- rep(FALSE, nrow(parsed$precise))
  keep_censored <- rep(FALSE, nrow(parsed$censored))
  keep_precise[precise_positions[precise_finite]] <- TRUE
  keep_censored[censored_positions[censored_usable]] <- TRUE

  unmatched_count <-
    (nrow(parsed$precise) - length(precise_positions)) +
    (nrow(parsed$censored) - length(censored_positions))
  non_finite_count <- sum(!precise_finite) + sum(!censored_finite)
  invalid_bounds_count <- sum(!censored_bounds_valid)
  warnings <- character(0)

  if (non_finite_count > 0) {
    warnings <- c(
      warnings,
      paste0(
        non_finite_count,
        " effect",
        if (non_finite_count == 1) " was" else "s were",
        " excluded because ",
        if (non_finite_count == 1) "its p-value was" else "their p-values were",
        " zero or otherwise produced non-finite z-values. ",
        "See `zcurve_exclusion_reason` in the disclosure table."
      )
    )
  }

  if (invalid_bounds_count > 0) {
    warnings <- c(
      warnings,
      paste0(
        invalid_bounds_count,
        " censored effect",
        if (invalid_bounds_count == 1) " was" else "s were",
        " excluded because decoded p-value bounds were outside [0, 1] or not finite. ",
        "See `zcurve_exclusion_reason` in the disclosure table."
      )
    )
  }

  if (unmatched_count > 0) {
    warnings <- c(
      warnings,
      paste0(
        unmatched_count,
        " decoded z-curve input",
        if (unmatched_count == 1) " could" else "s could",
        " not be linked back to its disclosure row and ",
        if (unmatched_count == 1) "was" else "were",
        " excluded."
      )
    )
  }

  parsed$precise <- parsed$precise[keep_precise, , drop = FALSE]
  parsed$censored <- parsed$censored[keep_censored, , drop = FALSE]

  list(
    parsed = parsed,
    disclosure_table = disclosure_table,
    warnings = warnings
  )
}

run_zcurve_analysis <- function(effect_table, config) {
  if (!nrow(effect_table)) {
    return(list(status = "error", message = "No extracted effects are available yet."))
  }

  effect_table <- validate_extracted_statistics(effect_table, config)
  analysis_input <- build_analysis_input(effect_table, config)
  cluster_id <- build_zcurve_cluster_id(effect_table, config)
  valid <- !is.na(analysis_input) & nzchar(trimws(analysis_input))

  disclosure_table <- dplyr::mutate(
    effect_table,
    analysis_input = analysis_input,
    zcurve_cluster_id = cluster_id,
    usable_for_zcurve = FALSE,
    analysis_p = NA_real_,
    analysis_z = NA_real_,
    zcurve_exclusion_reason = NA_character_
  )

  if (!any(valid)) {
    return(list(
      status = "error",
      message = "No effect rows contain a `reported_statistic`, `p_value`, or `z_value` field usable for z-curve.",
      disclosure_table = disclosure_table
    ))
  }

  valid_ids <- which(valid)
  parsed <- tryCatch(
    zcurve::zcurve_data(analysis_input[valid], id = cluster_id[valid]),
    error = function(e) e
  )

  if (inherits(parsed, "error")) {
    return(list(
      status = "error",
      message = parsed$message,
      disclosure_table = disclosure_table
    ))
  }

  row_map <- map_parsed_inputs_to_rows(
    parsed$precise$input %||% character(0),
    parsed$censored$input %||% character(0),
    analysis_input,
    valid_ids
  )
  prepared <- prepare_zcurve_results(parsed, row_map, disclosure_table)
  parsed <- prepared$parsed
  disclosure_table <- prepared$disclosure_table

  if (!nrow(parsed$precise) && !nrow(parsed$censored)) {
    return(list(
      status = "error",
      message = "No finite z-values remain after validating the decoded statistics.",
      warnings = prepared$warnings,
      disclosure_table = disclosure_table
    ))
  }

  fit_result <- fit_zcurve_with_parallel_fallback(parsed)
  fit <- fit_result$fit

  if (inherits(fit, "error")) {
    return(list(
      status = "error",
      message = fit$message,
      warnings = prepared$warnings,
      execution = fit_result$execution,
      disclosure_table = disclosure_table
    ))
  }

  fit_summary <- summary(fit)
  coefficients <- as.data.frame(fit_summary$coefficients)
  coefficients$metric <- rownames(coefficients)
  rownames(coefficients) <- NULL

  list(
    status = "ok",
    fit = fit,
    fit_summary = fit_summary,
    metrics = dplyr::select(coefficients, metric, Estimate),
    execution = fit_result$execution,
    warnings = prepared$warnings,
    disclosure_table = disclosure_table,
    message = NULL
  )
}

build_reference_table <- function(results, config) {
  successful <- purrr::keep(results, ~ identical(.x$status, "ok"))

  if (!length(successful)) {
    return(tibble::tibble())
  }

  lookup <- build_role_lookup(config)
  meta_names <- names(config$meta_data)
  citation_field <- lookup_field(lookup$meta, c("citation"), meta_names)
  doi_field <- lookup_field(lookup$meta, c("doi"), meta_names)
  url_field <- lookup_field(lookup$meta, c("url"), meta_names)

  refs <- purrr::map_dfr(successful, function(item) {
    study <- item$data$meta_data %||% item$data %||% list()
    tibble::tibble(
      source_name = item$file_name,
      citation = if (!is.null(citation_field)) normalize_for_table(study[[citation_field]]) else NA_character_,
      doi = if (!is.null(doi_field)) normalize_for_table(study[[doi_field]]) else NA_character_,
      url = if (!is.null(url_field)) normalize_for_table(study[[url_field]]) else NA_character_
    )
  })

  dplyr::distinct(refs)
}

default_effect_definition <- function() {
  paste(
    "Extract each article's 'focal' effects.",
    "Focal effects are those that support the claims in either the title or abstract of the article",
    "(a non-focal effect, for example, would be a manipulation check)."
  )
}

build_system_prompt <- function(config, instruction_path, effect_definition = NULL) {
  lookup <- build_role_lookup(config)
  reported_field <- lookup$effect$reported_statistic %||% lookup$effect$reported_test %||% "reported_statistic"

  base_prompt <- render_text_template(
    read_text_file(instruction_path),
    list(
      reported_statistic_field = reported_field
    )
  )

  effect_definition <- trimws(safe_character(effect_definition %||% default_effect_definition()))

  if (!is.na(effect_definition) && nzchar(effect_definition)) {
    paste(base_prompt, "", "## Effects of interest", effect_definition, sep = "\n")
  } else {
    base_prompt
  }
}
