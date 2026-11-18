# Understanding your results

After a run finishes, auto-zcurve stores everything in the project's
`output/` folder. Click **Open project folder** on the project page to jump
straight there, or **Download results** to get a ZIP copy.

| File | What it is |
| --- | --- |
| `report.html` | The z-curve plot, summary estimates, disclosure table, and any failed articles. Open this in any browser. |
| `report.qmd` | The [Quarto](https://quarto.org/) source that produced the report, starting from the disclosure table. Useful if you want to rerun or tweak the statistical fit yourself. |
| `disclosure_table.csv` | Every effect auto-zcurve extracted, with supporting metadata, as a spreadsheet you can open in Excel or R. |
| `zcurve_reproduction_settings.csv` | The seed, bootstrap, parallel, and package settings used to fit the model, recorded so the result is reproducible later. |
| `extractions.json` | The structured output for every article, plus which provider, model, input mode, and parser produced it. |
| `run_log.csv` | A log of every processing attempt: timing, token usage, input mode, and parser diagnostics. |
| `raw/*.json` | The raw extraction result for each article individually. |

## Spreadsheet-safe downloads

The disclosure table holds text quoted from articles. Excel and LibreOffice
treat a cell that starts with `=`, `+`, `-` or `@` (or a tab or carriage
return, even after leading spaces) as a formula. Text quoted from a PDF
could then display wrongly or, in the worst case, run a formula that a
malicious PDF planted.

To avoid this, the **Overview** tab has a **Spreadsheet-safe downloads**
section with copies of the disclosure table, the run log, and the
reproduction settings:

- Any cell that could be read as a formula starts with an apostrophe (`'`),
  headers included. The spreadsheet shows the text as it is and doesn't run
  it.
- Plain signed numbers such as `-1.25` or `-1.25e-3` are left alone, so they
  stay numeric.
- The files are saved as UTF-8 with a byte-order mark and Windows (CRLF) line
  endings, so Excel opens accented characters and symbols correctly.

**When to use them:** use the spreadsheet-safe copies whenever you open the
results in Excel, LibreOffice, Numbers, or Google Sheets. Use the regular
CSV files in the `output/` folder (or the results ZIP) for R, Python, or
any other script. Those files are never changed, and the extra apostrophes
in the spreadsheet-safe copies would show up as part of the text in a script.

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

## When an article fails

An article can fail for a few reasons: the AI model couldn't return
valid structured output for it, the PDF was corrupted or password-protected,
or it genuinely doesn't fit the schema. Failed articles are listed on the
**Articles** tab with a short error message. You can retry failed articles
without reprocessing the ones that already succeeded.

## Next step

See [FAQ and troubleshooting](faq.md) for common issues.
