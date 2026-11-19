# Legacy auto-zcurve Interfaces

> [!WARNING]
> **The CLI and arbitrary-folder workflow are deprecated and will be removed
> in a future version of auto-zcurve. New work should use the local
> browser app documented in [README.md](README.md).**

These interfaces remain temporarily available for reproducibility, automation,
and transition from earlier auto-zcurve versions.

## Reproducible command-line workflow

The CLI accepts an arbitrary project directory containing a `sources/` folder
of PDFs:

```sh
auto-zcurve extractors
auto-zcurve run /path/to/project --yes --extractor "Lab Gemini"
auto-zcurve retry /path/to/project --yes
```

Runs use an extractor created in the browser app under **Settings**. The
extractor sets the provider, model, API key, and request settings;
`auto-zcurve extractors` lists them. Without `--extractor`, a project uses
the extractor it ran with last. API keys come only from the extractor's saved
key, never from environment variables or `.env` files.

Common options include:

```text
--extractor NAME    run with this extractor (name, ignoring case)
--force             reprocess successful PDFs (run only)
--skip-report       skip R/Quarto report generation
--source PATH       retry one source path (retry only; repeatable)
```

An arbitrary project uses this layout:

```text
project/
├── sources/                        PDF inputs
├── extraction_schema.yml           structured extraction schema
├── extraction_instructions.md      optional project-specific instructions
└── output/                          generated artifacts
```

If `extraction_instructions.md` is absent, the bundled default instructions are
used. The CLI does not automatically invalidate old results when this file or
the extraction schema is edited; remove stale outputs or use `--force` yourself.

## Python-only installation

Technical users can install the Python package alone:

```sh
uv tool install auto-zcurve
```

This route does not install or manage R, Quarto, or the required R packages.
It also does not install the managed Docling model directory used by the local
parsing path. Complete report generation and managed local parsing therefore
require additional setup. The locked Pixi installer in the main README remains
the supported installation method.

## Launching the browser without opening it automatically

This command still uses the supported browser interface, but is useful in
terminal-driven development environments:

```sh
auto-zcurve web --no-browser
```
