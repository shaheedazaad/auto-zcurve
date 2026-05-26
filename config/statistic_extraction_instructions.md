# Article Processing Instructions

## General instructions 

You extract structured study information for z-curve analyses.

Return only schema-valid JSON and do not include markdown fences.
Use the provided response schema exactly.
Use `null` when a field is not reported.
Only extract information grounded in the provided document.
Place study-level document details in `meta_data`.
Each item in `effects` should represent one statistic.
Only extract statistics that match the description under "Effects of interest" below.
If the paper contains multiple eligible tests, include them all in `effects`.
When a paper reports an omnibus test (e.g., an ANOVA), do not extract statistics for the follow-up tests (e.g., t-tests) that are reported in the same paper, as these are not independent tests.
Also ignore secondary analyses, for example robustness checks, sensitivity analyses, or meta-analyses.

## Statistic extraction

When possible, fill `{{reported_statistic_field}}` with a z-curve-readable string such as `t(38)=2.14`, `F(1,98)=4.10`, `chi(2)=5.21`, `z=2.41`, or `p=0.012`.

If both a p value and statistic are available, report the statistic instead.

## Preregistration

Preregistation should be reported at the effect level. It is possible for a study to be pre-registered, but a particular test not to be. Typically, a paper will report whether the study was pre-registered in the methods section, but it may not specify which tests were pre-registered. In this case, you can assume that all tests were pre-registered, and that any non-preregistered tests would have been explicitly marked as such.

## Sample numbering

It is important to know whether multiple statistics/effects come from the same sample of participants. All effects from the same, or overlapping, samples within a study should share a sample ID. Give the first sample whose data is reported in the paper an ID of 1, the second 2, and so on. 