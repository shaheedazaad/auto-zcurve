# Contributing

This guide is for working on auto-zcurve itself. If you just want to use it,
see the [user guide](https://shaheedazaad.github.io/auto-zcurve/).

## Development setup

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

## Tests

Run the test suite and the real R/Quarto release smoke test with:

```sh
pixi run test
pixi run release-smoke
```

Browser behavior tests require Node.js 24 and pnpm 10.12.1 (pinned in
`package.json`) and use a simulated DOM with mocked
network requests; they do not call model providers or access credentials:

```sh
pnpm install --frozen-lockfile
pnpm test
pnpm run test:coverage
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

Python coverage must stay at 100% (line and branch). CI runs the Python suite
under coverage on every platform in the test matrix and fails if coverage drops
below 100%. Run the same check locally in the managed Pixi environment (which
includes the `test` extra):

```sh
pixi run test-coverage
```

Outside Pixi, install the `test` extra in your development environment
(`python -m pip install -e '.[test]'`), then run:

```sh
python -m coverage run -m unittest discover -s tests
python -m coverage report
```

New Python code needs tests that keep coverage at 100%. Reserve
`# pragma: no cover` for lines that genuinely cannot run in the test
environment, and give the reason in the comment.

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

## Python-only development

For Python-only development without managed R or Quarto (requires Python 3.11
or newer):

```sh
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e .
auto-zcurve
```

Use the Pixi route when testing complete report generation.

## Releases

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

## User guide

The user guide is published at
[shaheedazaad.github.io/auto-zcurve](https://shaheedazaad.github.io/auto-zcurve/).
Its source lives in [`docs/`](docs/index.md) and is deployed automatically on
every push to `main`.

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
in every runtime dependency (FastAPI, google-genai, ...) too, which
is unnecessary just to read or edit the docs and can be slow on a plain
system `pip`.
