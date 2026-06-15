"""Higher-variance materialization for people selected into detailed simulation."""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import Mapping

from library.mind_body import attractiveness_01, mind_body_from_genome
from library.person import Person
from library.random_traits import GENOME_MAX_MAGNITUDE


HIGH_VARIANCE_DETAIL_COMPOSITE = "High-Variance Detail"


@dataclass(frozen=True)
class DetailedSelectionProfile:
    key: str
    intensity: float
    center_chance: float
    predatory_bias_chance: float = 0.0
    extra_trait_chance: float = 0.0

_CENTERED_TRAITS: tuple[str, ...] = (
    "empathy",
    "justice",
    "honesty",
    "temperance",
    "discipline",
    "patience",
    "nurturance",
    "civics",
)

_EXTREME_TRAITS: tuple[str, ...] = (
    "ambition",
    "assertiveness",
    "courage",
    "creativity",
    "curiosity",
    "empathy",
    "focus",
    "frugality",
    "generosity",
    "honesty",
    "intellect",
    "justice",
    "loyalty",
    "mating drive",
    "neurochemical",
    "patience",
    "perception",
    "persuasion",
    "temperance",
    "wit",
)

_PREDATORY_RISK_TRAITS: Mapping[str, float] = {
    "empathy": -1.0,
    "justice": -1.0,
    "honesty": -1.0,
    "temperance": -1.0,
    "patience": -1.0,
    "neurochemical": 1.0,
    "assertiveness": 1.0,
    "perception": 1.0,
    "discipline": 1.0,
    "persuasion": 1.0,
}

_SELECTION_PROFILES: Mapping[str, DetailedSelectionProfile] = {
    "baseline": DetailedSelectionProfile(
        "baseline", 0.36, 0.30, predatory_bias_chance=0.01
    ),
    "founder": DetailedSelectionProfile(
        "founder", 0.46, 0.34, predatory_bias_chance=0.05, extra_trait_chance=0.10
    ),
    "ruler": DetailedSelectionProfile("ruler", 0.58, 0.28, extra_trait_chance=0.18),
    "officeholder": DetailedSelectionProfile(
        "officeholder", 0.50, 0.32, predatory_bias_chance=0.03, extra_trait_chance=0.10
    ),
    "elite": DetailedSelectionProfile(
        "elite", 0.52, 0.30, predatory_bias_chance=0.015, extra_trait_chance=0.16
    ),
    "specialist": DetailedSelectionProfile(
        "specialist", 0.48, 0.36, predatory_bias_chance=0.012, extra_trait_chance=0.12
    ),
    "criminal_outlaw": DetailedSelectionProfile(
        "criminal_outlaw",
        0.70,
        0.12,
        predatory_bias_chance=0.30,
        extra_trait_chance=0.25,
    ),
    "religious": DetailedSelectionProfile("religious", 0.44, 0.46, extra_trait_chance=0.08),
    "migrant_frontier": DetailedSelectionProfile(
        "migrant_frontier", 0.42, 0.26, extra_trait_chance=0.10
    ),
    "kinship_link": DetailedSelectionProfile("kinship_link", 0.30, 0.36),
    "inspection": DetailedSelectionProfile("inspection", 0.26, 0.30),
    "spotlight": DetailedSelectionProfile(
        "spotlight",
        0.66,
        0.18,
        predatory_bias_chance=0.18,
        extra_trait_chance=0.24,
    ),
}

_REASON_PROFILE_KEYS: Mapping[str, str] = {
    "founder": "founder",
    "office_selection": "officeholder",
    "settlement_detail_floor": "officeholder",
    "head_of_government": "ruler",
    "ruler": "ruler",
    "succession": "ruler",
    "usurpation": "ruler",
    "elite": "elite",
    "specialist": "specialist",
    "scholar": "specialist",
    "craft_specialist": "specialist",
    "religious": "religious",
    "priest": "religious",
    "marriage_into_detailed_family": "kinship_link",
    "inheritance": "kinship_link",
    "kinship_link": "kinship_link",
    "settlement_context": "migrant_frontier",
    "migration_into_focal_settlement": "migrant_frontier",
    "frontier": "migrant_frontier",
    "user_inspection": "inspection",
    "narrative_spotlight": "spotlight",
    "nondetailed_promotion": "baseline",
    "criminal": "criminal_outlaw",
    "outlaw": "criminal_outlaw",
    "crime": "criminal_outlaw",
    "murder": "criminal_outlaw",
    "violence": "criminal_outlaw",
    "scandal": "criminal_outlaw",
}


def _stable_seed(*parts: object) -> int:
    h = 14_695_981_039_346_656_037
    for part in parts:
        for ch in str(part):
            h ^= ord(ch)
            h = (h * 1_099_511_628_211) & 0xFFFFFFFFFFFFFFFF
    return h


def _clamp_genome_value(value: float) -> float:
    hi = float(GENOME_MAX_MAGNITUDE)
    return round(max(-hi, min(hi, float(value))), 2)


def detailed_selection_profile(
    reason: str,
    source: Mapping[str, object] | None = None,
) -> DetailedSelectionProfile:
    key = str(reason or "").strip().lower()
    profile_key = _REASON_PROFILE_KEYS.get(key)
    source_kind = str((source or {}).get("source_kind") or "").strip().lower()
    focus = str((source or {}).get("focus") or "").strip().lower()
    source_text = " ".join(str(value).lower() for value in (source or {}).values())
    combined = f"{key} {source_kind} {focus} {source_text}"
    if profile_key is None:
        for token, candidate_key in _REASON_PROFILE_KEYS.items():
            if token in combined:
                profile_key = candidate_key
                break
    if source_kind == "nondetailed_directory":
        profile_key = profile_key or "baseline"
        if profile_key == "baseline":
            return DetailedSelectionProfile(
                "nondetailed_directory",
                0.42,
                _SELECTION_PROFILES["baseline"].center_chance,
                extra_trait_chance=0.08,
            )
    if focus in {"spotlight", "narrative_spotlight"}:
        profile_key = "spotlight"
    profile = _SELECTION_PROFILES.get(profile_key or "baseline", _SELECTION_PROFILES["baseline"])
    return DetailedSelectionProfile(
        profile.key,
        max(0.0, min(0.80, profile.intensity)),
        max(0.0, min(0.80, profile.center_chance)),
        max(0.0, min(0.80, profile.predatory_bias_chance)),
        max(0.0, min(0.80, profile.extra_trait_chance)),
    )


def detail_variance_score(person: Person) -> float:
    """Return a 0..1 measure of how far a person's genome is from ordinary."""

    values = [
        abs(float(value))
        for value in (person.genome or {}).values()
        if value is not None
    ]
    if not values:
        return 0.0
    values.sort(reverse=True)
    top = values[: min(8, len(values))]
    return max(0.0, min(1.0, sum(top) / len(top) / float(GENOME_MAX_MAGNITUDE)))


def apply_detailed_selection_variance(
    person: Person,
    *,
    person_id: int,
    year: int,
    reason: str,
    source: Mapping[str, object] | None = None,
) -> Person:
    """Bias materialized detailed people toward center-or-extreme significance.

    Non-detailed rows carry the ordinary population baseline. When one of those
    people is promoted into full annual logic, this helper gives the detailed
    record more variance without making every promoted person heroic, competent,
    wealthy, or harmful. It pushes a small deterministic subset of traits either
    closer to the ideal center or further toward an extreme, based on why the
    person became detailed.
    """

    genome = dict(person.genome or {})
    if not genome:
        return person
    profile = detailed_selection_profile(reason, source)
    intensity = profile.intensity
    if intensity <= 0.0:
        return person

    rng = random.Random(_stable_seed(person_id, year, reason, source or {}, person.birthyear))
    extreme_traits = [trait for trait in _EXTREME_TRAITS if trait in genome]
    center_traits = [trait for trait in _CENTERED_TRAITS if trait in genome]
    if not extreme_traits and not center_traits:
        return person

    trait_count = (
        1
        + int(rng.random() < intensity)
        + int(rng.random() < intensity * 0.55)
        + int(rng.random() < profile.extra_trait_chance)
    )
    selected: set[str] = set()
    source_text = " ".join(str(value).lower() for value in (source or {}).values())
    reason_text = str(reason or "").lower()
    predatory_context = any(
        token in f"{reason_text} {source_text}"
        for token in ("criminal", "outlaw", "murder", "violence", "scandal", "spotlight")
    )

    for _ in range(trait_count):
        center_chance = profile.center_chance * (0.55 if predatory_context else 1.0)
        choose_center = bool(center_traits) and rng.random() < center_chance
        pool = center_traits if choose_center else extreme_traits
        if not pool:
            pool = extreme_traits or center_traits
        selected.add(rng.choice(pool))

    predatory_seed = bool(
        (predatory_context and rng.random() < max(0.18, profile.predatory_bias_chance))
        or (not predatory_context and rng.random() < profile.predatory_bias_chance)
    )
    if predatory_seed:
        if predatory_context:
            selected.update(rng.sample(list(_PREDATORY_RISK_TRAITS), k=3))
        else:
            selected.update(_PREDATORY_RISK_TRAITS)

    for trait in selected:
        if trait not in genome:
            continue
        current = float(genome[trait])
        if trait in center_traits and not predatory_seed and rng.random() < 0.40:
            genome[trait] = _clamp_genome_value(current * (1.0 - 0.55 * intensity))
            continue
        sign = 1.0 if current >= 0.0 else -1.0
        if predatory_seed and trait in _PREDATORY_RISK_TRAITS:
            sign = float(_PREDATORY_RISK_TRAITS[trait])
        target = sign * rng.uniform(82.0, 98.0)
        push = rng.uniform(0.45, 0.80) * intensity
        if predatory_seed and trait in _PREDATORY_RISK_TRAITS:
            target = sign * rng.uniform(90.0, 99.0)
            push = 1.0
        genome[trait] = _clamp_genome_value(
            current + (target - current) * push
        )

    mind_body = mind_body_from_genome(genome) if person.mind_body else {}
    composites = tuple(person.genome_composite_names or ())
    if HIGH_VARIANCE_DETAIL_COMPOSITE not in composites:
        composites = (*composites, HIGH_VARIANCE_DETAIL_COMPOSITE)
    updated = replace(
        person,
        genome=genome,
        mind_body=mind_body,
        genome_composite_names=composites,
    )
    if person.attractiveness_01 is not None:
        age_year = int(year)
        updated = replace(updated, attractiveness_01=round(attractiveness_01(updated, age_year), 5))
    return updated
