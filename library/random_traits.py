"""Per-trait random helpers, each override-aware and reusable."""

from __future__ import annotations

import math
import random
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from library.world_paths import config_db_path as _default_world_config_path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = _default_world_config_path()

LIFE_STAGES: tuple[str, ...] = ("child", "mature", "prime", "middleaged", "elder")
GENDERS: tuple[str, ...] = ("Male", "Female")

# ``random() ** power`` skew toward the minimum age when sampling elder years.
DEFAULT_ELDER_AGE_SKEW = 3.0

# Genome: magnitude ~ N(mean, stdev) clipped to [0, GENOME_MAX_MAGNITUDE]; 0 = ideal, ~±50 typical.
GENOME_MAX_MAGNITUDE = 99.99
DEFAULT_GENOME_MAGNITUDE_MEAN = 50.0
DEFAULT_GENOME_MAGNITUDE_STDEV = 15.0
# When ``gender_skew_high`` matches sex, P(positive) += strength; ``gender_skew_low`` -= strength.
DEFAULT_GENOME_SKEW_STRENGTH = 0.22

# ``sexual_nature`` / ``gender_mind``: target marginals + blend with genome softmax
# (calibrated ~50k Monte Carlo vs ``choose_genome``).
SEXUAL_NATURE_PRIOR: dict[str, float] = {
    "heterosexual": 0.85,
    "bisexual": 0.09,
    "homosexual": 0.06,
}
# Small weight keeps marginals near ``SEXUAL_NATURE_PRIOR`` (~85/9/6) while
# letting genome nudge outcomes (Monte Carlo–checked at ~15k draws).
SEXUAL_NATURE_GENOME_WEIGHT = 0.012
SEXUAL_NATURE_SOFTMAX_TEMP = 10.5

# Prior 90/10 aligned vs opposite mind; tiny genome blend keeps ~10% opposite.
GENDER_MIND_GENOME_WEIGHT = 0.05
GENDER_MIND_SOFTMAX_TEMP = 9.0


def _connect(db_path: Path | str | None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if not path.is_file():
        raise FileNotFoundError(
            f"Database not found: {path}. Run: python utils/util_load_config.py --world default"
        )
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _as_int(value: object, default: int = 0) -> int:
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        return default


def _gender_prefix(gender: str) -> str:
    return "m" if gender == "Male" else "f"


def _pick_token(cell: object) -> str:
    if cell is None:
        return ""
    parts = [p.strip() for p in str(cell).split(";") if p.strip()]
    if not parts:
        return ""
    return random.choice(parts)


def choose_species_row(
    *,
    species: str | None = None,
    ethnic: str | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Pick a species row from SQLite weighted by `rate`.

    - `rate <= 0` rows are excluded from random draws.
    - `species` and/or `ethnic` filters narrow the candidate set; if the
      filter leaves exactly one row it is returned as a hard override
      (even when its `rate` is 0).
    """
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path_s = str(path.resolve())
    if species is not None and ethnic is not None:
        rows = list(_species_rows_for_exact_key(path_s, species.strip(), ethnic.strip()))
        if len(rows) == 1:
            return rows[0]
    else:
        rows = list(_species_rows(path_s))
    if species is not None:
        s = species.strip()
        rows = [r for r in rows if str(r["species"] or "").strip() == s]
    if ethnic is not None:
        e = ethnic.strip()
        rows = [r for r in rows if str(r["ethnic"] or "").strip() == e]

    if not rows:
        detail = ", ".join(f"{k}={v!r}" for k, v in (
            ("species", species), ("ethnic", ethnic)
        ) if v is not None) or "(no filters)"
        raise LookupError(f"No species rows match: {detail}")

    if len(rows) == 1 and (species is not None or ethnic is not None):
        return rows[0]

    weighted = [(r, max(0, _as_int(r["rate"], 0))) for r in rows]
    pickable = [(r, w) for r, w in weighted if w > 0]
    if not pickable:
        raise LookupError(
            "No species row has rate > 0; pass species= or ethnic= to override."
        )
    rs, ws = zip(*pickable)
    return random.choices(rs, weights=ws, k=1)[0]


@lru_cache(maxsize=8)
def _species_rows(db_path_s: str) -> tuple[dict[str, Any], ...]:
    path = Path(db_path_s)
    conn = _connect(path)
    try:
        try:
            raw = conn.execute("SELECT * FROM species").fetchall()
        except sqlite3.OperationalError as exc:
            raise LookupError(
                "SQLite table `species` missing. Run: python utils/util_load_config.py --world default"
            ) from exc
        rows = tuple({k: r[k] for k in r.keys()} for r in raw)
    finally:
        conn.close()
    return rows


@lru_cache(maxsize=256)
def _species_rows_for_exact_key(
    db_path_s: str, species: str, ethnic: str
) -> tuple[dict[str, Any], ...]:
    return tuple(
        r
        for r in _species_rows(db_path_s)
        if str(r["species"] or "").strip() == species
        and str(r["ethnic"] or "").strip() == ethnic
    )


@lru_cache(maxsize=8)
def _species_ethnic_keyset(db_path_s: str) -> frozenset[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for row in _species_rows(db_path_s):
        keys.add(
            (
                str(row["species"] or "").strip(),
                str(row["ethnic"] or "").strip(),
            )
        )
    return frozenset(keys)


def species_ethnic_exists(*, species: str, ethnic: str, db_path: Path | str | None = None) -> bool:
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    key = (species.strip(), ethnic.strip())
    return key in _species_ethnic_keyset(str(path.resolve()))


def preload_trait_cache(*, db_path: Path | str | None = None, world: str = "default") -> None:
    """Warm trait/species caches once for simulation startup."""
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path_s = str(path.resolve())
    _species_rows(path_s)
    _species_ethnic_keyset(path_s)
    _sexual_nature_rows(path_s)
    _gender_mind_rows(path_s)
    _species_age_distribution_table(path_s, world)


def choose_gender(*, gender: str | None = None) -> str:
    """50/50 Male/Female unless overridden."""
    if gender is not None:
        return gender
    return random.choice(GENDERS)


@lru_cache(maxsize=16)
def _genome_trait_definitions(db_path_s: str) -> tuple[tuple[str, str, str], ...]:
    """Cached ``(trait, skew_high, skew_low)`` from ``genome`` (tags lowercased)."""
    path = Path(db_path_s)
    conn = _connect(path)
    try:
        try:
            rows = conn.execute(
                "SELECT trait, gender_skew_high, gender_skew_low FROM genome"
            ).fetchall()
        except sqlite3.OperationalError as exc:
            raise LookupError(
                "SQLite table `genome` missing. Run: python utils/util_load_config.py --world default"
            ) from exc
        out: list[tuple[str, str, str]] = []
        for row in rows:
            trait = str(row["trait"] or "").strip()
            if not trait:
                continue
            sh = str(row["gender_skew_high"] or "").strip().lower()
            sl = str(row["gender_skew_low"] or "").strip().lower()
            if sh == "none":
                sh = ""
            if sl == "none":
                sl = ""
            out.append((trait, sh, sl))
        return tuple(out)
    finally:
        conn.close()


def _p_positive_genome_sign(
    gender_lower: str, skew_high: str, skew_low: str, strength: float
) -> float:
    """Baseline 0.5; matching ``gender_skew_high`` biases +, ``gender_skew_low`` biases -."""
    p = 0.5
    if skew_high and gender_lower == skew_high:
        p += strength
    if skew_low and gender_lower == skew_low:
        p -= strength
    return min(0.93, max(0.07, p))


def choose_genome(
    gender: str,
    *,
    genome: dict[str, float] | None = None,
    db_path: Path | str | None = None,
    magnitude_mean: float = DEFAULT_GENOME_MAGNITUDE_MEAN,
    magnitude_stdev: float = DEFAULT_GENOME_MAGNITUDE_STDEV,
    skew_strength: float = DEFAULT_GENOME_SKEW_STRENGTH,
) -> dict[str, float]:
    """Roll a signed deviation per ``genome`` trait (0 = ideal, ~±50 ordinary).

    Draws a bell-shaped **magnitude** on ``[0, GENOME_MAX_MAGNITUDE]`` (clipped
    normal around ``magnitude_mean``), then applies **sign** with optional bias
    from ``gender_skew_high`` / ``gender_skew_low`` when they equal ``male`` or
    ``female`` for this person's sex. Values are rounded to two decimal places.
    """
    if genome is not None:
        return dict(genome)
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    defs = _genome_trait_definitions(str(path.resolve()))
    if not defs:
        raise LookupError("genome table is empty")
    g = gender.strip().lower()
    out: dict[str, float] = {}
    for trait, skew_high, skew_low in defs:
        mag = float(
            np.clip(
                np.random.normal(magnitude_mean, magnitude_stdev),
                0.0,
                GENOME_MAX_MAGNITUDE,
            )
        )
        p_pos = _p_positive_genome_sign(g, skew_high, skew_low, skew_strength)
        sign = 1.0 if random.random() < p_pos else -1.0
        out[trait] = round(sign * mag, 2)
    return out


def _trait_columns_from_definition_row(row: sqlite3.Row) -> list[str]:
    return [str(k) for k in row.keys() if str(k).strip().lower() != "type"]


def _aggregate_definition_score(
    genome: dict[str, float], row: sqlite3.Row, trait_cols: list[str]
) -> float:
    """Higher = genome better matches this definition row (``value`` / ``inverse``)."""
    total = 0.0
    n = 0
    for col in trait_cols:
        mode = str(row[col] or "").strip().lower()
        if mode not in ("value", "inverse"):
            continue
        if col not in genome:
            continue
        v = float(genome[col])
        total += v if mode == "value" else -v
        n += 1
    return total / max(n, 1)


def _softmax_distribution(scores: dict[str, float], temp: float) -> dict[str, float]:
    if not scores:
        return {}
    if temp <= 1e-9:
        best = max(scores, key=lambda k: scores[k])
        return {k: (1.0 if k == best else 0.0) for k in scores}
    keys = list(scores)
    xs = [scores[k] / temp for k in keys]
    xm = max(xs)
    exps = [math.exp(x - xm) for x in xs]
    s = sum(exps)
    return {keys[i]: exps[i] / s for i in range(len(keys))}


def _blend_priors_with_genome(
    scores: dict[str, float],
    prior: dict[str, float],
    genome_weight: float,
    temp: float,
) -> dict[str, float]:
    """``(1-w)*prior + w*softmax(scores/temp)`` over keys present in ``scores``."""
    if not scores:
        return dict(prior)
    pg = _softmax_distribution(scores, temp)
    keys = list(scores)
    raw_prior = [max(0.0, float(prior.get(k, 0.0))) for k in keys]
    sp = sum(raw_prior)
    p0 = {keys[i]: (raw_prior[i] / sp if sp > 0 else 1.0 / len(keys)) for i in range(len(keys))}
    w = max(0.0, min(1.0, genome_weight))
    blended = {k: (1.0 - w) * p0[k] + w * pg[k] for k in keys}
    sb = sum(blended.values())
    return {k: blended[k] / sb for k in keys} if sb > 0 else p0


def _sample_categorical(dist: dict[str, float]) -> str:
    keys = list(dist)
    ws = [max(0.0, dist[k]) for k in keys]
    s = sum(ws)
    if s <= 0:
        return keys[0]
    r = random.random() * s
    acc = 0.0
    for key, weight in zip(keys, ws):
        acc += weight
        if r <= acc:
            return key
    return keys[-1]


@lru_cache(maxsize=8)
def _sexual_nature_rows(db_path_s: str) -> tuple[dict[str, Any], ...]:
    path = Path(db_path_s)
    conn = _connect(path)
    try:
        try:
            raw = conn.execute("SELECT * FROM sexual_nature").fetchall()
        except sqlite3.OperationalError as exc:
            raise LookupError(
                "SQLite table `sexual_nature` missing. Run: python utils/util_load_config.py --world default"
            ) from exc
        return tuple({k: r[k] for k in r.keys()} for r in raw)
    finally:
        conn.close()


@lru_cache(maxsize=8)
def _sexual_nature_definitions(
    db_path_s: str,
) -> tuple[tuple[str, tuple[tuple[str, float], ...]], ...]:
    rows = _sexual_nature_rows(db_path_s)
    if not rows:
        return ()
    trait_cols = _trait_columns_from_definition_row(rows[0])
    out: list[tuple[str, tuple[tuple[str, float], ...]]] = []
    for row in rows:
        key = str(row["type"] or "").strip().lower()
        if not key:
            continue
        terms: list[tuple[str, float]] = []
        for col in trait_cols:
            mode = str(row[col] or "").strip().lower()
            if mode == "value":
                terms.append((col, 1.0))
            elif mode == "inverse":
                terms.append((col, -1.0))
        out.append((key, tuple(terms)))
    return tuple(out)


@lru_cache(maxsize=8)
def _gender_mind_rows(db_path_s: str) -> tuple[dict[str, Any], ...]:
    path = Path(db_path_s)
    conn = _connect(path)
    try:
        try:
            raw = conn.execute("SELECT * FROM gender_mind").fetchall()
        except sqlite3.OperationalError as exc:
            raise LookupError(
                "SQLite table `gender_mind` missing. Run: python utils/util_load_config.py --world default"
            ) from exc
        return tuple({k: r[k] for k in r.keys()} for r in raw)
    finally:
        conn.close()


@lru_cache(maxsize=8)
def _gender_mind_definitions(
    db_path_s: str,
) -> tuple[tuple[str, tuple[tuple[str, float], ...]], ...]:
    rows = _gender_mind_rows(db_path_s)
    if not rows:
        return ()
    trait_cols = _trait_columns_from_definition_row(rows[0])
    out: list[tuple[str, tuple[tuple[str, float], ...]]] = []
    for row in rows:
        key = str(row["type"] or "").strip().lower()
        if not key:
            continue
        terms: list[tuple[str, float]] = []
        for col in trait_cols:
            mode = str(row[col] or "").strip().lower()
            if mode == "value":
                terms.append((col, 1.0))
            elif mode == "inverse":
                terms.append((col, -1.0))
        out.append((key, tuple(terms)))
    return tuple(out)


def _score_compiled_definition(
    genome: dict[str, float], terms: tuple[tuple[str, float], ...]
) -> float:
    total = 0.0
    n = 0
    for trait, sign in terms:
        if trait not in genome:
            continue
        total += float(genome[trait]) * float(sign)
        n += 1
    return total / max(n, 1)


def choose_sexual_nature(
    genome: dict[str, float],
    *,
    sexual_nature: str | None = None,
    db_path: Path | str | None = None,
    prior: dict[str, float] | None = None,
    genome_weight: float = SEXUAL_NATURE_GENOME_WEIGHT,
    softmax_temp: float = SEXUAL_NATURE_SOFTMAX_TEMP,
) -> str:
    """Sample ``sexual_nature`` from a prior near 85/9/6 blended with genome fit.

    Per trait column: ``value`` adds ``genome[trait]``, ``inverse`` adds
    ``-genome[trait]``; mean over columns defines logits softened by
    ``softmax_temp``. ``genome_weight`` mixes that distribution with ``prior``
    (default ``SEXUAL_NATURE_PRIOR``).
    """
    if sexual_nature is not None:
        return str(sexual_nature).strip().lower()
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    definitions = _sexual_nature_definitions(str(path.resolve()))
    if not definitions:
        return "heterosexual"
    scores = {
        key: _score_compiled_definition(genome, terms)
        for key, terms in definitions
    }
    if not scores:
        return "heterosexual"
    pr = prior if prior is not None else SEXUAL_NATURE_PRIOR
    dist = _blend_priors_with_genome(scores, pr, genome_weight, softmax_temp)
    return _sample_categorical(dist)


def choose_gender_mind(
    genome: dict[str, float],
    gender: str,
    *,
    gender_mind: str | None = None,
    db_path: Path | str | None = None,
    genome_weight: float = GENDER_MIND_GENOME_WEIGHT,
    softmax_temp: float = GENDER_MIND_SOFTMAX_TEMP,
) -> str:
    """Sample gender mind: ~90% aligned with sex, ~10% opposite, blended with genome.

    Prior is 90/10 masculine/feminine for ``Male`` and the reverse for ``Female``.
    """
    if gender_mind is not None:
        return str(gender_mind).strip().lower()
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    fallback = "feminine" if gender.strip() == "Female" else "masculine"
    opposite = "masculine" if fallback == "feminine" else "feminine"
    definitions = _gender_mind_definitions(str(path.resolve()))
    if not definitions:
        return fallback
    scores = {
        key: _score_compiled_definition(genome, terms)
        for key, terms in definitions
    }
    if not scores:
        return fallback
    prior = {fallback: 0.9, opposite: 0.1}
    dist = _blend_priors_with_genome(scores, prior, genome_weight, softmax_temp)
    return _sample_categorical(dist)


@lru_cache(maxsize=32)
def _species_age_distribution_table(
    db_path_s: str, world: str
) -> dict[tuple[str, str], dict[str, float]]:
    from utils.util_age_distribution import species_age_distributions

    return species_age_distributions(Path(db_path_s), world=world)


def species_stage_age_bounds(species_row: sqlite3.Row) -> dict[str, tuple[int, int]]:
    """Inclusive (min_age, max_age) per ``LIFE_STAGES`` key from species thresholds."""
    m = _as_int(species_row["maturity"], 1)
    p = _as_int(species_row["prime"], 1)
    mid = _as_int(species_row["middleaged"], 1)
    e = _as_int(species_row["elder"], 1)
    life = _as_int(species_row["lifespan"], 1)
    return {
        "child": (0, m - 1),
        "mature": (m, p - 1),
        "prime": (p, mid - 1),
        "middleaged": (mid, e - 1),
        "elder": (e, life),
    }


def _infer_life_stage(age: int, species_row: sqlite3.Row) -> str:
    b = species_stage_age_bounds(species_row)
    for stage in LIFE_STAGES:
        lo, hi = b[stage]
        if lo <= age <= hi:
            return stage
    life = _as_int(species_row["lifespan"], 1)
    a = max(0, min(age, life))
    for stage in LIFE_STAGES:
        lo, hi = b[stage]
        if lo <= a <= hi:
            return stage
    return "elder"


def infer_life_stage_from_age(age: int, species_row: sqlite3.Row) -> str:
    """Map integer age in years to ``life_stage`` using ``species`` thresholds."""
    return _infer_life_stage(int(age), species_row)


def _random_age_in_stage(
    stage: str,
    species_row: sqlite3.Row,
    *,
    elder_skew: float = DEFAULT_ELDER_AGE_SKEW,
) -> int:
    lo, hi = species_stage_age_bounds(species_row)[stage]
    if lo > hi:
        raise ValueError(
            f"life_stage {stage!r} has no valid ages for this species "
            f"({species_row['species']!r} / {species_row['ethnic']!r})."
        )
    if stage == "elder" and hi > lo:
        span = hi - lo + 1
        u = random.random() ** max(elder_skew, 1e-6)
        return lo + int(u * span)
    return random.randint(lo, hi)


def choose_life_stage_and_age(
    species_row: sqlite3.Row,
    *,
    life_stage: str | None = None,
    age: int | None = None,
    db_path: Path | str | None = None,
    world: str = "default",
    elder_skew: float = DEFAULT_ELDER_AGE_SKEW,
) -> tuple[str, int]:
    """Pick ``(life_stage, age)`` from cohort distribution unless overridden.

    Uses ``utils.util_age_distribution`` weights for the row's
    ``(species, ethnic)``. Ages are uniform within a stage except **elder**,
    where draws skew toward the minimum (younger) end of the band.
    """
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    key = (
        (species_row["species"] or "").strip(),
        (species_row["ethnic"] or "").strip(),
    )

    if life_stage is not None and age is not None:
        lo, hi = species_stage_age_bounds(species_row)[life_stage]
        if lo > hi:
            raise ValueError(
                f"life_stage {life_stage!r} has no valid ages for this species row."
            )
        a = max(lo, min(int(age), hi))
        return life_stage, a

    if life_stage is not None:
        return life_stage, _random_age_in_stage(
            life_stage, species_row, elder_skew=elder_skew
        )

    if age is not None:
        a = int(age)
        return _infer_life_stage(a, species_row), a

    table = _species_age_distribution_table(str(path.resolve()), world)
    dist = table.get(key)
    if dist is None:
        raise LookupError(
            f"No age distribution cached for species={key[0]!r}, ethnic={key[1]!r}"
        )
    weights = [max(0.0, float(dist.get(s, 0.0))) for s in LIFE_STAGES]
    if sum(weights) <= 0:
        chosen = random.choice(LIFE_STAGES)
    else:
        chosen = random.choices(LIFE_STAGES, weights=weights, k=1)[0]
    return chosen, _random_age_in_stage(chosen, species_row, elder_skew=elder_skew)


def choose_life_stage(*, life_stage: str | None = None) -> str:
    """Uniform pick over LIFE_STAGES unless overridden."""
    if life_stage is not None:
        return life_stage
    return random.choice(LIFE_STAGES)


def choose_mature_height_cm(
    species_row: sqlite3.Row,
    gender: str,
    *,
    height_cm: float | None = None,
) -> float:
    """Mature-height draw: np.random.normal(baseline, baseline / 24)."""
    if height_cm is not None:
        return float(height_cm)
    baseline = _as_int(species_row[f"{_gender_prefix(gender)}height"], 0)
    if baseline <= 0:
        raise ValueError(
            f"Species row missing {_gender_prefix(gender)}height baseline."
        )
    return float(np.random.normal(baseline, baseline / 24))


def choose_weight_kg(
    height_cm: float,
    *,
    weight_kg: float | None = None,
    species_row: sqlite3.Row | None = None,
    gender: str | None = None,
) -> float:
    """Sample BMI from species baselines, then kg = BMI * (height_m)^2.

    BMI ~ N(baseline_bmi, stdev) with stdev from species baseline height/BMI;
    if baselines are missing, BMI ~ N(25, 4.5).
    """
    if weight_kg is not None:
        return float(weight_kg)
    baseline_bmi = 0.0
    baseline_height = 0.0
    if species_row is not None and gender is not None:
        prefix = _gender_prefix(gender)
        baseline_bmi = float(_as_int(species_row[f"{prefix}bmi"], 0))
        baseline_height = float(_as_int(species_row[f"{prefix}height"], 0))
    if baseline_bmi > 0 and baseline_height > 0:
        stdev = (
            (4 - ((baseline_height / baseline_bmi) / 1.8) + 4)
            * ((100 - (180 - baseline_height)) / 100)
        )
        stdev = max(1e-6, float(stdev))
        bmi = float(np.random.normal(baseline_bmi, stdev))
    else:
        bmi = float(np.random.normal(25, 4.5))
    height_m = height_cm / 100.0
    return bmi * (height_m ** 2)


def choose_skin(
    species_row: sqlite3.Row,
    gender: str,
    *,
    skin: str | None = None,
) -> str:
    if skin is not None:
        return skin
    return _pick_token(species_row[f"{_gender_prefix(gender)}skin"])


def choose_hair(
    species_row: sqlite3.Row,
    gender: str,
    *,
    hair: str | None = None,
) -> str:
    if hair is not None:
        return hair
    return _pick_token(species_row[f"{_gender_prefix(gender)}hair"])


def choose_eyes(
    species_row: sqlite3.Row,
    gender: str,
    *,
    eyes: str | None = None,
) -> str:
    if eyes is not None:
        return eyes
    return _pick_token(species_row[f"{_gender_prefix(gender)}eyes"])


def placeholder_name() -> tuple[str, str]:
    """Placeholder until real naming is implemented."""
    return ("Boaty", "McBoatface")
