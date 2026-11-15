# Decision log

- Reused the empty local Git repository and selected the official NORC single-year Stata file to keep the input small and preserve labeled missing codes.
- Used the bundled CPython 3.12 interpreter to make a local virtual environment because the system Python entries were Windows Store aliases.
- Held out one randomly assigned half per survey for AUC and the quiz so the reported predictive comparisons use the same rows.
- Prespecified a residual permutation score test to keep the covariate–happiness relationship intact across shuffles.
- Used the health-eligible GSS subset for exact matching because health was omitted from one ballot and cannot define strata for that ballot.
- Used a signed association and a weak-signal rule for quiz length because retaining 90% of a negligible or negative association does not establish a useful quiz.
- Reserved the common 1982/1992 omnivore citation for background because that study used arts participation surveys. GSS-specific background uses work based on the 1993 music module.
- Requested the original Slovak CSV pair because no Kaggle token is configured and the questionnaire dictionary is required before analysis.
- Classified the 139 inapplicable codes on derived GSS income as nonresponse because every one corresponds to a refusal, don't-know, or blank on the source income item.
- Corrected a transcription error in the preregistered archive checksum. The loader checks `98c828bbdb20b02ae0efc067a2f24cf1afaf87aaa012d3c97008901359b04e68`, verified twice against the official archive downloaded before the plan commit.
- Required at least half of a battery's answers for each observed-item mean to avoid representing a full battery with a handful of known items.
- Used training item means only in the short quiz to compare all lengths on the same respondents without borrowing answers the short quiz would skip.
- Used respondent-level ordinal fits for the main conditional estimates and a weighted top-category sensitivity to show how poststratification affects the GSS result.
- Used the original Slovak dictionary to describe the settlement control as childhood location and the lowest responses as low enjoyment or interest.
