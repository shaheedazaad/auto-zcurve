# auto-zcurve

auto-zcurve reads PDF or XML articles, uses an AI model to extract their focal
statistical results, and produces a z-curve analysis report with a plot,
summary statistics, and a full disclosure table.

It runs as a private browser app on your own computer.

**📖 [Read the user guide](https://shaheedazaad.github.io/auto-zcurve/)**: it
covers installing, adding an API key, running your first project, and reading
the results, and it's written for researchers rather than developers.

> [!NOTE]
> Statistic extraction has been validated as part of a study that is
> currently under peer review. Details of the validation are in the preprint's
> supplemental material:
>
> Azaad, S. (2026). Empirically derived effect size guidelines for social,
> individual differences, and cognitive psychology. PsyArXiv.
> https://doi.org/10.31234/osf.io/r4xwb_v1

## Install

**macOS and Linux:** open Terminal and paste:

```sh
curl -fsSL https://raw.githubusercontent.com/shaheedazaad/auto-zcurve/main/install.sh | sh
```

**Windows:** open PowerShell and paste:

```powershell
irm https://raw.githubusercontent.com/shaheedazaad/auto-zcurve/main/install.ps1 | iex
```

The installer brings everything it needs (Python, R, Quarto and the required
packages), so you don't need to install those yourself. It supports macOS 14+
(Apple Silicon and Intel), Windows x64 and mainstream Linux x64. Run the same
command again to update. See the [install guide](docs/installation.md) for
details and for [uninstalling](docs/installation.md#uninstalling).

## Run it

Open a new terminal and run:

```sh
auto-zcurve
```

Your browser opens the app. From there, create a project, add your articles,
choose an extractor (a provider, model and API key), and run the analysis.
[Your first project](docs/quickstart.md) walks through each step. Press
<kbd>Ctrl</kbd>+<kbd>C</kbd> in the terminal to stop the app.

## Privacy in brief

- Your articles, projects and results stay on your computer. The only thing
  sent anywhere is what extraction needs: the project's instructions and
  schema, plus each article's PDF or text, to the AI provider you choose.
- API keys are kept in your operating system's credential store, never in
  project files, logs or downloads.
- The app listens only on your own computer (`127.0.0.1`), behind a random
  one-time link.

See [API keys and privacy](docs/api-key.md) for the details.

## More

- [Contributing](CONTRIBUTING.md): development setup, tests and releases.
- [Legacy interfaces](LEGACY.md): the deprecated CLI and Python-only install.
