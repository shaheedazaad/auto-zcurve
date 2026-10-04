# auto-zcurve

> [!TIP]
> New to auto-zcurve? The [user guide](https://shaheedazaad.github.io/auto-zcurve/)
> is written for researchers, not developers, and covers install, adding an
> API key, running your first project, and reading the results.

auto-zcurve reads PDF articles, uses a supported LLM provider to extract focal
statistical results, and creates a z-curve analysis report with a plot, summary
statistics, and full disclosure table.

The default interface is a private browser app running only on your computer.
Your PDFs, projects, and results stay local; only the PDF or its locally parsed
text is sent to the selected LLM provider during extraction.

**Note:** I have validated statistic extraction as part of a study, which is currently under peer review. The preprint citation is below, and details of the validation are in the supplemental material.

Azaad, S. (2026). Empirically derived effect size guidelines for social, individual differences, and cognitive psychology. PsyArXiv. https://doi.org/10.31234/osf.io/r4xwb_v1

## Install

The supported installer uses [Pixi](https://pixi.sh/) to provide a locked
environment containing Python, R, Quarto, all required R packages, and the
auto-zcurve Python dependencies. You do not need to install those tools
separately.

### macOS and Linux

Open Terminal and paste:

```sh
curl -fsSL https://raw.githubusercontent.com/shaheedazaad/auto-zcurve/main/install.sh | sh
```

### Windows

Open PowerShell and paste:

```powershell
irm https://raw.githubusercontent.com/shaheedazaad/auto-zcurve/main/install.ps1 | iex
```

The installer supports Apple Silicon and Intel macOS 14 or newer, Windows x64,
and mainstream glibc-based Linux x64. It downloads the latest versioned release
bundle and installs its committed `pixi.lock`. To update, run the same command again. It
also adds the auto-zcurve launcher directory to your user `PATH` if needed;
open a new terminal after the first installation.

## Uninstall

Use [`uninstall.sh`](uninstall.sh) on macOS/Linux or
[`uninstall.ps1`](uninstall.ps1) on Windows. Preview with `sh uninstall.sh --dry-run`
or `.\uninstall.ps1 -WhatIf`, then run without the preview option to confirm.
Projects, settings, keys, and Pixi are preserved. See the
[uninstall guide](docs/installation.md#uninstalling) for download commands and
custom installation paths.

## Use the browser app

Run the following from your terminal:

```sh
auto-zcurve
```

auto-zcurve chooses a free local port, generates a one-time random URL token,
and opens your browser. The server binds only to `127.0.0.1` and stops when you
press <kbd>Ctrl</kbd>+<kbd>C</kbd> in the terminal.

In the app:

1. Create a named project.
2. Add several PDF articles by dragging or choosing files.
3. Review or customize the project’s extraction instructions and extraction schema.
4. Open **Settings**, expand a provider to configure its connection and default model, and set run defaults.
5. Select a Gemini model for the project.
6. Run the analysis and follow its live progress.
7. Review the summary, view or regenerate the report, retry failed articles, open the project
   folder, or download a ZIP of the result files.

Gemini accepts an exact Gemini API model ID and receives the original PDF. The
Gemini integration never falls back to prompt-only JSON or response healing.
OpenAI-compatible endpoints offer explicit JSON schema, JSON object, and
prompt-only modes; all responses are validated against the project schema.

### Model allowlist

The root [`models.yml`](models.yml) controls which Gemini models appear and are
accepted when a run starts:

```yaml
models:
  gemini:
    - id: gemini-3.6-flash
    - id: gemma-4-31b-it
    - id: gemma-4-26b-a4b-it
```

Changes are read when the model catalog is requested and again when a run starts,
so editing the file does not require rebuilding the application. Removing
`models.yml` restores live-catalog behavior for backward compatibility.

OpenRouter is available as an experimental backup provider. It is intentionally
not listed in `models.yml`: enter an exact OpenRouter model ID manually and the
app validates live support for PDF/file input and structured output before a run.
Quality, latency, and reliability can vary by model and provider route.

OpenAI-compatible Chat Completions endpoints can be configured under
**Settings → Provider connections → OpenAI-compatible**. Set the API base URL
and JSON output mode, optionally add a key, and enter the exact model ID in
the project. This provider sends locally extracted PDF text with page numbers;
scanned pages need OCR and figures are not sent. See the
[settings guide](docs/settings.md) for details.

Projects have left-hand workflow navigation. The project library supports
name search, sorting, and pagination. Settings has its own sidebar, with
expandable provider sections and independent save buttons.

Projects are stored in the normal per-user application-data directory:

- macOS: `~/Library/Application Support/Auto Z-Curve/projects`
- Windows: `%LOCALAPPDATA%\Auto Z-Curve\projects`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/auto-zcurve/projects`

Duplicate filenames are preserved with a numbered suffix.

## API key privacy

Create a Gemini key in [Google AI Studio](https://aistudio.google.com/app/apikey).
OpenRouter projects use an `OPENROUTER_API_KEY` (or its separately saved
credential) instead.
When “Remember securely” is selected, auto-zcurve uses the operating system’s
credential store through Python Keyring. If Linux has no usable secret-service
backend, the app clearly marks the key as session-only. It never falls back to
a plaintext file. Saved keys are not read
automatically when the app starts. Unlock them in Settings or let the app
load the selected provider's key when you run, retry, or validate an OpenRouter model. On macOS, the authorization
dialog may identify auto-zcurve's bundled runtime as “Python,” sometimes with
a version number.

API keys are not included in project files, logs, URLs, result downloads, or
reports.

## Results

Each managed project has an `output/` folder containing:

| File                               | Contents                                                                     |
| ---------------------------------- | ---------------------------------------------------------------------------- |
| `report.html`                      | Z-curve plot, estimates, disclosure table, and failures                      |
| `report.qmd`                       | Standalone Quarto source that reproduces the fit from the disclosure CSV     |
| `disclosure_table.csv`             | Every extracted effect and supporting metadata                               |
| `zcurve_reproduction_settings.csv` | Seed, bootstrap, parallel, and package settings used by the reproducible QMD |
| `extractions.json`                 | Structured output plus provider, model, input mode, and parser provenance     |
| `run_log.csv`                      | Attempts, timing, token usage, input mode, and parser diagnostics             |
| `raw/*.json`                       | Per-article extraction artifacts                                             |

## Documentation for researchers

A plain-language user guide — install, add an API key, run your first
project, and read the results — is published at
**[shaheedazaad.github.io/auto-zcurve](https://shaheedazaad.github.io/auto-zcurve/)**
and is not aimed at developers. Its source lives in [`docs/`](docs/index.md)
and is deployed automatically on every push to `main`.

To preview changes to it locally, it needs only `mkdocs` and the
`mkdocs-shadcn` theme, not the auto-zcurve package itself, so install it in
its own small virtual environment rather than editable-installing the whole
project:

```sh
python3 -m venv .venv-docs
source .venv-docs/bin/activate  # Windows PowerShell: .venv-docs\Scripts\Activate.ps1
pip install mkdocs mkdocs-shadcn
mkdocs serve
```

Then open the printed local address in your browser. `pip install -e ".[docs]"`
also works if you already have (or want) a full editable install, but it pulls
in every runtime dependency (FastAPI, google-genai, textual, ...) too, which
is unnecessary just to read or edit the docs and can be slow on a plain
system `pip`.

## Legacy interfaces

The CLI, TUI, arbitrary-folder workflow, and Python-only installation route
are deprecated. Their documentation has moved to [LEGACY.md](LEGACY.md).

## Development

For full local development, use Pixi so Python, R, Quarto, and the required
packages match the locked release environment. Install Pixi if needed:

```sh
# macOS and Linux
curl -fsSL https://pixi.sh/install.sh | sh
```

```powershell
# Windows PowerShell
irm https://pixi.sh/install.ps1 | iex
```

Restart the terminal after installing Pixi. Then, from the repository root:

```sh
pixi install --locked
pixi run auto-zcurve
```

The Python package is installed in editable mode, so source changes take
effect after restarting auto-zcurve. On Apple Silicon, the first launch may
briefly compile the bundled R `zcurve` package.

Run the test suite and the real R/Quarto release smoke test with:

```sh
pixi run test
pixi run release-smoke
```

Browser behavior tests require Node.js 24 and use a simulated DOM with mocked
network requests; they do not call model providers or access credentials:

```sh
npm ci
npm test
npm run test:coverage
```

The browser coverage command measures `auto_zcurve/static/app.js` and fails if
line coverage falls below 100%. CI runs this separately from the Python/R
matrix. Branch and function coverage are also reported.
The Python suite also runs the client script against real server-rendered home,
settings, and project pages when Node.js and these dependencies are installed.
CI installs them for every supported platform in the test matrix.

The client retains defensive fallbacks for missing message elements, empty
message-state inputs, and empty provider responses, plus two initial no-op
callbacks. Current page flows cannot invoke those paths; they remain visible
as uncovered branches/functions rather than being excluded from measurement.

For Python coverage, install the `test` extra in your development environment
(`python -m pip install -e '.[test]'`), then run:

```sh
python -m coverage run -m unittest discover -s tests
python -m coverage report
```

This measures Python application and release-tooling coverage. R regression
scripts run through unittest when `Rscript` is available; they are not included
in the Python coverage percentage. Use the managed Pixi environment to run them
with the project libraries. The real R/Quarto render is opt-in through
`pixi run release-smoke` and is skipped in the ordinary suite.

Python's coverage report fails below 100% measured line and branch coverage.
To measure the R sources, install the R package `covr` in your active R library
and run from the repository root:

```sh
Rscript scripts/check_r_coverage.R
# Optionally export the expression-level counts for inspection:
Rscript scripts/check_r_coverage.R /tmp/auto-zcurve-r-coverage.csv
```

The R check runs every `tests/r/test_*.R` script and enforces 100% coverage of
`R/*.R`. R's expression coverage and JavaScript/Python branch coverage are
different measurements; the real render smoke test remains a separate check.

For Python-only development without managed R or Quarto:

```sh
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e .
auto-zcurve
```

Use the Pixi route when testing complete report generation.

Before publishing, update the version in `pyproject.toml`, `pixi.toml`, and
`auto_zcurve/__init__.py`, then commit and push the change. Build, test, and
publish both release bundles with:

```sh
python scripts/publish_release.py
```

The publisher requires an authenticated [GitHub CLI](https://cli.github.com/),
a clean checkout whose current commit is pushed, and matching project versions.
It runs the locked tests and release smoke test, creates
`dist/auto-zcurve-bundle.tar.gz` and `dist/auto-zcurve-bundle.zip`, and publishes
them in a new versioned GitHub release. Pass `--draft` to review the release
before publishing or `--dry-run` to perform every local step without creating
the GitHub release.

## Security model

The local service uses:

- a cryptographically random token in every app URL;
- strict localhost `Host` validation and cross-origin request rejection;
- a fixed `127.0.0.1` bind with no novice-facing network option;
- streaming file limits, PDF signature checks, filename sanitization, and
  project-root containment;
- result-only ZIP downloads that exclude credentials and source PDFs.

The browser app is local software, not a hosted service. Anyone with access to
your operating-system account and managed project directory can read the local
project files.

### Extraction coverage and eligibility

New projects extract all reported statistical tests, including those in tables,
figures, captions, and available appendices. Every test stays in
`disclosure_table.csv`, with `eligible` and `eligibility_explanation` columns.
Only explicitly eligible tests with usable statistics enter the z-curve.
The default eligibility criteria select title/abstract claims and exclude
follow-up tests when an omnibus test is reported, plus secondary analyses.
Change these criteria in the project's extraction instructions as needed.

Existing projects retain their saved schemas, instructions, and results. When
an older schema has no eligibility field, its extracted effects are treated as
eligible under its original extraction criteria; regenerated disclosure tables
label this legacy assumption. A schema that defines eligibility requires an
explicit true value for inclusion. To collect previously omitted tests in an
old project, update its schema and instructions to the new defaults and rerun
extraction (the app's existing reset confirmation applies).
