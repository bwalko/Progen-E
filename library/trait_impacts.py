"""Genome trait banding and practical consequence profiles.

Genome values are signed deviations from the ideal centerpoint. The midpoint
of the population distribution is ordinary, not a hidden signal; this module
keeps center and strong/extreme values as the places where traits matter.
"""

from __future__ import annotations

import csv
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


TraitBand = Literal[
    "center",
    "mild_deviation",
    "ordinary",
    "strong_deviation",
    "extreme_deviation",
]
TraitPole = Literal["center", "deficient", "excess"]
ImpactCategory = Literal[
    "mortality_health",
    "work_capacity",
    "household_stability",
    "finances",
    "violence",
    "reputation",
    "legal_fallout",
    "marriage_paramour",
    "care_burden",
    "social_standing",
]
ImpactKind = Literal[
    "beneficial_center",
    "harmful_center",
    "harmful_extreme",
    "useful_extreme",
    "context_dependent_extreme",
]
CategoryWeights = tuple[tuple[ImpactCategory, float], ...]


CENTER_MAX_MAGNITUDE = 10.0
MILD_MAX_MAGNITUDE = 35.0
ORDINARY_MAX_MAGNITUDE = 65.0
EXTREME_MIN_MAGNITUDE = 90.0

CENTER_SIGNAL_FULL_MAGNITUDE = 0.0
CENTER_SIGNAL_ZERO_MAGNITUDE = 25.0
DEVIATION_SIGNAL_START_MAGNITUDE = 55.0
DEVIATION_SIGNAL_FULL_MAGNITUDE = 95.0
EXTREME_SIGNAL_START_MAGNITUDE = 80.0
EXTREME_SIGNAL_FULL_MAGNITUDE = 100.0

IMPACT_CATEGORIES: tuple[ImpactCategory, ...] = (
    "mortality_health",
    "work_capacity",
    "household_stability",
    "finances",
    "violence",
    "reputation",
    "legal_fallout",
    "marriage_paramour",
    "care_burden",
    "social_standing",
)


@dataclass(frozen=True)
class TraitDefinition:
    trait: str
    deficient_deviation: str = ""
    optimal_centerpoint: str = ""
    excess_deviation: str = ""
    deficient_description: str = ""
    optimal_description: str = ""
    excess_description: str = ""


@dataclass(frozen=True)
class TraitImpactRule:
    center_benefits: CategoryWeights = ()
    center_pressures: CategoryWeights = ()
    deficient_extreme_pressures: CategoryWeights = ()
    deficient_extreme_benefits: CategoryWeights = ()
    excess_extreme_pressures: CategoryWeights = ()
    excess_extreme_benefits: CategoryWeights = ()
    context_dependent_categories: tuple[ImpactCategory, ...] = ()


@dataclass(frozen=True)
class TraitClassification:
    trait: str
    value: float
    magnitude: float
    pole: TraitPole
    band: TraitBand
    center_signal_01: float
    strong_deviation_signal_01: float
    extreme_signal_01: float
    ordinary_signal_01: float
    definition: TraitDefinition | None = None
    impact_kinds: frozenset[ImpactKind] = frozenset()
    impact_categories: frozenset[ImpactCategory] = frozenset()


@dataclass(frozen=True)
class TraitImpactProfile:
    benefits: Mapping[ImpactCategory, float]
    pressures: Mapping[ImpactCategory, float]
    classifications: Mapping[str, TraitClassification]

    def benefit(self, category: ImpactCategory) -> float:
        return clamp01(float(self.benefits.get(category, 0.0)))

    def pressure(self, category: ImpactCategory) -> float:
        return clamp01(float(self.pressures.get(category, 0.0)))

    def net_benefit(self, category: ImpactCategory) -> float:
        return self.benefit(category) - self.pressure(category)


def clamp01(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _smoothstep(x: float) -> float:
    x = clamp01(x)
    return x * x * (3.0 - 2.0 * x)


def _weights(values: Mapping[ImpactCategory, float] | None) -> CategoryWeights:
    if not values:
        return ()
    return tuple((category, float(weight)) for category, weight in values.items())


def _rule(
    *,
    center_benefits: Mapping[ImpactCategory, float] | None = None,
    center_pressures: Mapping[ImpactCategory, float] | None = None,
    deficient_pressures: Mapping[ImpactCategory, float] | None = None,
    deficient_benefits: Mapping[ImpactCategory, float] | None = None,
    excess_pressures: Mapping[ImpactCategory, float] | None = None,
    excess_benefits: Mapping[ImpactCategory, float] | None = None,
    context: Iterable[ImpactCategory] = (),
) -> TraitImpactRule:
    return TraitImpactRule(
        center_benefits=_weights(center_benefits),
        center_pressures=_weights(center_pressures),
        deficient_extreme_pressures=_weights(deficient_pressures),
        deficient_extreme_benefits=_weights(deficient_benefits),
        excess_extreme_pressures=_weights(excess_pressures),
        excess_extreme_benefits=_weights(excess_benefits),
        context_dependent_categories=tuple(context),
    )


TRAIT_IMPACT_RULES: Mapping[str, TraitImpactRule] = {
    "physical": _rule(
        center_benefits={
            "mortality_health": 0.34,
            "work_capacity": 0.26,
            "marriage_paramour": 0.10,
            "social_standing": 0.08,
        },
        deficient_pressures={
            "mortality_health": 0.44,
            "work_capacity": 0.30,
            "care_burden": 0.22,
            "social_standing": 0.08,
        },
        excess_pressures={
            "mortality_health": 0.30,
            "work_capacity": 0.18,
            "reputation": 0.18,
            "social_standing": 0.16,
            "marriage_paramour": 0.12,
            "care_burden": 0.10,
        },
    ),
    "intellect": _rule(
        center_benefits={"work_capacity": 0.34, "social_standing": 0.14},
        deficient_pressures={
            "work_capacity": 0.34,
            "care_burden": 0.18,
            "social_standing": 0.12,
        },
        excess_pressures={
            "mortality_health": 0.22,
            "household_stability": 0.20,
            "violence": 0.12,
            "reputation": 0.18,
            "legal_fallout": 0.08,
            "care_burden": 0.16,
        },
        excess_benefits={"work_capacity": 0.08},
        context=("work_capacity", "reputation"),
    ),
    "symmetry": _rule(
        center_benefits={
            "marriage_paramour": 0.26,
            "social_standing": 0.16,
            "reputation": 0.08,
        },
        deficient_pressures={
            "marriage_paramour": 0.20,
            "social_standing": 0.14,
            "reputation": 0.08,
        },
        excess_pressures={
            "marriage_paramour": 0.20,
            "social_standing": 0.14,
            "reputation": 0.08,
        },
    ),
    "mating drive": _rule(
        center_benefits={"marriage_paramour": 0.18, "household_stability": 0.06},
        deficient_pressures={"marriage_paramour": 0.26, "household_stability": 0.08},
        excess_pressures={
            "marriage_paramour": 0.28,
            "household_stability": 0.20,
            "reputation": 0.20,
            "legal_fallout": 0.10,
            "violence": 0.08,
        },
        context=("marriage_paramour", "reputation"),
    ),
    "neurochemical": _rule(
        center_benefits={
            "mortality_health": 0.28,
            "work_capacity": 0.14,
            "household_stability": 0.24,
            "marriage_paramour": 0.14,
        },
        deficient_pressures={
            "household_stability": 0.16,
            "marriage_paramour": 0.16,
            "social_standing": 0.08,
            "care_burden": 0.08,
        },
        excess_pressures={
            "mortality_health": 0.34,
            "household_stability": 0.32,
            "violence": 0.28,
            "reputation": 0.22,
            "legal_fallout": 0.16,
            "care_burden": 0.24,
        },
    ),
    "courage": _rule(
        center_benefits={"work_capacity": 0.14, "social_standing": 0.16},
        deficient_pressures={"social_standing": 0.18, "work_capacity": 0.10},
        excess_pressures={
            "mortality_health": 0.16,
            "violence": 0.20,
            "legal_fallout": 0.12,
            "household_stability": 0.08,
        },
        excess_benefits={"social_standing": 0.08},
        context=("social_standing", "violence"),
    ),
    "temperance": _rule(
        center_benefits={
            "finances": 0.22,
            "mortality_health": 0.16,
            "household_stability": 0.16,
        },
        deficient_pressures={
            "finances": 0.24,
            "mortality_health": 0.16,
            "household_stability": 0.16,
            "reputation": 0.10,
        },
        excess_pressures={
            "work_capacity": 0.12,
            "social_standing": 0.10,
            "marriage_paramour": 0.08,
        },
    ),
    "patience": _rule(
        center_benefits={"household_stability": 0.18, "work_capacity": 0.16},
        deficient_pressures={
            "violence": 0.18,
            "legal_fallout": 0.12,
            "household_stability": 0.20,
            "work_capacity": 0.10,
        },
        excess_pressures={"work_capacity": 0.16, "social_standing": 0.10},
    ),
    "wit": _rule(
        center_benefits={"social_standing": 0.16, "marriage_paramour": 0.08},
        deficient_pressures={"social_standing": 0.12, "marriage_paramour": 0.06},
        excess_pressures={"reputation": 0.12, "social_standing": 0.10},
        excess_benefits={"social_standing": 0.04},
        context=("social_standing",),
    ),
    "friendliness": _rule(
        center_benefits={
            "social_standing": 0.18,
            "marriage_paramour": 0.10,
            "household_stability": 0.08,
        },
        deficient_pressures={
            "social_standing": 0.18,
            "household_stability": 0.14,
            "marriage_paramour": 0.10,
        },
        excess_pressures={"social_standing": 0.08, "reputation": 0.08},
        excess_benefits={"marriage_paramour": 0.04},
        context=("social_standing",),
    ),
    "modesty": _rule(
        center_benefits={"reputation": 0.16, "social_standing": 0.10},
        deficient_pressures={
            "reputation": 0.18,
            "marriage_paramour": 0.10,
            "household_stability": 0.08,
        },
        excess_pressures={
            "work_capacity": 0.12,
            "social_standing": 0.10,
            "marriage_paramour": 0.08,
        },
    ),
    "ambition": _rule(
        center_benefits={"work_capacity": 0.20, "social_standing": 0.12},
        deficient_pressures={"work_capacity": 0.24, "finances": 0.14},
        excess_pressures={
            "violence": 0.16,
            "legal_fallout": 0.18,
            "reputation": 0.14,
            "household_stability": 0.10,
        },
        excess_benefits={"work_capacity": 0.06, "social_standing": 0.05},
        context=("social_standing", "legal_fallout"),
    ),
    "frugality": _rule(
        center_benefits={"finances": 0.26, "household_stability": 0.08},
        deficient_pressures={"finances": 0.34, "household_stability": 0.18},
        excess_pressures={
            "finances": 0.12,
            "household_stability": 0.18,
            "reputation": 0.14,
            "marriage_paramour": 0.10,
            "legal_fallout": 0.06,
        },
        context=("finances", "reputation"),
    ),
    "persuasion": _rule(
        center_benefits={
            "work_capacity": 0.16,
            "social_standing": 0.16,
            "marriage_paramour": 0.08,
        },
        deficient_pressures={"work_capacity": 0.16, "social_standing": 0.12},
        excess_pressures={
            "legal_fallout": 0.18,
            "reputation": 0.16,
            "violence": 0.08,
            "household_stability": 0.08,
        },
        excess_benefits={"work_capacity": 0.06, "social_standing": 0.04},
        context=("legal_fallout", "social_standing"),
    ),
    "curiosity": _rule(
        center_benefits={"work_capacity": 0.18, "social_standing": 0.06},
        deficient_pressures={"work_capacity": 0.16, "social_standing": 0.06},
        excess_pressures={
            "work_capacity": 0.14,
            "household_stability": 0.10,
            "care_burden": 0.06,
        },
        excess_benefits={"work_capacity": 0.04},
        context=("work_capacity",),
    ),
    "justice": _rule(
        center_benefits={
            "legal_fallout": 0.22,
            "reputation": 0.18,
            "social_standing": 0.10,
        },
        deficient_pressures={
            "violence": 0.28,
            "legal_fallout": 0.34,
            "reputation": 0.24,
            "finances": 0.10,
        },
        excess_pressures={
            "violence": 0.18,
            "legal_fallout": 0.20,
            "social_standing": 0.12,
            "reputation": 0.10,
        },
        context=("legal_fallout", "violence"),
    ),
    "humility": _rule(
        center_benefits={"social_standing": 0.14, "household_stability": 0.08},
        deficient_pressures={
            "social_standing": 0.12,
            "reputation": 0.12,
            "marriage_paramour": 0.08,
        },
        excess_pressures={
            "social_standing": 0.16,
            "marriage_paramour": 0.12,
            "work_capacity": 0.08,
        },
        context=("social_standing",),
    ),
    "generosity": _rule(
        center_benefits={
            "household_stability": 0.12,
            "social_standing": 0.14,
            "marriage_paramour": 0.08,
        },
        deficient_pressures={
            "violence": 0.12,
            "finances": 0.10,
            "household_stability": 0.14,
            "social_standing": 0.10,
            "reputation": 0.08,
        },
        excess_pressures={"finances": 0.28, "household_stability": 0.14},
        excess_benefits={"social_standing": 0.06},
        context=("finances", "social_standing"),
    ),
    "empathy": _rule(
        center_benefits={
            "household_stability": 0.18,
            "marriage_paramour": 0.14,
            "social_standing": 0.10,
            "care_burden": 0.08,
        },
        deficient_pressures={
            "violence": 0.32,
            "legal_fallout": 0.20,
            "reputation": 0.20,
            "household_stability": 0.16,
            "care_burden": 0.16,
        },
        excess_pressures={
            "care_burden": 0.18,
            "mortality_health": 0.08,
            "work_capacity": 0.08,
            "reputation": 0.06,
        },
        context=("care_burden",),
    ),
    "discipline": _rule(
        center_benefits={
            "work_capacity": 0.24,
            "finances": 0.14,
            "legal_fallout": 0.08,
        },
        deficient_pressures={
            "work_capacity": 0.24,
            "legal_fallout": 0.10,
            "household_stability": 0.12,
        },
        excess_pressures={
            "mortality_health": 0.12,
            "work_capacity": 0.12,
            "household_stability": 0.14,
            "care_burden": 0.08,
        },
        excess_benefits={"work_capacity": 0.04},
        context=("work_capacity",),
    ),
    "adaptability": _rule(
        center_benefits={"work_capacity": 0.16, "household_stability": 0.12},
        deficient_pressures={
            "work_capacity": 0.16,
            "household_stability": 0.14,
            "social_standing": 0.08,
        },
        excess_pressures={"work_capacity": 0.16, "household_stability": 0.12},
        context=("work_capacity", "household_stability"),
    ),
    "resilience": _rule(
        center_benefits={"mortality_health": 0.28, "work_capacity": 0.16},
        deficient_pressures={
            "mortality_health": 0.32,
            "work_capacity": 0.20,
            "care_burden": 0.18,
        },
        excess_pressures={"social_standing": 0.08, "violence": 0.08, "reputation": 0.06},
        excess_benefits={"mortality_health": 0.06},
        context=("mortality_health", "violence"),
    ),
    "focus": _rule(
        center_benefits={"work_capacity": 0.24, "finances": 0.08},
        deficient_pressures={"work_capacity": 0.28, "finances": 0.08},
        excess_pressures={
            "work_capacity": 0.14,
            "household_stability": 0.14,
            "reputation": 0.10,
            "legal_fallout": 0.06,
        },
        excess_benefits={"work_capacity": 0.05},
        context=("work_capacity",),
    ),
    "honesty": _rule(
        center_benefits={"legal_fallout": 0.18, "reputation": 0.20},
        deficient_pressures={
            "finances": 0.12,
            "legal_fallout": 0.28,
            "reputation": 0.24,
            "social_standing": 0.12,
        },
        excess_pressures={
            "social_standing": 0.14,
            "reputation": 0.12,
            "work_capacity": 0.06,
        },
        context=("reputation", "legal_fallout"),
    ),
    "creativity": _rule(
        center_benefits={"work_capacity": 0.16, "social_standing": 0.08},
        deficient_pressures={"work_capacity": 0.16, "social_standing": 0.06},
        excess_pressures={
            "mortality_health": 0.18,
            "violence": 0.12,
            "reputation": 0.18,
            "legal_fallout": 0.10,
            "care_burden": 0.12,
        },
        excess_benefits={"work_capacity": 0.05},
        context=("work_capacity", "reputation"),
    ),
    "assertiveness": _rule(
        center_benefits={"work_capacity": 0.16, "social_standing": 0.12},
        deficient_pressures={"work_capacity": 0.16, "social_standing": 0.12},
        excess_pressures={
            "violence": 0.20,
            "legal_fallout": 0.14,
            "household_stability": 0.18,
            "reputation": 0.14,
        },
        excess_benefits={"social_standing": 0.04},
        context=("social_standing", "violence"),
    ),
    "loyalty": _rule(
        center_benefits={
            "household_stability": 0.16,
            "reputation": 0.14,
            "marriage_paramour": 0.14,
        },
        deficient_pressures={
            "household_stability": 0.20,
            "marriage_paramour": 0.16,
            "legal_fallout": 0.12,
            "reputation": 0.18,
        },
        excess_pressures={"social_standing": 0.10, "legal_fallout": 0.06},
        excess_benefits={"social_standing": 0.05},
        context=("social_standing", "legal_fallout"),
    ),
    "nurturance": _rule(
        center_benefits={
            "care_burden": 0.22,
            "household_stability": 0.18,
            "marriage_paramour": 0.10,
        },
        deficient_pressures={
            "care_burden": 0.30,
            "household_stability": 0.22,
            "reputation": 0.12,
            "marriage_paramour": 0.08,
        },
        excess_pressures={
            "care_burden": 0.16,
            "household_stability": 0.14,
            "social_standing": 0.06,
        },
        context=("care_burden", "household_stability"),
    ),
    "perception": _rule(
        center_benefits={
            "work_capacity": 0.20,
            "mortality_health": 0.08,
            "legal_fallout": 0.08,
        },
        deficient_pressures={
            "mortality_health": 0.14,
            "work_capacity": 0.18,
            "reputation": 0.08,
        },
        excess_pressures={
            "mortality_health": 0.16,
            "violence": 0.18,
            "household_stability": 0.18,
            "legal_fallout": 0.12,
            "reputation": 0.14,
        },
        excess_benefits={"work_capacity": 0.04},
        context=("work_capacity", "violence"),
    ),
    "civics": _rule(
        center_benefits={
            "legal_fallout": 0.16,
            "social_standing": 0.12,
            "reputation": 0.10,
        },
        deficient_pressures={
            "legal_fallout": 0.18,
            "violence": 0.16,
            "reputation": 0.14,
            "social_standing": 0.08,
        },
        excess_pressures={
            "legal_fallout": 0.18,
            "violence": 0.14,
            "social_standing": 0.10,
            "reputation": 0.10,
        },
        context=("legal_fallout", "social_standing"),
    ),
}


def _impact_kinds(rule: TraitImpactRule | None) -> frozenset[ImpactKind]:
    if rule is None:
        return frozenset()
    out: set[ImpactKind] = set()
    if rule.center_benefits:
        out.add("beneficial_center")
    if rule.center_pressures:
        out.add("harmful_center")
    if rule.deficient_extreme_pressures or rule.excess_extreme_pressures:
        out.add("harmful_extreme")
    if rule.deficient_extreme_benefits or rule.excess_extreme_benefits:
        out.add("useful_extreme")
    if rule.context_dependent_categories:
        out.add("context_dependent_extreme")
    return frozenset(out)


def _impact_categories(rule: TraitImpactRule | None) -> frozenset[ImpactCategory]:
    if rule is None:
        return frozenset()
    out: set[ImpactCategory] = set(rule.context_dependent_categories)
    for weights in (
        rule.center_benefits,
        rule.center_pressures,
        rule.deficient_extreme_pressures,
        rule.deficient_extreme_benefits,
        rule.excess_extreme_pressures,
        rule.excess_extreme_benefits,
    ):
        out.update(category for category, _weight in weights)
    return frozenset(out)


def trait_band_for_magnitude(magnitude: float) -> TraitBand:
    mag = abs(float(magnitude))
    if mag <= CENTER_MAX_MAGNITUDE:
        return "center"
    if mag <= MILD_MAX_MAGNITUDE:
        return "mild_deviation"
    if mag <= ORDINARY_MAX_MAGNITUDE:
        return "ordinary"
    if mag < EXTREME_MIN_MAGNITUDE:
        return "strong_deviation"
    return "extreme_deviation"


def trait_pole(value: float) -> TraitPole:
    v = float(value)
    if v < 0.0:
        return "deficient"
    if v > 0.0:
        return "excess"
    return "center"


def center_signal_01(value: float) -> float:
    mag = abs(float(value))
    if mag <= CENTER_SIGNAL_FULL_MAGNITUDE:
        return 1.0
    if mag >= CENTER_SIGNAL_ZERO_MAGNITUDE:
        return 0.0
    return _smoothstep((CENTER_SIGNAL_ZERO_MAGNITUDE - mag) / CENTER_SIGNAL_ZERO_MAGNITUDE)


def strong_deviation_signal_01(value: float) -> float:
    mag = abs(float(value))
    span = DEVIATION_SIGNAL_FULL_MAGNITUDE - DEVIATION_SIGNAL_START_MAGNITUDE
    if span <= 0.0:
        return 0.0
    return _smoothstep((mag - DEVIATION_SIGNAL_START_MAGNITUDE) / span)


def extreme_signal_01(value: float) -> float:
    mag = abs(float(value))
    span = EXTREME_SIGNAL_FULL_MAGNITUDE - EXTREME_SIGNAL_START_MAGNITUDE
    if span <= 0.0:
        return 0.0
    return _smoothstep((mag - EXTREME_SIGNAL_START_MAGNITUDE) / span)


def ordinary_signal_01(value: float) -> float:
    mag = abs(float(value))
    if mag < MILD_MAX_MAGNITUDE or mag > ORDINARY_MAX_MAGNITUDE:
        return 0.0
    midpoint = (MILD_MAX_MAGNITUDE + ORDINARY_MAX_MAGNITUDE) / 2.0
    radius = ORDINARY_MAX_MAGNITUDE - midpoint
    return clamp01(1.0 - abs(mag - midpoint) / radius)


def directional_deviation_signal_01(value: float, pole: TraitPole) -> float:
    if pole == "deficient" and float(value) >= 0.0:
        return 0.0
    if pole == "excess" and float(value) <= 0.0:
        return 0.0
    if pole == "center":
        return center_signal_01(value)
    return strong_deviation_signal_01(value)


def directional_extreme_signal_01(value: float, pole: TraitPole) -> float:
    if pole == "deficient" and float(value) >= 0.0:
        return 0.0
    if pole == "excess" and float(value) <= 0.0:
        return 0.0
    if pole == "center":
        return center_signal_01(value)
    return extreme_signal_01(value)


def classify_trait(
    trait: str,
    value: float,
    *,
    definition: TraitDefinition | None = None,
    rule: TraitImpactRule | None = None,
) -> TraitClassification:
    v = float(value)
    mag = abs(v)
    resolved_rule = TRAIT_IMPACT_RULES.get(trait) if rule is None else rule
    return TraitClassification(
        trait=str(trait),
        value=v,
        magnitude=mag,
        pole=trait_pole(v),
        band=trait_band_for_magnitude(mag),
        center_signal_01=center_signal_01(v),
        strong_deviation_signal_01=strong_deviation_signal_01(v),
        extreme_signal_01=extreme_signal_01(v),
        ordinary_signal_01=ordinary_signal_01(v),
        definition=definition,
        impact_kinds=_impact_kinds(resolved_rule),
        impact_categories=_impact_categories(resolved_rule),
    )


def trait_values(subject: Any) -> Mapping[str, float]:
    if isinstance(subject, Mapping):
        raw = subject.get("genome", subject)
    else:
        person = getattr(subject, "person", subject)
        raw = getattr(person, "genome", None)
    return raw if isinstance(raw, Mapping) else {}


def classify_traits(
    subject_or_traits: Any,
    *,
    definitions: Mapping[str, TraitDefinition] | None = None,
) -> dict[str, TraitClassification]:
    definitions = definitions or {}
    return {
        str(trait): classify_trait(
            str(trait),
            float(value),
            definition=definitions.get(str(trait)),
        )
        for trait, value in trait_values(subject_or_traits).items()
        if _coerce_float(value) is not None
    }


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _add_category(out: dict[ImpactCategory, float], category: ImpactCategory, value: float) -> None:
    out[category] = clamp01(out.get(category, 0.0) + float(value))


def _apply_weights(
    out: dict[ImpactCategory, float],
    weights: CategoryWeights,
    signal: float,
) -> None:
    if signal <= 0.0:
        return
    for category, weight in weights:
        _add_category(out, category, float(weight) * float(signal))


def build_trait_impact_profile(
    subject_or_traits: Any,
    *,
    definitions: Mapping[str, TraitDefinition] | None = None,
) -> TraitImpactProfile:
    classifications = classify_traits(subject_or_traits, definitions=definitions)
    benefits: dict[ImpactCategory, float] = {}
    pressures: dict[ImpactCategory, float] = {}
    for trait, classification in classifications.items():
        rule = TRAIT_IMPACT_RULES.get(trait)
        if rule is None:
            continue
        center = classification.center_signal_01
        _apply_weights(benefits, rule.center_benefits, center)
        _apply_weights(pressures, rule.center_pressures, center)
        if classification.pole == "deficient":
            signal = classification.strong_deviation_signal_01
            _apply_weights(pressures, rule.deficient_extreme_pressures, signal)
            _apply_weights(benefits, rule.deficient_extreme_benefits, signal)
        elif classification.pole == "excess":
            signal = classification.strong_deviation_signal_01
            _apply_weights(pressures, rule.excess_extreme_pressures, signal)
            _apply_weights(benefits, rule.excess_extreme_benefits, signal)
    return TraitImpactProfile(
        benefits=benefits,
        pressures=pressures,
        classifications=classifications,
    )


def trait_category_pressure(subject_or_traits: Any, category: ImpactCategory) -> float:
    return build_trait_impact_profile(subject_or_traits).pressure(category)


def trait_category_benefit(subject_or_traits: Any, category: ImpactCategory) -> float:
    return build_trait_impact_profile(subject_or_traits).benefit(category)


def load_trait_definitions_from_sqlite(db_path: str | Path) -> dict[str, TraitDefinition]:
    path = Path(db_path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
              trait,
              "deficient deviation" AS deficient_deviation,
              "optimal centerpoint" AS optimal_centerpoint,
              "excess deviation" AS excess_deviation,
              "deficient description" AS deficient_description,
              "optimal description" AS optimal_description,
              "excess description" AS excess_description
            FROM genome
            """
        ).fetchall()
    finally:
        conn.close()
    return {
        str(row["trait"]): TraitDefinition(
            trait=str(row["trait"]),
            deficient_deviation=str(row["deficient_deviation"] or ""),
            optimal_centerpoint=str(row["optimal_centerpoint"] or ""),
            excess_deviation=str(row["excess_deviation"] or ""),
            deficient_description=str(row["deficient_description"] or ""),
            optimal_description=str(row["optimal_description"] or ""),
            excess_description=str(row["excess_description"] or ""),
        )
        for row in rows
        if row["trait"]
    }


def load_trait_definitions_from_csv(csv_path: str | Path) -> dict[str, TraitDefinition]:
    path = Path(csv_path)
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return {
        str(row["trait"]): TraitDefinition(
            trait=str(row["trait"]),
            deficient_deviation=str(row.get("deficient deviation") or ""),
            optimal_centerpoint=str(row.get("optimal centerpoint") or ""),
            excess_deviation=str(row.get("excess deviation") or ""),
            deficient_description=str(row.get("deficient description") or ""),
            optimal_description=str(row.get("optimal description") or ""),
            excess_description=str(row.get("excess description") or ""),
        )
        for row in rows
        if row.get("trait")
    }


def traits_missing_impact_rules(
    definitions: Mapping[str, TraitDefinition],
) -> tuple[str, ...]:
    return tuple(sorted(set(definitions) - set(TRAIT_IMPACT_RULES)))
