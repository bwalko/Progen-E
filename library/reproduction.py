"""Reproduction rules and offspring generation gatekeeping."""

from __future__ import annotations

import random
from pathlib import Path
from typing import TYPE_CHECKING

from library.generator import generate_person_from_birth
from library.person import Person

if TYPE_CHECKING:
    from library.simulation_context import SimulationContext


def roll_birth_litter_size(rng: random.Random) -> int:
    """Singleton vs twin vs triplet for one birth event (same father)."""
    r = rng.random()
    if r < 0.01:
        return 3
    if r < 0.04:
        return 2
    return 1


def _age(person: Person, simulation_year: int) -> int:
    return int(simulation_year) - int(person.birthyear)


def _below_min_fertility(person: Person, simulation_year: int) -> bool:
    min_age = person.min_fertility_age
    if min_age is None:
        return False
    return _age(person, simulation_year) < int(min_age)


def _above_max_fertility(person: Person, simulation_year: int) -> bool:
    max_age = person.max_fertility_age
    if max_age is None:
        return False
    return _age(person, simulation_year) > int(max_age)


def having_sex(
    participant_a: Person,
    participant_b: Person,
    *,
    simulation_year: int,
    db_path: Path | str | None = None,
    world: str = "default",
    birth_reference_year: int | None = None,
    birthyear: int | None = None,
    gender: str | None = None,
    life_stage: str | None = None,
    age: int | None = None,
    maturity_height_cm: float | None = None,
    maturity_weight_kg: float | None = None,
    skin: str | None = None,
    hair: str | None = None,
    eyes: str | None = None,
    sexual_nature: str | None = None,
    gender_mind: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    birthplace: str | None = None,
    birthplace_region_id: str | None = None,
    birthplace_settlement_id: str | None = None,
    elder_skew: float = 0.35,
    simulation_context: "SimulationContext | None" = None,
    surname_convention: str | None = None,
) -> Person | None:
    """Return offspring when fertility checks pass; otherwise return ``None``."""
    if _below_min_fertility(participant_a, simulation_year):
        return None
    if _below_min_fertility(participant_b, simulation_year):
        return None
    if _above_max_fertility(participant_a, simulation_year):
        return None
    if _above_max_fertility(participant_b, simulation_year):
        return None
    mother = (
        participant_a
        if (participant_a.gender or "").strip().lower() == "female"
        else participant_b
    )
    resolved_region = (
        birthplace_region_id
        if birthplace_region_id is not None
        else mother.birthplace_region_id
    )
    base_settle = (mother.current_settlement_id or mother.birthplace_settlement_id)
    resolved_settlement = (
        birthplace_settlement_id
        if birthplace_settlement_id is not None
        else base_settle
    )
    return generate_person_from_birth(
        participant_a,
        participant_b,
        db_path=db_path,
        world=world,
        simulation_year=simulation_year,
        birth_reference_year=birth_reference_year,
        birthyear=birthyear,
        gender=gender,
        life_stage=life_stage,
        age=age,
        maturity_height_cm=maturity_height_cm,
        maturity_weight_kg=maturity_weight_kg,
        skin=skin,
        hair=hair,
        eyes=eyes,
        sexual_nature=sexual_nature,
        gender_mind=gender_mind,
        first_name=first_name,
        last_name=last_name,
        birthplace=birthplace,
        birthplace_region_id=resolved_region,
        birthplace_settlement_id=resolved_settlement,
        elder_skew=elder_skew,
        simulation_context=simulation_context,
        birth_litter_size=1,
        surname_convention=surname_convention,
    )


def having_sex_birth_event(
    participant_a: Person,
    participant_b: Person,
    *,
    simulation_year: int,
    rng: random.Random | None = None,
    db_path: Path | str | None = None,
    world: str = "default",
    birth_reference_year: int | None = None,
    birthyear: int | None = None,
    life_stage: str | None = None,
    age: int | None = None,
    birthplace: str | None = None,
    birthplace_region_id: str | None = None,
    birthplace_settlement_id: str | None = None,
    elder_skew: float = 0.35,
    simulation_context: "SimulationContext | None" = None,
    mother_person_id: int | None = None,
    surname_convention: str | None = None,
) -> list[Person] | None:
    """Possibly multiple newborns (twins/triplets) for one birth event, same parents.

    Fertility is checked once. Returns ``None`` if conception fails.
    """
    if _below_min_fertility(participant_a, simulation_year):
        return None
    if _below_min_fertility(participant_b, simulation_year):
        return None
    if _above_max_fertility(participant_a, simulation_year):
        return None
    if _above_max_fertility(participant_b, simulation_year):
        return None
    mother = (
        participant_a
        if (participant_a.gender or "").strip().lower() == "female"
        else participant_b
    )
    resolved_region = (
        birthplace_region_id
        if birthplace_region_id is not None
        else mother.birthplace_region_id
    )
    base_settle = mother.current_settlement_id or mother.birthplace_settlement_id
    resolved_settlement = (
        birthplace_settlement_id
        if birthplace_settlement_id is not None
        else base_settle
    )
    r = rng or random.Random()
    litter = roll_birth_litter_size(r)
    children: list[Person] = []
    for i in range(litter):
        children.append(
            generate_person_from_birth(
                participant_a,
                participant_b,
                db_path=db_path,
                world=world,
                simulation_year=simulation_year,
                birth_reference_year=birth_reference_year,
                birthyear=birthyear,
                life_stage=life_stage,
                age=age,
                birthplace=birthplace,
                birthplace_region_id=resolved_region,
                birthplace_settlement_id=resolved_settlement,
                elder_skew=elder_skew,
                simulation_context=simulation_context,
                birth_litter_size=litter,
                allow_secondary_settlement_spinoff=(i == 0),
                mother_person_id=mother_person_id,
                surname_convention=surname_convention,
            )
        )
    return children


# Annual conception roll (population growth + zero-point). Tunable after removing biennial parity.
BIRTH_BASE_CONCEPTION_PROB = 0.38
BIRTH_PROSPERITY_INVERSE_WEIGHT = 0.24
BIRTH_MATING_DRIVE_WEIGHT = 0.18
BIRTH_P_MIN = 0.05
BIRTH_P_MAX = 0.9

_MATING_DRIVE_KEY = "mating drive"


def _mating_drive_component(person: Person) -> float:
    """Map signed mind/body ``mating drive`` to [0, 1]; higher = more libido (excess positive)."""
    from library.mind_body import work_trait_values

    v = float(work_trait_values(person).get(_MATING_DRIVE_KEY, 0.0))
    return max(0.0, min(1.0, 0.5 + v / 110.0))


def pair_prosperity_01(
    a: Person,
    b: Person,
    *,
    pressure_a: float | None,
    pressure_b: float | None,
) -> float:
    """Higher when both have job prosperity and local resource pressure is low (0..1)."""
    def _wage_component(p: Person) -> float:
        if (p.employment_status or "").strip().lower() == "employed" and (
            p.job_prosperity_01 is not None
        ):
            return max(0.0, min(1.0, float(p.job_prosperity_01)))
        if (p.job or "").strip():
            return 0.55
        return 0.1

    wage_avg = (_wage_component(a) + _wage_component(b)) / 2.0
    pa = 0.0 if pressure_a is None else max(0.0, min(1.0, float(pressure_a) / 2.0))
    pb = 0.0 if pressure_b is None else max(0.0, min(1.0, float(pressure_b) / 2.0))
    pf = (pa + pb) / 2.0
    return 0.5 * wage_avg + 0.5 * (1.0 - pf)


def annual_conception_probability(
    mother: Person,
    father: Person,
    *,
    pressure: float | None,
) -> float:
    """Blend mating drive, dual employment prosperity, and inverse resource pressure."""
    drive_avg = (_mating_drive_component(mother) + _mating_drive_component(father)) / 2.0
    prosperity = pair_prosperity_01(
        mother, father, pressure_a=pressure, pressure_b=pressure
    )
    p = BIRTH_BASE_CONCEPTION_PROB
    p += BIRTH_PROSPERITY_INVERSE_WEIGHT * (1.0 - prosperity)
    p += BIRTH_MATING_DRIVE_WEIGHT * (drive_avg - 0.5) * 2.0
    return max(BIRTH_P_MIN, min(BIRTH_P_MAX, p))


def conception_rng(year: int, seed: int, mother_id: int, father_id: int) -> random.Random:
    """Deterministic RNG stream for one couple-year (``seed`` = sim_seed or region salt)."""
    return random.Random(
        int(year) * 991_693 + int(seed) * 17 + int(mother_id) * 1009 + int(father_id) + 88_219
    )
