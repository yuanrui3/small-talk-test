"""Reproduce the Part 1 analyses from the two original survey downloads."""

import os

for variable in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS"):
    os.environ[variable] = "1"

from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

from .data import item_scores, load_gss, load_young
from .models import heldout_auc, matched_check, ordinal_summary, permutation_p, weighted_top
from .quiz import evaluate_quiz

matplotlib.use("Agg")
from matplotlib import pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
DOMAIN_NAMES = {
    "music": "Music",
    "film": "Film",
    "hobbies": "Hobbies and interests",
    "combined": "Combined",
}


def encode_controls(survey, reference, frame, extra=()):
    """Use training scales and levels when encoding held-out rows."""
    design = pd.DataFrame(index=frame.index)
    unseen = {}
    for name in survey.numeric:
        base, values = reference[name], frame[name]
        if name == "age_sq":
            center = reference["age"].mean()
            base = (reference["age"] - center) ** 2
            values = (frame["age"] - center) ** 2
        scale = base.std(ddof=0)
        if scale > 0:
            design[name] = (values - base.mean()) / scale
    for name in (*survey.categorical, *extra):
        levels = sorted(reference[name].unique())
        for level in levels[1:]:
            design[f"{name}={level}"] = (frame[name] == level).astype(float)
        unseen[name] = int((~frame[name].isin(levels)).sum())
    return design, {name: count for name, count in unseen.items() if count}


def analyze_domain(survey, domain):
    source = survey.frame
    items = survey.domains[domain]
    scores = item_scores(source[items])
    eligibility = (
        [survey.domains[key] for key in ("music", "film", "hobbies")]
        if domain == "combined"
        else [items]
    )
    eligible = pd.Series(True, index=source.index)
    for battery in eligibility:
        eligible &= source[battery].notna().sum(axis=1) >= (len(battery) + 1) // 2
    columns = ["y", *survey.controls]
    cases = source.loc[eligible].dropna(subset=columns).join(scores)
    train = cases.loc[cases["train"]]
    test = cases.loc[~cases["train"]]
    controls, _ = encode_controls(survey, cases, cases)
    train_controls, _ = encode_controls(survey, train, train)
    test_controls, unseen = encode_controls(survey, train, test)

    age_education = controls.filter(regex=r"^(age|age_sq|educ)(=|$)")
    nuisance = np.column_stack((np.ones(len(cases)), age_education))
    projection = np.linalg.lstsq(nuisance, cases["liking"], rcond=None)[0]
    residual_liking = cases["liking"] - nuisance @ projection

    style_terms = ["endpoint_share", "item_sd", "answered_share"]
    style = cases[style_terms]
    style = style.loc[:, style.std(ddof=0) > 0]
    style_controls = controls.join((style - style.mean()) / style.std(ddof=0))
    y = cases["y"]
    return {
        "n_wave": len(source),
        "n_eligible": int(eligible.sum()),
        "n_outcome": int(source.loc[eligible, "y"].notna().sum()),
        "cases": cases,
        "controls": controls,
        "mean_scores": cases[scores.columns].mean(),
        "ordinal": ordinal_summary(y, controls, cases["liking"]),
        "residual_ordinal": ordinal_summary(y, controls, residual_liking),
        "style_ordinal": ordinal_summary(y, style_controls, cases["liking"]),
        "raw_r": y.corr(cases["liking"]),
        "residual_r": y.corr(residual_liking),
        "p": permutation_p(y, controls, cases["liking"]),
        "auc": heldout_auc(
            train["y"], test["y"], train_controls, test_controls,
            train["liking"], test["liking"],
        ),
        "unseen": unseen,
        "quiz": evaluate_quiz(
            train[items], test[items], train["y"], test["y"],
            train_controls, test_controls,
        ),
    }


def gss_checks(survey, analysis):
    cases = analysis["cases"]
    health_cases = cases.loc[cases["ballot"].isin([1, 3])].dropna(subset=["health"])
    match_cases = health_cases.dropna(subset=["realinc", "marital", "wrkstat"])
    controls, _ = encode_controls(survey, health_cases, health_cases)
    with_health, _ = encode_controls(survey, health_cases, health_cases, extra=("health",))
    y, liking = health_cases["y"], health_cases["liking"]
    return {
        "weighted": weighted_top(
            cases["y"], analysis["controls"], cases["liking"], cases["wtssps"],
        ),
        "matched": matched_check(match_cases),
        "health_without": ordinal_summary(y, controls, liking),
        "health_with": ordinal_summary(y, with_health, liking),
    }


def format_number(value, pattern=".3f"):
    return format(value, pattern) if np.isfinite(value) else "—"


def effect(fit):
    return f"{fit['beta']:+.3f} [{fit['lo']:+.3f}, {fit['hi']:+.3f}]"


def odds(fit):
    return f"{fit['odds_ratio']:.2f} [{fit['odds_lo']:.2f}, {fit['odds_hi']:.2f}]"


def quiz_text(quiz, labels):
    k = quiz["minimum_k"]
    full = quiz["full_test_r"]
    lower, upper = quiz["full_test_ci"]
    interval = f"{format_number(full)} (95% bootstrap interval {format_number(lower)} to {format_number(upper)})"
    if k is None:
        target = "No trained prefix reached the 90% target." if full > 0 else "The 90% target was undefined."
        return f"The full-battery held-out correlation was {interval}. {target}"
    selected = ", ".join(labels[item] for item in quiz["selected_items"])
    length = f"{k} item" if k == 1 else f"{k} items"
    attained = format_number(quiz["greedy_test"][k - 1])
    support = (
        " The threshold crossing is exploratory because the full-battery association was weak."
        if quiz["weak_support"] else ""
    )
    return (
        f"The full-battery held-out correlation was {interval}. The first crossing was "
        f"**{length}** ({selected}, r = {attained}).{support}"
    )


def draw_quiz(quiz, title, path):
    greedy, l1 = quiz["greedy_test"], quiz["l1_test"]
    counts = quiz["k"]
    fig, ax = plt.subplots(figsize=(7.5, 4.2), layout="constrained")
    ax.plot(counts, greedy, color="#175470", marker="o", markersize=3, label="Greedy order")
    ax.plot(counts, l1, color="#a34725", linestyle="--", label="L1 coefficient order")
    ax.axhline(0, color="#8a9298", linewidth=0.8)
    if np.isfinite(quiz["target_r"]):
        ax.axhline(quiz["target_r"], color="#636976", linestyle=":", label="90% of full battery")
    if quiz["minimum_k"] is not None:
        k = quiz["minimum_k"]
        ax.scatter(k, greedy[k - 1], color="#175470", s=48, zorder=3)
    if counts:
        step = max(1, (len(counts) + 9) // 10)
        ax.set_xticks(sorted({*counts[::step], *counts[-1:]}))
        ax.set_xlim(0.5, counts[-1] + 0.5)
    ax.set(xlabel="Number of items", ylabel="Held-out adjusted correlation", title=title)
    ax.grid(axis="y", color="#e3e8eb")
    ax.legend(frameon=False)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def domain_details(survey, domain, result, filename):
    q = result["quiz"]
    auc = result["auc"]
    fit = result["ordinal"]
    means = result["mean_scores"]
    low_rating = "disliked items" if survey.name == "1993 General Social Survey" else "low ratings (1–2)"
    train_cells = q["n_train"] * len(q["k"])
    test_cells = q["n_test"] * len(q["k"])
    train_missing = q["train_imputed_cells"] / train_cells if train_cells else np.nan
    test_missing = q["test_imputed_cells"] / test_cells if test_cells else np.nan
    rows = [
        f"### {survey.name} · {DOMAIN_NAMES[domain]}",
        "",
        f"Of {result['n_wave']:,} survey records, {result['n_eligible']:,} met the battery coverage requirement, "
        f"{result['n_outcome']:,} of those had an outcome, and {fit['n']:,} entered the complete-case analysis "
        f"({auc['n_train']:,} train, {auc['n_test']:,} test).",
        "",
        "| Estimate | Value |",
        "| --- | ---: |",
        f"| Primary log-odds slope per SD (95% CI) | {effect(fit)} |",
        f"| Cumulative odds ratio per SD (95% CI) | {odds(fit)} |",
        f"| Average modeled change in highest-outcome probability per SD | {fit['pp_change']:+.2f} pp |",
        f"| Two-sided permutation p | {result['p']:.4f} |",
        f"| Test AUC, controls only | {format_number(auc['controls_auc'])} |",
        f"| Test AUC, controls and liking | {format_number(auc['liking_auc'])} |",
        f"| Paired test AUC difference | {format_number(auc['delta_auc'], '+.3f')} |",
        f"| With liking residualized on age and education, slope (95% CI) | {effect(result['residual_ordinal'])} |",
        f"| Unadjusted r, raw liking / age- and education-residualized liking | {result['raw_r']:+.3f} / {result['residual_r']:+.3f} |",
        f"| With response-style terms, slope (95% CI) | {effect(result['style_ordinal'])} |",
        "",
        f"Mean score components across complete cases were {means['liking']:.2f} average liking, "
        f"{means['disliked']:.2f} {low_rating}, {means['very_liked']:.2f} maximum ratings, "
        f"{means['item_sd']:.2f} within-person SD, and {means['valid_count']:.2f} valid items.",
        "",
        quiz_text(q, survey.labels),
    ]
    l1_crossings = [i for i, correlation in enumerate(q["l1_test"]) if correlation >= q["target_r"]]
    if l1_crossings:
        first_crossing = l1_crossings[0]
        l1_items = ", ".join(survey.labels[item] for item in q["l1_order"][:first_crossing + 1])
        rows.append(
            f"The L1 order first crossed at k = {first_crossing + 1} "
            f"({l1_items}, r = {q['l1_test'][first_crossing]:.3f})."
        )
    rows += [
        f"Quiz gaps filled using training item means were {format_number(train_missing, '.1%')} in training and "
        f"{format_number(test_missing, '.1%')} in testing.",
        "",
        f"![Held-out association by number of items for {survey.name} {DOMAIN_NAMES[domain]}]({filename})",
        "",
    ]
    if result["unseen"]:
        unknown = ", ".join(f"{name} ({count})" for name, count in result["unseen"].items())
        rows += [f"Held-out factor responses mapped to training reference levels: {unknown}.", ""]
    if q["dropped_items"]:
        dropped = ", ".join(survey.labels[item] for item in q["dropped_items"])
        rows += [f"Items with no valid training response: {dropped}.", ""]
    if auc["reason"]:
        rows += [f"Held-out AUC unavailable. {auc['reason']}", ""]
    return rows


def write_report(out, gss, gss_results, checks, young, young_results):
    result = gss_results["music"]
    fit = result["ordinal"]
    auc = result["auc"]
    direction = "higher" if fit["pp_change"] >= 0 else "lower"
    gss_interpretation = (
        "The GSS showed no clear association between average music liking and happiness. "
        if fit["lo"] <= 0 <= fit["hi"] else ""
    )
    report = [
        "# Part 1 · General liking and happiness",
        "",
        "## Findings",
        "",
        f"**1993 GSS.** {gss_interpretation}Among {fit['n']:,} adults with analyzable responses, a one-standard-deviation "
        f"increase in average music liking corresponded to a {abs(fit['pp_change']):.1f} percentage-point "
        f"{direction} modeled probability of saying “very happy.” The adjusted cumulative odds ratio was "
        f"{odds(fit)} (95% CI), with permutation p = {result['p']:.4f}. On held-out respondents, adding "
        f"liking moved AUC from {format_number(auc['controls_auc'])} to {format_number(auc['liking_auc'])} "
        f"({format_number(auc['delta_auc'], '+.3f')}).",
        "",
        f"**Shortest quiz.** {quiz_text(result['quiz'], gss.labels)}",
        "",
    ]

    matched = checks["matched"]
    if matched["strata_two"]:
        report += [
            f"**Matched check.** Within comparable income, marital, health and work groups, "
            f"the pooled difference in “very happy” was {matched['risk_difference'] * 100:+.1f} percentage points "
            f"({matched['top_rate']:.1%} in the top liking tercile and {matched['bottom_rate']:.1%} in the bottom). "
            f"The comparison covered "
            f"{matched['n_extreme_matched']:,} people in {matched['strata_two']:,} overlapping strata.",
            "",
        ]
    if young is None:
        report += [
            "**Replication pending.** The original Slovak questionnaire dictionary and response CSV "
            "are required to estimate replication. This report contains the GSS results while those files are pending.",
            "",
        ]
    else:
        repl = young_results["combined"]
        repl_fit = repl["ordinal"]
        hobby_fit = young_results["hobbies"]["ordinal"]
        music_fit = young_results["music"]["ordinal"]
        film_fit = young_results["film"]["ordinal"]
        if repl_fit["lo"] > 0 and fit["lo"] > 0:
            interpretation = "Both surveys support the hypothesized positive direction."
        elif repl_fit["lo"] > 0:
            interpretation = "The Slovak survey supports the hypothesized positive direction and the GSS estimate is inconclusive."
        elif repl_fit["hi"] < 0:
            interpretation = "The combined Slovak result points in the opposite direction."
        else:
            interpretation = "The combined Slovak interval includes no association, so replication is inconclusive."
        report += [
            f"**Slovak replication.** In {repl_fit['n']:,} Young People Survey responses, the combined score "
            f"had an adjusted cumulative odds ratio of {odds(repl_fit)} per SD (95% CI), with permutation "
            f"p = {repl['p']:.4f}. A one-SD difference in liking corresponded to a "
            f"{repl_fit['pp_change']:+.1f} percentage-point change in the modeled probability of the highest happiness rating. "
            f"The descriptive domain odds ratios were {hobby_fit['odds_ratio']:.2f} for hobbies and interests, "
            f"{music_fit['odds_ratio']:.2f} for music, and {film_fit['odds_ratio']:.2f} for film. {interpretation}",
            "",
            f"**Slovak combined quiz.** {quiz_text(repl['quiz'], young.labels)}",
            "",
        ]

    report += [
        "## Methods and results",
        "",
        "The analysis followed the [plan committed before inspecting outcomes](../PLAN.md). Higher "
        "ratings mean more liking and higher ordered outcome values mean greater happiness. The liking "
        "score averages observed ratings. The main model adjusts for the retained background variables "
        "using ordinal logistic regression. AUC and quiz associations use the preassigned held-out half. "
        "AUC differences use the unrounded paired estimates.",
        "",
        "The GSS music battery and missing codes come from the NORC documentation and single-year file. "
        "[NORC documentation](https://gss.norc.org/get-documentation.html) · "
        "[NORC Stata files](https://gss.norc.org/get-the-data/stata.html)",
        "",
        "### GSS control missingness",
        "",
        "| Control | Design missing | Total missing | Primary model |",
        "| --- | ---: | ---: | --- |",
    ]
    for name, row in gss.audit["missingness"].iterrows():
        report.append(
            f"| {name.upper()} | {int(row['design_missing_n']):,} "
            f"({row['design_missing_pct']:.1f}%) | {int(row['missing_n']):,} "
            f"({row['missing_pct']:.1f}%) | {'retained' if row['retained'] else 'excluded'} |"
        )
    report.append("")

    for prefix, survey, results in (("gss", gss, gss_results), ("young", young, young_results)):
        if survey is None:
            continue
        for domain, analysis in results.items():
            filename = f"figures/{prefix}-{domain}-quiz.png"
            draw_quiz(analysis["quiz"], f"{survey.name} · {DOMAIN_NAMES[domain]}", out / filename)
            report.extend(domain_details(survey, domain, analysis, filename))

    health = checks["health_with"]
    weighted = checks["weighted"]
    report += [
        "### GSS matched and weighting checks",
        "",
        f"Among {matched['n']:,} health-complete matching cases, "
        f"{matched['strata_two']:,} of {matched['strata_total']:,} occupied strata contained both extremes. "
        f"They covered {matched['n_extreme_matched']:,} of {matched['n_extreme']:,} extreme-tercile "
        f"respondents ({format_number(matched['extreme_coverage'], '.1%')}). The shared-stratum rates "
        f"for bottom and top were {format_number(matched['bottom_rate'], '.1%')} and "
        f"{format_number(matched['top_rate'], '.1%')}.",
        "",
        f"The three-tercile display used {matched['strata_three']:,} strata and "
        f"{matched['n_three_matched']:,} respondents. Pooled “very happy” rates for bottom, middle, and top "
        f"were {', '.join(format_number(matched['three_rates'][key], '.1%') for key in ('bottom', 'middle', 'top'))}.",
        "",
        f"In the same {health['n']:,} health-complete cases, the liking slope was "
        f"{effect(checks['health_without'])} without health and {effect(health)} with health (95% CIs).",
        "",
    ]
    if weighted["n"]:
        report += [
            f"As a weighting sensitivity, the WTSSPS-weighted binary model for “very happy” gave an odds "
            f"ratio of {weighted['odds_ratio']:.2f} per SD ({weighted['n']:,} cases).",
            "",
        ]
    if young is not None:
        report += [
            "### Slovak questionnaire coverage",
            "",
            "The original questionnaire dictionary defined the item domains. The combined battery uses "
            "all items listed below. [Young People Survey](https://www.kaggle.com/datasets/miroslavsabo/young-people-survey)",
            "",
            f"The happiness outcome prompt was “{young.audit['outcome_prompt']}” "
            "Respondents rated their agreement on a 1–5 scale. Genre ratings ranged from low to high "
            "enjoyment. Hobby and interest ratings ranged from low to high interest. "
            "Village/town refers to where the respondent spent most of childhood.",
            "",
            "| Background field | Missing responses |",
            "| --- | ---: |",
        ]
        for name, row in young.audit["missingness"].iterrows():
            report.append(f"| {name} | {int(row['missing_n']):,} ({row['missing_pct']:.1f}%) |")
        if young.audit["unavailable_controls"]:
            unavailable = ", ".join(young.audit["unavailable_controls"])
            report += ["", f"Background fields unavailable in the extract were {unavailable}."]
        for domain in ("music", "film", "hobbies"):
            report += ["", f"#### {DOMAIN_NAMES[domain]}", "", "| Item | Original prompt |", "| --- | --- |"]
            for item in young.domains[domain]:
                prompt = " ".join(young.audit["item_prompts"][item].split()).replace("|", r"\|")
                report.append(f"| {young.labels[item]} | {prompt} |")
        report.append("")

    report += [
        "## Limitations",
        "",
        "- These are cross-sectional self-reports. Shared response tendencies, familiarity with the "
        "items, omitted circumstances, and the direction of influence remain open.",
        "- The main ordinal result and its interval are respondent-level and unweighted. The intervals "
        "do not account for GSS sampling clusters. The permutation result assumes exchangeable "
        "covariate-adjusted liking residuals.",
        "- GSS music taste varies by education and age. Residualizing on the same age and education terms "
        "already in the regression changes the scale of its coefficient while preserving the fitted "
        "probabilities. The unadjusted correlations show what changes before the remaining controls enter. "
        "[Bryson (1997)](https://doi.org/10.1016/S0304-422X(97)00008-9)",
        "- Exact matching describes the subset with overlapping liking groups. The shortest observed "
        "quiz scans several lengths on the test half, so the chosen length needs independent validation.",
        "- The surveys differ in age range, recruitment, wording, culture, and item content. Their effect "
        "sizes describe their respective analyzed samples.",
        "",
        "## Reproduction",
        "",
        "From the repository root, regenerate this report and the figures with `.venv\\Scripts\\python.exe -m part1`.",
        "",
        "| Raw input | SHA-256 |",
        "| --- | --- |",
    ]
    for survey in (gss, young):
        if survey is not None:
            for filename, checksum in survey.audit["sha256"].items():
                report.append(f"| `{filename}` | `{checksum}` |")
    report += [
        "",
    ]
    (out / "part1.md").write_text("\n".join(report), encoding="utf-8", newline="\n")


def main():
    raw = ROOT / "data" / "raw"
    out = ROOT / "reports"
    (out / "figures").mkdir(parents=True, exist_ok=True)
    gss = load_gss(raw)
    young = None
    missing = [name for name in ("columns.csv", "responses.csv") if not (raw / name).is_file()]
    if not missing:
        young = load_young(raw)

    gss_results = {"music": analyze_domain(gss, "music")}
    checks = gss_checks(gss, gss_results["music"])
    young_results = {} if young is None else {key: analyze_domain(young, key) for key in young.domains}
    write_report(out, gss, gss_results, checks, young, young_results)
    print(f"Wrote {out / 'part1.md'}")
    if missing:
        print(f"Replication pending. Add to {raw}: {', '.join(missing)}")


if __name__ == "__main__":
    main()
