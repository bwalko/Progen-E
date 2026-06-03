"""Genome-driven personal incidents with historical event consequences."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from library import simulation_timing

if TYPE_CHECKING:
    from library.simulation_context import (
        SimulationContext,
        SimulationPersonRecord,
    )


INCIDENT_ADULT_MIN_AGE = 16
MURDER_BASE_SETTLEMENT_CHANCE = 0.0032
MURDER_SETTLEMENT_CHANCE_CAP = 0.02
MURDER_PROPENSITY_THRESHOLD = 0.30
MURDER_SETTLEMENT_SAMPLE_CAP = 250
MURDER_MAX_EVENTS_PER_YEAR = 2
MURDER_RNG_STREAM = 610_019
MURDER_SAMPLE_STREAM = 610_021
THEFT_BASE_SETTLEMENT_CHANCE = 0.0075
THEFT_SETTLEMENT_CHANCE_CAP = 0.04
THEFT_PROPENSITY_THRESHOLD = 0.24
THEFT_SETTLEMENT_SAMPLE_CAP = 300
THEFT_MAX_EVENTS_PER_YEAR = 4
THEFT_RNG_STREAM = 610_033
THEFT_SAMPLE_STREAM = 610_039
SCANDAL_BASE_SETTLEMENT_CHANCE = 0.0032
SCANDAL_SETTLEMENT_CHANCE_CAP = 0.035
SCANDAL_PROPENSITY_THRESHOLD = 0.22
SCANDAL_SETTLEMENT_SAMPLE_CAP = 220
SCANDAL_MAX_EVENTS_PER_YEAR = 3
SCANDAL_RNG_STREAM = 610_051
SCANDAL_SAMPLE_STREAM = 610_057
VIRTUE_BASE_SETTLEMENT_CHANCE = 0.0120
VIRTUE_SETTLEMENT_CHANCE_CAP = 0.03
VIRTUE_PROPENSITY_THRESHOLD = 0.20
VIRTUE_SETTLEMENT_SAMPLE_CAP = 240
VIRTUE_MAX_EVENTS_PER_YEAR = 3
VIRTUE_RNG_STREAM = 610_067
VIRTUE_SAMPLE_STREAM = 610_069
KNOWLEDGE_BASE_SETTLEMENT_CHANCE = 0.0060
KNOWLEDGE_SETTLEMENT_CHANCE_CAP = 0.025
KNOWLEDGE_PROPENSITY_THRESHOLD = 0.24
KNOWLEDGE_SETTLEMENT_SAMPLE_CAP = 240
KNOWLEDGE_MAX_EVENTS_PER_YEAR = 2
KNOWLEDGE_RNG_STREAM = 610_079
KNOWLEDGE_SAMPLE_STREAM = 610_081


@dataclass(frozen=True)
class MurderIncident:
    killer: "SimulationPersonRecord"
    victim: "SimulationPersonRecord"
    incident_kind: str
    motive: str
    witness_person_ids: tuple[int, ...]
    settlement_id: str
    region_id: str
    actor_propensity: float
    resource_pressure: float
    historical_importance: float
    genome_signals: dict[str, float]


@dataclass(frozen=True)
class TheftFraudIncident:
    perpetrator: "SimulationPersonRecord"
    target: "SimulationPersonRecord"
    incident_kind: str
    motive: str
    witness_person_ids: tuple[int, ...]
    settlement_id: str
    region_id: str
    actor_propensity: float
    resource_pressure: float
    historical_importance: float
    loss_value: float
    genome_signals: dict[str, float]


@dataclass(frozen=True)
class AffairScandalIncident:
    accused: "SimulationPersonRecord"
    paramour: "SimulationPersonRecord"
    betrayed_partner_ids: tuple[int, ...]
    incident_kind: str
    motive: str
    witness_person_ids: tuple[int, ...]
    settlement_id: str
    region_id: str
    exposure_propensity: float
    pair_exposure_score: float
    resource_pressure: float
    historical_importance: float
    genome_signals: dict[str, float]


@dataclass(frozen=True)
class PublicVirtueIncident:
    benefactor: "SimulationPersonRecord"
    beneficiary: "SimulationPersonRecord"
    incident_kind: str
    motive: str
    witness_person_ids: tuple[int, ...]
    settlement_id: str
    region_id: str
    actor_propensity: float
    resource_pressure: float
    historical_importance: float
    relief_value: float
    genome_signals: dict[str, float]


@dataclass(frozen=True)
class KnowledgeCultureIncident:
    creator: "SimulationPersonRecord"
    patron: "SimulationPersonRecord | None"
    incident_kind: str
    knowledge_domain: str
    motive: str
    witness_person_ids: tuple[int, ...]
    settlement_id: str
    region_id: str
    actor_propensity: float
    resource_pressure: float
    historical_importance: float
    novelty_value: float
    genome_signals: dict[str, float]


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _trait(rec: "SimulationPersonRecord", trait: str) -> float:
    try:
        return float((rec.person.genome or {}).get(trait, 0.0))
    except (TypeError, ValueError):
        return 0.0


def _negative_extreme(rec: "SimulationPersonRecord", trait: str) -> float:
    return _clamp((-_trait(rec, trait) - 35.0) / 65.0)


def _positive_extreme(rec: "SimulationPersonRecord", trait: str) -> float:
    return _clamp((_trait(rec, trait) - 35.0) / 65.0)


def _ideal_strength(rec: "SimulationPersonRecord", trait: str) -> float:
    return _clamp(1.0 - abs(_trait(rec, trait)) / 55.0)


def violent_actor_propensity(rec: "SimulationPersonRecord") -> float:
    """Return a 0..1 tendency for severe personal violence under pressure."""
    risk = (
        _negative_extreme(rec, "justice") * 0.25
        + _negative_extreme(rec, "empathy") * 0.20
        + _negative_extreme(rec, "patience") * 0.12
        + _negative_extreme(rec, "temperance") * 0.08
        + _positive_extreme(rec, "courage") * 0.10
        + _positive_extreme(rec, "assertiveness") * 0.11
        + _positive_extreme(rec, "neurochemical") * 0.09
        + _positive_extreme(rec, "ambition") * 0.05
    )
    protection = (
        _ideal_strength(rec, "justice") * 0.16
        + _ideal_strength(rec, "empathy") * 0.12
        + _ideal_strength(rec, "temperance") * 0.10
        + _ideal_strength(rec, "patience") * 0.08
    )
    return _clamp(risk * (1.0 - min(0.55, protection)))


def property_crime_propensity(rec: "SimulationPersonRecord") -> float:
    """Return a 0..1 tendency for theft, fraud, or extortion under pressure."""
    predatory_intent = _clamp(
        _negative_extreme(rec, "justice")
        + _negative_extreme(rec, "honesty")
        + _positive_extreme(rec, "persuasion") * 0.6
        + _positive_extreme(rec, "ambition") * 0.5
    )
    skill_factor = (
        _ideal_strength(rec, "perception") * 0.09
        + _ideal_strength(rec, "adaptability") * 0.04
    ) * predatory_intent
    risk = (
        _negative_extreme(rec, "justice") * 0.24
        + _negative_extreme(rec, "honesty") * 0.23
        + _negative_extreme(rec, "empathy") * 0.10
        + _positive_extreme(rec, "persuasion") * 0.10
        + _positive_extreme(rec, "ambition") * 0.08
        + _positive_extreme(rec, "frugality") * 0.07
        + _negative_extreme(rec, "frugality") * 0.05
        + skill_factor
    )
    protection = (
        _ideal_strength(rec, "justice") * 0.16
        + _ideal_strength(rec, "honesty") * 0.14
        + _ideal_strength(rec, "empathy") * 0.08
        + _ideal_strength(rec, "discipline") * 0.06
    )
    return _clamp(risk * (1.0 - min(0.55, protection)))


def scandal_exposure_propensity(rec: "SimulationPersonRecord") -> float:
    """Return a 0..1 tendency for a secret relationship to become exposed."""
    risk = (
        _positive_extreme(rec, "mating drive") * 0.24
        + _negative_extreme(rec, "loyalty") * 0.20
        + _negative_extreme(rec, "modesty") * 0.16
        + _positive_extreme(rec, "neurochemical") * 0.10
        + _positive_extreme(rec, "assertiveness") * 0.07
        + _positive_extreme(rec, "persuasion") * 0.05
        + _negative_extreme(rec, "discipline") * 0.05
        + _negative_extreme(rec, "honesty") * 0.05
        + _positive_extreme(rec, "honesty") * 0.04
    )
    protection = (
        _ideal_strength(rec, "loyalty") * 0.16
        + _ideal_strength(rec, "modesty") * 0.10
        + _ideal_strength(rec, "discipline") * 0.07
        + _ideal_strength(rec, "temperance") * 0.05
    )
    return _clamp(risk * (1.0 - min(0.55, protection)))


def public_virtue_propensity(rec: "SimulationPersonRecord") -> float:
    """Return a 0..1 tendency for costly public virtue under pressure."""
    prosocial = (
        _ideal_strength(rec, "empathy") * 0.12
        + _ideal_strength(rec, "justice") * 0.11
        + _ideal_strength(rec, "nurturance") * 0.09
        + _ideal_strength(rec, "civics") * 0.08
        + _ideal_strength(rec, "honesty") * 0.05
    )
    action = (
        _positive_extreme(rec, "courage") * 0.16
        + _positive_extreme(rec, "assertiveness") * 0.05
        + _ideal_strength(rec, "discipline") * 0.05
        + _ideal_strength(rec, "resilience") * 0.05
    )
    generosity = _negative_extreme(rec, "frugality") * 0.05
    inhibitors = (
        _negative_extreme(rec, "empathy") * 0.18
        + _negative_extreme(rec, "justice") * 0.15
        + _negative_extreme(rec, "nurturance") * 0.10
        + _negative_extreme(rec, "civics") * 0.08
        + _negative_extreme(rec, "honesty") * 0.05
    )
    return _clamp((prosocial + action + generosity) * (1.0 - min(0.65, inhibitors)))


def knowledge_culture_propensity(rec: "SimulationPersonRecord") -> float:
    """Return a 0..1 tendency for invention, discovery, or cultural breakthrough."""
    drive = (
        _positive_extreme(rec, "curiosity") * 0.17
        + _positive_extreme(rec, "creativity") * 0.15
        + _positive_extreme(rec, "intellect") * 0.14
        + _positive_extreme(rec, "focus") * 0.11
        + _positive_extreme(rec, "perception") * 0.09
        + _ideal_strength(rec, "discipline") * 0.06
        + _positive_extreme(rec, "civics") * 0.05
        + _positive_extreme(rec, "wit") * 0.04
        + _ideal_strength(rec, "adaptability") * 0.04
    )
    inhibitors = (
        _negative_extreme(rec, "curiosity") * 0.18
        + _negative_extreme(rec, "creativity") * 0.15
        + _negative_extreme(rec, "intellect") * 0.14
        + _negative_extreme(rec, "focus") * 0.10
        + _negative_extreme(rec, "discipline") * 0.08
    )
    return _clamp(drive * (1.0 - min(0.60, inhibitors)))


def _residence_settlement_id(rec: "SimulationPersonRecord") -> str:
    return str(
        rec.person.current_settlement_id or rec.person.birthplace_settlement_id or ""
    ).strip()


def _residence_region_id(ctx: "SimulationContext", rec: "SimulationPersonRecord") -> str:
    sid = _residence_settlement_id(rec)
    st = ctx.settlements_by_id.get(sid)
    if st is not None and str(st.region_id or "").strip():
        return str(st.region_id).strip()
    return str(rec.person.birthplace_region_id or "").strip()


def _adult_alive(rec: "SimulationPersonRecord", year: int) -> bool:
    if rec.person.deathyear is not None and int(rec.person.deathyear) <= int(year):
        return False
    return int(year) - int(rec.person.birthyear) >= INCIDENT_ADULT_MIN_AGE


def _weighted_choice(
    items: list["SimulationPersonRecord"], weights: list[float], rng: random.Random
) -> "SimulationPersonRecord | None":
    if not items:
        return None
    total = sum(max(0.0, float(w)) for w in weights)
    if total <= 0.0:
        return None
    return rng.choices(items, weights=weights, k=1)[0]


def _relationship_motive(
    killer: "SimulationPersonRecord", victim: "SimulationPersonRecord"
) -> tuple[str, float]:
    kp = killer.person
    vp = victim.person
    if (
        kp.partner_person_id == victim.person_id
        or vp.partner_person_id == killer.person_id
    ):
        return "partner_conflict", 4.0
    if (
        kp.paramour_person_id == victim.person_id
        or vp.paramour_person_id == killer.person_id
    ):
        return "paramour_conflict", 3.0
    if killer.father_id == victim.person_id or killer.mother_id == victim.person_id:
        return "kin_conflict", 2.2
    if victim.father_id == killer.person_id or victim.mother_id == killer.person_id:
        return "kin_conflict", 2.2
    if kp.job and vp.job and str(kp.job).strip() == str(vp.job).strip():
        return "work_rivalry", 1.35
    return "settlement_grievance", 1.0


def _incident_kind(killer: "SimulationPersonRecord", motive: str) -> str:
    if motive in {"partner_conflict", "paramour_conflict"}:
        return "domestic_murder"
    if (
        _positive_extreme(killer, "justice") >= 0.35
        and _positive_extreme(killer, "courage") >= 0.20
    ):
        return "feud_killing"
    if (
        _negative_extreme(killer, "patience")
        + _positive_extreme(killer, "neurochemical")
    ) >= 0.9:
        return "rash_brawl_killing"
    if (
        _negative_extreme(killer, "empathy") >= 0.55
        and _positive_extreme(killer, "assertiveness") >= 0.35
    ):
        return "predatory_murder"
    return "murder"


def _genome_signal_payload(
    rec: "SimulationPersonRecord", traits: tuple[str, ...] | None = None
) -> dict[str, float]:
    chosen_traits = traits or (
        "justice",
        "empathy",
        "patience",
        "temperance",
        "courage",
        "assertiveness",
        "neurochemical",
        "ambition",
    )
    return {
        trait: round(_trait(rec, trait), 3)
        for trait in chosen_traits
        if trait in (rec.person.genome or {})
    }


def _historical_importance(
    killer: "SimulationPersonRecord",
    victim: "SimulationPersonRecord",
    witness_count: int,
) -> float:
    office_or_prominence = 0.0
    for rec in (killer, victim):
        if rec.person.job:
            job = str(rec.person.job).lower()
            if any(
                token in job
                for token in ("king", "duke", "count", "chief", "judge", "mayor")
            ):
                office_or_prominence += 0.18
    return _clamp(0.45 + min(0.25, witness_count * 0.08) + office_or_prominence)


def _property_crime_importance(
    perpetrator: "SimulationPersonRecord",
    target: "SimulationPersonRecord",
    witness_count: int,
    loss_value: float,
) -> float:
    prominence = 0.0
    for rec in (perpetrator, target):
        if rec.person.job:
            job = str(rec.person.job).lower()
            if any(
                token in job
                for token in ("king", "duke", "count", "chief", "judge", "merchant")
            ):
                prominence += 0.12
    return _clamp(0.25 + min(0.22, witness_count * 0.06) + loss_value * 0.8 + prominence)


def _public_virtue_importance(
    benefactor: "SimulationPersonRecord",
    beneficiary: "SimulationPersonRecord",
    witness_count: int,
    relief_value: float,
) -> float:
    prominence = 0.0
    for rec in (benefactor, beneficiary):
        if rec.person.job:
            job = str(rec.person.job).lower()
            if any(
                token in job
                for token in ("king", "duke", "count", "chief", "judge", "mayor")
            ):
                prominence += 0.10
    return _clamp(0.32 + min(0.24, witness_count * 0.07) + relief_value * 0.6 + prominence)


def _knowledge_culture_importance(
    creator: "SimulationPersonRecord",
    patron: "SimulationPersonRecord | None",
    witness_count: int,
    novelty_value: float,
) -> float:
    prominence = 0.0
    for rec in (creator, patron):
        if rec is None or not rec.person.job:
            continue
        job = str(rec.person.job).lower()
        if any(
            token in job
            for token in ("king", "duke", "count", "chief", "judge", "priest", "scribe")
        ):
            prominence += 0.10
    return _clamp(0.34 + min(0.22, witness_count * 0.06) + novelty_value * 0.55 + prominence)


def _settlement_pressure(
    ctx: "SimulationContext", year: int, settlement_id: str
) -> float:
    try:
        facts = ctx.annual_resource_facts(year)
        return float(facts.settlement_food_pressure.get(settlement_id, 0.0))
    except Exception:
        st = ctx.settlements_by_id.get(settlement_id)
        return float(getattr(st, "food_pressure", 0.0) or 0.0) if st else 0.0


def _choose_witnesses(
    residents: list["SimulationPersonRecord"],
    *,
    actor_id: int,
    target_id: int,
    rng: random.Random,
) -> tuple[int, ...]:
    pool = [
        int(rec.person_id)
        for rec in residents
        if rec.person_id not in {actor_id, target_id}
    ]
    if not pool:
        return ()
    rng.shuffle(pool)
    witness_count = 1 if len(pool) == 1 or rng.random() < 0.65 else 2
    return tuple(sorted(pool[: min(witness_count, len(pool))]))


def _choose_witnesses_excluding(
    residents: list["SimulationPersonRecord"],
    *,
    excluded_person_ids: set[int],
    rng: random.Random,
) -> tuple[int, ...]:
    pool = [
        int(rec.person_id)
        for rec in residents
        if int(rec.person_id) not in excluded_person_ids
    ]
    if not pool:
        return ()
    rng.shuffle(pool)
    witness_count = 1 if len(pool) == 1 or rng.random() < 0.65 else 2
    return tuple(sorted(pool[: min(witness_count, len(pool))]))


def _maybe_murder_in_settlement(
    ctx: "SimulationContext",
    year: int,
    settlement_id: str,
    residents: list["SimulationPersonRecord"],
    *,
    rng: random.Random,
    already_dead: set[int],
) -> MurderIncident | None:
    sampled = ctx.decision_sample_records(
        residents,
        year=year,
        scope=f"settlement:{settlement_id}:murder",
        stream=MURDER_SAMPLE_STREAM,
        cap=MURDER_SETTLEMENT_SAMPLE_CAP,
    )
    adults = [
        rec
        for rec in sampled
        if rec.person_id not in already_dead and _adult_alive(rec, year)
    ]
    if len(adults) < 2:
        return None
    pressure = _settlement_pressure(ctx, year, settlement_id)
    scarcity = _clamp((pressure - 0.75) / 0.75)
    propensities = {rec.person_id: violent_actor_propensity(rec) for rec in adults}
    max_propensity = max(propensities.values(), default=0.0)
    population_factor = _clamp((len(adults) - 1) / 80.0, 0.05, 1.0)
    chance = min(
        MURDER_SETTLEMENT_CHANCE_CAP,
        MURDER_BASE_SETTLEMENT_CHANCE
        * (0.35 + population_factor)
        * (1.0 + scarcity * 3.0)
        * (0.35 + max_propensity * 2.5),
    )
    if rng.random() >= chance:
        return None
    candidate_killers = [
        rec
        for rec in adults
        if propensities[rec.person_id] >= MURDER_PROPENSITY_THRESHOLD
    ]
    if not candidate_killers:
        return None
    killer = _weighted_choice(
        candidate_killers,
        [
            max(0.001, (propensities[rec.person_id] - MURDER_PROPENSITY_THRESHOLD) ** 2)
            for rec in candidate_killers
        ],
        rng,
    )
    if killer is None:
        return None
    victim_pool = [rec for rec in adults if rec.person_id != killer.person_id]
    victim_weights: list[float] = []
    motives: list[str] = []
    for victim in victim_pool:
        motive, rel_weight = _relationship_motive(killer, victim)
        motives.append(motive)
        vulnerability = 1.0 + _negative_extreme(victim, "physical") * 0.35
        victim_weights.append(rel_weight * vulnerability)
    victim = _weighted_choice(victim_pool, victim_weights, rng)
    if victim is None:
        return None
    motive = motives[victim_pool.index(victim)]
    witness_ids = _choose_witnesses(
        adults,
        actor_id=int(killer.person_id),
        target_id=int(victim.person_id),
        rng=rng,
    )
    region_id = _residence_region_id(ctx, killer)
    return MurderIncident(
        killer=killer,
        victim=victim,
        incident_kind=_incident_kind(killer, motive),
        motive=motive,
        witness_person_ids=witness_ids,
        settlement_id=settlement_id,
        region_id=region_id,
        actor_propensity=propensities[killer.person_id],
        resource_pressure=pressure,
        historical_importance=_historical_importance(
            killer, victim, len(witness_ids)
        ),
        genome_signals=_genome_signal_payload(killer),
    )


def _property_crime_motive(
    perpetrator: "SimulationPersonRecord", pressure: float
) -> str:
    if pressure >= 1.25:
        return "scarcity"
    if perpetrator.person.unemployment_started_year is not None:
        return "debt_or_hardship"
    if _positive_extreme(perpetrator, "ambition") >= 0.45:
        return "status_gain"
    if _positive_extreme(perpetrator, "frugality") >= 0.45:
        return "hoarding"
    return "opportunity"


def _property_crime_kind(perpetrator: "SimulationPersonRecord", motive: str) -> str:
    if _positive_extreme(perpetrator, "persuasion") >= 0.45 and _negative_extreme(
        perpetrator, "honesty"
    ) >= 0.35:
        return "fraud"
    if _positive_extreme(perpetrator, "assertiveness") >= 0.45:
        return "extortion"
    if motive == "hoarding":
        return "hoarding_theft"
    return "theft"


def _property_crime_loss(
    perpetrator: "SimulationPersonRecord",
    target: "SimulationPersonRecord",
    rng: random.Random,
) -> float:
    actor_pressure = max(
        0.0,
        _negative_extreme(perpetrator, "frugality")
        + _positive_extreme(perpetrator, "ambition") * 0.5,
    )
    target_prosperity = float(target.person.job_prosperity_01 or 0.35)
    return round(_clamp(0.02 + rng.random() * 0.08 + actor_pressure * 0.06 + target_prosperity * 0.05), 4)


def _beneficiary_need_score(rec: "SimulationPersonRecord", pressure: float) -> float:
    hardship = 0.8 + max(0.0, float(pressure)) * 0.35
    if rec.person.unemployment_started_year is not None:
        hardship += 0.55
    hardship += _negative_extreme(rec, "physical") * 0.45
    hardship += max(0.0, 0.45 - float(rec.person.job_prosperity_01 or 0.35)) * 0.8
    hardship += _negative_extreme(rec, "resilience") * 0.20
    return max(0.01, hardship)


def _public_virtue_kind(
    benefactor: "SimulationPersonRecord",
    beneficiary: "SimulationPersonRecord",
    pressure: float,
) -> str:
    if _positive_extreme(benefactor, "courage") >= 0.45 or _negative_extreme(
        beneficiary, "physical"
    ) >= 0.45:
        return "heroic_rescue"
    if pressure >= 1.0 or beneficiary.person.unemployment_started_year is not None:
        return "public_mercy"
    if _ideal_strength(benefactor, "justice") >= 0.7 and _ideal_strength(
        benefactor, "civics"
    ) >= 0.55:
        return "public_arbitration"
    if _ideal_strength(benefactor, "loyalty") >= 0.65:
        return "loyal_service"
    return "public_mercy"


def _public_virtue_motive(benefactor: "SimulationPersonRecord", pressure: float) -> str:
    if pressure >= 1.2:
        return "scarcity_relief"
    if _positive_extreme(benefactor, "courage") >= 0.45:
        return "dangerous_rescue"
    if _ideal_strength(benefactor, "justice") >= 0.7:
        return "fairness"
    if _ideal_strength(benefactor, "nurturance") >= 0.7:
        return "compassion"
    return "neighborly_duty"


def _public_virtue_relief_value(
    benefactor: "SimulationPersonRecord",
    beneficiary: "SimulationPersonRecord",
    pressure: float,
    rng: random.Random,
) -> float:
    actor_cost = _negative_extreme(benefactor, "frugality") * 0.05
    need = min(0.12, _beneficiary_need_score(beneficiary, pressure) * 0.035)
    return round(_clamp(0.03 + rng.random() * 0.06 + actor_cost + need), 4)


def _knowledge_culture_kind(creator: "SimulationPersonRecord") -> str:
    if _positive_extreme(creator, "civics") >= 0.45 and _ideal_strength(
        creator, "justice"
    ) >= 0.55:
        return "legal_precedent"
    if _positive_extreme(creator, "creativity") >= 0.55 and _positive_extreme(
        creator, "wit"
    ) >= 0.35:
        return "artistic_triumph"
    if _positive_extreme(creator, "perception") >= 0.50 and _positive_extreme(
        creator, "curiosity"
    ) >= 0.45:
        return "discovery"
    if _positive_extreme(creator, "intellect") >= 0.50 and _positive_extreme(
        creator, "focus"
    ) >= 0.35:
        return "scholarly_breakthrough"
    return "invention"


def _knowledge_domain(kind: str, creator: "SimulationPersonRecord") -> str:
    job = str(creator.person.job or "").strip().lower()
    if kind == "legal_precedent":
        return "law"
    if kind == "artistic_triumph":
        return "performance" if "bard" in job or "singer" in job else "art"
    if kind == "discovery":
        return "medicine" if "healer" in job else "natural_history"
    if kind == "scholarly_breakthrough":
        return "calendar" if _positive_extreme(creator, "focus") >= 0.55 else "scholarship"
    if "smith" in job or "artisan" in job or "craft" in job:
        return "craft"
    return "toolmaking"


def _knowledge_motive(creator: "SimulationPersonRecord", kind: str) -> str:
    if _positive_extreme(creator, "curiosity") >= 0.55:
        return "curiosity"
    if _positive_extreme(creator, "creativity") >= 0.55:
        return "artistic_drive" if kind == "artistic_triumph" else "experimentation"
    if _ideal_strength(creator, "civics") >= 0.65:
        return "public_order"
    return "careful_study"


def _knowledge_novelty_value(
    creator: "SimulationPersonRecord", pressure: float, rng: random.Random
) -> float:
    signal = (
        _positive_extreme(creator, "creativity") * 0.06
        + _positive_extreme(creator, "intellect") * 0.05
        + _positive_extreme(creator, "curiosity") * 0.04
        + _positive_extreme(creator, "focus") * 0.03
    )
    adversity = min(0.05, max(0.0, pressure - 0.8) * 0.03)
    return round(_clamp(0.04 + rng.random() * 0.08 + signal + adversity), 4)


def _paramour_pair_betrayed_partner_ids(
    ctx: "SimulationContext",
    a: "SimulationPersonRecord",
    b: "SimulationPersonRecord",
    year: int,
) -> tuple[int, ...]:
    out: list[int] = []
    seen: set[int] = set()
    for rec, other_id in ((a, int(b.person_id)), (b, int(a.person_id))):
        partner_id = rec.person.partner_person_id
        if partner_id is None:
            continue
        pid = int(partner_id)
        if pid == other_id or pid in seen:
            continue
        partner = ctx.id_to_record.get(pid)
        if partner is None or not _adult_alive(partner, year):
            continue
        seen.add(pid)
        out.append(pid)
    return tuple(out)


def _choose_scandal_accused(
    a: "SimulationPersonRecord",
    b: "SimulationPersonRecord",
    propensities: dict[int, float],
) -> "SimulationPersonRecord":
    a_has_partner = a.person.partner_person_id is not None and int(
        a.person.partner_person_id
    ) != int(b.person_id)
    b_has_partner = b.person.partner_person_id is not None and int(
        b.person.partner_person_id
    ) != int(a.person_id)
    if a_has_partner and not b_has_partner:
        return a
    if b_has_partner and not a_has_partner:
        return b
    if propensities.get(int(b.person_id), 0.0) > propensities.get(
        int(a.person_id), 0.0
    ):
        return b
    return a


def _scandal_motive(
    accused: "SimulationPersonRecord",
    witness_count: int,
    betrayed_partner_count: int,
) -> str:
    if _positive_extreme(accused, "honesty") >= 0.45:
        return "confession"
    if witness_count > 0:
        return "witnessed_meeting"
    if betrayed_partner_count > 1:
        return "double_household_rumor"
    return "household_rumor"


def _scandal_kind(motive: str, betrayed_partner_count: int) -> str:
    if betrayed_partner_count > 1:
        return "double_affair_exposed"
    if motive == "confession":
        return "confessed_affair"
    if motive == "witnessed_meeting":
        return "affair_witnessed"
    return "affair_exposed"


def _scandal_importance(
    accused: "SimulationPersonRecord",
    paramour: "SimulationPersonRecord",
    betrayed_partner_count: int,
    witness_count: int,
) -> float:
    prominence = 0.0
    for rec in (accused, paramour):
        if rec.person.job:
            job = str(rec.person.job).lower()
            if any(
                token in job
                for token in ("king", "duke", "count", "chief", "judge", "mayor")
            ):
                prominence += 0.12
    return _clamp(
        0.28
        + min(0.18, witness_count * 0.06)
        + min(0.14, betrayed_partner_count * 0.07)
        + prominence
    )


def _maybe_affair_scandal_in_settlement(
    ctx: "SimulationContext",
    year: int,
    settlement_id: str,
    residents: list["SimulationPersonRecord"],
    *,
    rng: random.Random,
) -> AffairScandalIncident | None:
    sampled = ctx.decision_sample_records(
        residents,
        year=year,
        scope=f"settlement:{settlement_id}:affair_scandal",
        stream=SCANDAL_SAMPLE_STREAM,
        cap=SCANDAL_SETTLEMENT_SAMPLE_CAP,
    )
    sampled_ids = {int(rec.person_id) for rec in sampled if _adult_alive(rec, year)}
    if len(sampled_ids) < 2:
        return None
    adult_by_id = {
        int(rec.person_id): rec
        for rec in residents
        if int(rec.person_id) in sampled_ids and _adult_alive(rec, year)
    }
    candidate_pairs: list[tuple["SimulationPersonRecord", "SimulationPersonRecord", tuple[int, ...]]] = []
    seen_pairs: set[tuple[int, int]] = set()
    for a_id, b_id in list(ctx.paramours):
        ia, ib = int(a_id), int(b_id)
        if ia not in adult_by_id or ib not in adult_by_id:
            continue
        pair_key = (ia, ib) if ia < ib else (ib, ia)
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)
        betrayed_ids = _paramour_pair_betrayed_partner_ids(
            ctx, adult_by_id[ia], adult_by_id[ib], year
        )
        if not betrayed_ids:
            continue
        candidate_pairs.append((adult_by_id[ia], adult_by_id[ib], betrayed_ids))
    if not candidate_pairs:
        return None
    pressure = _settlement_pressure(ctx, year, settlement_id)
    social_pressure = _clamp((pressure - 0.55) / 1.0)
    propensities = {
        pid: scandal_exposure_propensity(rec) for pid, rec in adult_by_id.items()
    }
    pair_scores: list[float] = []
    for a, b, betrayed_ids in candidate_pairs:
        score = (
            propensities.get(int(a.person_id), 0.0) * 0.46
            + propensities.get(int(b.person_id), 0.0) * 0.46
            + min(0.08, len(betrayed_ids) * 0.04)
        )
        pair_scores.append(_clamp(score))
    max_score = max(pair_scores, default=0.0)
    population_factor = _clamp((len(adult_by_id) - 1) / 80.0, 0.05, 1.0)
    chance = min(
        SCANDAL_SETTLEMENT_CHANCE_CAP,
        SCANDAL_BASE_SETTLEMENT_CHANCE
        * (0.45 + population_factor)
        * (1.0 + social_pressure * 1.5)
        * (0.45 + max_score * 2.4),
    )
    if rng.random() >= chance:
        return None
    eligible = [
        item
        for item, score in zip(candidate_pairs, pair_scores)
        if score >= SCANDAL_PROPENSITY_THRESHOLD
    ]
    eligible_scores = [
        score for score in pair_scores if score >= SCANDAL_PROPENSITY_THRESHOLD
    ]
    if not eligible:
        return None
    chosen = rng.choices(
        eligible,
        weights=[
            max(0.001, (score - SCANDAL_PROPENSITY_THRESHOLD) ** 2)
            for score in eligible_scores
        ],
        k=1,
    )[0]
    a, b, betrayed_ids = chosen
    accused = _choose_scandal_accused(a, b, propensities)
    paramour = b if accused.person_id == a.person_id else a
    excluded = {int(accused.person_id), int(paramour.person_id), *betrayed_ids}
    witness_ids = _choose_witnesses_excluding(
        list(adult_by_id.values()),
        excluded_person_ids=excluded,
        rng=rng,
    )
    motive = _scandal_motive(accused, len(witness_ids), len(betrayed_ids))
    pair_score = pair_scores[candidate_pairs.index(chosen)]
    return AffairScandalIncident(
        accused=accused,
        paramour=paramour,
        betrayed_partner_ids=tuple(sorted(betrayed_ids)),
        incident_kind=_scandal_kind(motive, len(betrayed_ids)),
        motive=motive,
        witness_person_ids=witness_ids,
        settlement_id=settlement_id,
        region_id=_residence_region_id(ctx, accused),
        exposure_propensity=propensities.get(int(accused.person_id), 0.0),
        pair_exposure_score=pair_score,
        resource_pressure=pressure,
        historical_importance=_scandal_importance(
            accused,
            paramour,
            len(betrayed_ids),
            len(witness_ids),
        ),
        genome_signals=_genome_signal_payload(
            accused,
            (
                "mating drive",
                "loyalty",
                "modesty",
                "honesty",
                "neurochemical",
                "assertiveness",
                "persuasion",
                "discipline",
            ),
        ),
    )


def _maybe_public_virtue_in_settlement(
    ctx: "SimulationContext",
    year: int,
    settlement_id: str,
    residents: list["SimulationPersonRecord"],
    *,
    rng: random.Random,
) -> PublicVirtueIncident | None:
    sampled = ctx.decision_sample_records(
        residents,
        year=year,
        scope=f"settlement:{settlement_id}:public_virtue",
        stream=VIRTUE_SAMPLE_STREAM,
        cap=VIRTUE_SETTLEMENT_SAMPLE_CAP,
    )
    adults = [rec for rec in sampled if _adult_alive(rec, year)]
    if len(adults) < 2:
        return None
    pressure = _settlement_pressure(ctx, year, settlement_id)
    hardship = _clamp((pressure - 0.45) / 1.0)
    propensities = {rec.person_id: public_virtue_propensity(rec) for rec in adults}
    max_propensity = max(propensities.values(), default=0.0)
    population_factor = _clamp((len(adults) - 1) / 70.0, 0.05, 1.0)
    chance = min(
        VIRTUE_SETTLEMENT_CHANCE_CAP,
        VIRTUE_BASE_SETTLEMENT_CHANCE
        * (0.40 + population_factor)
        * (1.0 + hardship * 1.7)
        * (0.35 + max_propensity * 2.2),
    )
    if rng.random() >= chance:
        return None
    candidates = [
        rec
        for rec in adults
        if propensities[rec.person_id] >= VIRTUE_PROPENSITY_THRESHOLD
    ]
    if not candidates:
        return None
    benefactor = _weighted_choice(
        candidates,
        [
            max(0.001, (propensities[rec.person_id] - VIRTUE_PROPENSITY_THRESHOLD) ** 2)
            for rec in candidates
        ],
        rng,
    )
    if benefactor is None:
        return None
    beneficiary_pool = [rec for rec in adults if rec.person_id != benefactor.person_id]
    beneficiary_weights = [_beneficiary_need_score(rec, pressure) for rec in beneficiary_pool]
    beneficiary = _weighted_choice(beneficiary_pool, beneficiary_weights, rng)
    if beneficiary is None:
        return None
    witness_ids = _choose_witnesses(
        adults,
        actor_id=int(benefactor.person_id),
        target_id=int(beneficiary.person_id),
        rng=rng,
    )
    relief_value = _public_virtue_relief_value(
        benefactor, beneficiary, pressure, rng
    )
    incident_kind = _public_virtue_kind(benefactor, beneficiary, pressure)
    return PublicVirtueIncident(
        benefactor=benefactor,
        beneficiary=beneficiary,
        incident_kind=incident_kind,
        motive=_public_virtue_motive(benefactor, pressure),
        witness_person_ids=witness_ids,
        settlement_id=settlement_id,
        region_id=_residence_region_id(ctx, benefactor),
        actor_propensity=propensities[benefactor.person_id],
        resource_pressure=pressure,
        historical_importance=_public_virtue_importance(
            benefactor,
            beneficiary,
            len(witness_ids),
            relief_value,
        ),
        relief_value=relief_value,
        genome_signals=_genome_signal_payload(
            benefactor,
            (
                "empathy",
                "justice",
                "nurturance",
                "civics",
                "honesty",
                "courage",
                "assertiveness",
                "discipline",
                "resilience",
                "frugality",
            ),
        ),
    )


def _maybe_knowledge_culture_in_settlement(
    ctx: "SimulationContext",
    year: int,
    settlement_id: str,
    residents: list["SimulationPersonRecord"],
    *,
    rng: random.Random,
) -> KnowledgeCultureIncident | None:
    sampled = ctx.decision_sample_records(
        residents,
        year=year,
        scope=f"settlement:{settlement_id}:knowledge_culture",
        stream=KNOWLEDGE_SAMPLE_STREAM,
        cap=KNOWLEDGE_SETTLEMENT_SAMPLE_CAP,
    )
    adults = [rec for rec in sampled if _adult_alive(rec, year)]
    if len(adults) < 2:
        return None
    pressure = _settlement_pressure(ctx, year, settlement_id)
    propensities = {rec.person_id: knowledge_culture_propensity(rec) for rec in adults}
    max_propensity = max(propensities.values(), default=0.0)
    population_factor = _clamp((len(adults) - 1) / 90.0, 0.05, 1.0)
    chance = min(
        KNOWLEDGE_SETTLEMENT_CHANCE_CAP,
        KNOWLEDGE_BASE_SETTLEMENT_CHANCE
        * (0.35 + population_factor)
        * (0.75 + max(0.0, 1.0 - min(1.4, pressure)) * 0.25)
        * (0.35 + max_propensity * 2.5),
    )
    if rng.random() >= chance:
        return None
    candidates = [
        rec
        for rec in adults
        if propensities[rec.person_id] >= KNOWLEDGE_PROPENSITY_THRESHOLD
    ]
    if not candidates:
        return None
    creator = _weighted_choice(
        candidates,
        [
            max(0.001, (propensities[rec.person_id] - KNOWLEDGE_PROPENSITY_THRESHOLD) ** 2)
            for rec in candidates
        ],
        rng,
    )
    if creator is None:
        return None
    patron_pool = [rec for rec in adults if rec.person_id != creator.person_id]
    patron = _weighted_choice(
        patron_pool,
        [
            0.35
            + float(rec.person.job_prosperity_01 or 0.35) * 0.7
            + _positive_extreme(rec, "civics") * 0.2
            + _ideal_strength(rec, "curiosity") * 0.1
            for rec in patron_pool
        ],
        rng,
    )
    witness_ids = _choose_witnesses(
        adults,
        actor_id=int(creator.person_id),
        target_id=int(patron.person_id) if patron is not None else int(creator.person_id),
        rng=rng,
    )
    kind = _knowledge_culture_kind(creator)
    novelty_value = _knowledge_novelty_value(creator, pressure, rng)
    return KnowledgeCultureIncident(
        creator=creator,
        patron=patron,
        incident_kind=kind,
        knowledge_domain=_knowledge_domain(kind, creator),
        motive=_knowledge_motive(creator, kind),
        witness_person_ids=witness_ids,
        settlement_id=settlement_id,
        region_id=_residence_region_id(ctx, creator),
        actor_propensity=propensities[creator.person_id],
        resource_pressure=pressure,
        historical_importance=_knowledge_culture_importance(
            creator,
            patron,
            len(witness_ids),
            novelty_value,
        ),
        novelty_value=novelty_value,
        genome_signals=_genome_signal_payload(
            creator,
            (
                "curiosity",
                "creativity",
                "intellect",
                "focus",
                "perception",
                "discipline",
                "civics",
                "wit",
                "adaptability",
            ),
        ),
    )


def _maybe_property_crime_in_settlement(
    ctx: "SimulationContext",
    year: int,
    settlement_id: str,
    residents: list["SimulationPersonRecord"],
    *,
    rng: random.Random,
) -> TheftFraudIncident | None:
    sampled = ctx.decision_sample_records(
        residents,
        year=year,
        scope=f"settlement:{settlement_id}:property_crime",
        stream=THEFT_SAMPLE_STREAM,
        cap=THEFT_SETTLEMENT_SAMPLE_CAP,
    )
    adults = [rec for rec in sampled if _adult_alive(rec, year)]
    if len(adults) < 2:
        return None
    pressure = _settlement_pressure(ctx, year, settlement_id)
    scarcity = _clamp((pressure - 0.65) / 0.85)
    propensities = {rec.person_id: property_crime_propensity(rec) for rec in adults}
    max_propensity = max(propensities.values(), default=0.0)
    population_factor = _clamp((len(adults) - 1) / 60.0, 0.05, 1.0)
    chance = min(
        THEFT_SETTLEMENT_CHANCE_CAP,
        THEFT_BASE_SETTLEMENT_CHANCE
        * (0.45 + population_factor)
        * (1.0 + scarcity * 2.2)
        * (0.35 + max_propensity * 2.4),
    )
    if rng.random() >= chance:
        return None
    candidates = [
        rec
        for rec in adults
        if propensities[rec.person_id] >= THEFT_PROPENSITY_THRESHOLD
    ]
    if not candidates:
        return None
    perpetrator = _weighted_choice(
        candidates,
        [
            max(0.001, (propensities[rec.person_id] - THEFT_PROPENSITY_THRESHOLD) ** 2)
            for rec in candidates
        ],
        rng,
    )
    if perpetrator is None:
        return None
    target_pool = [rec for rec in adults if rec.person_id != perpetrator.person_id]
    target_weights = [
        1.0
        + float(rec.person.job_prosperity_01 or 0.35) * 0.75
        + _ideal_strength(rec, "perception") * 0.15
        for rec in target_pool
    ]
    target = _weighted_choice(target_pool, target_weights, rng)
    if target is None:
        return None
    motive = _property_crime_motive(perpetrator, pressure)
    incident_kind = _property_crime_kind(perpetrator, motive)
    witness_ids = _choose_witnesses(
        adults,
        actor_id=int(perpetrator.person_id),
        target_id=int(target.person_id),
        rng=rng,
    )
    loss_value = _property_crime_loss(perpetrator, target, rng)
    return TheftFraudIncident(
        perpetrator=perpetrator,
        target=target,
        incident_kind=incident_kind,
        motive=motive,
        witness_person_ids=witness_ids,
        settlement_id=settlement_id,
        region_id=_residence_region_id(ctx, perpetrator),
        actor_propensity=propensities[perpetrator.person_id],
        resource_pressure=pressure,
        historical_importance=_property_crime_importance(
            perpetrator,
            target,
            len(witness_ids),
            loss_value,
        ),
        loss_value=loss_value,
        genome_signals=_genome_signal_payload(
            perpetrator,
            (
                "justice",
                "honesty",
                "empathy",
                "persuasion",
                "perception",
                "ambition",
                "frugality",
                "adaptability",
            ),
        ),
    )


def _record_murder_incident(
    ctx: "SimulationContext", year: int, incident: MurderIncident
) -> None:
    ctx._record_simulation_event(
        int(year),
        "murder",
        {
            "year": int(year),
            "event_type": "murder",
            "incident_kind": incident.incident_kind,
            "motive": incident.motive,
            "killer_person_id": int(incident.killer.person_id),
            "victim_person_id": int(incident.victim.person_id),
            "witness_person_ids": list(incident.witness_person_ids),
            "settlement_id": incident.settlement_id,
            "region_id": incident.region_id,
            "actor_violent_propensity": round(incident.actor_propensity, 5),
            "resource_pressure": round(incident.resource_pressure, 5),
            "historical_importance": round(incident.historical_importance, 5),
            "genome_signals": incident.genome_signals,
        },
    )


def _record_property_crime_incident(
    ctx: "SimulationContext", year: int, incident: TheftFraudIncident
) -> None:
    ctx._record_simulation_event(
        int(year),
        "property_crime",
        {
            "year": int(year),
            "event_type": "property_crime",
            "incident_kind": incident.incident_kind,
            "motive": incident.motive,
            "perpetrator_person_id": int(incident.perpetrator.person_id),
            "target_person_id": int(incident.target.person_id),
            "witness_person_ids": list(incident.witness_person_ids),
            "settlement_id": incident.settlement_id,
            "region_id": incident.region_id,
            "actor_property_crime_propensity": round(incident.actor_propensity, 5),
            "resource_pressure": round(incident.resource_pressure, 5),
            "historical_importance": round(incident.historical_importance, 5),
            "loss_value": incident.loss_value,
            "genome_signals": incident.genome_signals,
        },
    )


def _record_affair_scandal_incident(
    ctx: "SimulationContext", year: int, incident: AffairScandalIncident
) -> None:
    primary_betrayed_id = (
        int(incident.betrayed_partner_ids[0])
        if incident.betrayed_partner_ids
        else None
    )
    payload = {
        "year": int(year),
        "event_type": "affair_scandal",
        "incident_kind": incident.incident_kind,
        "motive": incident.motive,
        "accused_person_id": int(incident.accused.person_id),
        "betrayed_partner_person_id": primary_betrayed_id,
        "paramour_person_id": int(incident.paramour.person_id),
        "betrayed_partner_person_ids": list(incident.betrayed_partner_ids),
        "witness_person_ids": list(incident.witness_person_ids),
        "settlement_id": incident.settlement_id,
        "region_id": incident.region_id,
        "actor_scandal_exposure_propensity": round(
            incident.exposure_propensity, 5
        ),
        "pair_exposure_score": round(incident.pair_exposure_score, 5),
        "resource_pressure": round(incident.resource_pressure, 5),
        "historical_importance": round(incident.historical_importance, 5),
        "genome_signals": incident.genome_signals,
    }
    ctx._record_simulation_event(int(year), "affair_scandal", payload)


def _record_public_virtue_incident(
    ctx: "SimulationContext", year: int, incident: PublicVirtueIncident
) -> None:
    ctx._record_simulation_event(
        int(year),
        "public_virtue",
        {
            "year": int(year),
            "event_type": "public_virtue",
            "incident_kind": incident.incident_kind,
            "motive": incident.motive,
            "benefactor_person_id": int(incident.benefactor.person_id),
            "beneficiary_person_id": int(incident.beneficiary.person_id),
            "witness_person_ids": list(incident.witness_person_ids),
            "settlement_id": incident.settlement_id,
            "region_id": incident.region_id,
            "actor_public_virtue_propensity": round(incident.actor_propensity, 5),
            "resource_pressure": round(incident.resource_pressure, 5),
            "historical_importance": round(incident.historical_importance, 5),
            "relief_value": incident.relief_value,
            "genome_signals": incident.genome_signals,
        },
    )


def _record_knowledge_culture_incident(
    ctx: "SimulationContext", year: int, incident: KnowledgeCultureIncident
) -> None:
    payload = {
        "year": int(year),
        "event_type": "knowledge_culture",
        "incident_kind": incident.incident_kind,
        "knowledge_domain": incident.knowledge_domain,
        "motive": incident.motive,
        "creator_person_id": int(incident.creator.person_id),
        "patron_person_id": (
            int(incident.patron.person_id) if incident.patron is not None else None
        ),
        "witness_person_ids": list(incident.witness_person_ids),
        "settlement_id": incident.settlement_id,
        "region_id": incident.region_id,
        "actor_knowledge_culture_propensity": round(incident.actor_propensity, 5),
        "resource_pressure": round(incident.resource_pressure, 5),
        "historical_importance": round(incident.historical_importance, 5),
        "novelty_value": incident.novelty_value,
        "genome_signals": incident.genome_signals,
    }
    ctx._record_simulation_event(int(year), "knowledge_culture", payload)


def simulation_incidents_annual_tick(ctx: "SimulationContext", year: int) -> None:
    """Generate rare detailed personal incidents that can affect history."""
    y = int(year)
    prof = simulation_timing.active_for_year(y)
    tpc = time.perf_counter
    if prof:
        t0 = tpc()
    rng = random.Random(
        y * MURDER_RNG_STREAM + int(getattr(ctx, "placename_rng_salt", 0)) + 911
    )
    theft_rng = random.Random(
        y * THEFT_RNG_STREAM + int(getattr(ctx, "placename_rng_salt", 0)) + 1171
    )
    scandal_rng = random.Random(
        y * SCANDAL_RNG_STREAM + int(getattr(ctx, "placename_rng_salt", 0)) + 1409
    )
    virtue_rng = random.Random(
        y * VIRTUE_RNG_STREAM + int(getattr(ctx, "placename_rng_salt", 0)) + 1693
    )
    knowledge_rng = random.Random(
        y * KNOWLEDGE_RNG_STREAM + int(getattr(ctx, "placename_rng_salt", 0)) + 1877
    )
    dead_ids: set[int] = set()
    murder_count = 0
    property_crime_count = 0
    scandal_count = 0
    public_virtue_count = 0
    knowledge_culture_count = 0
    settlements = sorted(ctx.current_people_by_settlement().items())
    if prof:
        simulation_timing.accumulate("incidents.setup", tpc() - t0)
        t0 = tpc()
    for settlement_id, residents in settlements:
        if murder_count < MURDER_MAX_EVENTS_PER_YEAR:
            incident = _maybe_murder_in_settlement(
                ctx,
                y,
                settlement_id,
                residents,
                rng=rng,
                already_dead=dead_ids,
            )
            if incident is not None:
                _record_murder_incident(ctx, y, incident)
                dead_ids.add(int(incident.victim.person_id))
                murder_count += 1
        if property_crime_count < THEFT_MAX_EVENTS_PER_YEAR:
            property_crime = _maybe_property_crime_in_settlement(
                ctx,
                y,
                settlement_id,
                residents,
                rng=theft_rng,
            )
            if property_crime is not None:
                _record_property_crime_incident(ctx, y, property_crime)
                property_crime_count += 1
        if scandal_count < SCANDAL_MAX_EVENTS_PER_YEAR:
            scandal = _maybe_affair_scandal_in_settlement(
                ctx,
                y,
                settlement_id,
                residents,
                rng=scandal_rng,
            )
            if scandal is not None:
                _record_affair_scandal_incident(ctx, y, scandal)
                scandal_count += 1
        if public_virtue_count < VIRTUE_MAX_EVENTS_PER_YEAR:
            virtue = _maybe_public_virtue_in_settlement(
                ctx,
                y,
                settlement_id,
                residents,
                rng=virtue_rng,
            )
            if virtue is not None:
                _record_public_virtue_incident(ctx, y, virtue)
                public_virtue_count += 1
        if knowledge_culture_count < KNOWLEDGE_MAX_EVENTS_PER_YEAR:
            knowledge = _maybe_knowledge_culture_in_settlement(
                ctx,
                y,
                settlement_id,
                residents,
                rng=knowledge_rng,
            )
            if knowledge is not None:
                _record_knowledge_culture_incident(ctx, y, knowledge)
                knowledge_culture_count += 1
    if dead_ids:
        ctx.mark_dead(dead_ids, deathyear=y)
    if prof:
        simulation_timing.accumulate("incidents.generate", tpc() - t0)
        simulation_timing.record_gauge(y, "incidents", "murder_events", murder_count)
        simulation_timing.record_gauge(
            y, "incidents", "property_crime_events", property_crime_count
        )
        simulation_timing.record_gauge(
            y, "incidents", "affair_scandal_events", scandal_count
        )
        simulation_timing.record_gauge(
            y, "incidents", "public_virtue_events", public_virtue_count
        )
        simulation_timing.record_gauge(
            y, "incidents", "knowledge_culture_events", knowledge_culture_count
        )
