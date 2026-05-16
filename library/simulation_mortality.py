"""Apply annual mortality to simulation participants from historical milestones."""

from __future__ import annotations

import random

from library.simulation_context import SimulationContext

# Flat additive surcharge on annual infant probability for multiple births (same-year litter).
TWIN_INFANT_MORTALITY_SURCHARGE = 0.025
TRIPLET_INFANT_MORTALITY_SURCHARGE = 0.06

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


def _age_adjusted_annual_mortality(
    *,
    age: int,
    infant_annual: float,
    child_annual: float,
    adult_annual: float,
    birth_litter_size: int = 1,
) -> float:
    if age == 0:
        base = infant_annual * _PEACE_MORTALITY_OFFSET_BY_BAND["infant"]
        return min(0.999999, base + _litter_infant_surcharge(birth_litter_size))
    if 1 <= age <= 4:
        return child_annual * _PEACE_MORTALITY_OFFSET_BY_BAND["child"]
    if 5 <= age <= 14:
        return max(0.0005, child_annual * 0.18 * _PEACE_MORTALITY_OFFSET_BY_BAND["youth"])
    if 15 <= age <= 49:
        return max(0.001, adult_annual * 0.35 * _PEACE_MORTALITY_OFFSET_BY_BAND["adult"])
    if 50 <= age <= 69:
        return min(0.999999, max(0.002, adult_annual * 0.9 * _PEACE_MORTALITY_OFFSET_BY_BAND["older"]))
    return min(0.999999, max(0.004, adult_annual * 1.6 * _PEACE_MORTALITY_OFFSET_BY_BAND["elder"]))


def apply_annual_mortality(ctx: SimulationContext, simulation_year: int) -> dict[str, float]:
    rates = ctx.get_mortality_rates_for_year(simulation_year)
    infant_annual = max(0.0, min(1.0, rates["infant_mortality_pct"] / 100.0))
    child_annual = _annual_child_1_to_4_mortality(
        rates["infant_mortality_pct"], rates["under5_mortality_pct"]
    )
    adult_annual = _annual_adult_mortality(
        rates["under5_mortality_pct"], rates["percent_reaching_age_100"]
    )

    dead_ids: set[int] = set()
    for rec in ctx.iter_current_people():
        age = int(simulation_year) - int(rec.person.birthyear)
        if age < 0:
            continue
        p = _age_adjusted_annual_mortality(
            age=age,
            infant_annual=infant_annual,
            child_annual=child_annual,
            adult_annual=adult_annual,
            birth_litter_size=int(rec.person.birth_litter_size or 1),
        )
        if random.random() < p:
            dead_ids.add(rec.person_id)

    if dead_ids:
        ctx.mark_dead(dead_ids, deathyear=simulation_year)

    rates["deaths_count"] = float(len(dead_ids))
    return rates

