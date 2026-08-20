# Welcome

auto-zcurve helps you build a **z-curve analysis** from a folder of PDF
journal articles, without writing any code.

You give it PDFs. It reads each one with an AI model, pulls out the
statistical results you care about (t-tests, F-tests, z-scores, p-values...),
and turns them into a z-curve report: a plot, summary statistics, and a full
table of every effect it found, so you can check its work.

This guide is written for researchers, not programmers. If you can install an
app and use a web browser, you can use auto-zcurve.

## What it actually does, in plain terms

1. You create a **project** and drop in the PDFs you want analyzed.
2. auto-zcurve sends each PDF to an AI model (Google's Gemini, by default)
   along with instructions describing what a "reported statistic" looks like.
3. The AI returns a structured list of the effects it found in that article.
4. auto-zcurve hands that list to the `zcurve` R package, which fits the
   z-curve model and produces a report.
5. You get a webpage report, a spreadsheet of every extracted effect, and the
   files needed to reproduce the statistical fit later.

## What runs where

auto-zcurve is a small program that runs **on your own computer** and opens
in your normal web browser — it is not a website you sign up for. Nothing
about your project is uploaded anywhere, with one exception: when you run an
analysis, the PDF (or its extracted text) for each article is sent to the AI
provider you've configured, because that is how the statistics get extracted.
Nothing else — no project names, no other files — leaves your computer.

See [Getting an API key](api-key.md) for what that sending step involves and
how to keep your key safe.

## A note on validation

Statistic extraction accuracy was validated as part of a study, currently
under peer review:

> Azaad, S. (2026). *Empirically derived effect size guidelines for social,
> individual differences, and cognitive psychology.* PsyArXiv.
> [https://doi.org/10.31234/osf.io/r4xwb_v1](https://doi.org/10.31234/osf.io/r4xwb_v1)

## Where to go next

- New to auto-zcurve? Start with [Install](installation.md), then
  [Getting an API key](api-key.md), then walk through
  [Your first project](quickstart.md).
- Already have it running? Jump to [Your first project](quickstart.md).
- Confused by an output file or a report number? See
  [Understanding your results](results.md).
- Something not working? See [FAQ and troubleshooting](faq.md).
