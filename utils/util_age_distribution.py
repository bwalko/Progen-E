"""Age-stage population shares from mortality parameters and species life bands."""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path

from library.world_paths import config_db_path as _default_cfg_path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = _default_cfg_path()

LIFE_STAGES: tuple[str, ...] = ("child", "mature", "prime", "middleaged", "elder")


def calculate_age_distribution(
    infant_mortality: float,
    child_mortality: float,
    adult_decay: float,
    *,
    maturity: int,
    prime: int,
    middleaged: int,
    elder: int,
    lifespan: int,
) -> dict[str, float]:
    """
    Percentage distribution of a synthetic cohort by life stage, from mortality
    parameters and species age-band thresholds (same grain as ``species`` CSV).

    Survival: infant risk at age 0, power-law childhood decay for ages
    ``1 .. maturity-1``, exponential adult decay from ``maturity`` onward, capped
    at ``lifespan``.

    Stage keys match ``library.random_traits.LIFE_STAGES`` (lowercase).
    """
    _validate_thresholds(maturity, prime, middleaged, elder, lifespan)

    survival_curve: dict[int, float] = {}
    for x in range(lifespan + 1):
        if x == 0:
            s_x = 1.0
        elif 1 <= x < maturity:
            denom = max(maturity - 1, 1)
            s_x = (1.0 - infant_mortality) * (
                (1.0 - child_mortality) ** ((x - 1) / denom)
            )
        else:
            s_x = (1.0 - infant_mortality) * (1.0 - child_mortality) * math.exp(
                -adult_decay * (x - maturity)
            )
        survival_curve[x] = s_x

    categories = {
        "child": range(0, maturity),
        "mature": range(maturity, prime),
        "prime": range(prime, middleaged),
        "middleaged": range(middleaged, elder),
        "elder": range(elder, lifespan + 1),
    }

    weights = {
        name: sum(survival_curve[x] for x in age_range)
        for name, age_range in categories.items()
    }
    total_weight = sum(weights.values())
    if total_weight <= 0:
        raise ValueError("Total survival weight is zero; check mortality inputs.")

    return {
        name: round((w / total_weight) * 100.0, 2) for name, w in weights.items()
    }


def _validate_thresholds(
    maturity: int,
    prime: int,
    middleaged: int,
    elder: int,
    lifespan: int,
) -> None:
    if maturity < 1:
        raise ValueError("maturity must be >= 1")
    if not maturity <= prime <= middleaged <= elder <= lifespan:
        raise ValueError(
            "Require maturity <= prime <= middleaged <= elder <= lifespan; "
            f"got maturity={maturity}, prime={prime}, middleaged={middleaged}, "
            f"elder={elder}, lifespan={lifespan}"
        )


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


def _as_float(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def mortality_for_world(
    conn: sqlite3.Connection, world: str = "default"
) -> tuple[float, float, float]:
    """Return ``(infant_mortality, child_mortality, adult_decay)`` from ``world_start``."""
    cur = conn.cursor()
    row = cur.execute(
        """
        SELECT start_infant_mortality, start_child_mortality, start_adult_decay
        FROM world_start
        WHERE world = ?
        """,
        (world,),
    ).fetchone()
    if row is None:
        raise LookupError(f"No world_start row for world={world!r}")
    return (
        _as_float(row[0], 0.0),
        _as_float(row[1], 0.0),
        _as_float(row[2], 0.0),
    )


def species_age_distributions(
    db_path: Path | str | None = None,
    *,
    world: str = "default",
    infant_mortality: float | None = None,
    child_mortality: float | None = None,
    adult_decay: float | None = None,
) -> dict[tuple[str, str], dict[str, float]]:
    """
    For each ``species`` row, compute the age-stage percentage distribution.

    Keys are ``(species, ethnic)`` (stripped strings). Values are dicts with
    keys ``child``, ``mature``, ``prime``, ``middleaged``, ``elder``.

    Mortality defaults come from ``world_start`` for ``world`` unless all three
    overrides are provided.
    """
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if not path.is_file():
        raise FileNotFoundError(
            f"Database not found: {path}. Run: python utils/util_load_config.py --world default"
        )

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        if (
            infant_mortality is None
            or child_mortality is None
            or adult_decay is None
        ):
            i, c, a = mortality_for_world(conn, world)
            infant_mortality = infant_mortality if infant_mortality is not None else i
            child_mortality = child_mortality if child_mortality is not None else c
            adult_decay = adult_decay if adult_decay is not None else a

        rows = conn.execute("SELECT * FROM species").fetchall()
        out: dict[tuple[str, str], dict[str, float]] = {}
        for row in rows:
            species = (row["species"] or "").strip()
            ethnic = (row["ethnic"] or "").strip()
            dist = calculate_age_distribution(
                infant_mortality,
                child_mortality,
                adult_decay,
                maturity=_as_int(row["maturity"], 1),
                prime=_as_int(row["prime"], 1),
                middleaged=_as_int(row["middleaged"], 1),
                elder=_as_int(row["elder"], 1),
                lifespan=_as_int(row["lifespan"], 1),
            )
            out[(species, ethnic)] = dist
        return out
    finally:
        conn.close()


__all__ = [
    "LIFE_STAGES",
    "calculate_age_distribution",
    "mortality_for_world",
    "species_age_distributions",
    "DEFAULT_DB_PATH",
]
