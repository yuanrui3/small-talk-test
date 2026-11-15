"""Build a local report from the Part 2 session and provider ledgers."""

from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
import re
import sqlite3
from tempfile import NamedTemporaryFile
import warnings
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from statsmodels.regression.mixed_linear_model import MixedLM


SEED = 41393
PERMUTATIONS = 10_000
STUDY_DAYS = 14
LOCAL_ZONE = ZoneInfo("America/Indianapolis")
QUOTA_MODEL = "@cf/openai/gpt-oss-120b"
DAILY_FREE_NEURONS = 10_000
INPUT_NEURONS_PER_MILLION = 31_818
OUTPUT_NEURONS_PER_MILLION = 68_182
QUOTA_SOURCE = "https://developers.cloudflare.com/workers-ai/platform/pricing/"
WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?")
POSITIVE = frozenset(
    "amazing awesome beautiful best enjoy enjoyed excellent excited favorite favourite fun "
    "glad good great happy like liked love loved nice pleasant pleased wonderful".split()
)
NEGATIVE = frozenset(
    "angry annoying awful bad boring disappointed dislike disliked dread frustrating "
    "hate hated miserable rough sad terrible unhappy unpleasant upset worse worst".split()
)
READER_NAMES = {"cold": "Cold reader", "history": "History reader"}
BASELINES = {
    "loo_mean": "Leave-one-out mean",
    "yesterday": "Yesterday’s rating",
    "text": "Local text baseline",
}


class PilotIncompleteError(RuntimeError):
    """The report must wait for the owner's 14-day observation window."""


def _snapshot(db_path):
    uri = db_path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.execute("BEGIN")
        participants = pd.read_sql_query("SELECT id FROM participants ORDER BY id", connection)
        sessions = pd.read_sql_query(
            """SELECT s.id AS session_id, s.participant_id, s.day_number, s.local_day, s.phase,
                      s.rating, s.satisfaction, cold.score AS cold, cold.confidence AS cold_confidence,
                      history.score AS history, history.confidence AS history_confidence,
                      cold.error IS NOT NULL AS cold_failed, history.error IS NOT NULL AS history_failed
               FROM sessions AS s
               LEFT JOIN guesses AS cold
                 ON cold.session_id = s.id AND cold.mode = 'cold'
               LEFT JOIN guesses AS history
                 ON history.session_id = s.id AND history.mode = 'history'
               ORDER BY s.participant_id, s.local_day""",
            connection,
            index_col="session_id",
            parse_dates=["local_day"],
        )
        messages = pd.read_sql_query(
            "SELECT session_id, role, content, at FROM messages ORDER BY session_id, at, id",
            connection,
        )
        has_ledger = connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'api_calls'"
        ).fetchone()
        calls = pd.read_sql_query("SELECT * FROM api_calls ORDER BY id", connection) if has_ledger else pd.DataFrame()

    numeric = ["rating", "satisfaction", "cold", "cold_confidence", "history", "history_confidence"]
    sessions[numeric] = sessions[numeric].astype(float)
    return participants, sessions, messages, calls


def _require_closed_windows(participants, sessions, as_of):
    if participants.empty or sessions.empty:
        raise PilotIncompleteError("The pilot has no recorded first session yet.")

    for _, person in sessions.groupby("participant_id", sort=False):
        first = person.iloc[0]
        start = first["local_day"].date() - timedelta(days=int(first["day_number"]) - 1)
        last_day = start + timedelta(days=STUDY_DAYS - 1)
        last = person.loc[person["day_number"].eq(STUDY_DAYS)]
        terminal = last["phase"].isin(["done", "crisis"]).any()
        if as_of < last_day or (as_of == last_day and not terminal):
            raise PilotIncompleteError(f"The 14-day observation window is still open through {last_day:%Y-%m-%d}.")


def _text_features(messages):
    rows = []
    user_delays, assistant_delays = [], []
    for session_id, transcript in messages.groupby("session_id", sort=False):
        word_count = sentiment = user_count = 0
        reply_delays = []
        previous = None
        for message in transcript.itertuples(index=False):
            if message.role == "user":
                words = WORD.findall(message.content.casefold())
                user_count += 1
                word_count += len(words)
                sentiment += sum(word in POSITIVE for word in words) - sum(word in NEGATIVE for word in words)
                if previous is not None and previous.role == "assistant":
                    reply_delays.append(message.at - previous.at)
            elif message.role == "assistant" and previous is not None and previous.role == "user":
                assistant_delays.append(message.at - previous.at)
            previous = message
        if user_count:
            user_delays.extend(reply_delays)
            rows.append(
                (session_id, user_count, word_count / user_count,
                 float(np.median(reply_delays)) if reply_delays else 0.0,
                 sentiment / max(1, word_count))
            )
    columns = ["session_id", "message_count", "mean_words", "reply_seconds", "sentiment"]
    return pd.DataFrame(rows, columns=columns).set_index("session_id"), user_delays, assistant_delays


def _baseline_predictions(sessions, features, *, pilot):
    rated = sessions.dropna(subset="rating").copy()
    people = rated.groupby("participant_id", sort=False)["rating"]
    rated["loo_mean"] = (people.transform("sum") - rated["rating"]) / (people.transform("size") - 1)

    observed_days = rated.set_index(["participant_id", "local_day"])["rating"]
    yesterday = pd.MultiIndex.from_arrays([rated["participant_id"], rated["local_day"] - pd.Timedelta(days=1)])
    rated["yesterday"] = observed_days.reindex(yesterday).to_numpy()

    rated["text"] = np.nan
    usable = features.loc[features.index.intersection(rated.index)].astype(float)
    split_ids = rated.loc[usable.index, "participant_id"]
    if pilot:
        ordered = rated.loc[usable.index].sort_values("local_day").index
        splits = [(ordered[:position], ordered[position : position + 1]) for position in range(3, len(ordered))]
    else:
        splits = [(usable.index[split_ids.ne(person)], usable.index[split_ids.eq(person)]) for person in split_ids.unique()]

    for train, test in splits:
        if len(train) >= 3:
            model = make_pipeline(StandardScaler(), Ridge(alpha=1.0))
            model.fit(usable.loc[train], rated.loc[train, "rating"])
            rated.loc[test, "text"] = np.clip(model.predict(usable.loc[test]), 0, 10)
    return rated


def _pair_vectors(rated, reader):
    vectors = []
    paired = rated.dropna(subset=reader)
    for _, person in paired.groupby("participant_id", sort=False):
        y = person["rating"].to_numpy()
        x = person[reader].to_numpy()
        if len(y) >= 3 and np.ptp(y) > 0 and np.ptp(x) > 0:
            x = x - x.mean()
            y = y - y.mean()
            vectors.append((x / np.linalg.norm(x), y / np.linalg.norm(y)))
    return vectors


def _fisher_z(r):
    return np.arctanh(np.clip(r, -1 + 1e-12, 1 - 1e-12))


def _within_person(rated, reader, *, test=False):
    vectors = _pair_vectors(rated, reader)
    if not vectors:
        return np.nan, 0, 0, np.nan
    mean_z = np.mean([_fisher_z(np.dot(x, y)) for x, y in vectors])
    p = np.nan
    if test:
        rng = np.random.default_rng(SEED)
        null_z = np.zeros(PERMUTATIONS)
        for x, y in vectors:
            shuffled = rng.permuted(np.broadcast_to(y, (PERMUTATIONS, len(y))), axis=1)
            null_z += _fisher_z(shuffled @ x)
        exceedances = (abs(null_z / len(vectors)) >= abs(mean_z) - 1e-12).sum()
        p = (1 + exceedances) / (1 + PERMUTATIONS)
    return float(np.tanh(mean_z)), len(vectors), sum(len(x) for x, _ in vectors), float(p)


def _mixed_model(rated):
    rows = rated.dropna(subset="cold").copy()
    sizes = rows.groupby("participant_id", sort=False)["cold"].transform("size")
    rows = rows.loc[sizes.ge(3)].copy()
    if rows["participant_id"].nunique() < 2:
        return None
    rows["within_cold"] = rows["cold"] - rows.groupby("participant_id")["cold"].transform("mean")
    if rows["within_cold"].std() == 0:
        return None
    model = MixedLM.from_formula("rating ~ within_cold", groups="participant_id", data=rows)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            fit = model.fit(reml=False, method=["lbfgs", "powell"], disp=False)
        except (np.linalg.LinAlgError, ValueError):
            return None
    beta = fit.params["within_cold"]
    low, high = fit.conf_int().loc["within_cold"]
    if not fit.converged or not np.isfinite([beta, low, high]).all():
        return None
    return len(rows), rows["participant_id"].nunique(), beta, low, high


def _fmt(value, digits=3):
    return f"{value:,.{digits}f}" if np.isfinite(value) else "—"


def _median(values):
    return np.median(values) if len(values) else np.nan


def _mae(rows, prediction):
    return (rows[prediction] - rows["rating"]).abs().mean()


def _baseline_rows(rated):
    lines = [
        "| Reader | Common rated days | Reader MAE | Leave-one-out mean MAE | Yesterday MAE | Local text MAE |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for reader, name in READER_NAMES.items():
        common = rated.dropna(subset=[reader, *BASELINES])
        values = [_fmt(_mae(common, column), 2) for column in [reader, *BASELINES]]
        lines.append(f"| {name} | {len(common)} | {' | '.join(values)} |")
    return lines


def _calibration_rows(rated):
    lines = [
        "| Reader | Confidence bin | Days | Mean confidence | Fraction within 1 point |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    bins = ["0–<0.25", "0.25–<0.50", "0.50–<0.75", "0.75–1.00"]
    for reader, name in READER_NAMES.items():
        confidence = f"{reader}_confidence"
        pairs = rated.dropna(subset=[reader, confidence]).copy()
        pairs["within_one"] = (pairs[reader] - pairs["rating"]).abs().le(1)
        groups = np.minimum(np.floor(pairs[confidence] * 4), 3)
        for index, label in enumerate(bins):
            group = pairs.loc[groups.eq(index)]
            forecast = _fmt(group[confidence].mean(), 2)
            observed = _fmt(group["within_one"].mean(), 2)
            lines.append(f"| {name} | {label} | {len(group)} | {forecast} | {observed} |")
    return lines


def _inference_calls(calls):
    if calls.empty:
        return pd.DataFrame(columns=["neurons", "has_token_counts"])
    inference = calls.loc[calls["endpoint"].eq("messages")].copy()
    has_counts = inference[["input_tokens", "output_tokens"]].notna().all(axis=1)
    matching_model = inference["model"].eq(QUOTA_MODEL)
    neurons = (inference["input_tokens"] * INPUT_NEURONS_PER_MILLION
               + inference["output_tokens"] * OUTPUT_NEURONS_PER_MILLION) / 1_000_000
    return inference.assign(
        has_token_counts=has_counts,
        neurons=neurons.where(has_counts & matching_model).astype(float),
        utc_day=pd.to_datetime(inference["started_at"], format="ISO8601", utc=True).dt.floor("D"),
    )


def _quota_projection(inference):
    pilot_calls = inference.loc[inference["purpose"].isin(["chat", "reader_cold", "reader_history"])]
    known_daily = pilot_calls.groupby("utc_day")["neurons"].sum(min_count=1).dropna()
    if known_daily.empty:
        return ["The available pilot usage cannot estimate free-tier throughput for eight or twelve participants."]

    average, peak = known_daily.mean(), known_daily.max()
    lines = [
        f"Across {len(known_daily)} UTC dates with reported pilot usage, the app used an estimated "
        f"{average:,.1f} neurons per day on average and {peak:,.1f} on the highest observed day. "
        "The projection scales those figures to people using the same Cloudflare account. "
        "It omits one-time model verification and assumes the pilot's mix of chat and reader requests.",
        "",
        "| Participants | Neurons per UTC day, pilot average scaled | Neurons per UTC day, pilot peak scaled | Peak estimate vs. free quota |",
        "| --- | ---: | ---: | --- |",
    ]
    for people in (8, 12):
        expected = average * people
        peak_estimate = peak * people
        relation = "within quota" if peak_estimate <= DAILY_FREE_NEURONS else "exceeds quota"
        lines.append(f"| {people} | {expected:,.1f} | {peak_estimate:,.1f} | {relation} |")
    lines += [
        "",
        "Other workloads on the account, calls with unreported usage, and longer transcripts can reduce available headroom. "
        "An estimate within the quota is a capacity check for the observed workload.",
    ]
    return lines


def _usage_rows(inference, *, pilot):
    lines = [
        "The proposed paid spending cap is **$0** on Workers AI Free. The account shares a "
        f"{DAILY_FREE_NEURONS:,}-neuron allocation that resets at 00:00 UTC. Estimated usage for `{QUOTA_MODEL}` "
        f"uses {INPUT_NEURONS_PER_MILLION:,} neurons per million input tokens and "
        f"{OUTPUT_NEURONS_PER_MILLION:,} per million output tokens from the published conversion. "
        f"[Cloudflare Workers AI pricing]({QUOTA_SOURCE})",
        "",
    ]
    if inference.empty:
        return lines + ["The local ledger has no inference call records for a throughput estimate."]

    lines += [
        "| Purpose | Calls recorded | Calls with token counts | Reported input tokens | Reported output tokens | Estimated neurons | Median provider time (s) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for purpose, group in inference.groupby("purpose", sort=True):
        inputs = _fmt(group["input_tokens"].sum(min_count=1), 0)
        outputs = _fmt(group["output_tokens"].sum(min_count=1), 0)
        neurons = _fmt(group["neurons"].sum(min_count=1), 1)
        latency = _fmt(_median(group["elapsed_ms"].dropna()) / 1_000, 2)
        lines.append(
            f"| {purpose.replace('_', ' ')} | {len(group)} | {int(group['has_token_counts'].sum())} | "
            f"{inputs} | {outputs} | {neurons} | {latency} |"
        )

    status = ", ".join(
        f"{count} {status.replace('_', ' ')}" for status, count in inference["status"].value_counts().sort_index().items()
    )
    lines += [
        "",
        f"Ledger status counts were {status}. Calls without both token counts are excluded from neuron totals. "
        "These totals cover this app's inference activity.",
    ]
    if pilot:
        lines += ["", *_quota_projection(inference)]
    return lines


def _anchor_rows(sessions, participants):
    lines = [
        "| Calendar anchor | Available | Missing | Median satisfaction (0–10) |",
        "| --- | ---: | ---: | ---: |",
    ]
    for day in (1, STUDY_DAYS):
        values = sessions.loc[sessions["day_number"].eq(day), "satisfaction"].dropna()
        lines.append(f"| Day {day} | {len(values)} | {participants - len(values)} | {_fmt(values.median(), 1)} |")
    return lines


def _report_lines(participants, sessions, messages, calls, *, pilot):
    features, user_delays, assistant_delays = _text_features(messages)
    rated = _baseline_predictions(sessions, features, pilot=pilot)
    cold_days = rated["cold"].count()
    history_days = rated["history"].count()
    cold_r, cold_people, cold_pairs, p = _within_person(rated, "cold", test=not pilot)
    comparison = rated.dropna(subset=["cold", "history"])
    pair_groups = comparison.groupby("participant_id", sort=False)
    varied = pair_groups[["rating", "cold", "history"]].transform("nunique").gt(1).all(axis=1)
    correlation_comparison = comparison.loc[varied]
    cold_shared_r, shared_people, shared_days, _ = _within_person(correlation_comparison, "cold")
    history_shared_r, _, _, _ = _within_person(correlation_comparison, "history")
    inference = _inference_calls(calls)
    known_calls = inference["neurons"].count()
    if known_calls:
        total_neurons = _fmt(inference["neurons"].sum(), 1)
        usage_finding = f"Estimated quota use was {total_neurons} neurons from {known_calls} of {len(inference)} call records."
    else:
        usage_finding = f"The ledger's {len(inference)} inference call records provided no usable quota estimate."

    mode = "Owner pilot" if pilot else "Study"
    scope = "These descriptive observations are for debugging the pilot." if pilot else (
        f"The two-sided 10,000-shuffle permutation p-value was {_fmt(p, 4)}."
    )
    cold_observation = (
        f"The cold reader had an observed within-person correlation of **{cold_r:.3f}**."
        if np.isfinite(cold_r)
        else "The cold-reading pairs provided insufficient observations or variation for a within-person correlation."
    )
    common_baselines = rated.dropna(subset=["cold", *BASELINES])
    if common_baselines.empty:
        baseline_finding = "No rated days had a cold guess and all three baseline predictions."
    else:
        errors = {column: _mae(common_baselines, column) for column in BASELINES}
        best = min(errors, key=errors.get)
        baseline_finding = (
            f"On {len(common_baselines)} rated days eligible for all baselines, mean absolute error was "
            f"{_fmt(_mae(common_baselines, 'cold'), 2)} points for the cold reader and "
            f"{_fmt(errors[best], 2)} points for the lowest-error baseline ({BASELINES[best].lower()})."
        )
    lines = [
        "# Part 2 · Mood tracking from small talk",
        "",
        "## Findings",
        "",
        f"**{mode}.** There were {len(rated)} self-ratings and {cold_days} rated days with a cold guess. "
        f"{cold_observation} {scope}",
        "",
        f"**Baseline comparison.** {baseline_finding}",
        "",
        f"**Reader comparison.** On {len(comparison)} rated days with both guesses, the cold reader's mean absolute "
        f"error was {_fmt(_mae(comparison, 'cold'), 2)} points and the history reader's was "
        f"{_fmt(_mae(comparison, 'history'), 2)} points on the 0–10 scale.",
        "",
        f"**API usage.** {usage_finding} "
        "The paid spending cap is $0 on Workers AI Free.",
        "",
        "## Methods and results",
        "",
        "The [preregistered analysis](../PLAN.md) pairs guesses and independent self-ratings from the same calendar day. "
        "The primary estimate is the equal-participant average of Fisher-transformed Pearson correlations, transformed "
        "back to r. A person needs at least three paired days with variation in both series. Perfect correlations are "
        "clipped 10⁻¹² from ±1 to keep the transform finite.",
        "",
        "### Session and measurement coverage",
        "",
        "| Metric | Count |",
        "| --- | ---: |",
        f"| Participants | {participants} |",
        f"| Scheduled Indianapolis calendar days | {participants * STUDY_DAYS} |",
        f"| Sessions started | {len(sessions)} |",
        f"| Dates without a started session | {participants * STUDY_DAYS - len(sessions)} |",
        f"| Workflows completed, including any reader failures | {sessions['phase'].eq('done').sum()} |",
        f"| Sessions ended via the safety screen | {sessions['phase'].eq('crisis').sum()} |",
        f"| Started sessions without a self-rating | {len(sessions) - len(rated)} |",
        f"| Rated days missing a valid cold guess | {len(rated) - cold_days} |",
        f"| Rated days missing a valid history guess | {len(rated) - history_days} |",
        f"| Stored cold reader failures | {sessions['cold_failed'].sum()} |",
        f"| Stored history reader failures | {sessions['history_failed'].sum()} |",
        f"| Participants included in the primary correlation | {cold_people} |",
        f"| Participants excluded from the primary correlation | {participants - cold_people} |",
        f"| Rated days included in the primary correlation | {cold_pairs} |",
        f"| Rated days excluded from the primary correlation | {len(rated) - cold_pairs} |",
        "",
        f"Median user reply time was {_fmt(_median(user_delays), 2)} seconds across {len(user_delays)} replies. "
        f"Median chat assistant response time was {_fmt(_median(assistant_delays), 2)} seconds across "
        f"{len(assistant_delays)} replies.",
        "",
        "### Comparison with baselines",
        "",
        "Mean absolute error (MAE) is in points on the 0–10 scale. Each row uses the same rated days for the reader "
        "and all three baselines. The leave-one-out mean uses the person's other ratings as a retrospective benchmark. "
        "Yesterday means the immediately preceding calendar date. The local Ridge model uses user message count, mean "
        "words per user message, median reply time (zero when unavailable) and the net sentiment proportion from a "
        "fixed English lexicon. It uses standardized features, a penalty of 1, at least three training sessions and "
        "clips predictions to 0–10. Pilot training uses only earlier sessions. Study training holds out the person.",
        "",
        *_baseline_rows(rated),
        "",
        f"On {shared_days} common rated days from {shared_people} participants with variation in both guesses and "
        f"ratings, the within-person correlations were {_fmt(cold_shared_r)} for cold and "
        f"{_fmt(history_shared_r)} for history.",
        "",
        "### Reader confidence",
        "",
        "Confidence estimates the chance of being within one point of the rating.",
        "",
        *_calibration_rows(rated),
        "",
        "### Life satisfaction",
        "",
        *_anchor_rows(sessions, participants),
    ]
    if not pilot:
        mixed = _mixed_model(rated)
        lines += ["", "### Random-intercept model", ""]
        if mixed is None:
            lines.append("A stable random-intercept estimate was unavailable from the paired observations.")
        else:
            n, people, beta, lo, hi = mixed
            lines.append(
                f"Across {n} paired days from {people} participants, a one-point increase in the "
                f"within-person-centered cold guess corresponded to {beta:+.3f} points in rating "
                f"(95% model-based CI {lo:+.3f} to {hi:+.3f})."
            )
        lines += ["", "The permutation test assumes exchangeability of a person's paired days under the null."]

    return lines + [
        "",
        "### API usage",
        "",
        *_usage_rows(inference, pilot=pilot),
        "",
        "## Limitations",
        "",
        "- The design measures how guesses track each person's own day-to-day changes. Ranking people by mood is "
        "outside its scope.",
        "- Missing guesses and missed sessions shrink the paired samples. Some baselines have additional requirements, "
        "including an available rating from yesterday and a history of earlier text and ratings.",
        "- Calibration bins may contain few observations. Participants may volunteer language that states their mood "
        "even though the chat prompts use neutral topics.",
        *(["- The pilot describes one person's experience."] if pilot else []),
        "",
        "## Reproduction",
        "",
        "From the repository root, run `.venv\\Scripts\\python.exe -m part2 report` against the retained local database.",
        "",
    ]


def write_report(db_path: Path, output_path: Path, *, as_of: date | None = None) -> Path:
    """Write aggregate metrics after the observation window closes, using a read-only snapshot."""
    db_path, output_path = Path(db_path), Path(output_path)
    if not db_path.is_file():
        raise PilotIncompleteError("The pilot has no local database yet.")
    participants, sessions, messages, calls = _snapshot(db_path)
    _require_closed_windows(participants, sessions, as_of or datetime.now(LOCAL_ZONE).date())
    report = _report_lines(len(participants), sessions, messages, calls, pilot=len(participants) == 1)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            dir=output_path.parent, prefix=".part2-report-", mode="w", encoding="utf-8", newline="\n", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write("\n".join(report))
        temporary.replace(output_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return output_path
