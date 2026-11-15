# Decision log

- Reused the empty local Git repository and selected the official NORC single-year Stata file to keep the input small and preserve labeled missing codes.
- Used the bundled CPython 3.12 interpreter to make a local virtual environment because the system Python entries were Windows Store aliases.
- Held out one randomly assigned half per survey for AUC and the quiz so the reported predictive comparisons use the same rows.
- Prespecified a residual permutation score test to keep the covariate–happiness relationship intact across shuffles.
- Used the health-eligible GSS subset for exact matching because health was omitted from one ballot and cannot define strata for that ballot.
- Used a signed association and a weak-signal rule for quiz length because retaining 90% of a negligible or negative association does not establish a useful quiz.
- Reserved the common 1982/1992 omnivore citation for background because that study used arts participation surveys. GSS-specific background uses work based on the 1993 music module.
- Requested the original Slovak CSV pair because no Kaggle token is configured and the questionnaire dictionary is required before analysis.
