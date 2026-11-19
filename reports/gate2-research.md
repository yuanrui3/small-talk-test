# Research and mathematical recommendations for Gate 2

Saved 19 November 2026. Literature cutoff 18 November 2026.

The central measurement problem is separating enduring preferences and speaking style from daily changes in their expression. Recent research supports some inference of affect from language, while the specific question of tracking immediate mood from two minutes of neutral small talk remains open.

This is a prospective research note for the Gate 2 discussion. It preserves the literature review, independent mathematical analysis, synthetic calculations and proposed improvements. Participant ratings, reader predictions and transcripts were excluded from the research and simulations.

## Current pilot and scope

The current pilot continues under [the existing plan](../PLAN.md). The owner completes sessions on a fixed 14-day Indianapolis calendar. Missed dates remain missing. The cold reader is primary, and the history reader is secondary. The configured model remains Cloudflare Workers AI `@cf/openai/gpt-oss-120b`, with a paid spending cap of $0 on Workers Free, within the owner's original $10 maximum. Guesses stay hidden until pilot close.

The recommendations below are proposals for Gate 2. They do not amend the pilot protocol, analysis, prompts, topic schedule, model, costs or data. At Gate 2, estimate full-study quota needs from the pilot ledger and stop for the owner's decision before recruiting anyone else. The existing gate also requires a decision about participant access and university review-board approval if results will be published.

## Recent evidence on language and affect

The studies use different inputs, outcomes and evaluation designs. Their numerical results cannot be combined into a forecast of this pilot's performance.

| Study and status | Finding | Implication for neutral small talk |
| --- | --- | --- |
| Ringwald et al., Psychological Medicine, 21 July 2026, published | In 108 adults providing roughly 15 brief daily diaries, agreement between corresponding LLM and self-report psychopathology domains averaged r = .28 within people and r = .42 between people. | Supports the possibility of daily tracking. Participants described significant events and feelings, giving more direct evidence than neutral preferences. [Primary paper](https://doi.org/10.1017/S0033291726105261) |
| Koch et al., July 2026, author preprint | Across 410 participants and 3.45 million words of everyday digital language, private-language prediction reached median r = .12 for daily affect and r = .02 for momentary affect. Within-person momentary analyses also lacked meaningful predictive signal. | Naturalistic text can contain little information about immediate affect. The predictors used dictionaries, emoji and typing features, so contextual LLM performance remains unresolved. [Author manuscript](https://www.researchgate.net/publication/409297133_Affective_Signal_in_Everyday_Digital_Language_Across_Communication_Contexts_and_Timescales) |
| Fang et al., npj Digital Public Health, 27 October 2026, published | With 83 people over 17 weeks, Gemini predictions from smartphone-derived diaries showed positivity bias. Exact-item accuracy was 27.8–30.8%, versus 49.1% from the preceding week's self-reports. Longer context offered limited improvement. | Supports checking personal baselines, absolute error and signed bias alongside correlation. The input was sensor-derived narrative and the outcome was weekly affect. [Primary paper](https://www.nature.com/articles/s44482-026-00049-z) |

These findings make the timescale and information channel central to interpretation. Success with emotion-focused diaries does not establish success with neutral small talk. Weak results from dictionary features do not establish a ceiling for an LLM reader.

## Stable differences and daily changes

The following equations are independent analysis. Their assumptions are stated beside the relevant results.

Let X denote a liking score or language feature, Y a happiness or mood rating, and i the person. The law of total covariance gives

\[
\operatorname{Cov}(X,Y)
=\mathbb E[\operatorname{Cov}(X,Y\mid i)]
+\operatorname{Cov}(\mathbb E[X\mid i],\mathbb E[Y\mid i]).
\]

The first term concerns changes within a person. The second concerns differences between people. Part 1's cross-sectional association does not identify the daily within-person term tested in Part 2.

A person who generally likes many activities may also generally report greater happiness, while their preferences remain unchanged across good and bad days. Stable tendencies to give positive answers could also contribute to a cross-sectional association. Controlling circumstances does not fully separate that response tendency from general liking.

A minimal measurement model is

\[
Y_{it}=\mu_i+S_{it}+\epsilon_{it},
\qquad
X_{iqt}=a_{iq}+b_qS_{it}+v_{iqt}.
\]

Here mu is the person's usual rating, S is their daily departure from it, a is their enduring taste or speaking style on topic q, and b measures how strongly expression on that topic changes with daily mood. The remaining terms represent unexplained variation.

The key topic property is b. A topic can distinguish generally happier people from less happy people while having almost no sensitivity to a person's daily changes.

Chen, Montgomery and Garnett's April 2025 dynamic ordinal Gaussian-process IRT preprint combines evolving latent traits with flexible item-response curves. Its structure is relevant to this distinction, but jointly estimating topic loadings, response functions, person traits and dynamics would overwhelm 14 sessions. [Primary preprint](https://arxiv.org/abs/2504.02643)

## Language signal and rating noise

For a fixed person and topic, assume the state and the two noise terms are mutually uncorrelated and the observed variances are positive. The linear model implies

\[
\operatorname{Corr}(X,Y)^2
=
\underbrace{\frac{b^2\sigma_S^2}{b^2\sigma_S^2+\sigma_v^2}}_{\text{state information in language}}
\underbrace{\frac{\sigma_S^2}{\sigma_S^2+\sigma_\epsilon^2}}_{\text{state information in the rating}}.
\]

The conversation must express something that varies with mood, and the rating must measure the same state consistently. A better reader may extract more of the available language signal. It cannot recover information absent from the transcript.

This is an illustrative linear measurement model. Bounded ordinal ratings, nonlinear expression and mood changes during conversation can violate its assumptions. Neither fraction is estimated here, and existing research supplies no numerical ceiling for this pilot.

More generally, given transcript and history Z, the optimal squared-error predictor is E[Y | Z], and its minimum mean squared error is E[Var(Y | Z)]. The residual conditional uncertainty includes both unexpressed state information and variation in how the rating is answered.

## What history can add

Earlier transcripts may establish that a person is usually terse, enthusiastic or strongly interested in a topic. Today's departure from that pattern can then be easier to interpret.

Let F be the information in today's transcript and G include that transcript plus history, with F contained in G. For square-integrable Y and the true conditional-mean predictors mF and mG on the same population,

\[
\mathbb E[(Y-m_F)^2]-\mathbb E[(Y-m_G)^2]
=\mathbb E[(m_G-m_F)^2]\geq0.
\]

The identity concerns optimal average prediction risk. It gives no guarantee for an actual LLM or for a particular session. Longer context can lead an approximate reader to smooth away daily changes, become distracted or amplify bias.

The current history reader receives earlier transcripts without any earlier ratings. Published setups that provide labeled personal history or labeled examples give their models additional information. Compare our cold and history readers on the same eligible days and keep the cold reader primary.

## Choosing informative neutral topics

Under a Gaussian approximation, let the current state conditional on a particular history h have variance P(h). Suppose topic q produces a feature with known loading bq and independent additive Gaussian noise of variance Rq greater than zero. Its information about the latent state is

\[
I(S_t;X_q\mid H=h,Q=q)
=\tfrac12\log\!\left(1+\frac{b_q^2P(h)}{R_q}\right).
\]

Natural logarithms give information in nats. A prior variance alone is insufficient for this equality; the Gaussian and noise assumptions matter. This measures information about latent state, while information about a noisy self-rating is generally smaller. If uncertainty varies across histories, average the expression over histories.

The useful ratio is topic sensitivity to daily state relative to unrelated variation, bq squared divided by Rq. Part 1's cross-sectional associations do not identify that ratio.

Su, Choe and Chang's adaptive-testing preprint, revised 31 March 2026 and under review, derives stochastic item selection from expected posterior entropy while preserving broad item exposure. It offers a possible later approach to shortening conversations, once a measurement model has been calibrated. [Primary preprint](https://arxiv.org/abs/2504.15543v3)

For now, fixed balanced topics provide a simpler starting point. Scheduling three topics does not guarantee reaching all three before the timer ends. The current application progresses through topics by exchange count. Before a larger study, review actual topic exposure and order, and distinguish them from the planned schedule.

## Statistical resolution of 14 days

For 14 independent bivariate-normal pairs, the conventional two-sided 5% correlation threshold is

\[
|r|>0.532413.
\]

That threshold is distinct from power. In 100,000 synthetic datasets per effect size, the chance of reaching p less than .05 was

| True correlation | Simulated power with 14 independent days |
| --- | ---: |
| .30 | 18.074% |
| .50 | 47.171% |
| .70 | 84.960% |

A hypothetical observed r = .53 has a conventional approximate Fisher 95% interval of [-.000806, .827797]. These are independent-day calculations with complete observations. They support treating the owner pilot as descriptive debugging data.

For reference, the usual Fisher approximation for 80% power is

\[
n\approx3+\left[\frac{z_{.975}+z_{.8}}{\operatorname{atanh}(\rho)}\right]^2.
\]

Rounding up gives about 194 independent pairs for a true correlation of .2, 85 for .3, 47 for .4 and 30 for .5. These are illustrations, not sample-size recommendations for a repeated-measures study. The eventual 8–12-person design needs simulations incorporating temporal dependence, person-to-person differences and missing sessions.

## Temporal dependence and day shuffling

Independent day shuffling assumes exchangeability of a person's ratings under the null. It destroys serial structure. Both ratings and guesses can be persistent across days without either series containing information about the other.

Synthetic experiments used two mutually independent stationary AR(1) series, each with variance one and 14 observations. Each experiment applied a two-sided day-shuffle correlation test with 999 permutations and the plus-one correction. The seed was 41393, and there were 10,000 experiments per setting.

| Persistence in each independent series | Nominal test level | Simulated false-positive rate | Monte Carlo standard error |
| --- | ---: | ---: | ---: |
| 0 | 5% | 5.14% | 0.221 percentage points |
| .6 | 5% | 12.67% | 0.333 percentage points |

This demonstrates a possible failure mode. It does not estimate the actual pilot's error rate. Using 10,000 permutations in the planned analysis improves numerical precision but does not add observational information or restore exchangeability.

Shen et al.'s temporal independence method uses block permutation under assumptions including stationarity, weakening dependence at longer lags, and increasing blocks and block lengths. The asymptotic result does not establish reliability for a 14-point series. [Primary paper, TMLR 2024](https://arxiv.org/html/1908.06486v5)

A circular-shift sensitivity also needs temporal invariance assumptions. One complete 14-day series has only 14 distinct relative shifts, so the minimum exact shift p-value is 1/14, about .071. Repeating those shifts does not create additional permutations. Combining independent people permits more combinations but retains the assumptions.

## Baselines and correlation aggregation

For n varying ratings, the leave-one-out mean is

\[
B_t=\frac{n\bar Y-Y_t}{n-1},
\qquad
B_t-\bar Y=-\frac{Y_t-\bar Y}{n-1}.
\]

Its correlation with the observed ratings is mechanically -1. The current application appropriately compares that baseline using mean absolute error. It is a retrospective benchmark because it uses other days, including future days. Yesterday's rating and an expanding prior mean have different information constraints.

Correlation alone ignores additive bias and scale compression. A reader may follow rises and falls while being several points too high. Retain tracking correlations and absolute-error comparisons on common eligible days, and consider a clearly labeled descriptive signed-bias summary at Gate 2.

The current primary rule permits a participant with three paired observations and transforms each correlation using Fisher's z. Perfect sample correlations are clipped to 1 minus 10 to the power -12. One clipped perfect positive correlation among eight people, with seven zero correlations, yields

\[
\tanh\!\left[\frac{\operatorname{atanh}(1-10^{-12})}{8}\right]
\approx .943638.
\]

This exposes sensitivity to tiny samples and the arbitrary clipping boundary. Before recruiting, choose and preregister a stronger minimum coverage requirement and an aggregation sensitivity. Any alternative weighting or hierarchical model must state its estimand, since it may change the meaning of an equal-person average. The current pilot analysis remains as planned.

## Confidence and recent conformal methods

Yu, Zhu and Zhu's ICML 2026 method models conditional quantiles of prediction errors to stabilize time-series conformal intervals. Its conditional-validity guarantee is asymptotic under serial dependence. [Primary proceedings paper](https://proceedings.mlr.press/v306/yu26by.html)

A preprint posted 16 November 2026 combines adaptive conformal inference with anytime-valid confidence sequences to control true running or rolling average miscoverage under distribution shifts. Its guarantees concern average coverage rather than a useful narrow interval for every individual prediction. It is a recent unreviewed result, with limited grounds for immediate adoption in this pilot. [Fang, primary preprint](https://arxiv.org/abs/2611.20905)

Even under exchangeability, split conformal's calibration rank is ceil((m + 1)(1 - alpha)). With 13 calibration errors, a conventional 95% construction requires rank 14. The rank exceeds the observed errors, so the standard conservative construction returns the full outcome range after restricting predictions to 0–10. Temporal dependence creates a further validity issue.

The current confidence means the probability of landing within one rating point. Keep the planned descriptive bins and their counts. A future supplementary score could use the Brier loss

\[
\left(c_t-\mathbf1\{|\hat Y_t-Y_t|\leq1\}\right)^2.
\]

Fourteen observations support descriptive checking, with little resolution for estimating a calibration curve.

## Proposed Gate 2 discussion

1. Review pilot feasibility, missing sessions, model failures, actual topic exposure, absolute errors, signed bias and confidence counts. Keep tracking estimates descriptive.
2. Compare cold, history and baselines on common eligible days, with each baseline's information access made explicit.
3. Before recruitment, settle minimum participant coverage, treatment of extreme correlations and temporal-dependence sensitivity. Record any future amendment separately from the owner pilot's original analysis.
4. Simulate the proposed 8–12-person design with person-to-person differences, serial dependence and missingness. Raw row count alone is insufficient for a power calculation.
5. Estimate quota requirements for 8 and 12 participants from the pilot's call ledger and propose a spending cap before the owner's decision.
6. Keep adaptive topics and complex dynamic models as later possibilities requiring calibration data. The existing fixed protocol remains in use for this pilot.
7. Stop at Gate 2 for the owner's decision about recruitment and access, with the existing university review requirement if publishing.

## Reproducing the synthetic calculations

From the project folder in PowerShell, run

```powershell
.\.venv\Scripts\python.exe scripts\reproduce_gate2_math.py
```

The script uses the existing NumPy and SciPy dependencies, seed 41393 and CPU calculations. It prints JSON containing the sample-size calculations, correlation simulations and shuffle simulations. It reads no participant data, makes no API calls and creates no output files by default. The simulated numbers above are calculations, not findings from the owner pilot.
