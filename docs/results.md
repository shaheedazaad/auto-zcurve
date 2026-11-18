# Understanding your results

After a run finishes, auto-zcurve stores everything in the project's
`output/` folder. Click **Open project folder** on the project page to jump
straight there, or **Download results** to get a ZIP copy.

| File | What it is |
| --- | --- |
| `report.html` | The z-curve plot, summary estimates, disclosure table, and any failed or needs-review articles. Open this in any browser. |
| `report.qmd` | The [Quarto](https://quarto.org/) source that produced the report, starting from the disclosure table. Useful if you want to rerun or tweak the statistical fit yourself. |
| `disclosure_table.csv` | Every effect auto-zcurve extracted, with supporting metadata, as a spreadsheet you can open in Excel or R. |
| `zcurve_reproduction_settings.csv` | The seed, bootstrap, parallel, and package settings used to fit the model, recorded so the result is reproducible later. |
| `extractions.json` | The structured output for every article, its extraction status and any review reasons, plus which provider, model, input mode, and parser produced it. |
| `run_log.csv` | A log of every processing attempt: status, review reasons, timing, token usage, input mode, and parser diagnostics. |
| `raw/*.json` | The raw extraction result for each article individually. |

## Reading the report

The report's estimates and plot come directly from the `zcurve` R package.
auto-zcurve automates the *extraction* step that feeds it, but the
statistical method itself is documented there — see the [zcurve package
documentation](https://cran.r-project.org/package=zcurve) for what each
estimate means and how to interpret the plot.

## Checking the extraction, not just trusting it

The **disclosure table** is there so you can audit every effect that went
into the model: which article it came from, the exact reported statistic,
and how it was classified. Before drawing conclusions from a report, spot
check a handful of rows against the original PDFs, especially for articles
with unusual formatting (tables spanning pages, statistics reported in
figures rather than text, non-standard notation).

## Extraction statuses

Each article on the **Articles** tab has one of these statuses:

| Status | Meaning | In the z-curve? |
| --- | --- | --- |
| **Done** (`ok`) | The model returned complete, valid structured output. | Yes |
| **Needs review** (`needs_review`) | The values were extracted, but something suggests they may be incomplete. | Yes, marked |
| **Failed** (`error`) | No usable values were extracted. | No |
| **Ready** | The article hasn't been processed yet. | No |

### Needs review

An extraction is marked **Needs review** instead of **Done** when:

- **"JSON was repaired"**: the model's output was not valid JSON and
  auto-zcurve had to repair it before reading it. A repair can silently drop
  the end of a response.
- **"finish reason: …"**: the model stopped for a reason other than finishing
  normally. `MAX_TOKENS` means it ran out of output space, so effects at the
  end of the article may be missing or a statistic may be cut short.
  `SAFETY`, `RECITATION`, and other reasons mean the provider stopped the
  response early. (A response with no finish reason counts as normal.)

The reasons are shown under the article's name on the **Articles** tab, in
the CLI run summary, in the report's *Articles Needing Review* section, and
are saved in `extractions.json` and `run_log.csv`. Click **View output** to
compare the extracted values against the provider's original response (and
the repaired JSON, if there is one).

Extractions that need review are **included** in the z-curve. Every effect
in `disclosure_table.csv` carries an `extraction_status` column (`ok` or
`needs_review`) and an `extraction_review_reasons` column, so you and your
readers can see which statistics were not cleanly extracted. Filter on
`extraction_status` to check those rows against the PDFs, or to rerun the
fit without them using `report.qmd`.

To re-extract them instead, use **Retry** (or `auto-zcurve retry` in the
CLI). Retry reprocesses failed and needs-review articles together. A plain
**Run analysis** skips needs-review articles, just as it skips completed
ones. Currently only Gemini extractions are checked this way.

## When an article fails

An article can fail for a few reasons: the AI model couldn't return
valid structured output for it, the PDF was corrupted or password-protected,
or it genuinely doesn't fit the schema. Failed articles are listed on the
**Articles** tab with a short error message. **Retry** reprocesses failed
(and needs-review) articles without reprocessing the ones that already
succeeded.

## Next step

See [FAQ and troubleshooting](faq.md) for common issues.
