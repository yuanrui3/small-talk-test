"""Train question orders and evaluate fixed-length quizzes on held-out respondents."""

import numpy as np
import pandas as pd
from sklearn.linear_model import LassoCV
from sklearn.model_selection import KFold


SEED = 41393
BOOTSTRAP_DRAWS = 2_000
MIN_RESIDUAL_SD = 1e-12
CORRELATION_TIE_TOLERANCE = 8 * np.finfo(float).eps


def _pearson(items: np.ndarray, outcome: np.ndarray) -> np.ndarray:
    """Correlate each column with the outcome, preserving undefined correlations."""
    correlations = np.full(items.shape[1], np.nan)
    if len(outcome) < 2:
        return correlations

    centered_items = items - items.mean(axis=0)
    centered_outcome = outcome - outcome.mean()
    item_norms = np.linalg.norm(centered_items, axis=0)
    outcome_norm = np.linalg.norm(centered_outcome)
    min_norm = MIN_RESIDUAL_SD * np.sqrt(len(outcome))
    if outcome_norm > min_norm:
        valid = item_norms > min_norm
        correlations[valid] = (centered_items[:, valid].T @ centered_outcome) / (
            item_norms[valid] * outcome_norm
        )
    return np.clip(correlations, -1.0, 1.0)


def _greedy_order(items: np.ndarray, outcome: np.ndarray) -> tuple[list[int], list[float]]:
    remaining = list(range(items.shape[1]))
    order = []
    associations = []
    selected_sum = np.zeros(len(outcome))
    while remaining:
        trial_means = (selected_sum[:, None] + items[:, remaining]) / (len(order) + 1)
        trial_associations = _pearson(trial_means, outcome)
        available = np.flatnonzero(np.isfinite(trial_associations))
        choice = 0
        if available.size:
            best = trial_associations[available].max()
            # Candidates are visited in source order, including numerical ties.
            tied = trial_associations[available] >= best - CORRELATION_TIE_TOLERANCE
            choice = int(available[tied][0])
        selected = remaining.pop(choice)
        order.append(selected)
        associations.append(float(trial_associations[choice]))
        selected_sum += items[:, selected]
    return order, associations


def _lasso_order(items: np.ndarray, outcome: np.ndarray) -> tuple[list[int], float | None]:
    if len(outcome) < 5 or items.shape[1] == 0 or outcome.std() <= MIN_RESIDUAL_SD:
        return [], None

    centered = items - items.mean(axis=0)
    item_sd = items.std(axis=0)
    varying = item_sd > MIN_RESIDUAL_SD
    if not varying.any():
        return [], None

    scaled = np.zeros_like(centered)
    scaled[:, varying] = centered[:, varying] / item_sd[varying]
    folds = KFold(n_splits=5, shuffle=True, random_state=SEED)
    model = LassoCV(cv=folds, max_iter=10_000, n_jobs=1).fit(scaled, outcome)
    order = np.argsort(-np.abs(model.coef_), kind="stable")
    return order.tolist(), float(model.alpha_)


def _held_out_curve(items: np.ndarray, outcome: np.ndarray, order: list[int]) -> list[float]:
    means_by_k = np.cumsum(items[:, order], axis=1) / np.arange(1, len(order) + 1)
    if order:
        means_by_k[:, -1] = items.mean(axis=1)
    return _pearson(means_by_k, outcome).tolist()


def _bootstrap_interval(score: np.ndarray, outcome: np.ndarray) -> tuple[tuple[float, float], int]:
    rng = np.random.default_rng(SEED)
    draws = np.empty(BOOTSTRAP_DRAWS)
    for draw in range(BOOTSTRAP_DRAWS):
        rows = rng.integers(0, len(outcome), size=len(outcome))
        draws[draw] = _pearson(score[rows, None], outcome[rows])[0]

    valid_draws = draws[np.isfinite(draws)]
    if valid_draws.size == 0:
        return (np.nan, np.nan), 0
    lower, upper = np.percentile(valid_draws, [2.5, 97.5])
    return (float(lower), float(upper)), int(valid_draws.size)


def evaluate_quiz(
    items_train: pd.DataFrame,
    items_test: pd.DataFrame,
    y_train: pd.Series,
    y_test: pd.Series,
    controls_train: pd.DataFrame,
    controls_test: pd.DataFrame,
) -> dict:
    """Evaluate both fixed question orders on the same held-out respondents.

    Item columns follow source order. Missing ratings are allowed. The outcome
    is ordered numeric, and the numeric control columns exclude the intercept.
    Inputs for each half must use the same respondent index and row order.
    """
    if not items_train.columns.is_unique or not items_train.columns.equals(items_test.columns):
        raise ValueError("Quiz item names must be unique and ordered identically in both halves")
    if not controls_train.columns.equals(controls_test.columns):
        raise ValueError("Quiz controls must have the same columns in both halves")
    for items, outcome, controls in (
        (items_train, y_train, controls_train),
        (items_test, y_test, controls_test),
    ):
        if not items.index.equals(outcome.index) or not items.index.equals(controls.index):
            raise ValueError("Quiz items, outcomes, and controls must share the same row index")

    training_means = items_train.mean(axis=0).dropna()
    columns = training_means.index.tolist()
    dropped_items = items_train.columns[~items_train.columns.isin(columns)].tolist()
    train_ratings = items_train.loc[:, columns]
    test_ratings = items_test.loc[:, columns]

    train_design = np.column_stack((np.ones(len(y_train)), controls_train.to_numpy(dtype=float)))
    test_design = np.column_stack((np.ones(len(y_test)), controls_test.to_numpy(dtype=float)))
    train_values = np.column_stack((y_train, train_ratings.fillna(training_means))).astype(float)
    test_values = np.column_stack((y_test, test_ratings.fillna(training_means))).astype(float)
    if not all(np.isfinite(values).all() for values in (train_design, test_design, train_values, test_values)):
        raise ValueError("Quiz outcomes and controls must be complete and quiz ratings must be finite or missing")

    coefficients = np.linalg.lstsq(train_design, train_values, rcond=None)[0]
    train_residuals = train_values - train_design @ coefficients
    test_residuals = test_values - test_design @ coefficients

    greedy, greedy_train = _greedy_order(train_residuals[:, 1:], train_residuals[:, 0])
    lasso, l1_alpha = _lasso_order(train_residuals[:, 1:], train_residuals[:, 0])
    greedy_test = _held_out_curve(test_residuals[:, 1:], test_residuals[:, 0], greedy)
    l1_test = _held_out_curve(test_residuals[:, 1:], test_residuals[:, 0], lasso)
    if not lasso:
        l1_test = [np.nan] * len(columns)

    full_r = greedy_test[-1] if greedy_test else np.nan
    full_ci = (np.nan, np.nan)
    valid_draws = 0
    if np.isfinite(full_r):
        full_score = test_residuals[:, 1:].mean(axis=1)
        full_ci, valid_draws = _bootstrap_interval(full_score, test_residuals[:, 0])

    target = 0.9 * full_r if full_r > 0 else np.nan
    # A source-order fallback after an undefined training association cannot
    # establish a trained candidate, even if its test association crosses 90%.
    trained_prefix = np.logical_and.accumulate(np.isfinite(greedy_train))
    crossings = np.flatnonzero(trained_prefix & (np.asarray(greedy_test) >= target))
    minimum_k = int(crossings[0]) + 1 if crossings.size else None
    weak_support = bool(full_r < 0.10 or not np.isfinite(full_ci).all() or full_ci[0] <= 0 <= full_ci[1])
    greedy_order = [columns[index] for index in greedy]
    train_missing = train_ratings.isna()
    test_missing = test_ratings.isna()
    return {
        "k": list(range(1, len(columns) + 1)),
        "greedy_order": greedy_order,
        "l1_order": [columns[index] for index in lasso],
        "greedy_train": greedy_train,
        "greedy_test": greedy_test,
        "l1_test": l1_test,
        "minimum_k": minimum_k,
        "selected_items": greedy_order[:minimum_k] if minimum_k is not None else [],
        "full_test_r": full_r,
        "full_test_ci": full_ci,
        "target_r": target,
        "weak_support": weak_support,
        "bootstrap_valid_draws": valid_draws,
        "l1_alpha": l1_alpha,
        "n_train": len(y_train),
        "n_test": len(y_test),
        "dropped_items": dropped_items,
        "training_means": training_means.to_dict(),
        "train_imputed_cells": int(train_missing.to_numpy().sum()),
        "test_imputed_cells": int(test_missing.to_numpy().sum()),
        "train_rows_with_imputation": int(train_missing.any(axis=1).sum()),
        "test_rows_with_imputation": int(test_missing.any(axis=1).sum()),
    }
