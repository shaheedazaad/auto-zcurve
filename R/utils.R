`%||%` <- function(x, y) {
  if (is.null(x) || length(x) == 0) {
    y
  } else {
    x
  }
}

json_ready <- function(x) {
  if (is.null(x)) {
    return(NULL)
  }

  if (is.data.frame(x)) {
    out <- lapply(x, json_ready)
    class(out) <- "data.frame"
    row.names(out) <- row.names(x)
    return(out)
  }

  if (is.list(x)) {
    return(lapply(x, json_ready))
  }

  if (is.atomic(x) && length(x) > 1 && !is.null(names(x)) && any(nzchar(names(x)))) {
    return(stats::setNames(as.list(unname(x)), names(x)))
  }

  x
}

normalize_for_table <- function(value) {
  if (is.null(value) || length(value) == 0) {
    return(NA)
  }

  if (is.list(value) || length(value) > 1) {
    return(jsonlite::toJSON(json_ready(value), auto_unbox = TRUE, null = "null"))
  }

  if (is.logical(value)) {
    return(isTRUE(value))
  }

  value
}

safe_character <- function(x) {
  if (is.null(x) || length(x) == 0) {
    return(NA_character_)
  }

  as.character(x[[1]])
}

format_p_value <- function(x) {
  if (is.na(x) || !is.finite(x)) {
    return(NA_character_)
  }

  format(signif(x, 6), scientific = FALSE, trim = TRUE)
}

read_text_file <- function(path) {
  path <- trimws(path %||% "")

  if (!nzchar(path)) {
    stop("A text file path is required.", call. = FALSE)
  }

  if (!file.exists(path)) {
    stop("Text file not found: ", path, call. = FALSE)
  }

  paste(readLines(path, warn = FALSE, encoding = "UTF-8"), collapse = "\n")
}

replace_fixed_text <- function(text, needle, replacement) {
  parts <- strsplit(text, needle, fixed = TRUE)[[1]]

  if (length(parts) <= 1) {
    return(text)
  }

  paste(parts, collapse = replacement)
}

render_text_template <- function(text, values = list()) {
  out <- text %||% ""

  for (name in names(values)) {
    replacement <- safe_character(values[[name]])

    if (is.na(replacement)) {
      replacement <- ""
    }

    out <- replace_fixed_text(
      out,
      paste0("{{", name, "}}"),
      replacement
    )
  }

  out
}
