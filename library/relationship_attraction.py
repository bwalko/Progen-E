"""Relationship attraction, desirability, and extreme-trait compatibility."""

from __future__ import annotations

import random

from library.mind_body import attractiveness_01, work_trait_values
from library.person import Person

RELATIONSHIP_TRAIT_KEYS: tuple[str, ...] = (
    "physical",
    "symmetry",
    "intellect",
    "neurochemical",
    "persuasion",
    "wit",
    "mating drive",
)

EXTREME_TRAIT_KEYS: tuple[str, ...] = (
    "physical",
    "symmetry",
    "intellect",
    "neurochemical",
)

EXTREME_START_01 = 0.70
EXTREME_FULL_01 = 0.95


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def relationship_trait_data_available(person: Person) -> bool:
    traits = work_trait_values(person)
    return (
        any(k in traits for k in RELATIONSHIP_TRAIT_KEYS)
        or person.attractiveness_01 is not None
    )


def trait_deviation_01(person: Person, key: str) -> float:
    return clamp01(abs(float(work_trait_values(person).get(key, 0.0))) / 100.0)


def trait_ideal_signal_01(person: Person, key: str, *, default: float = 0.5) -> float:
    traits = work_trait_values(person)
    if key not in traits:
        return clamp01(default)
    magnitude = trait_deviation_01(person, key)
    return clamp01(max(0.0, 1.0 - magnitude) ** 1.55)


def person_attractiveness_01(person: Person, year: int) -> float:
    if person.attractiveness_01 is not None:
        return clamp01(float(person.attractiveness_01))
    return attractiveness_01(person, int(year))


def person_prosperity_01(
    person: Person,
    *,
    resource_pressure: float | None = None,
    default: float = 0.35,
) -> float:
    values: list[float] = []
    if person.household_prosperity is not None:
        values.append(clamp01(float(person.household_prosperity)))
    if person.job_prosperity_01 is not None:
        values.append(clamp01(float(person.job_prosperity_01)))
    if resource_pressure is not None:
        values.append(
            clamp01(1.0 - min(1.0, max(0.0, float(resource_pressure)) / 2.0))
        )
    if not values:
        return clamp01(default)
    return clamp01(sum(values) / len(values))


def _smooth_extreme_01(deviation_01: float) -> float:
    x = clamp01(
        (float(deviation_01) - EXTREME_START_01)
        / (EXTREME_FULL_01 - EXTREME_START_01)
    )
    return x * x * (3.0 - 2.0 * x)


def trait_extreme_01(person: Person, key: str) -> float:
    return _smooth_extreme_01(trait_deviation_01(person, key))


def person_pariah_pressure_01(person: Person) -> float:
    return max(trait_extreme_01(person, key) for key in EXTREME_TRAIT_KEYS)


def _surface_beauty_01(person: Person, year: int) -> float:
    attraction = person_attractiveness_01(person, int(year))
    symmetry = trait_ideal_signal_01(person, "symmetry", default=attraction)
    physical = trait_ideal_signal_01(person, "physical", default=attraction)
    return clamp01(0.58 * attraction + 0.28 * symmetry + 0.14 * physical)


def _core_viability_01(person: Person) -> float:
    physical = trait_ideal_signal_01(person, "physical")
    neuro = trait_ideal_signal_01(person, "neurochemical")
    intellect = trait_ideal_signal_01(person, "intellect")
    return clamp01(0.42 * physical + 0.34 * neuro + 0.24 * intellect)


def person_relationship_desirability_01(
    person: Person,
    year: int,
    *,
    prosperity_01: float | None = None,
    sustain: bool = False,
) -> float:
    if not relationship_trait_data_available(person):
        return 0.68
    prosperity = (
        person_prosperity_01(person)
        if prosperity_01 is None
        else clamp01(float(prosperity_01))
    )
    surface = _surface_beauty_01(person, int(year))
    core = _core_viability_01(person)
    pariah = person_pariah_pressure_01(person)
    if sustain:
        base = 0.36 * surface + 0.40 * core + 0.24 * prosperity
        penalty_strength = 0.86
        prosperity_floor = 0.10 + 0.06 * pariah
    else:
        base = 0.74 * surface + 0.12 * core + 0.14 * prosperity
        penalty_strength = 0.66
        prosperity_floor = 0.10 + 0.12 * pariah
    penalty = 1.0 - penalty_strength * (pariah ** 1.45)
    return clamp01(base * penalty + prosperity * prosperity_floor)


def pair_extreme_fit_multiplier(a: Person, b: Person) -> float:
    """Penalty for one-sided extreme dysfunction, with mutual-extreme tolerance."""
    multiplier = 1.0
    min_multipliers = {
        "neurochemical": 0.14,
        "physical": 0.18,
        "symmetry": 0.25,
        "intellect": 0.30,
    }
    for key in EXTREME_TRAIT_KEYS:
        ea = trait_extreme_01(a, key)
        eb = trait_extreme_01(b, key)
        mismatch = abs(ea - eb)
        if mismatch <= 0.0:
            continue
        floor = min_multipliers.get(key, 0.30)
        multiplier *= 1.0 - (1.0 - floor) * mismatch
    return clamp01(multiplier)


def pair_mutual_extreme_affinity_01(a: Person, b: Person) -> float:
    """How much both people share the same critical-trait extreme."""
    return max(
        min(trait_extreme_01(a, key), trait_extreme_01(b, key))
        for key in EXTREME_TRAIT_KEYS
    )


def relationship_pair_score_01(
    a: Person,
    b: Person,
    year: int,
    *,
    prosperity_a_01: float | None = None,
    prosperity_b_01: float | None = None,
    sustain: bool = False,
) -> float:
    da = person_relationship_desirability_01(
        a,
        int(year),
        prosperity_01=prosperity_a_01,
        sustain=sustain,
    )
    db = person_relationship_desirability_01(
        b,
        int(year),
        prosperity_01=prosperity_b_01,
        sustain=sustain,
    )
    base = 0.55 * min(da, db) + 0.45 * ((da + db) / 2.0)
    mutual_extreme = pair_mutual_extreme_affinity_01(a, b)
    base = clamp01(base + (0.04 if sustain else 0.34) * mutual_extreme)
    fit = pair_extreme_fit_multiplier(a, b)
    effective_fit = fit if sustain else 0.35 + 0.65 * fit
    return clamp01(base * effective_fit)


def partner_formation_probability_01(pair_score_01: float) -> float:
    score = clamp01(pair_score_01)
    if score >= 0.58:
        return 0.99
    if score >= 0.38:
        return 0.62 + 1.65 * (score - 0.38)
    if score >= 0.18:
        return 0.08 + 2.70 * (score - 0.18)
    return 0.01 * (score / 0.18)


def paramour_formation_multiplier(pair_score_01: float) -> float:
    score = clamp01(pair_score_01)
    return clamp01(0.06 + 3.25 * (score ** 1.45)) * 3.2


def deterministic_pair_rng(
    year: int,
    salt: int,
    person_a_id: int,
    person_b_id: int,
    *,
    stream: int,
) -> random.Random:
    lo, hi = sorted((int(person_a_id), int(person_b_id)))
    return random.Random(int(year) * int(stream) + int(salt) * 41 + lo * 11_017 + hi)
