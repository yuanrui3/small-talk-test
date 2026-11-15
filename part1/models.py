"""Preregistered ordinal models and the GSS matched comparison."""

import numpy as np
import pandas as pd
import statsmodels.api as sm
from sklearn.metrics import roc_auc_score
from statsmodels.miscmodels.ordinal_model import OrderedModel


SEED = 41393
N_PERMUTATIONS = 10_000
LIKING_COLUMN = "liking_z"


def _control_design(controls, test=None):
    """Scale from the fit sample and drop columns redundant with the intercept or other controls."""
    centers = controls.mean()
    scales = controls.std(ddof=0)
    varying = scales.index[scales.gt(0) & controls.nunique().gt(1)]
    scaled = (controls[varying] - centers[varying]) / scales[varying]

    retained = []
    for name in varying:
        columns = retained + [name]
        if np.linalg.matrix_rank(scaled[columns].to_numpy(dtype=float)) == len(columns):
            retained.append(name)

    test_design = None
    if test is not None:
        test_design = (test[retained] - centers[retained]) / scales[retained]
    return scaled[retained], test_design


def _liking_scale(liking):
    center = liking.mean()
    scale = liking.std(ddof=0)
    if not np.isfinite(scale) or scale <= 0 or liking.nunique() < 2:
        raise ValueError("The fitting sample needs a varying liking score.")
    return center, scale


def _fit_ordinal(y, design):
    if y.nunique() < 2:
        raise ValueError("An ordinal model needs at least two observed outcome categories.")
    if design.shape[1] and np.linalg.matrix_rank(design.to_numpy(dtype=float)) < design.shape[1]:
        raise ValueError("The ordinal model cannot identify the liking coefficient with these controls.")
    design = design.astype(float) if design.shape[1] else None
    model = OrderedModel(y.astype(float), design, distr="logit")
    fit = model.fit(method="bfgs", disp=False, maxiter=1000, gtol=1e-7)
    if not fit.mle_retvals["converged"]:
        raise RuntimeError("The ordinal logistic model did not converge.")
    return fit


def _top_probability(fit, design):
    if design.shape[1] == 0:
        top = fit.model.predict(fit.params)[0, -1]
        return np.full(len(design), top)
    return fit.model.predict(fit.params, exog=design)[:, -1]


def ordinal_summary(y, controls, liking):
    """Summarize the proportional-odds coefficient of one SD of liking."""
    design, _ = _control_design(controls)
    center, scale = _liking_scale(liking)
    design = design.assign(**{LIKING_COLUMN: (liking - center) / scale})
    fit = _fit_ordinal(y, design)
    beta = float(fit.params.loc[LIKING_COLUMN])
    lo, hi = map(float, fit.conf_int().loc[LIKING_COLUMN])

    low_liking = design.assign(**{LIKING_COLUMN: -0.5})
    high_liking = design.assign(**{LIKING_COLUMN: 0.5})
    pp_change = 100 * np.mean(_top_probability(fit, high_liking) - _top_probability(fit, low_liking))
    return {
        "n": len(y),
        "beta": beta,
        "lo": lo,
        "hi": hi,
        "odds_ratio": float(np.exp(beta)),
        "odds_lo": float(np.exp(lo)),
        "odds_hi": float(np.exp(hi)),
        "pp_change": float(pp_change),
    }


def heldout_auc(y_train, y_test, controls_train, controls_test, liking_train, liking_test):
    """Compare AUCs for the same held-out rows and highest observed category."""
    result = {
        "n_train": len(y_train),
        "n_test": len(y_test),
        "controls_auc": np.nan,
        "liking_auc": np.nan,
        "delta_auc": np.nan,
        "reason": None,
    }
    if y_train.nunique() < 2:
        return result | {"reason": "The training half has fewer than two outcome categories."}
    top = max(y_train.max(), y_test.max())
    if top > y_train.max():
        return result | {"reason": "The highest outcome category is absent from the training half."}
    is_top = y_test.eq(top)
    if is_top.nunique() < 2:
        return result | {"reason": "The test half lacks either highest-category or lower-category outcomes."}

    train_design, test_design = _control_design(controls_train, controls_test)
    baseline = _fit_ordinal(y_train, train_design)
    baseline_auc = float(roc_auc_score(is_top, _top_probability(baseline, test_design)))

    center, scale = _liking_scale(liking_train)
    train_full = train_design.assign(**{LIKING_COLUMN: (liking_train - center) / scale})
    test_full = test_design.assign(**{LIKING_COLUMN: (liking_test - center) / scale})
    full = _fit_ordinal(y_train, train_full)
    full_auc = float(roc_auc_score(is_top, _top_probability(full, test_full)))
    return result | {"controls_auc": baseline_auc, "liking_auc": full_auc, "delta_auc": full_auc - baseline_auc}


def permutation_p(y, controls, liking):
    """Two-sided ordinal score test by shuffling residualized liking."""
    design, _ = _control_design(controls)
    fit = _fit_ordinal(y, design)
    upper_cdf = fit.model.predict(fit.params, which="cumprob")
    cdf = np.column_stack((np.zeros(len(y)), upper_cdf))
    rows = np.arange(len(y))
    category = fit.model.endog
    # For logistic CDF F, (F'(a) - F'(b)) / (F(b) - F(a)) = F(a) + F(b) - 1.
    ordinal_score = cdf[rows, category] + cdf[rows, category + 1] - 1

    center, scale = _liking_scale(liking)
    standardized = ((liking - center) / scale).to_numpy(dtype=float)
    design_with_intercept = np.column_stack((np.ones(len(y)), design.to_numpy(dtype=float)))
    coefficients = np.linalg.lstsq(design_with_intercept, standardized, rcond=None)[0]
    residuals = standardized - design_with_intercept @ coefficients
    observed = abs(np.dot(residuals, ordinal_score))
    rng = np.random.default_rng(SEED)
    exceedances = sum(
        abs(np.dot(rng.permutation(residuals), ordinal_score)) >= observed
        for _ in range(N_PERMUTATIONS)
    )
    return float((1 + exceedances) / (1 + N_PERMUTATIONS))


def weighted_top(y, controls, liking, weights):
    """WTSSPS-weighted binary logit using the complete primary sample's liking scale."""
    center, scale = _liking_scale(liking)
    available = weights.gt(0) & np.isfinite(weights)
    is_top = y.loc[available].eq(y.max()).astype(float)
    if is_top.nunique() < 2:
        return {"n": len(is_top), "beta": np.nan, "odds_ratio": np.nan}
    design, _ = _control_design(controls.loc[available])
    design = design.assign(**{LIKING_COLUMN: (liking.loc[available] - center) / scale})
    model = sm.GLM(
        is_top,
        sm.add_constant(design.astype(float), has_constant="add"),
        family=sm.families.Binomial(),
        freq_weights=weights.loc[available].astype(float),
    )
    fit = model.fit()
    if not fit.converged:
        raise RuntimeError("The WTSSPS-weighted logistic model did not converge.")
    beta = float(fit.params.loc[LIKING_COLUMN])
    return {"n": len(is_top), "beta": beta, "odds_ratio": float(np.exp(beta))}


def _terciles(values):
    cuts = np.quantile(values, [1 / 3, 2 / 3])
    return np.searchsorted(cuts, values, side="left") + 1


def matched_check(frame):
    """Compare global liking terciles in health-complete GSS strata."""
    result = {
        "n": len(frame),
        "strata_total": 0,
        "strata_two": 0,
        "n_extreme": 0,
        "n_extreme_matched": 0,
        "extreme_coverage": np.nan,
        "bottom_rate": np.nan,
        "top_rate": np.nan,
        "risk_difference": np.nan,
        "strata_three": 0,
        "n_three_matched": 0,
        "three_rates": {"bottom": np.nan, "middle": np.nan, "top": np.nan},
    }
    if frame.empty:
        return result

    classified = frame.assign(
        income_tercile=_terciles(frame["realinc"]),
        liking_tercile=_terciles(frame["liking"]),
        very_happy=frame["y"].eq(3),
    )
    strata = ["income_tercile", "marital", "health", "wrkstat"]
    cells = classified.groupby(strata + ["liking_tercile"], observed=True)["very_happy"]
    counts = cells.size().unstack(fill_value=0).reindex(columns=[1, 2, 3], fill_value=0)
    rates = cells.mean().unstack().reindex(columns=[1, 2, 3])

    extreme_counts = counts[[1, 3]]
    two_overlap = extreme_counts.gt(0).all(axis=1)
    n_extreme = int(extreme_counts.to_numpy().sum())
    n_extreme_matched = int(extreme_counts.loc[two_overlap].to_numpy().sum())
    result.update(
        strata_total=len(counts),
        strata_two=int(two_overlap.sum()),
        n_extreme=n_extreme,
        n_extreme_matched=n_extreme_matched,
        extreme_coverage=n_extreme_matched / n_extreme if n_extreme else np.nan,
    )
    if two_overlap.any():
        shared = extreme_counts.loc[two_overlap]
        weights = shared[1] * shared[3] / shared.sum(axis=1)
        bottom, top = np.average(rates.loc[two_overlap, [1, 3]], weights=weights, axis=0)
        result.update(bottom_rate=float(bottom), top_rate=float(top), risk_difference=float(top - bottom))

    three_overlap = counts.gt(0).all(axis=1)
    if three_overlap.any():
        shared = counts.loc[three_overlap]
        weights = 3 / (1 / shared).sum(axis=1)
        pooled = np.average(rates.loc[three_overlap], weights=weights, axis=0)
        result.update(
            strata_three=int(three_overlap.sum()),
            n_three_matched=int(shared.to_numpy().sum()),
            three_rates=dict(zip(("bottom", "middle", "top"), map(float, pooled))),
        )
    return result
