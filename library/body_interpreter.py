"""Interpret stored body metrics (especially for immature individuals)."""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from pathlib import Path

from library.person import Person
from library.random_traits import (
    DEFAULT_DB_PATH,
    _as_int,
    choose_species_row,
    infer_life_stage_from_age,
)

# Human median height as a fraction of adult height, vs age as a fraction of
# maturity (u = age / maturity_age). Anchors approximate typical human males;
# scaling u maps any species maturity_age onto the same shape.
_HEIGHT_U = (0.0, 0.125, 0.375, 0.625, 0.875, 1.0)
_HEIGHT_FRAC = (0.29, 0.52, 0.68, 0.82, 0.93, 1.0)

_WEIGHT_U = (0.0, 0.125, 0.375, 0.625, 0.875, 1.0)
_WEIGHT_FRAC = (0.045, 0.18, 0.38, 0.58, 0.85, 1.0)


def _piecewise_linear(u: float, xs: tuple[float, ...], ys: tuple[float, ...]) -> float:
    u = max(0.0, min(1.0, float(u)))
    i = bisect.bisect_right(xs, u) - 1
    i = max(0, min(i, len(xs) - 2))
    x0, x1 = xs[i], xs[i + 1]
    y0, y1 = ys[i], ys[i + 1]
    if x1 <= x0:
        return y1
    t = (u - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)


def _human_height_fraction_toward_mature(age_years: float, maturity_age: int) -> float:
    if maturity_age <= 0:
        return 1.0
    u = max(0.0, min(1.0, age_years / float(maturity_age)))
    return _piecewise_linear(u, _HEIGHT_U, _HEIGHT_FRAC)


def _human_weight_fraction_toward_mature(age_years: float, maturity_age: int) -> float:
    if maturity_age <= 0:
        return 1.0
    u = max(0.0, min(1.0, age_years / float(maturity_age)))
    return _piecewise_linear(u, _WEIGHT_U, _WEIGHT_FRAC)


def _gender_prefix(gender: str) -> str:
    return "m" if gender.strip() == "Male" else "f"


def _mature_baseline_height_cm(species_row: "sqlite3.Row", gender: str) -> float:
    p = _gender_prefix(gender)
    return float(_as_int(species_row[f"{p}height"], 170))


def _mature_baseline_weight_kg(species_row: "sqlite3.Row", gender: str, height_cm: float) -> float:
    p = _gender_prefix(gender)
    bmi = float(_as_int(species_row[f"{p}bmi"], 24))
    hm = height_cm / 100.0
    return bmi * hm * hm


@dataclass(frozen=True)
class InterpretedPhysique:
    """Age, inferred life stage, and height/weight read against mature baselines."""

    age_years: int
    life_stage: str
    maturity_age: int
    is_prior_to_maturity: bool
    mature_height_cm: float
    mature_weight_kg: float
    recorded_height_cm: float | None
    recorded_weight_kg: float | None
    interpreted_height_cm: float | None
    interpreted_weight_kg: float | None
    height_growth_fraction: float
    weight_growth_fraction: float


def interpret_physique(
    person: Person,
    *,
    current_year: int,
    db_path: Path | str | None = None,
) -> InterpretedPhysique:
    """Infer life stage and rescale height/weight for individuals not yet mature.

    ``current_year`` minus ``person.birthyear`` gives age. The species row for
    ``(species, ethnic)`` supplies maturity thresholds and mature ``m*``/``f*``
    height and BMI baselines.

    If age is **below** the species ``maturity`` age, height and weight are
    interpreted as human-like fractions of those mature baselines (piecewise
    curves in normalized age ``age / maturity``). At or after maturity,
    ``Person.maturity_height_cm`` / ``maturity_weight_kg`` are taken at face
    value (fractions 1.0).
    """
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    species_row = choose_species_row(
        species=person.species, ethnic=person.ethnic, db_path=path
    )
    maturity_age = max(1, _as_int(species_row["maturity"], 16))
    age = int(current_year) - int(person.birthyear)
    age = max(0, age)

    life_stage = infer_life_stage_from_age(age, species_row)
    mature_h = _mature_baseline_height_cm(species_row, person.gender)
    mature_w = _mature_baseline_weight_kg(species_row, person.gender, mature_h)

    immature = age < maturity_age
    hf = _human_height_fraction_toward_mature(float(age), maturity_age) if immature else 1.0
    wf = _human_weight_fraction_toward_mature(float(age), maturity_age) if immature else 1.0

    rec_h = (
        float(person.maturity_height_cm)
        if person.maturity_height_cm is not None
        else None
    )
    rec_w = (
        float(person.maturity_weight_kg)
        if person.maturity_weight_kg is not None
        else None
    )

    if immature:
        int_h = mature_h * hf
        int_w = mature_w * wf
    else:
        int_h = rec_h
        int_w = rec_w

    return InterpretedPhysique(
        age_years=age,
        life_stage=life_stage,
        maturity_age=maturity_age,
        is_prior_to_maturity=immature,
        mature_height_cm=mature_h,
        mature_weight_kg=mature_w,
        recorded_height_cm=rec_h,
        recorded_weight_kg=rec_w,
        interpreted_height_cm=int_h,
        interpreted_weight_kg=int_w,
        height_growth_fraction=hf,
        weight_growth_fraction=wf,
    )


__all__ = ["InterpretedPhysique", "interpret_physique"]
