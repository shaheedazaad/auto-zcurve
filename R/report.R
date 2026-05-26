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
  normalized <- gsub("\u03c7", "chi", normalized, fixed = TRUE)
  normalized <- gsub("\u03c7²", "chi", normalized, fixed = TRUE)
  normalized <- gsub("chi-square", "chisquare", normalized, fixed = TRUE)
  gsub("\\s+", "", normalized)
}

map_parsed_inputs_to_rows <- function(precise_inputs, censored_inputs, analysis_input, valid_ids) {
  precise_inputs <- precise_inputs %||% character(0)
  censored_inputs <- censored_inputs %||% character(0)
  parsed_inputs <- c(precise_inputs, censored_inputs)

  if (!length(parsed_inputs)) {
    return(list(precise = integer(0), censored = integer(0)))
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

  list(
    precise = precise_rows[!is.na(precise_rows)],
    censored = censored_rows[!is.na(censored_rows)]
  )
}

parse_reported_statistic <- function(x) {
  stat <- safe_character(x)

  if (is.na(stat) || !nzchar(trimws(stat))) {
    return(NULL)
  }

  normalized <- tolower(trimws(stat))
  normalized <- gsub("\u03c7", "chi", normalized, fixed = TRUE)
  normalized <- gsub("\u03c7²", "chi", normalized, fixed = TRUE)
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
    analysis_z = NA_real_
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
  precise_rows <- row_map$precise
  censored_rows <- row_map$censored
  usable_rows <- unique(c(precise_rows, censored_rows))

  disclosure_table$usable_for_zcurve[usable_rows] <- TRUE

  if (length(precise_rows)) {
    disclosure_table$analysis_p[precise_rows] <- parsed$precise$p
    disclosure_table$analysis_z[precise_rows] <- stats::qnorm(parsed$precise$p / 2, lower.tail = FALSE)
  }

  if (length(censored_rows)) {
    disclosure_table$analysis_p[censored_rows] <- parsed$censored$p.rep
    disclosure_table$analysis_z[censored_rows] <- stats::qnorm(parsed$censored$p.rep / 2, lower.tail = FALSE)
  }

  fit <- tryCatch(
    zcurve::zcurve_clustered(data = parsed, bootstrap = 1000, parallel = TRUE),
    error = function(e) e
  )

  if (inherits(fit, "error")) {
    return(list(
      status = "error",
      message = fit$message,
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
