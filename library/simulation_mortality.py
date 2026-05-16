"""Apply annual mortality to simulation participants from historical milestones."""

from __future__ import annotations

import random
from pathlib import Path

import numpy as np

from library.random_traits import _as_int, _species_rows
from library.simulation_context import SimulationContext

# Flat additive surcharge on annual infant probability for multiple births (same-year litter).
TWIN_INFANT_MORTALITY_SURCHARGE = 0.025
TRIPLET_INFANT_MORTALITY_SURCHARGE = 0.06

DEFAULT_LIFESPAN_YEARS = 100
LIFESPAN_MORTALITY_RAMP_START_MULTIPLIER = 0.85
LIFESPAN_MORTALITY_HARD_CAP_MULTIPLIER = 1.15
LIFESPAN_MORTALITY_RAMP_MAX_PROBABILITY = 0.92
LIFESPAN_MORTALITY_RAMP_POWER = 2.2
LIFESPAN_MORTALITY_HARD_CAP_PROBABILITY = 0.999999

# In the current simulation, war/crime mortality is not explicitly modeled.
# Apply a small peace-time offset to historical all-cause mortality rates.
_PEACE_MORTALITY_OFFSET_BY_BAND: dict[str, float] = {
    "infant": 0.99,
    "child": 0.99,
    "youth": 0.97,   # historically conflict/violence contributes more here
    "adult": 0.95,   # strongest offset in working-age years
    "older": 0.98,
    "elder": 0.99,
}


def _annual_child_1_to_4_mortality(infant_pct: float, under5_pct: float) -> float:
    i = max(0.0, min(0.999999, infant_pct / 100.0))
    u = max(i, min(0.999999, under5_pct / 100.0))
    survive_to_5 = 1.0 - u
    survive_past_infant = max(1e-9, 1.0 - i)
    conditional_block = 1.0 - (survive_to_5 / survive_past_infant)
    conditional_block = max(0.0, min(0.999999, conditional_block))
    return 1.0 - ((1.0 - conditional_block) ** (1.0 / 4.0))


def _annual_adult_mortality(under5_pct: float, centenarian_pct: float) -> float:
    u = max(0.0, min(0.999999, under5_pct / 100.0))
    c = max(0.0, min(0.999999, centenarian_pct / 100.0))
    survive_to_5 = max(1e-9, 1.0 - u)
    target_survive_5_to_100 = max(1e-12, min(0.999999, c / survive_to_5))
    annual_survival = target_survive_5_to_100 ** (1.0 / 95.0)
    raw = 1.0 - annual_survival
    # Keep adult annual risk in a broad calibration band.
    return max(0.001, min(0.03, raw))


def _litter_infant_surcharge(birth_litter_size: int) -> float:
    if birth_litter_size >= 3:
        return TRIPLET_INFANT_MORTALITY_SURCHARGE
    if birth_litter_size >= 2:
        return TWIN_INFANT_MORTALITY_SURCHARGE
    return 0.0


def _lifespan_mortality_pressure(*, age: int, lifespan: int | None) -> float:
    if lifespan is None:
        return 0.0
    if lifespan <= 0:
        lifespan = DEFAULT_LIFESPAN_YEARS
    if age < 0:
        return 0.0

    ramp_start = max(1.0, float(lifespan) * LIFESPAN_MORTALITY_RAMP_START_MULTIPLIER)
    hard_cap = max(
        ramp_start + 1.0,
        float(lifespan) * LIFESPAN_MORTALITY_HARD_CAP_MULTIPLIER,
    )
    if age >= hard_cap:
        return LIFESPAN_MORTALITY_HARD_CAP_PROBABILITY
    if age <= ramp_start:
        return 0.0

    progress = (float(age) - ramp_start) / (hard_cap - ramp_start)
    progress = max(0.0, min(1.0, progress))
    return LIFESPAN_MORTALITY_RAMP_MAX_PROBABILITY * (
        progress ** LIFESPAN_MORTALITY_RAMP_POWER
    )


def _combine_independent_mortality_probabilities(base: float, extra: float) -> float:
    base = max(0.0, min(0.999999, float(base)))
    extra = max(0.0, min(0.999999, float(extra)))
    return min(0.999999, 1.0 - ((1.0 - base) * (1.0 - extra)))


def _apply_lifespan_pressure_array(
    *,
    probs: np.ndarray,
    ages: np.ndarray,
    lifespans: np.ndarray,
) -> np.ndarray:
    lifespans_f = np.maximum(lifespans.astype(float), 1.0)
    ramp_start = lifespans_f * LIFESPAN_MORTALITY_RAMP_START_MULTIPLIER
    hard_cap = np.maximum(
        ramp_start + 1.0,
        lifespans_f * LIFESPAN_MORTALITY_HARD_CAP_MULTIPLIER,
    )
    progress = np.clip(
        (ages.astype(float) - ramp_start) / (hard_cap - ramp_start),
        0.0,
        1.0,
    )
    extra = LIFESPAN_MORTALITY_RAMP_MAX_PROBABILITY * (
        progress ** LIFESPAN_MORTALITY_RAMP_POWER
    )
    extra = np.where(ages >= hard_cap, LIFESPAN_MORTALITY_HARD_CAP_PROBABILITY, extra)
    combined = 1.0 - ((1.0 - probs) * (1.0 - extra))
    return np.minimum(0.999999, np.maximum(0.0, combined))


def _species_lifespan_maps(
    ctx: SimulationContext,
) -> tuple[dict[tuple[str, str], int], dict[str, int]]:
    rows = _species_rows(str(Path(ctx.db_path).resolve()))
    by_key: dict[tuple[str, str], int] = {}
    by_species: dict[str, int] = {}
    for row in rows:
        species = str(row.get("species") or "").strip()
        ethnic = str(row.get("ethnic") or "").strip()
        lifespan = max(1, _as_int(row.get("lifespan"), DEFAULT_LIFESPAN_YEARS))
        if species and ethnic:
            by_key[(species, ethnic)] = lifespan
        if species and species not in by_species:
            by_species[species] = lifespan
    return by_key, by_species


def _lifespan_for_person(
    *,
    species: str | None,
    ethnic: str | None,
    lifespan_by_key: dict[tuple[str, str], int],
    lifespan_by_species: dict[str, int],
) -> int:
    species_s = str(species or "").strip()
    ethnic_s = str(ethnic or "").strip()
    if species_s and ethnic_s:
        lifespan = lifespan_by_key.get((species_s, ethnic_s))
        if lifespan is not None:
            return lifespan
    if species_s:
        lifespan = lifespan_by_species.get(species_s)
        if lifespan is not None:
            return lifespan
    return DEFAULT_LIFESPAN_YEARS


def _age_adjusted_annual_mortality(
    *,
    age: int,
    infant_annual: float,
    child_annual: float,
    adult_annual: float,
    birth_litter_size: int = 1,
    lifespan: int | None = None,
) -> float:
    if age == 0:
        base = infant_annual * _PEACE_MORTALITY_OFFSET_BY_BAND["infant"]
        prob = min(0.999999, base + _litter_infant_surcharge(birth_litter_size))
    elif 1 <= age <= 4:
        prob = child_annual * _PEACE_MORTALITY_OFFSET_BY_BAND["child"]
    elif 5 <= age <= 14:
        prob = max(0.0005, child_annual * 0.18 * _PEACE_MORTALITY_OFFSET_BY_BAND["youth"])
    elif 15 <= age <= 49:
        prob = max(0.001, adult_annual * 0.35 * _PEACE_MORTALITY_OFFSET_BY_BAND["adult"])
    elif 50 <= age <= 69:
        prob = min(
            0.999999,
            max(0.002, adult_annual * 0.9 * _PEACE_MORTALITY_OFFSET_BY_BAND["older"]),
        )
    else:
        prob = min(
            0.999999,
            max(0.004, adult_annual * 1.6 * _PEACE_MORTALITY_OFFSET_BY_BAND["elder"]),
        )
    return _combine_independent_mortality_probabilities(
        prob,
        _lifespan_mortality_pressure(age=age, lifespan=lifespan),
    )


def _age_adjusted_annual_mortality_array(
    *,
    ages: np.ndarray,
    infant_annual: float,
    child_annual: float,
    adult_annual: float,
    birth_litter_sizes: np.ndarray,
    lifespans: np.ndarray | None = None,
) -> np.ndarray:
    """Vectorized probability calculation matching ``_age_adjusted_annual_mortality``."""
    probs = np.zeros_like(ages, dtype=float)

    infant = ages == 0
    if np.any(infant):
        surcharge = np.zeros_like(probs)
        surcharge = np.where(
            birth_litter_sizes >= 3,
            TRIPLET_INFANT_MORTALITY_SURCHARGE,
            surcharge,
        )
        surcharge = np.where(
            (birth_litter_sizes >= 2) & (birth_litter_sizes < 3),
            TWIN_INFANT_MORTALITY_SURCHARGE,
            surcharge,
        )
        probs = np.where(
            infant,
            np.minimum(
                0.999999,
                infant_annual * _PEACE_MORTALITY_OFFSET_BY_BAND["infant"] + surcharge,
            ),
            probs,
        )

    child = (ages >= 1) & (ages <= 4)
    probs = np.where(
        child,
        child_annual * _PEACE_MORTALITY_OFFSET_BY_BAND["child"],
        probs,
    )

    youth = (ages >= 5) & (ages <= 14)
    probs = np.where(
        youth,
        max(0.0005, child_annual * 0.18 * _PEACE_MORTALITY_OFFSET_BY_BAND["youth"]),
        probs,
    )

    adult = (ages >= 15) & (ages <= 49)
    probs = np.where(
        adult,
        max(0.001, adult_annual * 0.35 * _PEACE_MORTALITY_OFFSET_BY_BAND["adult"]),
        probs,
    )

    older = (ages >= 50) & (ages <= 69)
    probs = np.where(
        older,
        min(
            0.999999,
            max(0.002, adult_annual * 0.9 * _PEACE_MORTALITY_OFFSET_BY_BAND["older"]),
        ),
        probs,
    )

    elder = ages >= 70
    probs = np.where(
        elder,
        min(
            0.999999,
            max(0.004, adult_annual * 1.6 * _PEACE_MORTALITY_OFFSET_BY_BAND["elder"]),
        ),
        probs,
    )
    if lifespans is not None:
        probs = _apply_lifespan_pressure_array(
            probs=probs,
            ages=ages,
            lifespans=lifespans,
        )
    return probs


def apply_annual_mortality(ctx: SimulationContext, simulation_year: int) -> dict[str, float]:
    rates = ctx.get_mortality_rates_for_year(simulation_year)
    infant_annual = max(0.0, min(1.0, rates["infant_mortality_pct"] / 100.0))
    child_annual = _annual_child_1_to_4_mortality(
        rates["infant_mortality_pct"], rates["under5_mortality_pct"]
    )
    adult_annual = _annual_adult_mortality(
        rates["under5_mortality_pct"], rates["percent_reaching_age_100"]
    )

    records = list(ctx.iter_current_people())
    if not records:
        rates["deaths_count"] = 0.0
        return rates
    ages = np.fromiter(
        (int(simulation_year) - int(rec.person.birthyear) for rec in records),
        dtype=np.int64,
        count=len(records),
    )
    litter_sizes = np.fromiter(
        (int(rec.person.birth_litter_size or 1) for rec in records),
        dtype=np.int64,
        count=len(records),
    )
    lifespan_by_key, lifespan_by_species = _species_lifespan_maps(ctx)
    lifespans = np.fromiter(
        (
            _lifespan_for_person(
                species=rec.person.species,
                ethnic=rec.person.ethnic,
                lifespan_by_key=lifespan_by_key,
                lifespan_by_species=lifespan_by_species,
            )
            for rec in records
        ),
        dtype=np.int64,
        count=len(records),
    )
    probs = _age_adjusted_annual_mortality_array(
        ages=ages,
        infant_annual=infant_annual,
        child_annual=child_annual,
        adult_annual=adult_annual,
        birth_litter_sizes=litter_sizes,
        lifespans=lifespans,
    )
    rolls = np.fromiter(
        (random.random() for _ in records),
        dtype=float,
        count=len(records),
    )
    dead_ids = {
        rec.person_id
        for rec, age, roll, p in zip(records, ages, rolls, probs)
        if int(age) >= 0 and float(roll) < float(p)
    }

    if dead_ids:
        ctx.mark_dead(dead_ids, deathyear=simulation_year)

    rates["deaths_count"] = float(len(dead_ids))
    return rates

