# Auto Z-Curve

Auto Z-Curve reads PDF articles, uses Google Gemini to extract focal
statistical results, and creates a z-curve analysis report with a plot, summary
statistics, and full disclosure table.

The default interface is a private browser app running only on your computer.
Your PDFs, projects, and results stay local; only PDF extraction requests are
sent to Gemini.

**Note:** I have validated statistic extraction as part of a study, which is currently under peer review. The preprint citation is below, and details of the validation are in the supplemental material.

Azaad, S. (2026). Empirically derived effect size guidelines for social, individual differences, and cognitive psychology. PsyArXiv. https://doi.org/10.31234/osf.io/r4xwb_v1


## Install

The supported installer uses [Pixi](https://pixi.sh/) to provide a locked
environment containing Python, R, Quarto, all required R packages, and the
Auto Z-Curve Python dependencies. You do not need to install those tools
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

The installer supports Apple Silicon and Intel macOS, Windows x64, and
mainstream glibc-based Linux x64. It downloads the latest versioned release
bundle and installs its committed `pixi.lock`. To update, run the same command
again.

## Use the browser app

Run:

```sh
auto-zcurve
```

Auto Z-Curve chooses a free local port, generates a one-time random URL token,
and opens your browser. The server binds only to `127.0.0.1` and stops when you
press <kbd>Ctrl</kbd>+<kbd>C</kbd> in the terminal.

In the app:

1. Create a named project.
2. Add several PDF articles by dragging or choosing files.
3. Review or customize the project’s extraction instructions and extraction schema.
4. Paste a Gemini API key and choose whether to remember it securely.
5. Run the analysis and follow its live progress.
6. Review the summary, view or regenerate the report, retry failed articles, open the project
   folder, or download a ZIP of the result files.

The model field accepts an exact Gemini API model ID. Its listed models are
suggestions rather than a restriction, so you can enter another model ID.

Projects are stored in the normal per-user application-data directory:

- macOS: `~/Library/Application Support/Auto Z-Curve/projects`
- Windows: `%LOCALAPPDATA%\Auto Z-Curve\projects`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/auto-zcurve/projects`

Duplicate filenames are preserved with a numbered suffix.

## Gemini API key privacy

Create a key in [Google AI Studio](https://aistudio.google.com/app/apikey).
When “Remember securely” is selected, Auto Z-Curve uses the operating system’s
credential store through Python Keyring. If Linux has no usable secret-service
backend, the app clearly marks the key as session-only. It never falls back to
a plaintext file.

API keys are not included in project files, logs, URLs, result downloads, or
reports.

## Results

Each managed project has an `output/` folder containing:

| File | Contents |
|---|---|
| `report.html` | Z-curve plot, estimates, disclosure table, and failures |
| `report.qmd` | Standalone Quarto source that reproduces the fit from the disclosure CSV |
| `disclosure_table.csv` | Every extracted effect and supporting metadata |
| `zcurve_reproduction_settings.csv` | Seed, bootstrap, parallel, and package settings used by the reproducible QMD |
| `extractions.json` | Full structured Gemini output for each PDF |
| `run_log.csv` | Processing attempts, timing, status, and token usage |
| `raw/*.json` | Per-article extraction artifacts |

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
effect after restarting Auto Z-Curve. On Apple Silicon, the first launch may
briefly compile the bundled R `zcurve` package.

Run the test suite and the real R/Quarto release smoke test with:

```sh
pixi run test
pixi run release-smoke
```

For Python-only development without managed R or Quarto:

```sh
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e .
auto-zcurve
```

Use the Pixi route when testing complete report generation.

Build release bundles:

```sh
python scripts/build_release.py
```

This creates `dist/auto-zcurve-bundle.tar.gz` for macOS/Linux and
`dist/auto-zcurve-bundle.zip` for Windows. Publish both assets with a versioned
GitHub release before advertising the installer commands.

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
