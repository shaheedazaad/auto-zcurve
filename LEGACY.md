# Legacy Auto Z-Curve Interfaces

> [!WARNING]
> **The CLI, terminal UI, and arbitrary-folder workflow are deprecated and will
> be removed in a future version of Auto Z-Curve. New work should use the local
> browser app documented in [README.md](README.md).**

These interfaces remain temporarily available for reproducibility, automation,
and transition from earlier Auto Z-Curve versions.

## Reproducible command-line workflow

The CLI accepts an arbitrary project directory containing a `sources/` folder
of PDFs:

```sh
auto-zcurve run /path/to/project --yes --model gemini-3.6-flash
auto-zcurve retry /path/to/project --yes
```

Common options include:

```text
--api-key KEY       use a session key without saving it
--parallel N        process N PDFs concurrently
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

## Legacy terminal interface

Launch the terminal UI with:

```sh
auto-zcurve tui
```

The hidden `auto-zcurve gui` compatibility alias launches the same terminal
interface. Neither command launches a native desktop application.

## Python-only installation

Technical users can install the Python package alone:

```sh
uv tool install auto-zcurve
```

This route does not install or manage R, Quarto, or the required R packages.
Complete report generation therefore requires those dependencies to be
installed separately. The locked Pixi installer in the main README remains the
supported installation method.

## Launching the browser without opening it automatically

This command still uses the supported browser interface, but is useful in
terminal-driven development environments:

```sh
auto-zcurve web --no-browser
```
