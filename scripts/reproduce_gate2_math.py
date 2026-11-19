"""Reproduce the Gate 2 mathematical examples using synthetic data only.

Run from the repository root with
    .venv\\Scripts\\python.exe -B scripts/reproduce_gate2_math.py

The script prints JSON to stdout and creates no output files. It reads no
participant data and imports no project modules. NumPy and SciPy run on the CPU.

The ordinary correlation test and Fisher interval assume independent,
bivariate-normal observations. Power simulations satisfy those assumptions.
Null simulations compare independent, stationary Gaussian AR(1) processes,
each initialized at its unit-variance stationary distribution. When phi > 0,
ordinary day permutation breaks serial dependence. Its simulated rejection
rate describes that particular null process, not all possible mood dynamics.

The clipped Fisher example is an algebraic illustration of the equal-person
average with one perfect positive correlation among eight people. It uses the
same 1e-12 clipping constant as the preregistered reporting implementation.
"""

import json
import os

for variable in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS"):
    os.environ[variable] = "1"

import numpy as np
import scipy
from scipy.stats import norm, t


SEED = 41393
OBSERVATIONS = 14
ALPHA = 0.05
ORDINARY_TRIALS = 100_000
SHUFFLE_TRIALS = 10_000
PERMUTATIONS = 999
BATCH_SIZE = 250


def _stationary_pair(rng, trials, phi):
    """Draw independent AR(1) pairs in the original, time-major RNG order."""
    first = np.empty((trials, OBSERVATIONS))
    second = np.empty_like(first)
    first[:, 0] = rng.normal(size=trials)
    second[:, 0] = rng.normal(size=trials)
    innovation_sd = np.sqrt(1 - phi * phi)
    for day in range(1, OBSERVATIONS):
        first[:, day] = phi * first[:, day - 1] + innovation_sd * rng.normal(size=trials)
        second[:, day] = phi * second[:, day - 1] + innovation_sd * rng.normal(size=trials)
    return first, second


def _normalize_rows(values):
    centered = values - values.mean(axis=1, keepdims=True)
    return centered / np.linalg.norm(centered, axis=1, keepdims=True)


def _correlations(first, second):
    return np.sum(_normalize_rows(first) * _normalize_rows(second), axis=1)


def _ordinary_simulations(critical_r):
    """Reuse one RNG through both null conditions and then the three powers."""
    rng = np.random.default_rng(SEED)
    null_rates = []
    for phi in (0.0, 0.6):
        first, second = _stationary_pair(rng, ORDINARY_TRIALS, phi)
        rejected = np.abs(_correlations(first, second)) > critical_r
        null_rates.append({"ar1_phi": phi, "rejections": int(rejected.sum()), "rate": float(rejected.mean())})

    powers = []
    for rho in (0.3, 0.5, 0.7):
        first = rng.normal(size=(ORDINARY_TRIALS, OBSERVATIONS))
        second = rho * first + np.sqrt(1 - rho * rho) * rng.normal(size=first.shape)
        rejected = np.abs(_correlations(first, second)) > critical_r
        powers.append({"true_rho": rho, "rejections": int(rejected.sum()), "power": float(rejected.mean())})
    return null_rates, powers


def _day_shuffle_simulations():
    """Use a fresh RNG and the original 250-trial batches for the day shuffles."""
    rng = np.random.default_rng(SEED)
    null_rates = []
    for phi in (0.0, 0.6):
        rejected = 0
        for start in range(0, SHUFFLE_TRIALS, BATCH_SIZE):
            batch = min(BATCH_SIZE, SHUFFLE_TRIALS - start)
            first, second = _stationary_pair(rng, batch, phi)
            first = _normalize_rows(first)
            second = _normalize_rows(second)
            observed = np.abs(np.sum(first * second, axis=1))
            shuffled = rng.permuted(
                np.broadcast_to(second[:, None, :], (batch, PERMUTATIONS, OBSERVATIONS)),
                axis=2,
            )
            null = np.einsum("bpn,bn->bp", shuffled, first)
            exceedances = (np.abs(null) >= observed[:, None] - 1e-12).sum(axis=1)
            p_values = (1 + exceedances) / (1 + PERMUTATIONS)
            rejected += int((p_values <= ALPHA).sum())
        rate = rejected / SHUFFLE_TRIALS
        null_rates.append({
            "ar1_phi": phi,
            "rejections": rejected,
            "rate": rate,
            "monte_carlo_standard_error": float(np.sqrt(rate * (1 - rate) / SHUFFLE_TRIALS)),
        })
    return null_rates


def main():
    critical_t = t.ppf(1 - ALPHA / 2, OBSERVATIONS - 2)
    critical_r = float(critical_t / np.sqrt(critical_t * critical_t + OBSERVATIONS - 2))
    observed_r = 0.53
    fisher_interval = np.tanh(
        np.arctanh(observed_r) + np.array([-1, 1]) * 1.959963984540054 / np.sqrt(OBSERVATIONS - 3)
    )
    power_quantiles = norm.ppf(1 - ALPHA / 2) + norm.ppf(0.8)
    sample_sizes = [
        {"true_rho": rho, "observations": int(np.ceil(3 + (power_quantiles / np.arctanh(rho)) ** 2))}
        for rho in (0.2, 0.3, 0.4, 0.5)
    ]
    ordinary_null, powers = _ordinary_simulations(critical_r)
    result = {
        "seed": SEED,
        "numpy_version": np.__version__,
        "scipy_version": scipy.__version__,
        "observations_per_series": OBSERVATIONS,
        "two_sided_alpha": ALPHA,
        "iid_normal_critical_abs_r": critical_r,
        "approximate_iid_fisher_95_interval_at_r_0_53": fisher_interval.tolist(),
        "clipped_fisher_average_one_perfect_among_eight": float(np.tanh(np.arctanh(1 - 1e-12) / 8)),
        "ordinary_test_trials_per_condition": ORDINARY_TRIALS,
        "ordinary_test_null_rejection_rates": ordinary_null,
        "iid_normal_powers": powers,
        "approximate_iid_fisher_sample_sizes_for_80_percent_power": sample_sizes,
        "day_shuffle_trials_per_condition": SHUFFLE_TRIALS,
        "day_shuffle_permutations_per_trial": PERMUTATIONS,
        "day_shuffle_batch_size": BATCH_SIZE,
        "day_shuffle_null_rejection_rates": _day_shuffle_simulations(),
    }
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
