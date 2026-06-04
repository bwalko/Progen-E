"""Reusable genome/context scoring helpers for event generation."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal


TraitMode = Literal["negative_extreme", "positive_extreme", "ideal_strength"]


@dataclass(frozen=True)
class TraitFactor:
    trait: str
    mode: TraitMode
    weight: float


@dataclass(frozen=True)
class EventPropensitySpec:
    """Weighted trait/composite/role scoring recipe for one event tendency."""

    key: str
    risk_factors: tuple[TraitFactor, ...] = ()
    protective_factors: tuple[TraitFactor, ...] = ()
    protective_cap: float = 0.55
    additive_factors: tuple[TraitFactor, ...] = ()
    inhibitors: tuple[TraitFactor, ...] = ()
    inhibitor_cap: float = 0.65
    composite_weights: Mapping[str, float] = field(default_factory=dict)
    role_weights: Mapping[str, float] = field(default_factory=dict)
    pressure_weights: Mapping[str, float] = field(default_factory=dict)
    opportunity_weights: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class EventScoringContext:
    """Optional role/pressure/opportunity facts supplied by an event system."""

    role_tags: frozenset[str] = frozenset()
    pressure_tags: frozenset[str] = frozenset()
    opportunity_tags: frozenset[str] = frozenset()
    resource_pressure: float = 0.0
    crowding: float = 0.0
    prosperity: float | None = None
    witness_count: int = 0


def clamp01(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def genome_traits(subject: Any) -> Mapping[str, float]:
    """Return a best-effort genome trait mapping from a record, person, or dict."""

    if isinstance(subject, Mapping):
        raw = subject.get("genome", subject)
    else:
        person = getattr(subject, "person", subject)
        raw = getattr(person, "genome", None)
    return raw if isinstance(raw, Mapping) else {}


def trait_value(subject: Any, trait: str) -> float:
    try:
        return float(genome_traits(subject).get(trait, 0.0))
    except (TypeError, ValueError):
        return 0.0


def negative_extreme(subject: Any, trait: str) -> float:
    return clamp01((-trait_value(subject, trait) - 35.0) / 65.0)


def positive_extreme(subject: Any, trait: str) -> float:
    return clamp01((trait_value(subject, trait) - 35.0) / 65.0)


def ideal_strength(subject: Any, trait: str) -> float:
    return clamp01(1.0 - abs(trait_value(subject, trait)) / 55.0)


def score_trait_factor(subject: Any, factor: TraitFactor) -> float:
    if factor.mode == "negative_extreme":
        basis = negative_extreme(subject, factor.trait)
    elif factor.mode == "positive_extreme":
        basis = positive_extreme(subject, factor.trait)
    elif factor.mode == "ideal_strength":
        basis = ideal_strength(subject, factor.trait)
    else:
        basis = 0.0
    return float(factor.weight) * basis


def score_trait_factors(subject: Any, factors: Iterable[TraitFactor]) -> float:
    return sum(score_trait_factor(subject, factor) for factor in factors)


def composite_name_set(subject: Any) -> frozenset[str]:
    person = getattr(subject, "person", subject)
    raw = getattr(person, "genome_composite_names", ())
    if isinstance(raw, str):
        values = (raw,)
    else:
        try:
            values = tuple(raw or ())
        except TypeError:
            values = ()
    return frozenset(str(value).strip().lower() for value in values if str(value).strip())


def score_named_composites(subject: Any, weights: Mapping[str, float]) -> float:
    if not weights:
        return 0.0
    names = composite_name_set(subject)
    return sum(
        float(weight)
        for name, weight in weights.items()
        if str(name).strip().lower() in names
    )


def infer_role_tags(subject: Any, *, year: int | None = None) -> frozenset[str]:
    """Infer coarse role tags without requiring a full simulation context."""

    person = getattr(subject, "person", subject)
    tags: set[str] = set()
    birthyear = getattr(person, "birthyear", None)
    if year is not None and birthyear is not None:
        try:
            age = int(year) - int(birthyear)
        except (TypeError, ValueError):
            age = None
        if age is not None:
            if age < 16:
                tags.add("child")
            if age >= 60:
                tags.add("elder")
    if str(getattr(person, "partner_person_id", "") or "").strip():
        tags.add("spouse")
    if getattr(person, "unemployment_started_year", None) is not None:
        tags.add("unemployed")
    job = str(getattr(person, "job", "") or "").strip().lower()
    if job:
        if any(token in job for token in ("ruler", "chief", "king", "queen", "duke")):
            tags.add("ruler")
        if any(token in job for token in ("priest", "shaman", "druid", "oracle")):
            tags.add("priest")
        if any(token in job for token in ("guard", "soldier", "warrior", "militia")):
            tags.add("soldier")
        if any(token in job for token in ("merchant", "trader", "sailor", "ship")):
            tags.add("trader")
        if any(token in job for token in ("smith", "weaver", "potter", "carpenter")):
            tags.add("artisan")
    return frozenset(tags)


def score_tag_weights(tags: Iterable[str], weights: Mapping[str, float]) -> float:
    tag_set = {str(tag).strip().lower() for tag in tags if str(tag).strip()}
    return sum(
        float(weight)
        for tag, weight in weights.items()
        if str(tag).strip().lower() in tag_set
    )


def pressure_excess(value: float, baseline: float, spread: float) -> float:
    if spread <= 0:
        return 0.0
    return clamp01((float(value) - float(baseline)) / float(spread))


def score_propensity(
    subject: Any,
    spec: EventPropensitySpec,
    *,
    context: EventScoringContext | None = None,
    extra_risk: float = 0.0,
    extra_additive: float = 0.0,
    extra_inhibition: float = 0.0,
) -> float:
    """Score an event tendency from small additive factors and protections."""

    ctx = context or EventScoringContext()
    risk = (
        score_trait_factors(subject, spec.risk_factors)
        + score_named_composites(subject, spec.composite_weights)
        + score_tag_weights(ctx.role_tags, spec.role_weights)
        + score_tag_weights(ctx.pressure_tags, spec.pressure_weights)
        + score_tag_weights(ctx.opportunity_tags, spec.opportunity_weights)
        + float(extra_risk)
    )
    protection = score_trait_factors(subject, spec.protective_factors)
    additive = score_trait_factors(subject, spec.additive_factors) + float(extra_additive)
    inhibitors = score_trait_factors(subject, spec.inhibitors) + float(extra_inhibition)
    if spec.additive_factors or spec.inhibitors:
        return clamp01((risk + additive) * (1.0 - min(float(spec.inhibitor_cap), inhibitors)))
    return clamp01(risk * (1.0 - min(float(spec.protective_cap), protection)))


def propensity_by_person_id(
    records: Iterable[Any], score_fn: Callable[[Any], float]
) -> dict[int, float]:
    out: dict[int, float] = {}
    for rec in records:
        pid = getattr(rec, "person_id", None)
        if pid is None:
            continue
        out[int(pid)] = clamp01(score_fn(rec))
    return out


def eligible_records_by_threshold(
    records: Iterable[Any],
    scores: Mapping[int, float],
    threshold: float,
) -> list[Any]:
    thr = float(threshold)
    return [
        rec
        for rec in records
        if scores.get(int(getattr(rec, "person_id", 0)), 0.0) >= thr
    ]


def threshold_excess_weights(
    records: Sequence[Any],
    scores: Mapping[int, float],
    threshold: float,
    *,
    exponent: float = 2.0,
    floor: float = 0.001,
) -> list[float]:
    thr = float(threshold)
    return [
        max(
            float(floor),
            (scores.get(int(getattr(rec, "person_id", 0)), 0.0) - thr)
            ** float(exponent),
        )
        for rec in records
    ]


def threshold_excess_value_weights(
    values: Iterable[float],
    threshold: float,
    *,
    exponent: float = 2.0,
    floor: float = 0.001,
) -> list[float]:
    thr = float(threshold)
    return [max(float(floor), (float(value) - thr) ** float(exponent)) for value in values]


VIOLENT_ACTOR_SPEC = EventPropensitySpec(
    key="violent_actor",
    risk_factors=(
        TraitFactor("justice", "negative_extreme", 0.25),
        TraitFactor("empathy", "negative_extreme", 0.20),
        TraitFactor("patience", "negative_extreme", 0.12),
        TraitFactor("temperance", "negative_extreme", 0.08),
        TraitFactor("courage", "positive_extreme", 0.10),
        TraitFactor("assertiveness", "positive_extreme", 0.11),
        TraitFactor("neurochemical", "positive_extreme", 0.09),
        TraitFactor("ambition", "positive_extreme", 0.05),
    ),
    protective_factors=(
        TraitFactor("justice", "ideal_strength", 0.16),
        TraitFactor("empathy", "ideal_strength", 0.12),
        TraitFactor("temperance", "ideal_strength", 0.10),
        TraitFactor("patience", "ideal_strength", 0.08),
    ),
    role_weights={
        "soldier": 0.03,
        "ruler": 0.02,
    },
    pressure_weights={
        "scarcity": 0.04,
        "relationship_strain": 0.06,
        "status_fall": 0.04,
        "succession_crisis": 0.04,
        "war": 0.05,
    },
    opportunity_weights={
        "co_residence": 0.03,
        "isolated": 0.04,
        "battlefield": 0.05,
    },
)

PROPERTY_CRIME_SPEC = EventPropensitySpec(
    key="property_crime",
    risk_factors=(
        TraitFactor("justice", "negative_extreme", 0.24),
        TraitFactor("honesty", "negative_extreme", 0.23),
        TraitFactor("empathy", "negative_extreme", 0.10),
        TraitFactor("persuasion", "positive_extreme", 0.10),
        TraitFactor("ambition", "positive_extreme", 0.08),
        TraitFactor("frugality", "positive_extreme", 0.07),
        TraitFactor("frugality", "negative_extreme", 0.05),
    ),
    protective_factors=(
        TraitFactor("justice", "ideal_strength", 0.16),
        TraitFactor("honesty", "ideal_strength", 0.14),
        TraitFactor("empathy", "ideal_strength", 0.08),
        TraitFactor("discipline", "ideal_strength", 0.06),
    ),
    role_weights={
        "unemployed": 0.04,
        "trader": 0.02,
    },
    pressure_weights={
        "scarcity": 0.06,
        "debt": 0.07,
        "crowding": 0.03,
        "status_fall": 0.04,
    },
    opportunity_weights={
        "market_day": 0.04,
        "storehouse_access": 0.05,
        "shared_household": 0.03,
    },
)

SCANDAL_EXPOSURE_SPEC = EventPropensitySpec(
    key="scandal_exposure",
    risk_factors=(
        TraitFactor("mating drive", "positive_extreme", 0.24),
        TraitFactor("loyalty", "negative_extreme", 0.20),
        TraitFactor("modesty", "negative_extreme", 0.16),
        TraitFactor("neurochemical", "positive_extreme", 0.10),
        TraitFactor("assertiveness", "positive_extreme", 0.07),
        TraitFactor("persuasion", "positive_extreme", 0.05),
        TraitFactor("discipline", "negative_extreme", 0.05),
        TraitFactor("honesty", "negative_extreme", 0.05),
        TraitFactor("honesty", "positive_extreme", 0.04),
    ),
    protective_factors=(
        TraitFactor("loyalty", "ideal_strength", 0.16),
        TraitFactor("modesty", "ideal_strength", 0.10),
        TraitFactor("discipline", "ideal_strength", 0.07),
        TraitFactor("temperance", "ideal_strength", 0.05),
    ),
    role_weights={
        "ruler": 0.03,
        "spouse": 0.04,
    },
    pressure_weights={
        "relationship_strain": 0.07,
        "status_pressure": 0.04,
        "succession_crisis": 0.03,
    },
    opportunity_weights={
        "co_residence": 0.04,
        "court": 0.03,
        "public_witness": 0.05,
    },
)

PUBLIC_VIRTUE_SPEC = EventPropensitySpec(
    key="public_virtue",
    risk_factors=(
        TraitFactor("empathy", "ideal_strength", 0.12),
        TraitFactor("justice", "ideal_strength", 0.11),
        TraitFactor("nurturance", "ideal_strength", 0.09),
        TraitFactor("civics", "ideal_strength", 0.08),
        TraitFactor("honesty", "ideal_strength", 0.05),
    ),
    additive_factors=(
        TraitFactor("courage", "positive_extreme", 0.16),
        TraitFactor("assertiveness", "positive_extreme", 0.05),
        TraitFactor("discipline", "ideal_strength", 0.05),
        TraitFactor("resilience", "ideal_strength", 0.05),
        TraitFactor("frugality", "negative_extreme", 0.05),
    ),
    inhibitors=(
        TraitFactor("empathy", "negative_extreme", 0.18),
        TraitFactor("justice", "negative_extreme", 0.15),
        TraitFactor("nurturance", "negative_extreme", 0.10),
        TraitFactor("civics", "negative_extreme", 0.08),
        TraitFactor("honesty", "negative_extreme", 0.05),
    ),
    inhibitor_cap=0.65,
    role_weights={
        "ruler": 0.03,
        "priest": 0.03,
        "soldier": 0.02,
    },
    pressure_weights={
        "scarcity": 0.05,
        "bereavement": 0.03,
        "disaster": 0.06,
        "war": 0.04,
    },
    opportunity_weights={
        "public_crisis": 0.06,
        "shared_household": 0.02,
        "witnessed_need": 0.05,
    },
)

KNOWLEDGE_CULTURE_SPEC = EventPropensitySpec(
    key="knowledge_culture",
    risk_factors=(
        TraitFactor("curiosity", "positive_extreme", 0.17),
        TraitFactor("creativity", "positive_extreme", 0.15),
        TraitFactor("intellect", "positive_extreme", 0.14),
        TraitFactor("focus", "positive_extreme", 0.11),
        TraitFactor("perception", "positive_extreme", 0.09),
        TraitFactor("discipline", "ideal_strength", 0.06),
        TraitFactor("civics", "positive_extreme", 0.05),
        TraitFactor("wit", "positive_extreme", 0.04),
        TraitFactor("adaptability", "ideal_strength", 0.04),
    ),
    inhibitors=(
        TraitFactor("curiosity", "negative_extreme", 0.18),
        TraitFactor("creativity", "negative_extreme", 0.15),
        TraitFactor("intellect", "negative_extreme", 0.14),
        TraitFactor("focus", "negative_extreme", 0.10),
        TraitFactor("discipline", "negative_extreme", 0.08),
    ),
    inhibitor_cap=0.60,
    role_weights={
        "artisan": 0.05,
        "priest": 0.03,
        "trader": 0.02,
        "ruler": 0.02,
    },
    pressure_weights={
        "patronage": 0.04,
        "civic_need": 0.04,
        "war": 0.02,
    },
    opportunity_weights={
        "workshop": 0.05,
        "archive": 0.04,
        "court": 0.03,
        "market_day": 0.02,
    },
)


def property_crime_skill_factor(subject: Any) -> float:
    predatory_intent = clamp01(
        negative_extreme(subject, "justice")
        + negative_extreme(subject, "honesty")
        + positive_extreme(subject, "persuasion") * 0.6
        + positive_extreme(subject, "ambition") * 0.5
    )
    return (
        ideal_strength(subject, "perception") * 0.09
        + ideal_strength(subject, "adaptability") * 0.04
    ) * predatory_intent


def violent_actor_propensity(
    subject: Any, *, context: EventScoringContext | None = None
) -> float:
    return score_propensity(subject, VIOLENT_ACTOR_SPEC, context=context)


def property_crime_propensity(
    subject: Any, *, context: EventScoringContext | None = None
) -> float:
    return score_propensity(
        subject,
        PROPERTY_CRIME_SPEC,
        context=context,
        extra_risk=property_crime_skill_factor(subject),
    )


def scandal_exposure_propensity(
    subject: Any, *, context: EventScoringContext | None = None
) -> float:
    return score_propensity(subject, SCANDAL_EXPOSURE_SPEC, context=context)


def public_virtue_propensity(
    subject: Any, *, context: EventScoringContext | None = None
) -> float:
    return score_propensity(subject, PUBLIC_VIRTUE_SPEC, context=context)


def knowledge_culture_propensity(
    subject: Any, *, context: EventScoringContext | None = None
) -> float:
    return score_propensity(subject, KNOWLEDGE_CULTURE_SPEC, context=context)
