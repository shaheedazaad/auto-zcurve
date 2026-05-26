normalize_schema_type <- function(type) {
  type <- toupper(trimws(type %||% ""))
  supported <- c("STRING", "NUMBER", "INTEGER", "BOOLEAN", "ARRAY")

  if (!nzchar(type) || !(type %in% supported)) {
    stop("Unsupported field type: ", type, call. = FALSE)
  }

  type
}

validate_field_spec <- function(field_name, spec, section_name) {
  if (!is.list(spec)) {
    stop(section_name, ".", field_name, " must be a mapping in YAML.", call. = FALSE)
  }

  type <- normalize_schema_type(spec$type %||% "")
  role <- safe_character(spec$role)

  if (identical(type, "ARRAY")) {
    item_type <- normalize_schema_type(spec$items$type %||% spec$items_type %||% "")

    if (identical(item_type, "ARRAY")) {
      stop(section_name, ".", field_name, " cannot be an array of arrays.", call. = FALSE)
    }
  }

  list(
    type = type,
    description = safe_character(spec$description),
    required = isTRUE(spec$required),
    role = if (!is.na(role) && nzchar(role)) role else NULL,
    items_type = if (identical(type, "ARRAY")) item_type else NULL
  )
}

normalize_field_section <- function(fields, section_name, allow_empty = FALSE) {
  if (is.null(fields) || !length(fields)) {
    if (isTRUE(allow_empty)) {
      return(list())
    }

    stop("`", section_name, "` must contain at least one field.", call. = FALSE)
  }

  names(fields) <- names(fields) %||% rep("", length(fields))

  if (any(!nzchar(names(fields)))) {
    stop("Every field in `", section_name, "` must be named.", call. = FALSE)
  }

  lapply(names(fields), function(field_name) {
    validate_field_spec(field_name, fields[[field_name]], section_name)
  }) |>
    stats::setNames(names(fields))
}

read_extraction_config <- function(path) {
  if (!file.exists(path)) {
    stop("Schema file not found: ", path, call. = FALSE)
  }

  raw <- yaml::read_yaml(path)

  meta_data <- normalize_field_section(raw$meta_data, "meta_data", allow_empty = TRUE)
  effects <- normalize_field_section(raw$effects, "effects")

  config_name <- safe_character(raw$name)
  if (is.na(config_name) || !nzchar(config_name)) {
    config_name <- "zcurve_extraction"
  }

  list(
    name = config_name,
    description = safe_character(raw$description),
    meta_data = meta_data,
    effects = effects,
    path = normalizePath(path, winslash = "/", mustWork = TRUE)
  )
}

build_role_lookup <- function(config) {
  collect_roles <- function(fields) {
    out <- list()

    for (field_name in names(fields)) {
      role <- fields[[field_name]]$role
      if (!is.null(role) && nzchar(role)) {
        out[[role]] <- field_name
      }
    }

    out
  }

  meta_roles <- collect_roles(config$meta_data)
  effect_roles <- collect_roles(config$effects)

  list(
    meta = meta_roles,
    study = meta_roles,
    effect = effect_roles
  )
}
