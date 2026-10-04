source(file.path("R", "utils.R"))
source(file.path("R", "schema.R"))

expect_error <- function(expression, pattern) {
  error <- tryCatch({ force(expression); NULL }, error = identity)
  stopifnot(inherits(error, "error"), grepl(pattern, conditionMessage(error)))
}

stopifnot(
  identical(NULL %||% "fallback", "fallback"),
  identical(character() %||% "fallback", "fallback"),
  identical(FALSE %||% TRUE, FALSE),
  is.null(json_ready(NULL)),
  identical(json_ready(c(first = 1, second = 2)), list(first = 1, second = 2)),
  identical(json_ready(list(a = list(b = 1))), list(a = list(b = 1))),
  identical(json_ready(data.frame(a = 1:2))$a, 1:2),
  identical(json_ready(1:2), 1:2),
  is.na(normalize_for_table(NULL)),
  is.na(normalize_for_table(character())),
  identical(normalize_for_table(TRUE), TRUE),
  identical(normalize_for_table(FALSE), FALSE),
  identical(normalize_for_table("plain"), "plain"),
  identical(jsonlite::fromJSON(normalize_for_table(list(a = 1))), list(a = 1L)),
  identical(jsonlite::fromJSON(normalize_for_table(c(1, 2))), 1:2),
  is.na(safe_character(NULL)),
  is.na(safe_character(character())),
  identical(safe_character(c(42, 43)), "42"),
  is.na(format_p_value(NA_real_)),
  is.na(format_p_value(Inf)),
  identical(format_p_value(0.012345678), "0.0123457"),
  identical(format_p_value(0), "0")
)

scratch <- tempfile("r-foundations-")
dir.create(scratch)
on.exit_cleanup <- function() unlink(scratch, recursive = TRUE)
text_path <- file.path(scratch, "instructions.txt")
writeLines(c("First line", "Second line — UTF-8"), text_path, useBytes = TRUE)
stopifnot(identical(read_text_file(text_path), "First line\nSecond line — UTF-8"))
expect_error(read_text_file(""), "path is required")
expect_error(read_text_file(file.path(scratch, "missing")), "not found")

for (type in c("STRING", "NUMBER", "INTEGER", "BOOLEAN", "ARRAY")) {
  stopifnot(identical(normalize_schema_type(paste0(" ", tolower(type), " ")), type))
}
expect_error(normalize_schema_type(NULL), "Unsupported field type")
expect_error(normalize_schema_type("object"), "Unsupported field type")
expect_error(validate_field_spec("field", "string", "effects"), "must be a mapping")
expect_error(validate_field_spec("field", list(type = "array", items_type = "array"), "effects"), "array of arrays")
expect_error(normalize_field_section(NULL, "effects"), "at least one field")
expect_error(normalize_field_section(list(list(type = "number")), "effects"), "must be named")
stopifnot(
  identical(normalize_field_section(NULL, "meta_data", TRUE), list()),
  identical(validate_field_spec("field", list(type = "array", items = list(type = "number"), required = TRUE, role = "values"), "effects"), list(type = "ARRAY", description = NA_character_, required = TRUE, role = "values", items_type = "NUMBER")),
  identical(validate_field_spec("field", list(type = "array", items_type = "string", role = ""), "effects")$items_type, "STRING"),
  is.null(validate_field_spec("field", list(type = "string"), "effects")$role)
)

schema_path <- file.path(scratch, "schema.yml")
yaml::write_yaml(list(effects = list(p = list(type = "number", role = "p_value"), label = list(type = "string"))), schema_path)
config <- read_extraction_config(schema_path)
stopifnot(identical(config$name, "zcurve_extraction"), identical(config$meta_data, list()), identical(config$path, normalizePath(schema_path, winslash = "/")))
stopifnot(identical(build_role_lookup(config), list(meta = list(), study = list(), effect = list(p_value = "p"))))
yaml::write_yaml(list(name = "Custom", description = "Test schema", meta_data = list(study = list(type = "string", role = "study_id")), effects = list(p = list(type = "number", role = "p_value"))), schema_path)
config <- read_extraction_config(schema_path)
stopifnot(identical(config$name, "Custom"), identical(config$description, "Test schema"), identical(build_role_lookup(config)$study, list(study_id = "study")))
expect_error(read_extraction_config(file.path(scratch, "missing.yml")), "Schema file not found")
unlink(scratch, recursive = TRUE)

# Template substitution is literal and works at every position, including the end.
stopifnot(
  identical(replace_fixed_text("plain", "missing", "value"), "plain"),
  identical(replace_fixed_text("a.b.c", ".", "$1"), "a$1b$1c"),
  identical(render_text_template(NULL), ""),
  identical(render_text_template("{{name}} inside", list(name = "A")), "A inside"),
  identical(render_text_template("before {{name}} after", list(name = NULL)), "before  after"),
  identical(render_text_template("{{name}}", list(name = "A")), "A"),
  identical(render_text_template("end {{name}}", list(name = "A")), "end A"),
  identical(render_text_template("{{name}}{{name}}", list(name = "A")), "AA")
)
cat("R schema and utility tests passed\n")
