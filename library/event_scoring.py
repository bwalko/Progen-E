"""Reusable genome/context scoring helpers for event generation."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from library.trait_impacts import (
    center_signal_01 as _trait_center_signal_01,
    directional_deviation_signal_01 as _trait_directional_deviation_signal_01,
    trait_category_pressure,
    trait_values as _trait_values,
)


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
    composite_score_risk_weights: Mapping[str, float] = field(default_factory=dict)
    composite_score_protective_weights: Mapping[str, float] = field(default_factory=dict)
    composite_score_additive_weights: Mapping[str, float] = field(default_factory=dict)
    composite_score_inhibitor_weights: Mapping[str, float] = field(default_factory=dict)
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

    return _trait_values(subject)


def trait_value(subject: Any, trait: str) -> float:
    raw = _trait_value_if_present(subject, trait)
    return 0.0 if raw is None else raw


def _trait_value_if_present(subject: Any, trait: str) -> float | None:
    try:
        traits = genome_traits(subject)
        if trait not in traits:
            return None
        return float(traits.get(trait, 0.0))
    except (TypeError, ValueError):
        return None


def negative_extreme(subject: Any, trait: str) -> float:
    value = _trait_value_if_present(subject, trait)
    if value is None:
        return 0.0
    return _trait_directional_deviation_signal_01(value, "deficient")


def positive_extreme(subject: Any, trait: str) -> float:
    value = _trait_value_if_present(subject, trait)
    if value is None:
        return 0.0
    return _trait_directional_deviation_signal_01(value, "excess")


def ideal_strength(subject: Any, trait: str) -> float:
    value = _trait_value_if_present(subject, trait)
    if value is None:
        return 0.0
    return _trait_center_signal_01(value)


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


def composite_score(subject: Any, rating_id: str, default: float = 0.0) -> float:
    """Return a numeric composite score from a record, person, or score map."""

    key = str(rating_id).strip()
    if not key:
        return float(default)
    source = subject
    if isinstance(source, Mapping):
        raw_scores = source
    else:
        person = getattr(source, "person", source)
        raw_scores = getattr(person, "genome_composite_scores", None)
    if not isinstance(raw_scores, Mapping):
        return float(default)
    raw = raw_scores.get(key)
    if raw is None:
        raw = raw_scores.get(key.lower())
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float(default)
    if value != value:
        return float(default)
    return max(0.0, value)


def score_composite_scores(subject: Any, weights: Mapping[str, float]) -> float:
    if not weights:
        return 0.0
    return sum(
        composite_score(subject, rating_id) * float(weight)
        for rating_id, weight in weights.items()
    )


_MORAL_FRICTION_RELIEF_WEIGHTS: Mapping[str, float] = {
    "survival_need": 0.28,
    "scarcity": 0.18,
    "debt": 0.20,
    "street_precarity": 0.22,
    "relationship_strain": 0.18,
    "status_fall": 0.14,
    "war": 0.20,
    "succession_crisis": 0.16,
    "faction_tension": 0.14,
    "office_tension": 0.12,
    "bereavement": 0.12,
}
_MORAL_FRICTION_OPPORTUNITY_RELIEF_WEIGHTS: Mapping[str, float] = {
    "battlefield": 0.18,
    "isolated": 0.08,
    "street": 0.08,
    "begging": 0.08,
    "guard_gap": 0.08,
}


def moral_friction_relief(context: EventScoringContext | None) -> float:
    """How much pressure makes normally protective morals easier to override."""

    if context is None:
        return 0.0
    relief = score_tag_weights(context.pressure_tags, _MORAL_FRICTION_RELIEF_WEIGHTS)
    relief += score_tag_weights(
        context.opportunity_tags, _MORAL_FRICTION_OPPORTUNITY_RELIEF_WEIGHTS
    )
    relief += clamp01(float(context.resource_pressure) - 0.90, 0.0, 1.0) * 0.18
    relief += clamp01(float(context.crowding) - 0.65, 0.0, 1.0) * 0.08
    return clamp01(relief, 0.0, 0.75)


def _adjusted_composite_protection(
    subject: Any,
    weights: Mapping[str, float],
    *,
    context: EventScoringContext | None,
) -> float:
    if not weights:
        return 0.0
    protection = score_composite_scores(subject, weights)
    moral_weight = 0.0
    for rating_id, weight in weights.items():
        if str(rating_id).strip().lower() in {
            "good_done_desire",
            "honest_work_desire",
        }:
            moral_weight += composite_score(subject, rating_id) * float(weight)
    if moral_weight <= 0.0:
        return protection
    return max(0.0, protection - moral_weight * moral_friction_relief(context))


def _subject_person_id(subject: Any) -> int | None:
    raw = getattr(subject, "person_id", None)
    if raw is None:
        person = getattr(subject, "person", subject)
        raw = getattr(person, "person_id", None)
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _id_set(values: Iterable[int] | None) -> frozenset[int]:
    out: set[int] = set()
    for value in values or ():
        try:
            out.add(int(value))
        except (TypeError, ValueError):
            continue
    return frozenset(out)


def infer_role_tags(
    subject: Any,
    *,
    year: int | None = None,
    care_indexes: Any | None = None,
    office_holder_ids: Iterable[int] | None = None,
    ruler_ids: Iterable[int] | None = None,
    parent_ids: Iterable[int] | None = None,
) -> frozenset[str]:
    """Infer coarse role tags without requiring a full simulation context."""

    person = getattr(subject, "person", subject)
    person_id = _subject_person_id(subject)
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
    housing_status = str(getattr(person, "housing_status", "") or "").strip().lower()
    if housing_status == "street":
        tags.add("street_precarious")
    elif housing_status in {"kin_board", "charity_board"}:
        tags.add("boarded_adult")
    market_type = str(getattr(person, "job_market_type", "") or "").strip().lower()
    if market_type:
        tags.add(f"job_market_{market_type}")
    if person_id is not None:
        if person_id in _id_set(parent_ids):
            tags.add("parent")
        children_by_parent = getattr(care_indexes, "children_by_parent", None)
        if children_by_parent is not None and children_by_parent.get(person_id):
            tags.add("parent")
        if person_id in _id_set(office_holder_ids):
            tags.add("title_holder")
        if person_id in _id_set(ruler_ids):
            tags.add("ruler")
            tags.add("title_holder")
        purseholder = getattr(person, "household_purseholder_person_id", None)
        try:
            if purseholder is not None and int(purseholder) == person_id:
                tags.add("household_head")
        except (TypeError, ValueError):
            pass
    current_settlement = str(getattr(person, "current_settlement_id", "") or "").strip()
    birth_settlement = str(getattr(person, "birthplace_settlement_id", "") or "").strip()
    current_region = str(getattr(person, "current_region_id", "") or "").strip()
    birth_region = str(getattr(person, "birthplace_region_id", "") or "").strip()
    if (current_settlement and birth_settlement and current_settlement != birth_settlement) or (
        current_region and birth_region and current_region != birth_region
    ):
        tags.add("migrant")
    job = str(getattr(person, "job", "") or "").strip().lower()
    if job:
        if any(token in job for token in ("ruler", "chief", "king", "queen", "duke")):
            tags.add("ruler")
            tags.add("title_holder")
        if any(
            token in job
            for token in (
                "count",
                "mayor",
                "judge",
                "officer",
                "noble",
                "guild master",
                "settlement leader",
            )
        ):
            tags.add("title_holder")
        if "heir" in job:
            tags.add("heir")
            tags.add("title_holder")
        if "household head" in job:
            tags.add("household_head")
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
        + score_composite_scores(subject, spec.composite_score_risk_weights)
        + score_tag_weights(ctx.role_tags, spec.role_weights)
        + score_tag_weights(ctx.pressure_tags, spec.pressure_weights)
        + score_tag_weights(ctx.opportunity_tags, spec.opportunity_weights)
        + float(extra_risk)
    )
    protection = score_trait_factors(
        subject, spec.protective_factors
    ) + _adjusted_composite_protection(
        subject, spec.composite_score_protective_weights, context=ctx
    )
    additive = (
        score_trait_factors(subject, spec.additive_factors)
        + score_composite_scores(subject, spec.composite_score_additive_weights)
        + float(extra_additive)
    )
    inhibitors = (
        score_trait_factors(subject, spec.inhibitors)
        + score_composite_scores(subject, spec.composite_score_inhibitor_weights)
        + float(extra_inhibition)
    )
    if (
        spec.additive_factors
        or spec.inhibitors
        or spec.composite_score_additive_weights
        or spec.composite_score_inhibitor_weights
    ):
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


def contextual_propensity_by_person_id(
    records: Iterable[Any],
    score_fn: Callable[..., float],
    context_by_person_id: Mapping[int, EventScoringContext],
) -> dict[int, float]:
    out: dict[int, float] = {}
    for rec in records:
        pid = getattr(rec, "person_id", None)
        if pid is None:
            continue
        ipid = int(pid)
        out[ipid] = clamp01(score_fn(rec, context=context_by_person_id.get(ipid)))
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
    composite_weights={
        "short fuse": 0.04,
        "slow fuse": 0.03,
        "patient grudge": 0.04,
        "grudge nurturer": 0.04,
        "possessive lover": 0.05,
        "paranoid tester": 0.04,
        "destructive spark": 0.04,
    },
    composite_score_risk_weights={
        "psychopathy": 0.18,
        "force_get_way_desire": 0.12,
        "revenge_desire": 0.11,
        "evil_done_desire": 0.10,
        "insanity": 0.08,
        "physical_strength": 0.04,
    },
    composite_score_protective_weights={
        "good_done_desire": 0.18,
        "honest_work_desire": 0.07,
        "make_friends": 0.04,
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
        "street_precarious": 0.09,
        "job_market_vice": 0.04,
        "job_market_criminal": 0.08,
        "trader": 0.02,
    },
    pressure_weights={
        "scarcity": 0.06,
        "debt": 0.07,
        "street_precarity": 0.10,
        "survival_need": 0.08,
        "crowding": 0.03,
        "status_fall": 0.04,
    },
    opportunity_weights={
        "market_day": 0.04,
        "storehouse_access": 0.05,
        "shared_household": 0.03,
        "street": 0.07,
        "begging": 0.04,
    },
    composite_weights={
        "rank hunger": 0.04,
        "scorekeeper": 0.04,
        "bottomless want": 0.05,
        "pinched miser": 0.04,
        "opportunistic chameleon": 0.04,
        "sweet withholder": 0.03,
        "hardened heart": 0.03,
    },
    composite_score_risk_weights={
        "lie_or_cheat_willingness": 0.16,
        "enrich_self_desire": 0.13,
        "disguise_motive": 0.08,
        "ruthless_ambition": 0.08,
        "psychopathy": 0.06,
        "evil_done_desire": 0.05,
    },
    composite_score_protective_weights={
        "good_done_desire": 0.16,
        "honest_work_desire": 0.16,
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
    composite_weights={
        "possessive lover": 0.05,
        "restless lover": 0.04,
        "consuming caregiver": 0.04,
        "paranoid tester": 0.04,
        "dramatic leaver": 0.03,
        "bottomless want": 0.03,
    },
    composite_score_risk_weights={
        "sexual_magnetism": 0.12,
        "sexual_object": 0.05,
        "lie_or_cheat_willingness": 0.06,
        "insanity": 0.04,
    },
    composite_score_protective_weights={
        "honest_work_desire": 0.06,
        "good_done_desire": 0.04,
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
    composite_score_risk_weights={
        "good_done_desire": 0.18,
        "honest_work_desire": 0.08,
        "make_friends": 0.06,
        "lead_others_ability": 0.04,
    },
    composite_score_inhibitor_weights={
        "psychopathy": 0.14,
        "evil_done_desire": 0.12,
        "ruthless_ambition": 0.05,
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
    composite_score_risk_weights={
        "creative_intellect": 0.18,
        "practical_intellect": 0.10,
        "lead_others_ability": 0.03,
    },
    composite_score_inhibitor_weights={
        "insanity": 0.04,
    },
)


POLITICAL_CRIME_SPEC = EventPropensitySpec(
    key="political_crime",
    risk_factors=(
        TraitFactor("ambition", "positive_extreme", 0.16),
        TraitFactor("loyalty", "negative_extreme", 0.14),
        TraitFactor("justice", "negative_extreme", 0.10),
        TraitFactor("honesty", "negative_extreme", 0.09),
        TraitFactor("persuasion", "positive_extreme", 0.11),
        TraitFactor("discipline", "positive_extreme", 0.07),
        TraitFactor("courage", "positive_extreme", 0.06),
        TraitFactor("civics", "negative_extreme", 0.05),
    ),
    protective_factors=(
        TraitFactor("loyalty", "ideal_strength", 0.14),
        TraitFactor("justice", "ideal_strength", 0.10),
        TraitFactor("honesty", "ideal_strength", 0.08),
        TraitFactor("empathy", "ideal_strength", 0.06),
        TraitFactor("civics", "ideal_strength", 0.06),
    ),
    composite_weights={
        "tyrant": 0.05,
        "criminal mastermind": 0.06,
        "demagogue": 0.05,
        "hidden manipulator": 0.05,
        "legitimacy seizer": 0.06,
        "self-anointed": 0.05,
        "procedural predator": 0.05,
    },
    composite_score_risk_weights={
        "ruthless_ambition": 0.15,
        "disguise_motive": 0.12,
        "lie_or_cheat_willingness": 0.10,
        "lead_others_ability": 0.08,
        "evil_done_desire": 0.06,
    },
    composite_score_protective_weights={
        "good_done_desire": 0.10,
        "honest_work_desire": 0.08,
    },
    role_weights={
        "ruler": 0.03,
        "title_holder": 0.05,
        "heir": 0.06,
        "soldier": 0.02,
        "trader": 0.01,
    },
    pressure_weights={
        "succession_crisis": 0.08,
        "office_tension": 0.07,
        "faction_tension": 0.05,
        "status_fall": 0.05,
        "war": 0.04,
        "debt": 0.03,
    },
    opportunity_weights={
        "court": 0.05,
        "office_access": 0.06,
        "faction_network": 0.05,
        "document_access": 0.04,
        "guard_gap": 0.03,
    },
)

RELIGIOUS_CULTURAL_CONFLICT_SPEC = EventPropensitySpec(
    key="religious_cultural_conflict",
    risk_factors=(
        TraitFactor("justice", "positive_extreme", 0.12),
        TraitFactor("loyalty", "positive_extreme", 0.09),
        TraitFactor("civics", "positive_extreme", 0.08),
        TraitFactor("empathy", "negative_extreme", 0.11),
        TraitFactor("persuasion", "positive_extreme", 0.11),
        TraitFactor("creativity", "positive_extreme", 0.08),
        TraitFactor("discipline", "positive_extreme", 0.06),
        TraitFactor("courage", "positive_extreme", 0.05),
        TraitFactor("adaptability", "negative_extreme", 0.05),
    ),
    protective_factors=(
        TraitFactor("empathy", "ideal_strength", 0.14),
        TraitFactor("temperance", "ideal_strength", 0.10),
        TraitFactor("adaptability", "ideal_strength", 0.08),
        TraitFactor("honesty", "ideal_strength", 0.06),
    ),
    composite_weights={
        "fanatic": 0.07,
        "fanatical loyalist": 0.06,
        "cult leader": 0.07,
        "false revelator": 0.06,
        "pious aggressor": 0.06,
        "moral scold": 0.04,
        "mystic": 0.03,
    },
    role_weights={
        "priest": 0.06,
        "ruler": 0.03,
        "title_holder": 0.02,
        "elder": 0.02,
    },
    pressure_weights={
        "doctrine_tension": 0.07,
        "social_stress": 0.05,
        "disaster": 0.05,
        "war": 0.04,
        "scarcity": 0.03,
    },
    opportunity_weights={
        "temple": 0.05,
        "ritual_site": 0.05,
        "crowd": 0.04,
        "court": 0.03,
        "scribe_network": 0.03,
    },
)

PRIVATE_LIFE_SEED_SPEC = EventPropensitySpec(
    key="private_life_seed",
    risk_factors=(
        TraitFactor("patience", "negative_extreme", 0.08),
        TraitFactor("temperance", "negative_extreme", 0.06),
        TraitFactor("justice", "positive_extreme", 0.06),
        TraitFactor("empathy", "negative_extreme", 0.06),
        TraitFactor("loyalty", "negative_extreme", 0.05),
        TraitFactor("honesty", "negative_extreme", 0.06),
        TraitFactor("persuasion", "positive_extreme", 0.06),
        TraitFactor("perception", "positive_extreme", 0.05),
        TraitFactor("ambition", "positive_extreme", 0.05),
        TraitFactor("neurochemical", "positive_extreme", 0.05),
        TraitFactor("empathy", "ideal_strength", 0.04),
        TraitFactor("nurturance", "ideal_strength", 0.04),
        TraitFactor("humility", "positive_extreme", 0.04),
        TraitFactor("resilience", "positive_extreme", 0.03),
    ),
    protective_cap=0.35,
    composite_weights={
        "jealous rival": 0.05,
        "hidden manipulator": 0.04,
        "truth-teller": 0.03,
        "good neighbor": 0.03,
        "hermit": 0.04,
        "mystic": 0.03,
        "gentle mentor": 0.03,
    },
    composite_score_risk_weights={
        "disguise_motive": 0.10,
        "lie_or_cheat_willingness": 0.10,
        "revenge_desire": 0.08,
        "isolation_preference": 0.04,
        "insanity": 0.04,
    },
    role_weights={
        "spouse": 0.03,
        "parent": 0.03,
        "household_head": 0.03,
        "migrant": 0.03,
        "elder": 0.02,
        "artisan": 0.02,
    },
    pressure_weights={
        "relationship_strain": 0.06,
        "bereavement": 0.05,
        "scarcity": 0.04,
        "status_fall": 0.04,
        "debt": 0.03,
    },
    opportunity_weights={
        "shared_household": 0.05,
        "privacy": 0.04,
        "mentor_access": 0.03,
        "secret_access": 0.04,
        "travel_route": 0.03,
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


def instability_crime_pressure(subject: Any) -> float:
    """Extreme unstable, delusional, or paranoid traits that can spill into crime."""

    directed = clamp01(
        positive_extreme(subject, "neurochemical") * 0.55
        + positive_extreme(subject, "creativity") * 0.20
        + positive_extreme(subject, "intellect") * 0.15
        + positive_extreme(subject, "perception") * 0.10
    )
    practical = trait_category_pressure(subject, "violence")
    health = trait_category_pressure(subject, "mortality_health")
    return clamp01(directed * 0.78 + practical * 0.18 + health * 0.04)


def greed_crime_pressure(subject: Any) -> float:
    """Ruthless, miserly, and envious extremes most relevant to property crime."""

    directed = clamp01(
        positive_extreme(subject, "ambition") * 0.35
        + positive_extreme(subject, "frugality") * 0.30
        + negative_extreme(subject, "generosity") * 0.35
    )
    practical = trait_category_pressure(subject, "finances")
    legal = trait_category_pressure(subject, "legal_fallout")
    return clamp01(directed * 0.78 + practical * 0.17 + legal * 0.05)


def jealousy_crime_pressure(
    subject: Any, *, context: EventScoringContext | None = None
) -> float:
    """Possessive/envious extremes, strongest under relationship opportunity."""

    directed = clamp01(
        negative_extreme(subject, "generosity") * 0.24
        + positive_extreme(subject, "loyalty") * 0.22
        + positive_extreme(subject, "mating drive") * 0.22
        + positive_extreme(subject, "perception") * 0.20
        + positive_extreme(subject, "neurochemical") * 0.12
    )
    base = clamp01(
        directed * 0.82
        + trait_category_pressure(subject, "marriage_paramour") * 0.10
        + trait_category_pressure(subject, "household_stability") * 0.08
    )
    if base <= 0.0:
        return 0.0
    if context is None:
        return base * 0.25
    pressure_tags = {str(tag).strip().lower() for tag in context.pressure_tags}
    opportunity_tags = {str(tag).strip().lower() for tag in context.opportunity_tags}
    context_factor = 0.25
    if "relationship_strain" in pressure_tags:
        context_factor += 0.45
    if opportunity_tags.intersection({"co_residence", "shared_household", "privacy"}):
        context_factor += 0.25
    return base * clamp01(context_factor)


def serial_killer_composite_pressure(subject: Any) -> float:
    """Serial-predator signal that needs several aligned composite drives."""

    coldness = max(
        composite_score(subject, "psychopathy"),
        composite_score(subject, "evil_done_desire") * 0.80,
    )
    domination = max(
        composite_score(subject, "force_get_way_desire"),
        composite_score(subject, "revenge_desire") * 0.80,
    )
    concealment = max(
        composite_score(subject, "disguise_motive"),
        composite_score(subject, "lie_or_cheat_willingness") * 0.70,
    )
    separation = max(
        composite_score(subject, "isolation_preference"),
        composite_score(subject, "insanity") * 0.80,
    )
    parts = (coldness, domination, concealment, separation)
    if max(parts) <= 0.0:
        return 0.0
    product = 1.0
    aligned = 0
    for value in parts:
        v = max(0.02, float(value))
        product *= v
        if v >= 0.45:
            aligned += 1
    geometric = product ** (1.0 / len(parts))
    return clamp01(geometric * (0.40 + 0.15 * aligned))


def violent_actor_propensity(
    subject: Any, *, context: EventScoringContext | None = None
) -> float:
    extra_risk = (
        instability_crime_pressure(subject) * 0.14
        + jealousy_crime_pressure(subject, context=context) * 0.24
        + greed_crime_pressure(subject) * 0.03
    )
    return score_propensity(
        subject, VIOLENT_ACTOR_SPEC, context=context, extra_risk=extra_risk
    )


def serial_predator_propensity(
    subject: Any,
    *,
    context: EventScoringContext | None = None,
    previous_murders: int = 0,
) -> float:
    """Very rare repeat-predator signal inside the already-calibrated murder rate.

    This is not a separate event-volume knob. It only identifies detailed people
    whose extreme profile should make predatory or repeated killing more likely
    when a murder event has already passed the era/population rate gates.
    """

    coldness = clamp01(
        negative_extreme(subject, "empathy") * 0.48
        + negative_extreme(subject, "justice") * 0.34
        + negative_extreme(subject, "honesty") * 0.18
    )
    control = clamp01(
        positive_extreme(subject, "perception") * 0.24
        + positive_extreme(subject, "assertiveness") * 0.24
        + ideal_strength(subject, "patience") * 0.18
        + positive_extreme(subject, "discipline") * 0.14
        + positive_extreme(subject, "persuasion") * 0.12
        + positive_extreme(subject, "neurochemical") * 0.08
    )
    instability = clamp01(
        positive_extreme(subject, "neurochemical") * 0.55
        + negative_extreme(subject, "temperance") * 0.30
        + positive_extreme(subject, "mating drive") * 0.15
    )
    prior = clamp01(float(previous_murders) / 3.0)
    role = 0.0
    if context is not None:
        role += score_tag_weights(context.pressure_tags, {"scarcity": 0.02, "war": 0.02})
        role += score_tag_weights(context.opportunity_tags, {"isolated": 0.04, "privacy": 0.03})
    serial_profile = serial_killer_composite_pressure(subject)
    moral_brake = (
        composite_score(subject, "good_done_desire") * 0.10
        + composite_score(subject, "honest_work_desire") * 0.06
    )
    return clamp01(
        coldness * 0.42
        + control * 0.24
        + instability * 0.14
        + serial_profile * 0.35
        + violent_actor_propensity(subject, context=context) * 0.10
        + prior * 0.10
        + role
        - ideal_strength(subject, "empathy") * 0.10
        - ideal_strength(subject, "justice") * 0.08
        - moral_brake
    )


def property_crime_propensity(
    subject: Any, *, context: EventScoringContext | None = None
) -> float:
    return score_propensity(
        subject,
        PROPERTY_CRIME_SPEC,
        context=context,
        extra_risk=(
            property_crime_skill_factor(subject)
            + greed_crime_pressure(subject) * 0.37
            + instability_crime_pressure(subject) * 0.04
        ),
    )


def scandal_exposure_propensity(
    subject: Any, *, context: EventScoringContext | None = None
) -> float:
    extra_risk = (
        jealousy_crime_pressure(subject, context=context) * 0.12
        + instability_crime_pressure(subject) * 0.04
    )
    return score_propensity(
        subject, SCANDAL_EXPOSURE_SPEC, context=context, extra_risk=extra_risk
    )


def public_virtue_propensity(
    subject: Any, *, context: EventScoringContext | None = None
) -> float:
    return score_propensity(subject, PUBLIC_VIRTUE_SPEC, context=context)


def knowledge_culture_propensity(
    subject: Any, *, context: EventScoringContext | None = None
) -> float:
    return score_propensity(subject, KNOWLEDGE_CULTURE_SPEC, context=context)


def political_crime_propensity(
    subject: Any, *, context: EventScoringContext | None = None
) -> float:
    return score_propensity(subject, POLITICAL_CRIME_SPEC, context=context)


def religious_cultural_conflict_propensity(
    subject: Any, *, context: EventScoringContext | None = None
) -> float:
    return score_propensity(
        subject, RELIGIOUS_CULTURAL_CONFLICT_SPEC, context=context
    )


def private_life_seed_propensity(
    subject: Any, *, context: EventScoringContext | None = None
) -> float:
    return score_propensity(subject, PRIVATE_LIFE_SEED_SPEC, context=context)
