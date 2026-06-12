"""Current mind/body trait layer: copied from genome at birth, mutable by simulation.

``Person.genome`` stays immutable for inheritance and mating. Work, composites, and
social attractiveness read signed magnitudes from ``mind_body`` (with genome fill-in
for legacy saves), clamped to [-99.99, 99.99].
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from library.person import Person

MIND_BODY_MIN = -99.99
MIND_BODY_MAX = 99.99

# Past max fertility (or fallback age), stochastic trait drift may apply.
ELDER_MIND_BODY_FALLBACK_AGE = 55

# Per-trait annual trial probability (elderly only).
ELDER_MIND_BODY_TRIAL_P_INTELLECT = 0.11
ELDER_MIND_BODY_TRIAL_P_PHYSICAL = 0.11
ELDER_MIND_BODY_TRIAL_P_MATING = 0.10
ELDER_MIND_BODY_TRIAL_P_NEURO = 0.09

# Typical downward / upward step sizes (uniform range endpoints).
ELDER_STEP_DOWN_MAX = 1.15
ELDER_STEP_DOWN_MIN = 0.2
ELDER_STEP_UP_MAX = 1.05
ELDER_STEP_UP_MIN = 0.18


def clamp_mind_body_value(raw: float) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(MIND_BODY_MIN, min(MIND_BODY_MAX, v))


def mind_body_from_genome(genome: Mapping[str, float] | None) -> dict[str, float]:
    """Full trait copy for newborns / repair; values clamped to mind/body bounds."""
    if not genome:
        return {}
    return {str(k): clamp_mind_body_value(v) for k, v in genome.items()}


def work_trait_values(person: "Person") -> dict[str, float]:
    """Trait magnitudes for jobs, fitness, and composites: mind_body with genome backfill."""
    g = person.genome or {}
    mb = person.mind_body or {}
    if not mb:
        return mind_body_from_genome(g)
    out: dict[str, float] = {str(k): clamp_mind_body_value(v) for k, v in mb.items()}
    for k, v in g.items():
        if k not in out:
            out[str(k)] = clamp_mind_body_value(float(v))
    return out


def ensure_full_mind_body(person: "Person") -> dict[str, float]:
    """Return a full mind_body map (mutating nothing)."""
    return work_trait_values(person)


def _is_elder_for_mind_body(person: "Person", year: int) -> bool:
    age = int(year) - int(person.birthyear)
    mx = person.max_fertility_age
    if mx is not None:
        return age > int(mx)
    return age >= ELDER_MIND_BODY_FALLBACK_AGE


def _elderly_attractiveness_multiplier(person: "Person", year: int) -> float:
    """Applies only to the aggregate attractiveness score; does not alter symmetry."""
    age = int(year) - int(person.birthyear)
    mx = person.max_fertility_age
    threshold = int(mx) if mx is not None else 40
    if age <= threshold:
        return 1.0
    excess = float(age - threshold)
    # Gentle taper; floor so elders are not zeroed out.
    return max(0.38, 1.0 - 0.0125 * excess)


def _romantic_trait_weights() -> tuple[tuple[str, float], ...]:
    return (
        ("symmetry", 0.34),
        ("physical", 0.24),
        ("neurochemical", 0.18),
        ("intellect", 0.12),
        ("persuasion", 0.06),
        ("wit", 0.04),
        ("mating drive", 0.02),
    )


def _near_ideal_signal_01(value: float) -> float:
    """Nonlinear appeal for genome/mind-body values where 0 is the ideal."""
    magnitude = max(0.0, min(1.0, abs(float(value)) / 100.0))
    return max(0.0, 1.0 - magnitude) ** 1.55


def base_romantic_trait_score_01(person: "Person") -> float:
    """Weighted romantic appeal from current mind/body.

    Symmetry and physical condition dominate first impressions, while severe
    neurochemical or intellectual extremes pull the score down sharply.
    """
    traits = work_trait_values(person)
    acc = 0.0
    total_weight = 0.0
    for k, weight in _romantic_trait_weights():
        if k not in traits:
            continue
        acc += _near_ideal_signal_01(float(traits[k])) * float(weight)
        total_weight += float(weight)
    if total_weight <= 0.0:
        return 0.5
    return max(0.0, min(1.0, acc / total_weight))


def attractiveness_01(person: "Person", year: int) -> float:
    """0..1 appeal: trait base (symmetry unmodified) times elderly-only multiplier."""
    base = base_romantic_trait_score_01(person)
    m = _elderly_attractiveness_multiplier(person, year)
    return max(0.0, min(1.0, base * m))


def mind_body_aging_rng_seed(year: int, person_id: int, salt: int) -> int:
    return int(year) * 1_804_211 + int(person_id) * 12_989 + int(salt) + 61_003


def maybe_apply_elder_mind_body_year(
    person: "Person",
    *,
    year: int,
    rng: random.Random,
) -> dict[str, float]:
    """Return updated mind_body dict after optional elder stochastic nudges."""
    if not _is_elder_for_mind_body(person, year):
        return dict(ensure_full_mind_body(person))

    mb = dict(ensure_full_mind_body(person))

    def nudge_down(key: str, p_trial: float) -> None:
        if key not in mb or rng.random() >= p_trial:
            return
        delta = -rng.uniform(ELDER_STEP_DOWN_MIN, ELDER_STEP_DOWN_MAX)
        mb[key] = clamp_mind_body_value(float(mb[key]) + delta)

    def nudge_up(key: str, p_trial: float) -> None:
        if key not in mb or rng.random() >= p_trial:
            return
        delta = rng.uniform(ELDER_STEP_UP_MIN, ELDER_STEP_UP_MAX)
        mb[key] = clamp_mind_body_value(float(mb[key]) + delta)

    nudge_down("intellect", ELDER_MIND_BODY_TRIAL_P_INTELLECT)
    nudge_down("physical", ELDER_MIND_BODY_TRIAL_P_PHYSICAL)
    nudge_down("mating drive", ELDER_MIND_BODY_TRIAL_P_MATING)
    nudge_up("neurochemical", ELDER_MIND_BODY_TRIAL_P_NEURO)
    return mb
