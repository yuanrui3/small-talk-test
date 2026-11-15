"""Load the two Part 1 surveys and apply the preregistered codings."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd


SEED = 41393
GSS_ARCHIVE_SHA256 = "98c828bbdb20b02ae0efc067a2f24cf1afaf87aaa012d3c97008901359b04e68"
GSS_GENRES = {
    "bigband": "Big band",
    "blugrass": "Bluegrass",
    "country": "Country western",
    "blues": "Blues or R&B",
    "musicals": "Broadway musicals",
    "classicl": "Classical music",
    "folk": "Folk",
    "gospel": "Gospel",
    "jazz": "Jazz",
    "latin": "Latin",
    "moodeasy": "Easy listening",
    "newage": "New age",
    "opera": "Opera",
    "rap": "Rap",
    "reggae": "Reggae",
    "conrock": "Contemporary rock",
    "oldies": "Oldies rock",
    "hvymetal": "Heavy metal",
}
GSS_CONTROL_TERMS = {
    "age": ["age", "age_sq"],
    "sex": ["sex"],
    "race": ["race"],
    "educ": ["educ"],
    "realinc": ["log_income"],
    "marital": ["marital"],
    "health": ["health"],
    "wrkstat": ["wrkstat"],
    "childs": ["childs"],
}
GSS_CATEGORICAL = {"sex", "race", "marital", "health", "wrkstat"}
YOUNG_CONTROL_TERMS = {
    "Age": ["age", "age_sq"],
    "Gender": ["gender"],
    "Education": ["educ"],
    "Village - town": ["settlement"],
    "Number of siblings": ["siblings"],
}
YOUNG_CATEGORICAL = {"gender", "educ", "settlement"}


@dataclass
class Survey:
    name: str
    frame: pd.DataFrame
    domains: dict[str, list[str]]
    labels: dict[str, str]
    controls: list[str]
    numeric: list[str]
    categorical: list[str]
    audit: dict[str, object]


def item_scores(items: pd.DataFrame) -> pd.DataFrame:
    """Summarize valid 1–5 responses for each respondent before eligibility rules."""
    if items.shape[1] == 0:
        raise ValueError("At least one survey item is required to compute scores.")
    valid_count = items.count(axis=1)
    very_liked = items.eq(5).sum(axis=1)
    return pd.DataFrame(
        {
            "liking": items.mean(axis=1),
            "disliked": items.le(2).sum(axis=1),
            "very_liked": very_liked,
            "item_sd": items.std(axis=1, ddof=0),
            "valid_count": valid_count,
            "endpoint_share": items.eq(1).sum(axis=1).add(very_liked).div(
                valid_count.where(valid_count > 0)
            ),
            "answered_share": valid_count / items.shape[1],
        },
        index=items.index,
    )


def _train_half(index: pd.Index) -> np.ndarray:
    positions = np.random.default_rng(SEED).permutation(len(index))
    train = np.zeros(len(index), dtype=bool)
    train[positions[: len(index) // 2]] = True
    return train


def _control_missingness(series: pd.Series, design: pd.Series) -> dict[str, int | float]:
    return {
        "missing_n": int(series.isna().sum()),
        "missing_pct": 100 * series.isna().mean(),
        "design_missing_n": int(design.sum()),
        "design_missing_pct": 100 * design.mean(),
    }


def load_gss(raw_dir: Path) -> Survey:
    """Load the pinned NORC Stata archive, preserving design missingness for audit."""
    archive_path = raw_dir / "1993_stata.zip"
    if not archive_path.is_file():
        raise FileNotFoundError(f"Place NORC's 1993_stata.zip in {raw_dir}.")
    digest = sha256(archive_path.read_bytes()).hexdigest()
    if digest != GSS_ARCHIVE_SHA256:
        raise ValueError(f"{archive_path} does not match the pinned NORC archive SHA-256.")

    required = [
        "year", "id", "ballot", "happy", "income91", "wtssps",
        *GSS_GENRES, *GSS_CONTROL_TERMS,
    ]
    with ZipFile(archive_path) as archive, archive.open("GSS1993.dta") as source:
        raw = pd.read_stata(
            source, columns=required, convert_categoricals=False, convert_missing=True
        )
    raw = raw.loc[raw["year"].eq(1993)].set_index("id", verify_integrity=True).sort_index()
    if raw.empty or raw.index.hasnans or not raw["ballot"].isin([1, 2, 3]).all():
        raise ValueError("The GSS must contain identified 1993 respondents on ballots A/B/C.")

    frame = raw.drop(columns="income91").apply(pd.to_numeric, errors="coerce")
    frame["y"] = 4 - frame["happy"].where(frame["happy"].isin([1, 2, 3]))
    genres = list(GSS_GENRES)
    frame[genres] = 6 - frame[genres].where(frame[genres].isin([1, 2, 3, 4, 5]))
    frame["age_sq"] = frame["age"].astype(float).pow(2)
    frame["log_income"] = np.log1p(frame["realinc"])
    frame["train"] = _train_half(frame.index)

    missingness = {}
    controls = []
    for name, terms in GSS_CONTROL_TERMS.items():
        iap = raw[name].map(str).eq(".i")
        design = iap.copy()
        if name == "health":
            design = frame["ballot"].eq(2)
            if not design.equals(iap):
                raise ValueError("The Stata HEALTH missing codes disagree with ballot routing.")
        if name == "realinc":
            # REALINC marks missing source answers as inapplicable in the derived field.
            source_nonresponse = raw["income91"].map(str).isin([".d", ".n", ".r"])
            design &= ~source_nonresponse
        audit = _control_missingness(frame[name], design)
        retained = audit["design_missing_pct"] <= 30
        missingness[name.upper()] = {**audit, "iap_n": int(iap.sum()), "retained": retained}
        if retained:
            controls.extend(terms)

    return Survey(
        name="1993 General Social Survey",
        frame=frame,
        domains={"music": genres},
        labels=GSS_GENRES.copy(),
        controls=controls,
        numeric=[name for name in controls if name not in GSS_CATEGORICAL],
        categorical=[name for name in controls if name in GSS_CATEGORICAL],
        audit={
            "n_rows": len(frame),
            "source_files": [archive_path.name],
            "archive_member": "GSS1993.dta",
            "sha256": {archive_path.name: digest},
            "missingness": pd.DataFrame.from_dict(missingness, orient="index"),
            "ballot_counts": frame["ballot"].value_counts().sort_index().to_dict(),
            "item_prompts": {item: f"Like or dislike {label}" for item, label in GSS_GENRES.items()},
        },
    )


def _young_dictionary(path: Path) -> tuple[list[str], dict[str, str]]:
    dictionary = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    dictionary = dictionary.apply(lambda col: col.str.strip())
    anchors = ["Music", "Slow songs or fast songs", "Movies", "Happiness in life"]
    short_columns = [
        column for column in dictionary if dictionary[column].isin(anchors).sum() == len(anchors)
    ]
    if len(dictionary.columns) != 2 or len(short_columns) != 1:
        raise ValueError(f"{path} must identify the survey column names and original prompts.")
    short_column = short_columns[0]
    prompt_column = dictionary.columns.drop(short_column)[0]
    names = dictionary[short_column].tolist()
    if len(names) < 63 or len(names) != len(set(names)) or "" in names:
        raise ValueError(f"{path} has an incomplete or duplicated list of survey columns.")
    if names[:2] != anchors[:2] or names[19] != "Movies" or names.index(anchors[3]) < 63:
        raise ValueError(f"{path} does not match the questionnaire order documented in PLAN.md.")
    prompts = dict(zip(names, dictionary[prompt_column]))
    if not all(prompts[name] for name in [*names[:63], anchors[3]]):
        raise ValueError(f"{path} lacks an original prompt for a planned survey item.")
    return names, prompts


def _young_numeric(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    invalid = series.notna() & (~np.isfinite(numeric) | numeric.mod(1).ne(0))
    if invalid.any():
        raise ValueError(f"Young People Survey field {series.name!r} contains invalid numeric codes.")
    return numeric


def load_young(raw_dir: Path) -> Survey:
    """Read the publisher's dictionary before opening any Slovak survey responses."""
    dictionary_path = raw_dir / "columns.csv"
    responses_path = raw_dir / "responses.csv"
    for path in [dictionary_path, responses_path]:
        if not path.is_file():
            raise FileNotFoundError(
                f"Missing {path.name}. Extract columns.csv and responses.csv from the Kaggle "
                f"Young People Survey into {raw_dir}."
            )

    names, prompts = _young_dictionary(dictionary_path)
    music = names[2:19]
    film = names[20:31]
    hobbies = names[31:63]
    items = music + film + hobbies
    outcome = "Happiness in life"
    raw = pd.read_csv(responses_path, dtype=str, encoding="utf-8-sig")
    if raw.empty or raw.columns.str.strip().tolist() != names:
        raise ValueError(f"{responses_path} does not match {dictionary_path} in column order.")
    raw.columns = names
    raw.index = pd.RangeIndex(1, len(raw) + 1, name="id")
    frame = pd.DataFrame(index=raw.index)
    for name in [*items, outcome]:
        series = _young_numeric(raw[name])
        if (series.notna() & ~series.between(1, 5)).any():
            raise ValueError(f"Young People Survey field {name!r} has a code outside 1–5.")
        frame["y" if name == outcome else name] = series
    frame["train"] = _train_half(frame.index)

    controls = []
    missingness = {}
    unavailable = []
    for source_name, terms in YOUNG_CONTROL_TERMS.items():
        if source_name not in raw:
            unavailable.append(source_name)
            continue
        target = terms[0]
        series = raw[source_name].str.strip().replace("", np.nan)
        if target not in YOUNG_CATEGORICAL:
            series = _young_numeric(series)
        frame[target] = series
        if target == "age":
            frame["age_sq"] = series.astype(float).pow(2)
        controls.extend(terms)
        missingness[source_name] = {
            **_control_missingness(series, pd.Series(False, index=series.index)),
            "retained": True,
        }

    return Survey(
        name="Young People Survey",
        frame=frame,
        domains={"music": music, "film": film, "hobbies": hobbies, "combined": items},
        labels={item: item for item in items},
        controls=controls,
        numeric=[name for name in controls if name not in YOUNG_CATEGORICAL],
        categorical=[name for name in controls if name in YOUNG_CATEGORICAL],
        audit={
            "n_rows": len(frame),
            "source_files": [dictionary_path.name, responses_path.name],
            "sha256": {path.name: sha256(path.read_bytes()).hexdigest()
                       for path in [dictionary_path, responses_path]},
            "missingness": pd.DataFrame.from_dict(missingness, orient="index"),
            "unavailable_controls": unavailable,
            "item_prompts": {item: prompts[item] for item in items},
            "outcome_prompt": prompts[outcome],
        },
    )
