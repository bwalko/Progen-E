"""Genome-driven personal incidents with historical event consequences."""

from __future__ import annotations

import json
import sqlite3
import random
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from library import simulation_timing
from library.event_catalog import choose_event_catalog_kind
from library.event_scoring import (
    EventScoringContext,
    clamp01 as _clamp,
    composite_score as _composite_score,
    contextual_propensity_by_person_id,
    eligible_records_by_threshold,
    ideal_strength as _ideal_strength,
    infer_role_tags,
    knowledge_culture_propensity,
    negative_extreme as _negative_extreme,
    positive_extreme as _positive_extreme,
    property_crime_propensity,
    public_virtue_propensity,
    scandal_exposure_propensity,
    SerialPredationRisk,
    serial_predation_risk,
    threshold_excess_value_weights,
    threshold_excess_weights,
    trait_value as _trait,
    violent_actor_propensity,
)
from library.geography import get_region, list_routes_from
from library.incident_rates import IncidentRateParams, incident_rate_for_year
from library.serious_crime_taxonomy import (
    murder_taxonomy_category,
    serious_crime_category_label,
    serial_classification_eligible_category,
)
from library.simulation_outlaws import (
    OUTLAW_STATUS_FUGITIVE,
    OUTLAW_STATUS_WANTED,
    is_outlaw_absent,
    outlaw_case_from_murder,
    outlaw_case_from_property_crime,
)
from library.world_save import event_payload_from_row

if TYPE_CHECKING:
    from library.simulation_context import (
        SimulationContext,
        SimulationPersonRecord,
    )


INCIDENT_ADULT_MIN_AGE = 16
MURDER_BASE_SETTLEMENT_CHANCE = 0.00075
MURDER_TARGET_PER_10K_PER_YEAR = 4.0
MURDER_ANNUAL_CAP_HEADROOM = 2.0
MURDER_DETAILED_NARRATIVE_SAMPLE_SHARE = 0.06
MURDER_RATE_CONTEXT_MULTIPLIER = 0.18
MURDER_SETTLEMENT_CHANCE_CAP = 0.30
MURDER_PROPENSITY_THRESHOLD = 0.24
MURDER_SETTLEMENT_SAMPLE_CAP = 250
MURDER_SETTLEMENT_TRIAL_POPULATION = 250
MURDER_MAX_SETTLEMENT_TRIALS = 24
MURDER_MAX_EVENTS_PER_YEAR = 24
MURDER_RNG_STREAM = 610_019
MURDER_BACKGROUND_RNG_STREAM = 610_023
MURDER_SAMPLE_STREAM = 610_021
MURDER_SERIAL_PROPENSITY_WEIGHT = 3.25
MURDER_PRIOR_KILLER_WEIGHT = 0.42
MURDER_REPEAT_KILLER_SELECTION_MULTIPLIER_CAP = 2.25
SERIAL_CANDIDATE_TRIGGER_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "genome_composite_profile_refreshed",
        "job_assigned",
        "job_lost",
        "job_seeker_migration",
        "settlement_moved",
        "passive_person_promoted",
        "person_traits_recalculated",
        "trait_profile_updated",
        "outlaw_escape",
        "outlaw_returned",
        "outlaw_forgotten",
        "outlaw_bought_off",
    }
)
SERIAL_CANDIDATE_FORCE_RECHECK_TRIGGERS: frozenset[str] = frozenset(
    {
        "adulthood",
        "promotion_to_detailed",
        "major_trait_recalculation",
        "job_or_location_change",
        "release_or_escape",
        "successful_serious_crime",
    }
)
SERIAL_CANDIDATE_REEVALUATION_LIMIT = 64
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
HOUSEHOLD_CONSEQUENCE_DEFAULT_PROSPERITY = 1.0
HOUSEHOLD_CONSEQUENCE_MIN_PROSPERITY = 0.0
HOUSEHOLD_CONSEQUENCE_MAX_PROSPERITY = 25.0
SETTLEMENT_CONSEQUENCE_MAX_PROSPERITY = 2.5
LEGAL_FALLOUT_SCANDAL_KINDS: frozenset[str] = frozenset(
    {"heir_legitimacy_rumor", "inheritance_scandal"}
)


@dataclass(frozen=True)
class SeriousCrimeContext:
    motive_category: str
    motive_detail: str
    motive_prose: str
    victim_person_id: int
    offender_person_id: int
    settlement_id: str
    region_id: str
    witness_count: int
    witness_person_ids: tuple[int, ...]
    witness_social_status: str
    witness_status_power_01: float
    victim_kin_count: int
    victim_kin_status: str
    victim_kin_power_01: float
    offender_boldness_01: float
    offender_ruthlessness_01: float
    offender_fear_panic_01: float
    offender_relationship_to_victim: str
    seen_identified: bool
    discovered: bool
    body_or_evidence_found: bool
    offender_seen: bool
    offender_identified: bool
    witness_status_score: float
    witness_pressure_score: float
    victim_kin_pressure: float
    authority_capacity: float
    evidence_strength: float
    public_suspicion_score: float
    offender_identity_confidence: float
    pattern_recognized: bool
    justice_pressure_score: float
    retaliation_risk_score: float

    def payload(self) -> dict[str, object]:
        return {
            "motive_category": self.motive_category,
            "motive_detail": self.motive_detail,
            "motive_prose": self.motive_prose,
            "victim_person_id": int(self.victim_person_id),
            "offender_person_id": int(self.offender_person_id),
            "settlement_id": self.settlement_id,
            "region_id": self.region_id,
            "witness_count": int(self.witness_count),
            "witness_person_ids": [int(pid) for pid in self.witness_person_ids],
            "witness_social_status": self.witness_social_status,
            "witness_status_power_01": round(float(self.witness_status_power_01), 5),
            "victim_kin_count": int(self.victim_kin_count),
            "victim_kin_status": self.victim_kin_status,
            "victim_kin_power_01": round(float(self.victim_kin_power_01), 5),
            "offender_boldness_01": round(float(self.offender_boldness_01), 5),
            "offender_ruthlessness_01": round(float(self.offender_ruthlessness_01), 5),
            "offender_fear_panic_01": round(float(self.offender_fear_panic_01), 5),
            "offender_relationship_to_victim": self.offender_relationship_to_victim,
            "seen_identified": bool(self.seen_identified),
            "discovered": bool(self.discovered),
            "body_or_evidence_found": bool(self.body_or_evidence_found),
            "offender_seen": bool(self.offender_seen),
            "offender_identified": bool(self.offender_identified),
            "witness_status_score": round(float(self.witness_status_score), 5),
            "witness_pressure_score": round(float(self.witness_pressure_score), 5),
            "victim_kin_pressure": round(float(self.victim_kin_pressure), 5),
            "authority_capacity": round(float(self.authority_capacity), 5),
            "evidence_strength": round(float(self.evidence_strength), 5),
            "public_suspicion_score": round(float(self.public_suspicion_score), 5),
            "offender_identity_confidence": round(
                float(self.offender_identity_confidence), 5
            ),
            "pattern_recognized": bool(self.pattern_recognized),
            "justice_pressure_score": round(float(self.justice_pressure_score), 5),
            "retaliation_risk_score": round(float(self.retaliation_risk_score), 5),
        }


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
    serial_predator_propensity: float = 0.0
    serial_predation_lane: str = "none"
    serial_predation_candidate_status: str = "none"
    serial_predation_opportunity_score: float = 0.0
    previous_murder_count: int = 0
    previous_predatory_murder_count: int = 0
    crime_context: SeriousCrimeContext | None = None


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
    outlaw_case_key: str | None = None
    outlaw_status: str | None = None


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


@dataclass(frozen=True)
class IncidentScoringFacts:
    care_indexes: object | None
    office_holder_ids: frozenset[int]
    ruler_ids: frozenset[int]
    court_settlement_ids: frozenset[str]
    succession_crisis_region_ids: frozenset[str]
    faction_tension_region_ids: frozenset[str]
    war_region_ids: frozenset[str]


def _residence_settlement_id(rec: "SimulationPersonRecord") -> str:
    if is_outlaw_absent(rec.person):
        return ""
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
    if is_outlaw_absent(rec.person):
        return False
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


def _incident_chance_multiplier(rate: IncidentRateParams | None) -> float:
    return max(0.0, float(rate.chance_multiplier if rate is not None else 1.0))


def _incident_cap_multiplier(rate: IncidentRateParams | None) -> float:
    return max(0.0, float(rate.annual_cap_multiplier if rate is not None else 1.0))


def _scaled_chance_cap(base_cap: float, rate: IncidentRateParams | None) -> float:
    return min(1.0, max(0.0, float(base_cap)) * _incident_chance_multiplier(rate))


def _annual_event_limit(base_limit: int, rate: IncidentRateParams | None) -> int:
    multiplier = _incident_cap_multiplier(rate)
    if int(base_limit) <= 0 or multiplier <= 0.0:
        return 0
    return max(1, int(int(base_limit) * multiplier + 0.999999))


def _murder_target_per_10k(rate: IncidentRateParams | None) -> float:
    if rate is not None and rate.target_per_10k_per_year is not None:
        return max(0.0, float(rate.target_per_10k_per_year))
    return max(0.0, float(MURDER_TARGET_PER_10K_PER_YEAR))


def _murder_annual_event_cap(
    settlements: list[tuple[str, list["SimulationPersonRecord"]]],
    rate: IncidentRateParams | None = None,
    population_by_settlement: dict[str, int] | None = None,
) -> int:
    detailed_population = sum(len(residents) for _settlement_id, residents in settlements)
    mixed_population = sum(
        max(
            len(residents),
            int((population_by_settlement or {}).get(str(settlement_id), len(residents))),
        )
        for settlement_id, residents in settlements
    )
    if detailed_population <= 0 or mixed_population <= 0:
        return 0
    target_per_10k = _murder_target_per_10k(rate)
    cap_multiplier = _incident_cap_multiplier(rate)
    if target_per_10k <= 0.0 or cap_multiplier <= 0.0:
        return 0
    detailed_target = detailed_population * target_per_10k / 10_000.0
    mixed_narrative_target = (
        mixed_population
        * target_per_10k
        / 10_000.0
        * max(0.0, float(MURDER_DETAILED_NARRATIVE_SAMPLE_SHARE))
    )
    target = max(detailed_target, mixed_narrative_target)
    cap = int(
        target
        * max(0.0, float(MURDER_ANNUAL_CAP_HEADROOM))
        * cap_multiplier
        + 0.999999
    )
    safety_cap = int(int(MURDER_MAX_EVENTS_PER_YEAR) * cap_multiplier + 0.999999)
    if cap <= 0 or safety_cap <= 0:
        return 0
    return max(1, min(safety_cap, cap))


def _murder_settlement_trial_count(
    residents: list["SimulationPersonRecord"],
    population_count: int | None = None,
) -> int:
    if not residents:
        return 0
    population = max(len(residents), int(population_count or len(residents)))
    pop_per_trial = max(1, int(MURDER_SETTLEMENT_TRIAL_POPULATION))
    trials = (population + pop_per_trial - 1) // pop_per_trial
    return max(1, min(int(MURDER_MAX_SETTLEMENT_TRIALS), trials))


def _stochastic_count(expected: float, rng: random.Random) -> int:
    base = int(max(0.0, float(expected)))
    fraction = max(0.0, float(expected) - float(base))
    return base + (1 if rng.random() < fraction else 0)


def _murder_worldwide_target_count(
    population: int,
    rate: IncidentRateParams | None,
    rng: random.Random,
) -> int:
    target = (
        max(0, int(population))
        * _murder_target_per_10k(rate)
        / 10_000.0
        * _incident_chance_multiplier(rate)
    )
    return _stochastic_count(target, rng)


def _murder_chance_from_propensity(
    *,
    adults_count: int,
    scarcity: float,
    max_propensity: float,
    rate: IncidentRateParams | None,
) -> float:
    population_factor = _clamp((int(adults_count) - 1) / 80.0, 0.05, 1.0)
    population_rate_chance = (
        _murder_target_per_10k(rate)
        / 10_000.0
        * min(int(adults_count), max(1, int(MURDER_SETTLEMENT_TRIAL_POPULATION)))
    )
    raw_chance = (
        MURDER_BASE_SETTLEMENT_CHANCE
        * (0.35 + population_factor)
        * (1.0 + float(scarcity) * 3.0)
        * (0.35 + float(max_propensity) * 2.5)
        + population_rate_chance
        * MURDER_RATE_CONTEXT_MULTIPLIER
        * (0.50 + float(max_propensity) * 2.0)
        * (1.0 + float(scarcity) * 1.5)
    )
    return min(
        _scaled_chance_cap(MURDER_SETTLEMENT_CHANCE_CAP, rate),
        raw_chance * _incident_chance_multiplier(rate),
    )


def _scored_family_chance_from_propensity(
    *,
    base_chance: float,
    population_denominator: float,
    population_base: float,
    pressure_base: float,
    pressure_factor: float,
    pressure_weight: float,
    propensity_base: float,
    propensity_weight: float,
    max_propensity: float,
    adults_count: int,
    chance_cap: float,
    rate: IncidentRateParams | None,
) -> float:
    population_factor = _clamp(
        (int(adults_count) - 1) / float(population_denominator), 0.05, 1.0
    )
    raw_chance = (
        float(base_chance)
        * (float(population_base) + population_factor)
        * (float(pressure_base) + float(pressure_factor) * float(pressure_weight))
        * (float(propensity_base) + float(max_propensity) * float(propensity_weight))
    )
    return min(
        _scaled_chance_cap(chance_cap, rate),
        raw_chance * _incident_chance_multiplier(rate),
    )


def _relationship_motive(
    ctx: "SimulationContext",
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
        int(victim.person_id) in ctx.paramour_ids_for_person(killer.person_id)
        or int(killer.person_id) in ctx.paramour_ids_for_person(victim.person_id)
    ):
        return "paramour_conflict", 3.0
    if killer.father_id == victim.person_id or killer.mother_id == victim.person_id:
        return "kin_conflict", 2.2
    if victim.father_id == killer.person_id or victim.mother_id == killer.person_id:
        return "kin_conflict", 2.2
    if kp.job and vp.job and str(kp.job).strip() == str(vp.job).strip():
        return "work_rivalry", 1.35
    return "settlement_grievance", 1.0


def _record_status_power(
    facts: IncidentScoringFacts,
    rec: "SimulationPersonRecord | None",
) -> float:
    if rec is None:
        return 0.0
    person = rec.person
    standing = _clamp(float(person.social_standing_01 or 0.0))
    prosperity = _clamp(float(person.household_prosperity or 0.0) / 4.0)
    job_prosperity = _clamp(float(person.job_prosperity_01 or 0.0))
    office = 0.0
    pid = int(rec.person_id)
    if pid in facts.office_holder_ids:
        office += 0.18
    if pid in facts.ruler_ids:
        office += 0.18
    return _clamp(standing * 0.46 + prosperity * 0.22 + job_prosperity * 0.14 + office)


def _status_label(power: float, *, plural: bool = False) -> str:
    value = float(power)
    if value >= 0.72:
        return "powerful witnesses" if plural else "powerful kin"
    if value >= 0.48:
        return "high-status witnesses" if plural else "high-status kin"
    if value >= 0.24:
        return "local householders" if plural else "local kin"
    return "low-status witnesses" if plural else "minor kin"


def _offender_boldness(killer: "SimulationPersonRecord") -> float:
    return _clamp(
        _positive_extreme(killer, "courage") * 0.42
        + _positive_extreme(killer, "assertiveness") * 0.36
        + _positive_extreme(killer, "ambition") * 0.12
        + _composite_score(killer, "force_get_way_desire") * 0.10
    )


def _offender_ruthlessness(killer: "SimulationPersonRecord") -> float:
    return _clamp(
        _negative_extreme(killer, "empathy") * 0.28
        + _negative_extreme(killer, "justice") * 0.24
        + _negative_extreme(killer, "temperance") * 0.12
        + _positive_extreme(killer, "ambition") * 0.10
        + _composite_score(killer, "psychopathy") * 0.16
        + _composite_score(killer, "ruthless_ambition") * 0.10
    )


def _offender_fear_panic(killer: "SimulationPersonRecord", pressure: float) -> float:
    return _clamp(
        _negative_extreme(killer, "courage") * 0.34
        + _positive_extreme(killer, "neurochemical") * 0.28
        + _negative_extreme(killer, "discipline") * 0.16
        + _clamp(float(pressure) - 0.70) * 0.22
    )


def _offender_concealment_capacity(killer: "SimulationPersonRecord") -> float:
    return _clamp(
        _composite_score(killer, "lie_or_cheat_willingness") * 0.30
        + _composite_score(killer, "disguise_motive") * 0.25
        + _composite_score(killer, "practical_intellect") * 0.20
        + _composite_score(killer, "convince_people") * 0.15
        + _composite_score(killer, "isolation_preference") * 0.10
    )


def _settlement_authority_capacity(
    ctx: "SimulationContext",
    facts: IncidentScoringFacts,
    settlement_id: str,
) -> float:
    st = getattr(ctx, "settlements_by_id", {}).get(str(settlement_id or "").strip())
    stability = float(getattr(st, "stability", 0.5) or 0.5) if st is not None else 0.5
    prosperity = (
        float(getattr(st, "prosperity_pool", 1.0) or 1.0) if st is not None else 1.0
    )
    market_pull = float(getattr(st, "market_pull", 0.0) or 0.0) if st is not None else 0.0
    court = 0.18 if str(settlement_id or "").strip() in facts.court_settlement_ids else 0.0
    return _clamp(
        0.18
        + _clamp(stability) * 0.22
        + min(1.0, max(0.0, prosperity) / 2.0) * 0.12
        + _clamp(market_pull) * 0.08
        + court
    )


def _stable_community_attachment(rec: "SimulationPersonRecord") -> float:
    person = rec.person
    anchored = 0.0
    if str(person.current_settlement_id or "").strip():
        anchored += 0.18
    if str(person.job or "").strip():
        anchored += 0.16
    if person.partner_person_id is not None:
        anchored += 0.16
    if person.household_prosperity is not None:
        anchored += _clamp(float(person.household_prosperity or 0.0) / 4.0) * 0.12
    return _clamp(anchored + _composite_score(rec, "make_friends") * 0.16)


def _candidate_in_custody(rec: "SimulationPersonRecord") -> bool:
    status = str(getattr(rec.person, "outlaw_custody_status", "") or "").strip().lower()
    housing = str(getattr(rec.person, "housing_status", "") or "").strip().lower()
    outlaw_status = str(getattr(rec.person, "outlaw_status", "") or "").strip().lower()
    return status == "active" or housing == "custody" or outlaw_status == "imprisoned"


def _candidate_intense_pursuit(
    ctx: "SimulationContext", rec: "SimulationPersonRecord"
) -> bool:
    pid = int(rec.person_id)
    for case in (getattr(ctx, "outlaw_cases", {}) or {}).values():
        try:
            accused = int(getattr(case, "accused_person_id", 0) or 0)
        except (TypeError, ValueError):
            continue
        if accused != pid:
            continue
        if str(getattr(case, "status", "") or "").strip().lower() not in {
            "active",
            "wanted",
            "fugitive",
        }:
            continue
        if float(getattr(case, "pursuit_pressure_01", 0.0) or 0.0) >= 0.65:
            return True
    return False


def _active_serial_candidate_cap(ctx: "SimulationContext") -> int:
    current_ids = getattr(ctx, "current_people_ids", set()) or set()
    if current_ids:
        detailed_alive = len(current_ids)
    else:
        detailed_alive = sum(1 for rec in getattr(ctx, "people", ()) if rec.person.deathyear is None)
    return max(0, int(detailed_alive) // 15_000)


def _serial_candidate_next_check_year(person_id: int, year: int) -> int:
    return int(year) + 9 + (abs(int(person_id)) % 7)


def _serial_candidate_row_from_risk(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    risk: SerialPredationRisk,
    *,
    year: int,
    trigger: str,
    status: str,
) -> dict[str, object]:
    existing = (getattr(ctx, "serial_predation_candidates", {}) or {}).get(int(rec.person_id), {})
    hidden = int(existing.get("hidden_linked_kill_count", 0) or 0) if isinstance(existing, dict) else 0
    suspected = int(existing.get("suspected_linked_kill_count", 0) or 0) if isinstance(existing, dict) else 0
    public_suspicion = (
        float(existing.get("public_suspicion_score", 0.0) or 0.0)
        if isinstance(existing, dict)
        else 0.0
    )
    identity_confidence = (
        float(existing.get("offender_identity_confidence", 0.0) or 0.0)
        if isinstance(existing, dict)
        else 0.0
    )
    details = (
        dict(existing.get("details") or {})
        if isinstance(existing, dict) and isinstance(existing.get("details"), dict)
        else {}
    )
    trigger_key = str(trigger or "").strip() or "candidate_check"
    trigger_count = 0
    try:
        trigger_count = int(details.get("trigger_count", 0) or 0)
    except (TypeError, ValueError):
        trigger_count = 0
    trigger_history_raw = details.get("trigger_history")
    trigger_history = (
        list(trigger_history_raw)
        if isinstance(trigger_history_raw, list)
        else []
    )
    trigger_history.append({"year": int(year), "trigger": trigger_key})
    details.update(
        {
            "last_trigger": trigger_key,
            "last_trigger_year": int(year),
            "trigger_count": trigger_count + 1,
            "trigger_history": trigger_history[-6:],
        }
    )
    return {
        "person_id": int(rec.person_id),
        "risk_lane": risk.risk_lane,
        "status": status,
        "risk_score": round(float(risk.risk_score), 5),
        "harm_drive": round(float(risk.harm_drive), 5),
        "inhibition": round(float(risk.inhibition), 5),
        "control": round(float(risk.control), 5),
        "exposure_noise": round(float(risk.exposure_noise), 5),
        "organized_serial_risk": round(float(risk.organized_serial_risk), 5),
        "disorganized_serial_risk": round(float(risk.disorganized_serial_risk), 5),
        "next_check_year": _serial_candidate_next_check_year(int(rec.person_id), int(year)),
        "last_checked_year": int(year),
        "last_serious_crime_year": (
            existing.get("last_serious_crime_year")
            if isinstance(existing, dict)
            else None
        ),
        "hidden_linked_kill_count": max(0, hidden),
        "suspected_linked_kill_count": max(0, suspected),
        "public_suspicion_score": round(_clamp(public_suspicion), 5),
        "pattern_recognized": bool(existing.get("pattern_recognized", False))
        if isinstance(existing, dict)
        else False,
        "offender_identity_confidence": round(_clamp(identity_confidence), 5),
        "rejection_reasons": list(risk.rejection_reasons),
        "details": details,
    }


def _event_payload_person_ids(payload: object, keys: tuple[str, ...]) -> set[int]:
    if not isinstance(payload, dict):
        return set()
    out: set[int] = set()
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (list, tuple, set)):
            values = value
        else:
            values = (value,)
        for item in values:
            try:
                pid = int(item)
            except (TypeError, ValueError):
                continue
            if pid > 0:
                out.add(pid)
    return out


def _serial_trigger_kind_from_event_type(event_type: str) -> str | None:
    event_key = str(event_type or "").strip()
    if event_key not in SERIAL_CANDIDATE_TRIGGER_EVENT_TYPES:
        return None
    if event_key in {
        "genome_composite_profile_refreshed",
        "person_traits_recalculated",
        "trait_profile_updated",
    }:
        return "major_trait_recalculation"
    if event_key in {"job_assigned", "job_lost", "job_seeker_migration", "settlement_moved"}:
        return "job_or_location_change"
    if event_key == "passive_person_promoted":
        return "promotion_to_detailed"
    if event_key in {"outlaw_escape", "outlaw_returned", "outlaw_forgotten", "outlaw_bought_off"}:
        return "release_or_escape"
    return None


def _pending_serial_candidate_trigger_ids(
    ctx: "SimulationContext", year: int
) -> dict[str, set[int]]:
    triggered: dict[str, set[int]] = {}
    for sim_year, event_type, payload in getattr(ctx, "_pending_simulation_events", ()):
        if sim_year is not None and int(sim_year) != int(year):
            continue
        trigger = _serial_trigger_kind_from_event_type(str(event_type or ""))
        if trigger is None:
            continue
        ids = _event_payload_person_ids(
            payload,
            (
                "person_id",
                "worker_person_id",
                "accused_person_id",
                "perpetrator_person_id",
                "actor_person_id",
                "moved_person_id",
                "moved_person_ids",
                "person_ids",
            ),
        )
        if ids:
            triggered.setdefault(trigger, set()).update(ids)
    for entry in getattr(ctx, "passive_promotion_log", ()) or ():
        try:
            sim_year = int(getattr(entry, "sim_year"))
            person_id = int(getattr(entry, "person_id"))
        except (TypeError, ValueError):
            continue
        if sim_year == int(year) and person_id > 0:
            triggered.setdefault("promotion_to_detailed", set()).add(person_id)
    return triggered


def _records_for_person_ids(
    ctx: "SimulationContext", person_ids: set[int]
) -> tuple["SimulationPersonRecord", ...]:
    records: list["SimulationPersonRecord"] = []
    for pid in sorted(int(value) for value in person_ids if int(value) > 0):
        rec = getattr(ctx, "id_to_record", {}).get(pid)
        if rec is not None:
            records.append(rec)
    return tuple(records)


def _due_serial_candidate_records(
    ctx: "SimulationContext", year: int
) -> tuple["SimulationPersonRecord", ...]:
    due_ids: set[int] = set()
    for raw_pid, row in (getattr(ctx, "serial_predation_candidates", {}) or {}).items():
        if not isinstance(row, dict):
            continue
        try:
            pid = int(row.get("person_id", raw_pid))
        except (TypeError, ValueError):
            continue
        try:
            next_check = int(row.get("next_check_year"))
        except (TypeError, ValueError):
            continue
        if next_check <= int(year):
            due_ids.add(pid)
    return _records_for_person_ids(ctx, due_ids)


def _newly_adult_records(
    records: list["SimulationPersonRecord"] | tuple["SimulationPersonRecord", ...],
    year: int,
) -> tuple["SimulationPersonRecord", ...]:
    out: list["SimulationPersonRecord"] = []
    for rec in records:
        try:
            age = int(year) - int(rec.person.birthyear)
        except (TypeError, ValueError):
            continue
        if age == INCIDENT_ADULT_MIN_AGE and rec.person.deathyear is None:
            out.append(rec)
    return tuple(out)


def _refresh_serial_predation_event_triggers(
    ctx: "SimulationContext",
    *,
    year: int,
    current_records: list["SimulationPersonRecord"] | tuple["SimulationPersonRecord", ...],
) -> dict[int, dict[str, object]]:
    checked: dict[int, dict[str, object]] = {}
    groups: list[tuple[str, tuple["SimulationPersonRecord", ...]]] = [
        ("scheduled_candidate_check", _due_serial_candidate_records(ctx, int(year))),
        ("adulthood", _newly_adult_records(current_records, int(year))),
    ]
    for trigger, person_ids in sorted(
        _pending_serial_candidate_trigger_ids(ctx, int(year)).items()
    ):
        groups.append((trigger, _records_for_person_ids(ctx, person_ids)))
    seen_for_trigger: set[tuple[str, int]] = set()
    for trigger, records in groups:
        filtered: list["SimulationPersonRecord"] = []
        for rec in records:
            key = (trigger, int(rec.person_id))
            if key in seen_for_trigger:
                continue
            seen_for_trigger.add(key)
            if _adult_alive(rec, int(year)):
                filtered.append(rec)
        if not filtered:
            continue
        refreshed = _refresh_serial_predation_candidates(
            ctx,
            tuple(filtered[:SERIAL_CANDIDATE_REEVALUATION_LIMIT]),
            year=int(year),
            trigger=trigger,
        )
        checked.update(refreshed)
    return checked


def _serial_candidate_rows_for_records(
    ctx: "SimulationContext",
    records: list["SimulationPersonRecord"] | tuple["SimulationPersonRecord", ...],
    *,
    year: int,
) -> dict[int, dict[str, object]]:
    candidates = getattr(ctx, "serial_predation_candidates", {}) or {}
    rows: dict[int, dict[str, object]] = {}
    due: list["SimulationPersonRecord"] = []
    for rec in records:
        pid = int(rec.person_id)
        row = candidates.get(pid)
        if not isinstance(row, dict):
            continue
        rows[pid] = row
        try:
            next_check = int(row.get("next_check_year"))
        except (TypeError, ValueError):
            next_check = int(year) + 1
        if next_check <= int(year):
            due.append(rec)
    if due:
        rows.update(
            _refresh_serial_predation_candidates(
                ctx,
                tuple(due[:SERIAL_CANDIDATE_REEVALUATION_LIMIT]),
                year=int(year),
                trigger="scheduled_candidate_check",
            )
        )
    return rows


def _refresh_serial_predation_candidates(
    ctx: "SimulationContext",
    records: list["SimulationPersonRecord"] | tuple["SimulationPersonRecord", ...],
    *,
    year: int,
    trigger: str,
) -> dict[int, dict[str, object]]:
    candidates = getattr(ctx, "serial_predation_candidates", None)
    if candidates is None:
        candidates = {}
        ctx.serial_predation_candidates = candidates
    cap = _active_serial_candidate_cap(ctx)
    active_count = sum(
        1
        for row in candidates.values()
        if isinstance(row, dict) and str(row.get("status") or "") == "active"
    )
    checked: dict[int, dict[str, object]] = {}
    for rec in records:
        if not _adult_alive(rec, int(year)):
            continue
        pid = int(rec.person_id)
        existing = candidates.get(pid)
        next_check = None
        if isinstance(existing, dict):
            try:
                next_check = int(existing.get("next_check_year"))
            except (TypeError, ValueError):
                next_check = None
        due = existing is None or next_check is None or int(next_check) <= int(year)
        if (
            not due
            and str(trigger or "") not in SERIAL_CANDIDATE_FORCE_RECHECK_TRIGGERS
        ):
            if isinstance(existing, dict):
                checked[pid] = existing
            continue
        risk = serial_predation_risk(
            rec,
            stable_community_attachment=_stable_community_attachment(rec),
            in_custody=_candidate_in_custody(rec),
            intense_pursuit=_candidate_intense_pursuit(ctx, rec),
        )
        if risk.eligible:
            prior_status = str(existing.get("status") or "") if isinstance(existing, dict) else ""
            if cap <= 0:
                status = "dormant_throttled"
            elif prior_status == "active" and active_count <= cap:
                status = "active"
            elif active_count < cap:
                status = "active"
                active_count += 1
            else:
                status = "dormant_throttled"
            row = _serial_candidate_row_from_risk(
                ctx, rec, risk, year=int(year), trigger=trigger, status=status
            )
            candidates[pid] = row
            checked[pid] = row
        elif isinstance(existing, dict):
            row = _serial_candidate_row_from_risk(
                ctx, rec, risk, year=int(year), trigger=trigger, status="dormant_rejected"
            )
            candidates[pid] = row
            checked[pid] = row
    return checked


def _relationship_to_victim(
    ctx: "SimulationContext",
    killer: "SimulationPersonRecord",
    victim: "SimulationPersonRecord",
    motive_category: str,
) -> str:
    pair = {int(killer.person_id), int(victim.person_id)}
    if (
        killer.person.partner_person_id == victim.person_id
        or victim.person.partner_person_id == killer.person_id
        or any({int(a), int(b)} == pair for a, b in ctx.couples)
    ):
        return "partner"
    if (
        int(victim.person_id) in ctx.paramour_ids_for_person(killer.person_id)
        or int(killer.person_id) in ctx.paramour_ids_for_person(victim.person_id)
    ):
        return "paramour"
    if (
        killer.father_id == victim.person_id
        or killer.mother_id == victim.person_id
        or victim.father_id == killer.person_id
        or victim.mother_id == killer.person_id
    ):
        return "kin"
    if motive_category == "work_rivalry":
        return "work_rival"
    return "neighbor"


def _murder_motive_detail(
    killer: "SimulationPersonRecord",
    victim: "SimulationPersonRecord",
    *,
    motive_category: str,
    incident_kind: str,
    pressure: float,
) -> str:
    kind = str(incident_kind or "").strip()
    if motive_category in {"partner_conflict", "kin_conflict"}:
        return "household_grievance"
    if motive_category == "paramour_conflict":
        return "intimate_rivalry"
    if motive_category == "work_rivalry":
        return "workplace_rivalry"
    if kind in {"feud_killing", "feud_murder"}:
        return "neighborhood_feud"
    if kind in {"rash_brawl_killing"}:
        return "deliberate_quarrel"
    exposure_pressure = (
        _negative_extreme(killer, "honesty")
        + _negative_extreme(killer, "justice")
        + _positive_extreme(victim, "justice")
        + _positive_extreme(victim, "perception")
    )
    if kind in {"ambush_killing", "predatory_murder"} and exposure_pressure >= 1.10:
        return "exposure_threat"
    if float(pressure) >= 0.95:
        return "debt_dispute"
    return "neighborhood_feud"


def _murder_motive_prose(detail: str) -> str:
    return {
        "neighborhood_feud": "killed after a long-running neighborhood feud",
        "debt_dispute": "murdered during a dispute over debts",
        "exposure_threat": "ambushed after threatening to expose fraud",
        "deliberate_quarrel": "killed in a quarrel that turned deliberate",
        "household_grievance": "slain amid a bitter household grievance",
        "intimate_rivalry": "killed after a jealous intimate rivalry",
        "workplace_rivalry": "killed after a bitter working rivalry",
    }.get(str(detail or "").strip(), "killed after a local grievance hardened into violence")


def _direct_victim_kin_records(
    ctx: "SimulationContext",
    victim: "SimulationPersonRecord",
    local_records: list["SimulationPersonRecord"] | tuple["SimulationPersonRecord", ...],
) -> tuple["SimulationPersonRecord", ...]:
    victim_id = int(victim.person_id)
    kin: dict[int, "SimulationPersonRecord"] = {}
    for pid in (
        victim.father_id,
        victim.mother_id,
        victim.person.partner_person_id,
    ):
        if pid is None:
            continue
        rec = ctx.id_to_record.get(int(pid))
        if rec is not None:
            kin[int(pid)] = rec
    for rec in local_records:
        if rec.person_id == victim_id:
            continue
        if rec.father_id == victim_id or rec.mother_id == victim_id:
            kin[int(rec.person_id)] = rec
    return tuple(kin[pid] for pid in sorted(kin))


def _build_serious_crime_context(
    ctx: "SimulationContext",
    facts: IncidentScoringFacts,
    *,
    year: int,
    killer: "SimulationPersonRecord",
    victim: "SimulationPersonRecord",
    incident_kind: str,
    motive_category: str,
    witness_person_ids: tuple[int, ...],
    settlement_id: str,
    region_id: str,
    local_records: list["SimulationPersonRecord"] | tuple["SimulationPersonRecord", ...],
    pressure: float,
) -> SeriousCrimeContext:
    witness_records = [
        ctx.id_to_record.get(int(pid))
        for pid in tuple(witness_person_ids or ())
        if int(pid) in ctx.id_to_record
    ]
    witness_power = max(
        (_record_status_power(facts, rec) for rec in witness_records if rec is not None),
        default=0.0,
    )
    kin_records = _direct_victim_kin_records(ctx, victim, local_records)
    kin_power = max((_record_status_power(facts, rec) for rec in kin_records), default=0.0)
    victim_power = _record_status_power(facts, victim)
    boldness = _offender_boldness(killer)
    ruthlessness = _offender_ruthlessness(killer)
    fear_panic = _offender_fear_panic(killer, pressure)
    relationship = _relationship_to_victim(ctx, killer, victim, motive_category)
    detail = _murder_motive_detail(
        killer,
        victim,
        motive_category=motive_category,
        incident_kind=incident_kind,
        pressure=pressure,
    )
    witness_count = len(tuple(witness_person_ids or ()))
    offender_seen = witness_count > 0
    witness_pressure = _clamp(min(0.28, witness_count * 0.08) + witness_power * 0.22)
    kin_pressure = _clamp(
        min(0.22, len(kin_records) * 0.06) + kin_power * 0.30 + victim_power * 0.12
    )
    authority_capacity = _settlement_authority_capacity(ctx, facts, settlement_id)
    concealment = _offender_concealment_capacity(killer)
    body_or_evidence_found = bool(
        offender_seen
        or incident_kind
        in {
            "domestic_murder",
            "kin_killing",
            "feud_killing",
            "feud_murder",
            "rash_brawl_killing",
        }
        or (kin_pressure + authority_capacity) >= 0.82
    )
    evidence_strength = _clamp(
        (0.20 if body_or_evidence_found else 0.04)
        + witness_pressure * 0.35
        + authority_capacity * 0.18
        + kin_pressure * 0.12
        + (0.10 if offender_seen else 0.0)
        - concealment * 0.20
    )
    offender_identity_confidence = _clamp(
        (0.18 if offender_seen else 0.0)
        + witness_pressure * 0.34
        + evidence_strength * 0.36
        + authority_capacity * 0.12
        - concealment * 0.18
    )
    offender_identified = offender_identity_confidence >= 0.60
    public_suspicion = _clamp(
        evidence_strength * 0.40
        + witness_pressure * 0.24
        + kin_pressure * 0.22
        + authority_capacity * 0.14
    )
    discovered = bool(body_or_evidence_found or public_suspicion >= 0.42)
    justice_pressure = _clamp(
        witness_pressure
        + kin_pressure
        + authority_capacity * 0.35
        + victim_power * 0.15
        + evidence_strength * 0.25
    )
    relationship_heat = {
        "partner": 0.18,
        "paramour": 0.16,
        "kin": 0.20,
        "work_rival": 0.10,
        "neighbor": 0.06,
    }.get(relationship, 0.06)
    retaliation_risk = _clamp(
        0.10
        + min(0.24, len(kin_records) * 0.08)
        + kin_power * 0.30
        + relationship_heat
        + (0.10 if offender_seen else 0.0)
        + (0.10 if detail in {"neighborhood_feud", "household_grievance"} else 0.0)
    )
    return SeriousCrimeContext(
        motive_category=str(motive_category or "").strip() or "unknown",
        motive_detail=detail,
        motive_prose=_murder_motive_prose(detail),
        victim_person_id=int(victim.person_id),
        offender_person_id=int(killer.person_id),
        settlement_id=str(settlement_id or "").strip(),
        region_id=str(region_id or "").strip(),
        witness_count=witness_count,
        witness_person_ids=tuple(int(pid) for pid in tuple(witness_person_ids or ())),
        witness_social_status=(
            _status_label(witness_power, plural=True)
            if witness_records
            else "no detailed witnesses"
        ),
        witness_status_power_01=witness_power,
        victim_kin_count=len(kin_records),
        victim_kin_status=_status_label(kin_power, plural=False) if kin_records else "no detailed kin",
        victim_kin_power_01=kin_power,
        offender_boldness_01=boldness,
        offender_ruthlessness_01=ruthlessness,
        offender_fear_panic_01=fear_panic,
        offender_relationship_to_victim=relationship,
        seen_identified=offender_seen,
        discovered=discovered,
        body_or_evidence_found=body_or_evidence_found,
        offender_seen=offender_seen,
        offender_identified=offender_identified,
        witness_status_score=witness_power,
        witness_pressure_score=witness_pressure,
        victim_kin_pressure=kin_pressure,
        authority_capacity=authority_capacity,
        evidence_strength=evidence_strength,
        public_suspicion_score=public_suspicion,
        offender_identity_confidence=offender_identity_confidence,
        pattern_recognized=False,
        justice_pressure_score=justice_pressure,
        retaliation_risk_score=retaliation_risk,
    )


def _incident_kind(
    ctx: "SimulationContext",
    killer: "SimulationPersonRecord",
    motive: str,
    serial_score: float,
    rng: random.Random,
) -> str:
    if motive in {"partner_conflict", "paramour_conflict"}:
        return _catalog_incident_kind(
            ctx,
            "murder",
            tags=("domestic", "household"),
            default="domestic_murder",
            rng=rng,
        )
    if motive == "kin_conflict":
        return _catalog_incident_kind(
            ctx,
            "murder",
            tags=("kin", "household"),
            default="kin_killing",
            rng=rng,
        )
    if (
        _positive_extreme(killer, "justice") >= 0.35
        and _positive_extreme(killer, "courage") >= 0.20
    ):
        return _catalog_incident_kind(
            ctx,
            "murder",
            tags=("feud", "revenge"),
            default="feud_killing",
            rng=rng,
        )
    if (
        _negative_extreme(killer, "patience")
        + _positive_extreme(killer, "neurochemical")
    ) >= 0.9:
        return _catalog_incident_kind(
            ctx,
            "murder",
            tags=("brawl", "impulse"),
            default="rash_brawl_killing",
            rng=rng,
        )
    if serial_score > 0.0:
        return "predatory_murder"
    return _catalog_incident_kind(
        ctx,
        "murder",
        tags=("ordinary",),
        default="murder",
        rng=rng,
    )


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
        "creativity",
        "perception",
        "generosity",
        "loyalty",
        "mating drive",
    )
    return {
        trait: round(_trait(rec, trait), 3)
        for trait in chosen_traits
        if trait in (rec.person.genome or {})
    }


def _murder_payload_is_predatory(payload: object) -> bool:
    return isinstance(payload, dict) and str(payload.get("incident_kind") or "").strip() == "predatory_murder"


def _previous_murder_counts_by_killer(
    ctx: "SimulationContext",
    person_ids: set[int],
    *,
    before_year: int,
    predatory_only: bool = False,
) -> dict[int, int]:
    """Count known prior killer-role murder events from pending and saved events."""

    ids = {int(pid) for pid in person_ids if pid is not None}
    counts = {pid: 0 for pid in ids}
    if not ids:
        return counts
    for sim_year, event_type, payload in getattr(ctx, "_pending_simulation_events", ()):
        if str(event_type or "") != "murder":
            continue
        try:
            if sim_year is not None and int(sim_year) >= int(before_year):
                continue
        except (TypeError, ValueError):
            pass
        try:
            pid = int(payload.get("killer_person_id"))
        except (AttributeError, TypeError, ValueError):
            continue
        if predatory_only and not _murder_payload_is_predatory(payload):
            continue
        if pid in counts:
            counts[pid] += 1
    save_path = getattr(ctx, "save_db_path", None)
    if save_path is None:
        return counts
    try:
        with sqlite3.connect(save_path) as conn:
            conn.row_factory = sqlite3.Row
            exists = conn.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table' AND name = 'simulation_event_people'
                """
            ).fetchone()
            if exists is None:
                return counts
            id_list = sorted(ids)
            for start in range(0, len(id_list), 500):
                chunk = id_list[start : start + 500]
                placeholders = ",".join("?" for _ in chunk)
                params: list[object] = [*chunk, int(before_year)]
                if predatory_only:
                    rows = conn.execute(
                        f"""
                        SELECT ep.person_id, e.id AS event_id, e.event_type,
                               e.payload_json, e.primary_person_id, e.secondary_person_id,
                               e.settlement_key, e.region_key, e.event_origin
                        FROM simulation_event_people ep
                        JOIN simulation_events e ON e.id = ep.event_id
                        WHERE ep.role = 'killer'
                          AND e.event_type = 'murder'
                          AND ep.person_id IN ({placeholders})
                          AND (e.sim_year IS NULL OR e.sim_year < ?)
                        """,
                        params,
                    ).fetchall()
                    for row in rows:
                        payload = event_payload_from_row(row, conn, expand=True)
                        if _murder_payload_is_predatory(payload):
                            pid = int(row["person_id"])
                            counts[pid] = counts.get(pid, 0) + 1
                else:
                    rows = conn.execute(
                        f"""
                        SELECT ep.person_id, COUNT(*) AS c
                        FROM simulation_event_people ep
                        JOIN simulation_events e ON e.id = ep.event_id
                        WHERE ep.role = 'killer'
                          AND e.event_type = 'murder'
                          AND ep.person_id IN ({placeholders})
                          AND (e.sim_year IS NULL OR e.sim_year < ?)
                        GROUP BY ep.person_id
                        """,
                        params,
                    ).fetchall()
                    for row in rows:
                        pid = int(row["person_id"])
                        counts[pid] = counts.get(pid, 0) + int(row["c"])
    except sqlite3.Error:
        return counts
    return counts


def _repeat_murder_selection_multiplier(
    *, serial_propensity: float, previous_murders: int
) -> float:
    serial_pressure = max(0.0, float(serial_propensity) - 0.50)
    prior_pressure = (
        min(1.0, max(0, int(previous_murders)) / 3.0)
        if float(serial_propensity) > 0.0
        else 0.0
    )
    multiplier = (
        1.0
        + serial_pressure * float(MURDER_SERIAL_PROPENSITY_WEIGHT)
        + prior_pressure * float(MURDER_PRIOR_KILLER_WEIGHT)
    )
    return max(1.0, min(float(MURDER_REPEAT_KILLER_SELECTION_MULTIPLIER_CAP), multiplier))


def _serial_predation_opportunity_score(
    ctx: "SimulationContext",
    facts: IncidentScoringFacts,
    rec: "SimulationPersonRecord",
    *,
    settlement_id: str,
    pressure: float,
    previous_predatory_murders: int,
    candidate_row: dict[str, object],
) -> float:
    """Abstract opportunity/oversight score for an already-gated candidate."""

    authority = _settlement_authority_capacity(ctx, facts, settlement_id)
    oversight_gap = max(0.0, 1.0 - authority)
    person = rec.person
    job_text = (
        f"{person.job or ''} {person.job_market_type or ''} "
        f"{getattr(person, 'job_family', '') or ''}"
    ).lower()
    access = 0.0
    if any(
        token in job_text
        for token in (
            "trade",
            "merchant",
            "porter",
            "guard",
            "soldier",
            "service",
            "servant",
            "criminal",
            "vice",
            "outlaw",
            "traveler",
        )
    ):
        access += 0.22
    if str(person.current_settlement_id or "").strip() and str(
        person.current_settlement_id or ""
    ) != str(person.birthplace_settlement_id or ""):
        access += 0.10
    if person.partner_person_id is None:
        access += 0.07
    stress = _clamp((float(pressure) - 0.75) / 0.75)
    if str(person.housing_status or "").strip().lower() in {"street", "vagrant", "unstable"}:
        stress += 0.16
    if person.unemployment_started_year is not None:
        stress += 0.12
    if person.job_lost_year is not None:
        stress += 0.08
    prior_hidden = 0.0
    if int(candidate_row.get("hidden_linked_kill_count", 0) or 0) > 0:
        public_suspicion = float(candidate_row.get("public_suspicion_score", 0.0) or 0.0)
        prior_hidden = 0.22 if public_suspicion < 0.45 else 0.08
    if int(previous_predatory_murders) > 0:
        prior_hidden = max(prior_hidden, min(0.24, int(previous_predatory_murders) * 0.08))
    isolation = _composite_score(rec, "isolation_preference")
    opportunity = (
        oversight_gap * 0.34
        + _clamp(access) * 0.22
        + _clamp(stress) * 0.18
        + _clamp(prior_hidden) * 0.16
        + _clamp(isolation) * 0.10
    )
    return _clamp(opportunity)


def _serial_predation_effective_score(
    row: dict[str, object], opportunity_score: float
) -> float:
    if str(row.get("status") or "") != "active":
        return 0.0
    opportunity = _clamp(float(opportunity_score or 0.0))
    if opportunity < 0.28:
        return 0.0
    risk_score = _clamp(float(row.get("risk_score", 0.0) or 0.0))
    return _clamp(risk_score * (0.35 + opportunity * 0.65))


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


def _int_or_none(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _settlement_region_id(ctx: "SimulationContext", settlement_id: str) -> str:
    sid = str(settlement_id or "").strip()
    if not sid:
        return ""
    st = ctx.settlements_by_id.get(sid)
    return str(getattr(st, "region_id", "") or "").strip() if st is not None else ""


def _polity_scope_region_ids(ctx: "SimulationContext", polity_id: int) -> set[str]:
    out: set[str] = set()
    for terr in getattr(ctx, "gov_territory_rows", []) or []:
        if _int_or_none(getattr(terr, "polity_id", None)) != int(polity_id):
            continue
        target_kind = str(getattr(terr, "target_kind", "") or "").strip().lower()
        target_id = str(getattr(terr, "target_id", "") or "").strip()
        if not target_id:
            continue
        if target_kind == "region":
            out.add(target_id)
        elif target_kind == "settlement":
            rid = _settlement_region_id(ctx, target_id)
            if rid:
                out.add(rid)
    pol = getattr(ctx, "gov_polities", {}).get(int(polity_id))
    cap = str(getattr(pol, "capital_settlement_id", "") or "").strip()
    if cap:
        rid = _settlement_region_id(ctx, cap)
        if rid:
            out.add(rid)
    return out


def _build_incident_scoring_facts(
    ctx: "SimulationContext", year: int
) -> IncidentScoringFacts:
    try:
        care_indexes = ctx.annual_care_indexes(int(year))
    except Exception:
        care_indexes = None
    office_holder_ids: set[int] = set()
    ruler_ids: set[int] = set()
    court_settlement_ids: set[str] = set()
    vacant_polity_ids: set[int] = set()
    ruler_title_tokens = (
        "head",
        "ruler",
        "king",
        "queen",
        "duke",
        "count",
        "chief",
        "mayor",
        "monarch",
    )
    for seat in getattr(ctx, "gov_office_seats", {}).values():
        if str(getattr(seat, "status", "active") or "").strip().lower() != "active":
            continue
        holder_id = _int_or_none(getattr(seat, "holder_person_id", None))
        polity_id = _int_or_none(getattr(seat, "polity_id", None))
        title_id = str(getattr(seat, "title_id", "") or "").strip().lower()
        scope_sid = str(getattr(seat, "scope_settlement_id", "") or "").strip()
        if scope_sid:
            court_settlement_ids.add(scope_sid)
        if holder_id is None:
            if polity_id is not None:
                vacant_polity_ids.add(polity_id)
            continue
        office_holder_ids.add(holder_id)
        if any(token in title_id for token in ruler_title_tokens):
            ruler_ids.add(holder_id)
    for pol in getattr(ctx, "gov_polities", {}).values():
        if str(getattr(pol, "status", "active") or "").strip().lower() != "active":
            continue
        cap = str(getattr(pol, "capital_settlement_id", "") or "").strip()
        if cap:
            court_settlement_ids.add(cap)
    succession_regions: set[str] = set()
    for polity_id in vacant_polity_ids:
        succession_regions.update(_polity_scope_region_ids(ctx, polity_id))
    war_regions: set[str] = set()
    for campaign in getattr(ctx, "gov_campaigns", []) or []:
        if getattr(campaign, "end_sim_year", None) is not None:
            continue
        outcome = str(getattr(campaign, "outcome", "ongoing") or "").strip().lower()
        if outcome not in {"", "ongoing", "active"}:
            continue
        for attr in ("attacker_polity_id", "defender_polity_id"):
            polity_id = _int_or_none(getattr(campaign, attr, None))
            if polity_id is not None:
                war_regions.update(_polity_scope_region_ids(ctx, polity_id))
    faction_regions: set[str] = set(war_regions)
    for alliance in getattr(ctx, "gov_alliances", []) or []:
        until = _int_or_none(getattr(alliance, "until_sim_year", None))
        if until is not None and until <= int(year):
            continue
        try:
            loyalty = float(getattr(alliance, "loyalty_score", 1.0))
        except (TypeError, ValueError):
            loyalty = 1.0
        if loyalty >= 0.35:
            continue
        for attr in ("polity_a_id", "polity_b_id"):
            polity_id = _int_or_none(getattr(alliance, attr, None))
            if polity_id is not None:
                faction_regions.update(_polity_scope_region_ids(ctx, polity_id))
    return IncidentScoringFacts(
        care_indexes=care_indexes,
        office_holder_ids=frozenset(office_holder_ids),
        ruler_ids=frozenset(ruler_ids),
        court_settlement_ids=frozenset(court_settlement_ids),
        succession_crisis_region_ids=frozenset(succession_regions),
        faction_tension_region_ids=frozenset(faction_regions),
        war_region_ids=frozenset(war_regions),
    )


def _job_tokens(rec: "SimulationPersonRecord") -> str:
    return str(rec.person.job or "").strip().lower()


def _person_has_household_tie(facts: IncidentScoringFacts, person_id: int) -> bool:
    care_indexes = facts.care_indexes
    if care_indexes is None:
        return False
    hids = getattr(care_indexes, "household_ids_by_adult", {}).get(int(person_id), ())
    return len(tuple(hids or ())) > 1


def _incident_pressure_tags(
    ctx: "SimulationContext",
    facts: IncidentScoringFacts,
    rec: "SimulationPersonRecord",
    *,
    settlement_id: str,
    region_id: str,
    event_family: str,
    pressure: float,
    adults_count: int,
) -> frozenset[str]:
    tags: set[str] = set()
    st = ctx.settlements_by_id.get(settlement_id)
    stability = float(getattr(st, "stability", 0.5) or 0.5) if st is not None else 0.5
    prosperity = (
        float(getattr(st, "prosperity_pool", 1.0) or 1.0) if st is not None else 1.0
    )
    if pressure >= 0.75:
        tags.add("scarcity")
    if pressure >= 1.20:
        tags.add("disaster")
    if pressure >= 1.00 or adults_count >= 90:
        tags.add("crowding")
    if region_id in facts.war_region_ids:
        tags.add("war")
    if region_id in facts.succession_crisis_region_ids:
        tags.update({"succession_crisis", "office_tension"})
    if region_id in facts.faction_tension_region_ids:
        tags.add("faction_tension")
    job_prosperity = float(rec.person.job_prosperity_01 or 0.35)
    household_prosperity = float(rec.person.household_prosperity or 1.0)
    if (
        rec.person.unemployment_started_year is not None
        or job_prosperity < 0.24
        or household_prosperity < 0.55
    ):
        tags.add("debt")
    if (rec.person.housing_status or "").strip().lower() == "street":
        tags.update({"street_precarity", "survival_need", "status_fall"})
    status = str(rec.person.status_tendency or "").strip().lower()
    if status in {"low", "very low", "fallen"} or (
        rec.person.unemployment_started_year is not None and job_prosperity < 0.35
    ):
        tags.add("status_fall")
    if pressure >= 1.05 or stability < 0.34:
        tags.update({"social_stress", "civic_need"})
    if ctx.paramour_count_for_person(rec.person_id) > 0 or (
        event_family == "affair_scandal" and rec.person.partner_person_id is not None
    ):
        tags.add("relationship_strain")
    if event_family == "affair_scandal" and int(rec.person_id) in (
        facts.office_holder_ids | facts.ruler_ids
    ):
        tags.add("status_pressure")
    if event_family == "knowledge_culture" and (
        prosperity >= 1.20
        or settlement_id in facts.court_settlement_ids
        or int(rec.person_id) in facts.office_holder_ids
    ):
        tags.add("patronage")
    return frozenset(tags)


def _incident_opportunity_tags(
    facts: IncidentScoringFacts,
    rec: "SimulationPersonRecord",
    *,
    settlement_id: str,
    event_family: str,
    pressure: float,
    adults_count: int,
) -> frozenset[str]:
    tags: set[str] = {"same_settlement"}
    pid = int(rec.person_id)
    job = _job_tokens(rec)
    has_household_tie = (
        rec.person.partner_person_id is not None
        or bool(rec.person.paramour_person_ids or (() if rec.person.paramour_person_id is None else (rec.person.paramour_person_id,)))
        or _person_has_household_tie(facts, pid)
    )
    if has_household_tie:
        tags.update({"shared_household", "co_residence", "privacy"})
    if adults_count >= 4:
        tags.update({"public_witness", "witnessed_need", "crowd"})
    else:
        tags.add("isolated")
    if settlement_id in facts.court_settlement_ids or pid in facts.office_holder_ids:
        tags.update({"court", "office_access", "document_access"})
    if pid in facts.office_holder_ids or pid in facts.ruler_ids:
        tags.add("faction_network")
    if any(token in job for token in ("merchant", "trader", "market", "sailor", "ship")):
        tags.add("market_day")
    if (rec.person.housing_status or "").strip().lower() == "street":
        tags.update({"street", "begging", "public_witness"})
    if any(
        token in job
        for token in ("farmer", "herder", "merchant", "trader", "store", "granary")
    ):
        tags.add("storehouse_access")
    if any(
        token in job
        for token in (
            "smith",
            "weaver",
            "potter",
            "carpenter",
            "scribe",
            "ship",
            "artisan",
            "craft",
        )
    ):
        tags.add("workshop")
    if any(token in job for token in ("scribe", "priest", "judge", "scholar", "oracle")):
        tags.add("archive")
    if event_family == "public_virtue" and pressure >= 0.85:
        tags.add("public_crisis")
    return frozenset(tags)


def _incident_context_for_record(
    ctx: "SimulationContext",
    facts: IncidentScoringFacts,
    *,
    year: int,
    settlement_id: str,
    rec: "SimulationPersonRecord",
    event_family: str,
    pressure: float,
    adults_count: int,
) -> EventScoringContext:
    region_id = _residence_region_id(ctx, rec) or _settlement_region_id(ctx, settlement_id)
    role_tags = infer_role_tags(
        rec,
        year=int(year),
        care_indexes=facts.care_indexes,
        office_holder_ids=facts.office_holder_ids,
        ruler_ids=facts.ruler_ids,
    )
    st = ctx.settlements_by_id.get(settlement_id)
    prosperity = (
        float(getattr(st, "prosperity_pool", 1.0) or 1.0) if st is not None else None
    )
    return EventScoringContext(
        role_tags=role_tags,
        pressure_tags=_incident_pressure_tags(
            ctx,
            facts,
            rec,
            settlement_id=settlement_id,
            region_id=region_id,
            event_family=event_family,
            pressure=pressure,
            adults_count=adults_count,
        ),
        opportunity_tags=_incident_opportunity_tags(
            facts,
            rec,
            settlement_id=settlement_id,
            event_family=event_family,
            pressure=pressure,
            adults_count=adults_count,
        ),
        resource_pressure=float(pressure),
        crowding=_clamp((float(adults_count) - 1.0) / 100.0),
        prosperity=prosperity,
        witness_count=max(0, int(adults_count) - 1),
    )


def _incident_context_map(
    ctx: "SimulationContext",
    facts: IncidentScoringFacts,
    *,
    year: int,
    settlement_id: str,
    records: list["SimulationPersonRecord"] | tuple["SimulationPersonRecord", ...],
    event_family: str,
    pressure: float,
) -> dict[int, EventScoringContext]:
    adults_count = len(records)
    return {
        int(rec.person_id): _incident_context_for_record(
            ctx,
            facts,
            year=year,
            settlement_id=settlement_id,
            rec=rec,
            event_family=event_family,
            pressure=pressure,
            adults_count=adults_count,
        )
        for rec in records
    }


def _incident_priority_pool(
    ctx: "SimulationContext",
    residents: list["SimulationPersonRecord"],
    *,
    year: int,
    settlement_id: str,
    event_key: str,
    stream: int,
    cap: int,
    priority_score,
) -> list["SimulationPersonRecord"]:
    sampled = ctx.decision_sample_records(
        residents,
        year=year,
        scope=f"settlement:{settlement_id}:{event_key}",
        stream=stream,
        cap=cap,
    )
    limit = int(cap or 0)
    if limit <= 0 or len(residents) <= limit:
        return sampled
    by_id = {int(rec.person_id): rec for rec in sampled}
    priority_scores: dict[int, float] = {}
    priority_cap = min(limit, max(12, limit // 3))
    probe_limit = min(len(residents), max(limit, limit * 2))
    seed = ctx._stable_decision_seed(
        "|".join(
            (
                str(ctx.world),
                str(ctx.placename_rng_salt),
                str(int(year)),
                str(int(stream) + 1),
                f"settlement:{settlement_id}:{event_key}:priority",
                str(len(residents)),
            )
        )
    )
    priority_probe = random.Random(seed).sample(residents, probe_limit)
    candidates: list[tuple[float, int, "SimulationPersonRecord"]] = []
    for rec in priority_probe:
        pid = int(rec.person_id)
        try:
            score = float(priority_score(rec))
        except (TypeError, ValueError):
            score = 0.0
        if score <= 0.0:
            continue
        priority_scores[pid] = score
        if pid not in by_id:
            candidates.append((score, pid, rec))
    if candidates:
        candidates.sort(key=lambda item: (-item[0], item[1]))
        for score, pid, rec in candidates[:priority_cap]:
            by_id[pid] = rec
            priority_scores[pid] = score
    if len(by_id) <= limit:
        return sorted(by_id.values(), key=lambda rec: int(rec.person_id))
    ranked = sorted(
        by_id.values(),
        key=lambda rec: (
            -priority_scores.get(int(rec.person_id), 0.0),
            int(rec.person_id),
        ),
    )
    return sorted(ranked[:limit], key=lambda rec: int(rec.person_id))


def _violent_incident_priority_score(rec: "SimulationPersonRecord") -> float:
    return max(
        _composite_score(rec, "psychopathy"),
        _composite_score(rec, "evil_done_desire"),
        _composite_score(rec, "force_get_way_desire"),
        _composite_score(rec, "revenge_desire") * 0.9,
        _composite_score(rec, "insanity") * 0.8,
        _positive_extreme(rec, "aggression") * 0.75,
        _negative_extreme(rec, "empathy") * 0.65,
        _negative_extreme(rec, "temperance") * 0.55,
    )


def _property_crime_priority_score(rec: "SimulationPersonRecord") -> float:
    outlaw_status = str(getattr(rec.person, "outlaw_status", "") or "").strip().lower()
    outlaw_bonus = 1.0 if outlaw_status in {OUTLAW_STATUS_WANTED, OUTLAW_STATUS_FUGITIVE} else 0.0
    return max(
        outlaw_bonus,
        _composite_score(rec, "survival_need"),
        _composite_score(rec, "evil_done_desire") * 0.85,
        _composite_score(rec, "lie_or_cheat_willingness") * 0.75,
        _composite_score(rec, "force_get_way_desire") * 0.65,
        _positive_extreme(rec, "ambition") * 0.55,
        _negative_extreme(rec, "honesty") * 0.65,
        _negative_extreme(rec, "temperance") * 0.55,
    )


def _scandal_priority_ids(ctx: "SimulationContext") -> set[int]:
    ids: set[int] = set()
    for a_id, b_id in getattr(ctx, "paramours", ()):
        ids.add(int(a_id))
        ids.add(int(b_id))
    return ids


def _scandal_priority_score(
    rec: "SimulationPersonRecord", paramour_ids: set[int]
) -> float:
    paramour_bonus = 1.0 if int(rec.person_id) in paramour_ids else 0.0
    return max(
        paramour_bonus,
        _positive_extreme(rec, "mating drive") * 0.75,
        _positive_extreme(rec, "persuasion") * 0.45,
        _negative_extreme(rec, "loyalty") * 0.75,
        _negative_extreme(rec, "honesty") * 0.55,
        _negative_extreme(rec, "modesty") * 0.45,
    )


def _virtue_priority_score(rec: "SimulationPersonRecord") -> float:
    return max(
        _composite_score(rec, "good_done_desire"),
        _composite_score(rec, "honest_work_desire") * 0.7,
        _positive_extreme(rec, "generosity") * 0.7,
        _ideal_strength(rec, "honesty") * 0.55,
        _ideal_strength(rec, "civics") * 0.55,
        _ideal_strength(rec, "nurturance") * 0.55,
        _ideal_strength(rec, "patience") * 0.45,
    )


def _knowledge_priority_score(rec: "SimulationPersonRecord") -> float:
    job = str(rec.person.job or "").strip().lower()
    role_bonus = 0.55 if any(
        token in job
        for token in ("scholar", "scribe", "priest", "physician", "teacher", "artisan")
    ) else 0.0
    return max(
        role_bonus,
        _positive_extreme(rec, "curiosity") * 0.75,
        _positive_extreme(rec, "creativity") * 0.75,
        _positive_extreme(rec, "ideation") * 0.65,
        _ideal_strength(rec, "focus") * 0.55,
        _ideal_strength(rec, "perception") * 0.45,
        _ideal_strength(rec, "memory") * 0.45,
    )


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
    rate: IncidentRateParams | None = None,
    scoring_facts: IncidentScoringFacts | None = None,
    population_count: int | None = None,
) -> MurderIncident | None:
    pressure = _settlement_pressure(ctx, year, settlement_id)
    scarcity = _clamp((pressure - 0.75) / 0.75)
    chance_roll = rng.random()
    if chance_roll >= _murder_chance_from_propensity(
        adults_count=max(
            min(len(residents), MURDER_SETTLEMENT_SAMPLE_CAP),
            int(population_count or len(residents)),
        ),
        scarcity=scarcity,
        max_propensity=1.0,
        rate=rate,
    ):
        return None
    sampled = _incident_priority_pool(
        ctx,
        residents,
        year=year,
        settlement_id=settlement_id,
        event_key="murder",
        stream=MURDER_SAMPLE_STREAM,
        cap=MURDER_SETTLEMENT_SAMPLE_CAP,
        priority_score=_violent_incident_priority_score,
    )
    adults = [
        rec
        for rec in sampled
        if rec.person_id not in already_dead and _adult_alive(rec, year)
    ]
    if len(adults) < 2:
        return None
    facts = scoring_facts or _build_incident_scoring_facts(ctx, year)
    contexts = _incident_context_map(
        ctx,
        facts,
        year=year,
        settlement_id=settlement_id,
        records=adults,
        event_family="murder",
        pressure=pressure,
    )
    propensities = contextual_propensity_by_person_id(
        adults, violent_actor_propensity, contexts
    )
    previous_murders = _previous_murder_counts_by_killer(
        ctx,
        {int(rec.person_id) for rec in adults},
        before_year=int(year),
    )
    previous_predatory_murders = _previous_murder_counts_by_killer(
        ctx,
        {int(rec.person_id) for rec in adults},
        before_year=int(year),
        predatory_only=True,
    )
    serial_candidate_rows = _serial_candidate_rows_for_records(
        ctx,
        adults,
        year=int(year),
    )
    serial_opportunities: dict[int, float] = {}
    serial_propensities: dict[int, float] = {}
    for pid, row in serial_candidate_rows.items():
        if not isinstance(row, dict) or str(row.get("status") or "") != "active":
            continue
        rec = ctx.id_to_record.get(int(pid))
        if rec is None:
            continue
        opportunity = _serial_predation_opportunity_score(
            ctx,
            facts,
            rec,
            settlement_id=settlement_id,
            pressure=pressure,
            previous_predatory_murders=previous_predatory_murders.get(int(pid), 0),
            candidate_row=row,
        )
        serial_opportunities[int(pid)] = opportunity
        serial_propensities[int(pid)] = _serial_predation_effective_score(
            row, opportunity
        )
    max_propensity = max(propensities.values(), default=0.0)
    chance = _murder_chance_from_propensity(
        adults_count=max(len(adults), int(population_count or len(adults))),
        scarcity=scarcity,
        max_propensity=max_propensity,
        rate=rate,
    )
    if chance_roll >= chance:
        return None
    candidate_killers = eligible_records_by_threshold(
        adults, propensities, MURDER_PROPENSITY_THRESHOLD
    )
    if not candidate_killers:
        return None
    killer = _weighted_choice(
        candidate_killers,
        [
            base_weight
            * _repeat_murder_selection_multiplier(
                serial_propensity=serial_propensities.get(int(rec.person_id), 0.0),
                previous_murders=previous_predatory_murders.get(int(rec.person_id), 0),
            )
            for rec, base_weight in zip(
                candidate_killers,
                threshold_excess_weights(
                    candidate_killers, propensities, MURDER_PROPENSITY_THRESHOLD
                ),
            )
        ],
        rng,
    )
    if killer is None:
        return None
    victim_pool = [rec for rec in adults if rec.person_id != killer.person_id]
    victim_weights: list[float] = []
    motives: list[str] = []
    for victim in victim_pool:
        motive, rel_weight = _relationship_motive(ctx, killer, victim)
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
    serial_score = serial_propensities.get(int(killer.person_id), 0.0)
    serial_opportunity = serial_opportunities.get(int(killer.person_id), 0.0)
    serial_row = serial_candidate_rows.get(int(killer.person_id), {})
    incident_kind = _incident_kind(
        ctx,
        killer,
        motive,
        serial_score,
        rng,
    )
    historical_importance = _historical_importance(
        killer, victim, len(witness_ids)
    )
    crime_context = _build_serious_crime_context(
        ctx,
        facts,
        year=int(year),
        killer=killer,
        victim=victim,
        incident_kind=incident_kind,
        motive_category=motive,
        witness_person_ids=witness_ids,
        settlement_id=settlement_id,
        region_id=region_id,
        local_records=adults,
        pressure=pressure,
    )
    return MurderIncident(
        killer=killer,
        victim=victim,
        incident_kind=incident_kind,
        motive=motive,
        witness_person_ids=witness_ids,
        settlement_id=settlement_id,
        region_id=region_id,
        actor_propensity=propensities[killer.person_id],
        serial_predator_propensity=serial_score,
        serial_predation_opportunity_score=serial_opportunity,
        serial_predation_lane=(
            str(serial_row.get("risk_lane") or "none")
            if isinstance(serial_row, dict)
            else "none"
        ),
        serial_predation_candidate_status=(
            str(serial_row.get("status") or "none")
            if isinstance(serial_row, dict)
            else "none"
        ),
        previous_murder_count=previous_murders.get(int(killer.person_id), 0),
        previous_predatory_murder_count=previous_predatory_murders.get(
            int(killer.person_id), 0
        ),
        resource_pressure=pressure,
        historical_importance=historical_importance,
        genome_signals=_genome_signal_payload(killer),
        crime_context=crime_context,
    )


def _property_crime_motive(
    perpetrator: "SimulationPersonRecord", pressure: float
) -> str:
    if (perpetrator.person.housing_status or "").strip().lower() == "street":
        return "survival"
    if pressure >= 1.25:
        return "scarcity"
    if perpetrator.person.unemployment_started_year is not None:
        return "debt_or_hardship"
    if _positive_extreme(perpetrator, "ambition") >= 0.45:
        return "status_gain"
    if _positive_extreme(perpetrator, "frugality") >= 0.45:
        return "hoarding"
    return "opportunity"


def _property_crime_kind(
    ctx: "SimulationContext",
    perpetrator: "SimulationPersonRecord",
    target: "SimulationPersonRecord",
    motive: str,
    rng: random.Random,
) -> str:
    if _positive_extreme(perpetrator, "persuasion") >= 0.45 and _negative_extreme(
        perpetrator, "honesty"
    ) >= 0.35:
        tags = ("fraud", "debt") if motive == "debt_or_hardship" else ("fraud",)
        if str(target.person.partner_person_id or "").strip():
            tags = (*tags, "household")
        return _catalog_incident_kind(
            ctx,
            "property_crime",
            tags=tags,
            default="fraud",
            rng=rng,
        )
    if _positive_extreme(perpetrator, "assertiveness") >= 0.45:
        tags = ("extortion", "market") if target.person.job else ("extortion",)
        return _catalog_incident_kind(
            ctx,
            "property_crime",
            tags=tags,
            default="extortion",
            rng=rng,
        )
    if motive == "hoarding":
        return _catalog_incident_kind(
            ctx,
            "property_crime",
            tags=("hoarding", "theft", "scarcity"),
            default="hoarding_theft",
            rng=rng,
        )
    tags = ("theft",)
    if motive == "survival":
        tags = ("theft", "survival", "street", "scarcity")
    elif motive in {"scarcity", "debt_or_hardship"}:
        tags = ("theft", "survival", "scarcity")
    if float(target.person.job_prosperity_01 or 0.0) >= 0.55:
        tags = (*tags, "valuable_target")
    if (
        float(target.person.household_prosperity or 0.0) >= 3.0
        or float(target.person.social_standing_01 or 0.0) >= 0.65
    ):
        tags = (*tags, "valuable_target")
    if target.person.job:
        tags = (*tags, "market")
    return _catalog_incident_kind(
        ctx,
        "property_crime",
        tags=tags,
        default="theft",
        rng=rng,
    )


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
    ctx: "SimulationContext",
    benefactor: "SimulationPersonRecord",
    beneficiary: "SimulationPersonRecord",
    pressure: float,
    rng: random.Random,
) -> str:
    if _positive_extreme(benefactor, "courage") >= 0.45 or _negative_extreme(
        beneficiary, "physical"
    ) >= 0.45:
        return _catalog_incident_kind(
            ctx,
            "public_virtue",
            tags=("rescue", "danger"),
            default="heroic_rescue",
            rng=rng,
        )
    if pressure >= 1.0 or beneficiary.person.unemployment_started_year is not None:
        tags = ("mercy", "relief", "scarcity") if pressure >= 1.0 else ("mercy", "relief")
        return _catalog_incident_kind(
            ctx,
            "public_virtue",
            tags=tags,
            default="public_mercy",
            rng=rng,
        )
    if _ideal_strength(benefactor, "justice") >= 0.7 and _ideal_strength(
        benefactor, "civics"
    ) >= 0.55:
        tags = ("arbitration", "legal")
        if beneficiary.person.partner_person_id is not None:
            tags = (*tags, "succession")
        return _catalog_incident_kind(
            ctx,
            "public_virtue",
            tags=tags,
            default="public_arbitration",
            rng=rng,
        )
    if _ideal_strength(benefactor, "loyalty") >= 0.65:
        return _catalog_incident_kind(
            ctx,
            "public_virtue",
            tags=("loyal_service", "succession"),
            default="loyal_service",
            rng=rng,
        )
    return _catalog_incident_kind(
        ctx,
        "public_virtue",
        tags=("mercy", "relief"),
        default="public_mercy",
        rng=rng,
    )


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


LEGAL_KNOWLEDGE_KINDS = frozenset(
    {
        "legal_precedent",
        "boundary_judgment",
        "inheritance_judgment",
        "succession_precedent",
        "calendar_reform",
    }
)
ART_KNOWLEDGE_KINDS = frozenset(
    {"artistic_triumph", "famous_performance", "dye_recipe"}
)
DISCOVERY_KNOWLEDGE_KINDS = frozenset(
    {"discovery", "medicinal_discovery", "new_star_record"}
)
SCHOLARLY_KNOWLEDGE_KINDS = frozenset(
    {"scholarly_breakthrough", "calendar_reform", "new_star_record"}
)
INVENTION_KNOWLEDGE_KINDS = frozenset(
    {"invention", "improved_plow", "water_lift", "kiln_improvement", "dye_recipe"}
)
MARITIME_KNOWLEDGE_KINDS = frozenset(
    {"shipbuilding_advance", "navigation_discovery"}
)
MERCANTILE_KNOWLEDGE_KINDS = frozenset(
    {"writing_system", "accounting_method", "trade_law_precedent"}
)
PORTABLE_CRAFT_KNOWLEDGE_KINDS = frozenset(
    {"standard_container", "luxury_dye_recipe"}
)
PORTABLE_MERCANTILE_DOMAINS = frozenset(
    {
        "navigation",
        "shipbuilding",
        "writing",
        "accounting",
        "trade_law",
        "craft",
        "art",
    }
)
_MARITIME_JOB_TOKENS = frozenset(
    {"sail", "ship", "dock", "ferry", "fish", "boat", "navigator", "pilot"}
)
_MERCANTILE_JOB_TOKENS = frozenset(
    {"merchant", "trader", "market", "scribe", "clerk", "account", "admin", "judge", "law"}
)
_PORT_CRAFT_JOB_TOKENS = frozenset(
    {"artisan", "craft", "smith", "potter", "carpenter", "weaver", "dyer"}
)
_COASTAL_TRADE_REGION_TOKENS = frozenset(
    {"coast", "port", "harbor", "harbour", "delta", "fishery", "trade", "bay", "maritime"}
)


def _creator_residence_region_id(
    ctx: "SimulationContext", creator: "SimulationPersonRecord"
) -> str | None:
    sid = (
        creator.person.current_settlement_id
        or creator.person.birthplace_settlement_id
        or ""
    ).strip()
    if sid:
        st = ctx.settlements_by_id.get(sid)
        if st is not None and (st.region_id or "").strip():
            return st.region_id.strip()
    rid = (creator.person.birthplace_region_id or "").strip()
    return rid or None


def _creator_in_maritime_trade_place(
    ctx: "SimulationContext", creator: "SimulationPersonRecord"
) -> bool:
    rid = _creator_residence_region_id(ctx, creator)
    if not rid:
        return False
    try:
        region = get_region(rid, world=ctx.world, db_path=ctx.db_path)
    except LookupError:
        return False
    text = " ".join(
        str(getattr(region, attr, "") or "").lower()
        for attr in ("region_id", "region_name", "biome", "terrain", "keywords")
    )
    if not any(token in text for token in _COASTAL_TRADE_REGION_TOKENS):
        return False
    try:
        return any(
            route.route_type.strip().lower() == "sea"
            for route in list_routes_from(
                rid,
                world=ctx.world,
                db_path=ctx.db_path,
                simulation_year=ctx.current_year,
            )
        )
    except LookupError:
        return False


def _knowledge_culture_kind(
    ctx: "SimulationContext", creator: "SimulationPersonRecord", rng: random.Random
) -> str:
    job = str(creator.person.job or "").strip().lower()
    maritime_job = any(token in job for token in _MARITIME_JOB_TOKENS)
    mercantile_job = any(token in job for token in _MERCANTILE_JOB_TOKENS)
    craft_job = any(token in job for token in _PORT_CRAFT_JOB_TOKENS)
    maritime_place = _creator_in_maritime_trade_place(ctx, creator)
    if maritime_job or maritime_place:
        if any(token in job for token in ("ship", "boat", "carpenter")):
            return _catalog_incident_kind(
                ctx,
                "knowledge_culture",
                tags=("shipbuilding",),
                default="shipbuilding_advance",
                rng=rng,
            )
        if _positive_extreme(creator, "perception") >= 0.35 or _positive_extreme(
            creator, "curiosity"
        ) >= 0.35:
            return _catalog_incident_kind(
                ctx,
                "knowledge_culture",
                tags=("navigation",),
                default="navigation_discovery",
                rng=rng,
            )
    if mercantile_job:
        if any(token in job for token in ("judge", "law")):
            return _catalog_incident_kind(
                ctx,
                "knowledge_culture",
                tags=("trade_law",),
                default="trade_law_precedent",
                rng=rng,
            )
        if any(token in job for token in ("scribe", "clerk", "admin")):
            return _catalog_incident_kind(
                ctx,
                "knowledge_culture",
                tags=("writing",),
                default="writing_system",
                rng=rng,
            )
        return _catalog_incident_kind(
            ctx,
            "knowledge_culture",
            tags=("accounting",),
            default="accounting_method",
            rng=rng,
        )
    if _positive_extreme(creator, "civics") >= 0.45 and _ideal_strength(
        creator, "justice"
    ) >= 0.55:
        tags = ("legal", "succession") if any(
            token in job for token in ("king", "duke", "chief", "judge", "heir")
        ) else ("legal",)
        return _catalog_incident_kind(
            ctx,
            "knowledge_culture",
            tags=tags,
            default="legal_precedent",
            rng=rng,
        )
    if _positive_extreme(creator, "creativity") >= 0.55 and _positive_extreme(
        creator, "wit"
    ) >= 0.35:
        return _catalog_incident_kind(
            ctx,
            "knowledge_culture",
            tags=("art", "performance"),
            default="artistic_triumph",
            rng=rng,
        )
    if _positive_extreme(creator, "perception") >= 0.50 and _positive_extreme(
        creator, "curiosity"
    ) >= 0.45:
        tags = ("discovery", "medicine") if "healer" in str(creator.person.job or "").lower() else ("discovery",)
        return _catalog_incident_kind(
            ctx,
            "knowledge_culture",
            tags=tags,
            default="discovery",
            rng=rng,
        )
    if _positive_extreme(creator, "intellect") >= 0.50 and _positive_extreme(
        creator, "focus"
    ) >= 0.35:
        return _catalog_incident_kind(
            ctx,
            "knowledge_culture",
            tags=("scholarship", "calendar"),
            default="scholarly_breakthrough",
            rng=rng,
        )
    if craft_job and maritime_place:
        if "dyer" in job or _positive_extreme(creator, "creativity") >= 0.45:
            return _catalog_incident_kind(
                ctx,
                "knowledge_culture",
                tags=("luxury_dye",),
                default="luxury_dye_recipe",
                rng=rng,
            )
        return _catalog_incident_kind(
            ctx,
            "knowledge_culture",
            tags=("standard_container",),
            default="standard_container",
            rng=rng,
        )
    tags = ("invention", "craft") if any(
        token in job for token in ("smith", "artisan", "craft", "potter")
    ) else ("invention",)
    return _catalog_incident_kind(
        ctx,
        "knowledge_culture",
        tags=tags,
        default="invention",
        rng=rng,
    )


def _knowledge_domain(kind: str, creator: "SimulationPersonRecord") -> str:
    job = str(creator.person.job or "").strip().lower()
    if kind == "shipbuilding_advance":
        return "shipbuilding"
    if kind == "navigation_discovery":
        return "navigation"
    if kind == "writing_system":
        return "writing"
    if kind == "accounting_method":
        return "accounting"
    if kind == "trade_law_precedent":
        return "trade_law"
    if kind in PORTABLE_CRAFT_KNOWLEDGE_KINDS:
        return "craft"
    if kind in LEGAL_KNOWLEDGE_KINDS:
        return "law"
    if kind in ART_KNOWLEDGE_KINDS:
        return "performance" if "bard" in job or "singer" in job else "art"
    if kind in DISCOVERY_KNOWLEDGE_KINDS:
        return "medicine" if "healer" in job else "natural_history"
    if kind in SCHOLARLY_KNOWLEDGE_KINDS:
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


def _catalog_incident_kind(
    ctx: "SimulationContext",
    event_type: str,
    *,
    tags: tuple[str, ...],
    default: str,
    rng: random.Random,
) -> str:
    try:
        return choose_event_catalog_kind(
            db_path=ctx.db_path,
            event_type=event_type,
            any_tags=tags,
            default=default,
            rng=rng,
        )
    except Exception:
        return str(default)


def _household_member_ids_for_consequence(
    ctx: "SimulationContext", rec: "SimulationPersonRecord", year: int
) -> list[int]:
    try:
        from library.simulation_careers import _household_ids_for_job_move

        return _household_ids_for_job_move(ctx, rec, int(year))
    except Exception:
        return [int(rec.person_id)]


def _household_prosperity_value(
    ctx: "SimulationContext", member_ids: list[int]
) -> float:
    for pid in member_ids:
        rec = ctx.id_to_record.get(int(pid))
        if rec is None:
            continue
        value = rec.person.household_prosperity
        if value is not None:
            return float(value)
    return HOUSEHOLD_CONSEQUENCE_DEFAULT_PROSPERITY


def _apply_household_prosperity_delta(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    *,
    year: int,
    delta: float,
) -> dict[str, object]:
    member_ids = _household_member_ids_for_consequence(ctx, rec, int(year))
    before = _household_prosperity_value(ctx, member_ids)
    after = _clamp(
        before + float(delta),
        HOUSEHOLD_CONSEQUENCE_MIN_PROSPERITY,
        HOUSEHOLD_CONSEQUENCE_MAX_PROSPERITY,
    )
    rounded_after = round(after, 5)
    for pid in member_ids:
        member = ctx.id_to_record.get(int(pid))
        if member is None:
            continue
        member.person = replace(member.person, household_prosperity=rounded_after)
    return {
        "household_member_ids": [int(pid) for pid in member_ids],
        "prosperity_before": round(before, 5),
        "prosperity_after": rounded_after,
        "prosperity_delta": round(after - before, 5),
    }


def _apply_settlement_deltas(
    ctx: "SimulationContext",
    settlement_id: str,
    *,
    prosperity_delta: float = 0.0,
    stability_delta: float = 0.0,
    food_pressure_delta: float = 0.0,
) -> dict[str, object]:
    sid = str(settlement_id or "").strip()
    st = ctx.settlements_by_id.get(sid)
    if st is None:
        return {}
    before_prosperity = float(getattr(st, "prosperity_pool", 1.0) or 0.0)
    before_stability = float(getattr(st, "stability", 0.5) or 0.0)
    before_food = float(getattr(st, "food_pressure", 0.0) or 0.0)
    after_prosperity = _clamp(
        before_prosperity + float(prosperity_delta),
        0.0,
        SETTLEMENT_CONSEQUENCE_MAX_PROSPERITY,
    )
    after_stability = _clamp(before_stability + float(stability_delta), 0.0, 1.0)
    after_food = _clamp(before_food + float(food_pressure_delta), 0.0, 2.0)
    ctx.settlements_by_id[sid] = replace(
        st,
        prosperity_pool=round(after_prosperity, 5),
        stability=round(after_stability, 5),
        food_pressure=round(after_food, 5),
    )
    return {
        "settlement_id": sid,
        "prosperity_pool_before": round(before_prosperity, 5),
        "prosperity_pool_after": round(after_prosperity, 5),
        "prosperity_pool_delta": round(after_prosperity - before_prosperity, 5),
        "stability_before": round(before_stability, 5),
        "stability_after": round(after_stability, 5),
        "stability_delta": round(after_stability - before_stability, 5),
        "food_pressure_before": round(before_food, 5),
        "food_pressure_after": round(after_food, 5),
        "food_pressure_delta": round(after_food - before_food, 5),
    }


def _apply_region_pool_delta(
    ctx: "SimulationContext", region_id: str, delta: float
) -> dict[str, object]:
    rid = str(region_id or "").strip()
    if not rid:
        return {}
    before = float(ctx.region_prosperity_pool.get(rid, 1.0) or 0.0)
    after = _clamp(
        before + float(delta),
        0.0,
        SETTLEMENT_CONSEQUENCE_MAX_PROSPERITY,
    )
    ctx.region_prosperity_pool[rid] = round(after, 5)
    return {
        "region_id": rid,
        "prosperity_pool_before": round(before, 5),
        "prosperity_pool_after": round(after, 5),
        "prosperity_pool_delta": round(after - before, 5),
    }


def _person_faction_key(rec: "SimulationPersonRecord") -> str:
    person = rec.person
    place = (
        person.current_settlement_id
        or person.birthplace_settlement_id
        or person.birthplace_region_id
        or "unknown"
    )
    household_anchor = (
        person.household_purseholder_person_id
        or person.partner_person_id
        or rec.person_id
    )
    return f"household:{place}:{int(household_anchor)}"


def _faction_memory_row(
    *,
    memory_type: str,
    principal: "SimulationPersonRecord",
    opposing: "SimulationPersonRecord | None",
    region_id: str,
    settlement_id: str,
    strength: float,
    polarity: str,
    year: int,
    duration_years: int,
    source_role: str,
    incident_kind: str,
) -> dict[str, object]:
    opposing_id = int(opposing.person_id) if opposing is not None else 0
    return {
        "memory_key": f"{memory_type}:{int(principal.person_id)}:{opposing_id}:{incident_kind}",
        "memory_type": memory_type,
        "status": "active",
        "faction_a_key": _person_faction_key(principal),
        "faction_b_key": _person_faction_key(opposing) if opposing is not None else None,
        "principal_person_id": int(principal.person_id),
        "opposing_person_id": int(opposing.person_id) if opposing is not None else None,
        "polarity": polarity,
        "strength": round(_clamp(strength), 5),
        "start_year": int(year),
        "expected_duration_years": int(duration_years),
        "settlement_id": settlement_id,
        "region_id": region_id,
        "source_role": source_role,
        "incident_kind": incident_kind,
    }


def _apply_murder_consequences(
    ctx: "SimulationContext", year: int, incident: MurderIncident
) -> dict[str, object]:
    memory_type = (
        "blood_feud"
        if incident.incident_kind
        in {"feud_killing", "feud_murder", "kin_killing", "assassination_attempt"}
        or incident.motive in {"kin_conflict", "partner_conflict", "paramour_conflict"}
        else "violent_grievance"
    )
    strength = 0.45 + float(incident.historical_importance) * 0.45
    consequences: dict[str, object] = {
        "faction_memory": [
            _faction_memory_row(
                memory_type=memory_type,
                principal=incident.victim,
                opposing=incident.killer,
                region_id=incident.region_id,
                settlement_id=incident.settlement_id,
                strength=strength,
                polarity="negative",
                year=int(year),
                duration_years=32 if memory_type == "blood_feud" else 18,
                source_role="murder_grievance",
                incident_kind=incident.incident_kind,
            )
        ]
    }
    relationship_closures = _close_murder_victim_relationships(ctx, int(year), incident)
    if relationship_closures:
        consequences["relationship_closures"] = relationship_closures
    outlaw_case = outlaw_case_from_murder(ctx, int(year), incident)
    if outlaw_case is not None:
        consequences["outlaw_case"] = outlaw_case
    return consequences


def _pressure_level(score: float) -> str:
    value = _clamp(float(score or 0.0))
    if value >= 0.82:
        return "intense"
    if value >= 0.62:
        return "high"
    if value >= 0.36:
        return "moderate"
    if value > 0.0:
        return "low"
    return "none"


def _murder_public_case_status(crime_context: dict[str, object] | None) -> str:
    context = crime_context if isinstance(crime_context, dict) else {}
    if not bool(context.get("discovered")):
        return "undiscovered"
    if bool(context.get("offender_identified")) or (
        float(context.get("offender_identity_confidence", 0.0) or 0.0) >= 0.60
    ):
        return "accused_killer"
    if float(context.get("public_suspicion_score", 0.0) or 0.0) >= 0.45:
        return "suspected_murder"
    if bool(context.get("body_or_evidence_found")):
        return "suspicious_death"
    return "unresolved_disappearance"


def _build_murder_justice_response(
    *,
    crime_context: dict[str, object] | None,
    pattern_recognition_score: float,
    public_pattern_recognized: bool,
    hidden_linked_kills: int,
) -> dict[str, object]:
    context = crime_context if isinstance(crime_context, dict) else {}
    witness_pressure = _clamp(float(context.get("witness_pressure_score", 0.0) or 0.0))
    witness_status = _clamp(float(context.get("witness_status_score", 0.0) or 0.0))
    kin_pressure = _clamp(float(context.get("victim_kin_pressure", 0.0) or 0.0))
    authority_capacity = _clamp(float(context.get("authority_capacity", 0.0) or 0.0))
    evidence_strength = _clamp(float(context.get("evidence_strength", 0.0) or 0.0))
    public_suspicion = _clamp(float(context.get("public_suspicion_score", 0.0) or 0.0))
    identity_confidence = _clamp(
        float(context.get("offender_identity_confidence", 0.0) or 0.0)
    )
    justice_pressure = _clamp(float(context.get("justice_pressure_score", 0.0) or 0.0))
    retaliation_risk = _clamp(float(context.get("retaliation_risk_score", 0.0) or 0.0))
    offender_panic_base = _clamp(
        float(context.get("offender_fear_panic_01", 0.0) or 0.0)
    )
    pursuit_pressure = _clamp(
        justice_pressure * 0.38
        + evidence_strength * 0.22
        + identity_confidence * 0.18
        + authority_capacity * 0.12
        + (0.10 if public_pattern_recognized else 0.0)
    )
    accusation_pressure = _clamp(
        identity_confidence * 0.42
        + evidence_strength * 0.28
        + witness_pressure * 0.18
        + authority_capacity * 0.08
        + (0.04 if bool(context.get("offender_seen")) else 0.0)
    )
    kin_vengeance_pressure = _clamp(
        retaliation_risk * 0.55
        + kin_pressure * 0.26
        + witness_status * 0.09
        + (0.10 if bool(context.get("offender_seen")) else 0.0)
    )
    offender_panic_pressure = _clamp(
        pursuit_pressure * 0.42
        + public_suspicion * 0.20
        + accusation_pressure * 0.18
        + offender_panic_base * 0.20
    )
    actions: list[str] = []
    if pursuit_pressure >= 0.36:
        actions.append("investigation_pressure")
    if accusation_pressure >= 0.52:
        actions.append("accusation_pressure")
    if kin_vengeance_pressure >= 0.50:
        actions.append("kin_retaliation_pressure")
    if offender_panic_pressure >= 0.45:
        actions.append("offender_flight_or_panic_pressure")
    if int(hidden_linked_kills) >= 2:
        actions.append("pattern_watch")
    if public_pattern_recognized:
        actions.append("public_pattern_recognition")
    case_status = _murder_public_case_status(context)
    if public_pattern_recognized:
        case_status = "recognized_pattern"
    if int(hidden_linked_kills) >= 3:
        pattern_status = (
            "public_pattern_recognized"
            if public_pattern_recognized
            else "internal_pattern_only"
        )
    elif int(hidden_linked_kills) == 2:
        pattern_status = "linked_pattern_possible"
    else:
        pattern_status = "none"
    return {
        "justice_pressure_score": round(justice_pressure, 5),
        "response_level": _pressure_level(justice_pressure),
        "witness_pressure_score": round(witness_pressure, 5),
        "kin_pressure_score": round(kin_pressure, 5),
        "authority_pressure_score": round(authority_capacity, 5),
        "evidence_strength": round(evidence_strength, 5),
        "pursuit_pressure_score": round(pursuit_pressure, 5),
        "accusation_pressure_score": round(accusation_pressure, 5),
        "kin_vengeance_pressure": round(kin_vengeance_pressure, 5),
        "offender_panic_pressure": round(offender_panic_pressure, 5),
        "public_case_status": case_status,
        "pattern_status": pattern_status,
        "pattern_recognition_score": round(_clamp(pattern_recognition_score), 5),
        "abstract_actions": actions,
    }


def _murder_justice_legal_fallout(
    year: int,
    incident: MurderIncident,
    justice_response: dict[str, object],
    crime_context: dict[str, object] | None,
) -> list[dict[str, object]]:
    context = crime_context if isinstance(crime_context, dict) else {}
    identity_confidence = _clamp(
        float(context.get("offender_identity_confidence", 0.0) or 0.0)
    )
    accusation_pressure = _clamp(
        float(justice_response.get("accusation_pressure_score", 0.0) or 0.0)
    )
    offender_identified = bool(context.get("offender_identified")) or identity_confidence >= 0.60
    if not offender_identified or accusation_pressure < 0.52:
        return []
    killer_id = int(incident.killer.person_id)
    victim_id = int(incident.victim.person_id)
    severity = _clamp(
        0.30
        + float(justice_response.get("justice_pressure_score", 0.0) or 0.0) * 0.36
        + float(justice_response.get("pursuit_pressure_score", 0.0) or 0.0) * 0.20
        + float(incident.historical_importance) * 0.14
    )
    return [
        {
            "fallout_key": f"murder_accusation:{killer_id}:{victim_id}:{int(year)}",
            "fallout_type": "murder_accusation",
            "status": "active",
            "principal_person_id": killer_id,
            "opposing_person_id": victim_id,
            "related_person_id": None,
            "severity": round(severity, 5),
            "start_year": int(year),
            "expected_duration_years": 14,
            "settlement_id": incident.settlement_id,
            "region_id": incident.region_id,
            "source_role": "murder_justice_pressure",
            "incident_kind": incident.incident_kind,
            "justice_response": justice_response,
        }
    ]


def _murder_reputation_marks(
    year: int,
    incident: MurderIncident,
    justice_response: dict[str, object],
    crime_context: dict[str, object] | None,
    *,
    public_pattern_recognized: bool,
) -> list[dict[str, object]]:
    context = crime_context if isinstance(crime_context, dict) else {}
    identity_confidence = _clamp(
        float(context.get("offender_identity_confidence", 0.0) or 0.0)
    )
    if identity_confidence < 0.60 and not public_pattern_recognized:
        return []
    strength = _clamp(
        float(justice_response.get("pursuit_pressure_score", 0.0) or 0.0) * 0.52
        + float(justice_response.get("pattern_recognition_score", 0.0) or 0.0) * 0.28
        + float(incident.historical_importance) * 0.20
    )
    after = "violent-suspect"
    if public_pattern_recognized:
        after = "pattern-suspect"
    elif identity_confidence >= 0.80:
        after = "accused-killer"
    return [
        {
            "mark_key": f"infamy:{int(incident.killer.person_id)}:{int(year)}",
            "person_id": int(incident.killer.person_id),
            "reputation_axis": "infamy",
            "reputation_before": "unknown",
            "reputation_after": after,
            "direction": "negative",
            "mark_strength": round(max(0.05, strength), 5),
            "mark_year": int(year),
            "settlement_id": incident.settlement_id,
            "region_id": incident.region_id,
            "source_role": "murder_public_infamy",
            "public_pattern_recognized": bool(public_pattern_recognized),
        }
    ]


def _append_murder_pattern_memory(
    consequences: dict[str, object],
    *,
    incident: MurderIncident,
    year: int,
    justice_response: dict[str, object],
    public_pattern_recognized: bool,
) -> None:
    if not public_pattern_recognized:
        return
    memory_rows = consequences.setdefault("faction_memory", [])
    if not isinstance(memory_rows, list):
        return
    memory_rows.append(
        _faction_memory_row(
            memory_type="violent_pattern_fear",
            principal=incident.victim,
            opposing=incident.killer,
            region_id=incident.region_id,
            settlement_id=incident.settlement_id,
            strength=0.42
            + float(justice_response.get("pattern_recognition_score", 0.0) or 0.0)
            * 0.42
            + float(incident.historical_importance) * 0.16,
            polarity="negative",
            year=int(year),
            duration_years=24,
            source_role="public_pattern_recognition",
            incident_kind=incident.incident_kind,
        )
    )


def _relationship_update_at_year(
    ctx: "SimulationContext", year: int, method_name: str, a_id: int, b_id: int
) -> dict[str, object]:
    old_year = ctx.current_year
    ctx.current_year = int(year)
    try:
        getattr(ctx, method_name)(int(a_id), int(b_id))
    finally:
        ctx.current_year = old_year
    if ctx._pending_simulation_events:
        return ctx._pending_simulation_events[-1][2]
    return {}


def _close_murder_victim_relationships(
    ctx: "SimulationContext", year: int, incident: MurderIncident
) -> list[dict[str, object]]:
    killer_id = int(incident.killer.person_id)
    victim_id = int(incident.victim.person_id)
    pair_set = {killer_id, victim_id}
    closures: list[dict[str, object]] = []
    coupled = (
        incident.killer.person.partner_person_id == victim_id
        or incident.victim.person.partner_person_id == killer_id
        or any({int(a), int(b)} == pair_set for a, b in ctx.couples)
    )
    if coupled:
        payload = _relationship_update_at_year(
            ctx, int(year), "dissolve_couple", killer_id, victim_id
        )
        if payload:
            payload.update(
                {
                    "breakup_reasons": ["murder"],
                    "breakup_trigger": "murder",
                }
            )
            closures.append({"relationship": "partner", "payload": payload})
    paramours = (
        int(victim_id) in ctx.paramour_ids_for_person(killer_id)
        or int(killer_id) in ctx.paramour_ids_for_person(victim_id)
        or any({int(a), int(b)} == pair_set for a, b in ctx.paramours)
    )
    if paramours:
        payload = _relationship_update_at_year(
            ctx, int(year), "end_paramour_relationship", killer_id, victim_id
        )
        if payload:
            payload.update(
                {
                    "end_reason": "murder",
                    "end_reasons": ["murder"],
                }
            )
            closures.append({"relationship": "paramour", "payload": payload})
    return closures


def _reputation_rank(value: object) -> int:
    text = str(value or "").strip().lower()
    if text in {"high", "strong", "middle-high", "volatile-high"}:
        return 3
    if text in {"medium", "middle-variable", "middle-high but brittle", "middle-high but cold"}:
        return 2
    if text in {"low", "weak", "middle-low", "low-middle", "low-variable", "volatile-low"}:
        return 1
    return 0


def _raise_person_reputation(
    rec: "SimulationPersonRecord",
    *,
    leader_tendency_at_least: str | None = None,
    status_tendency_at_least: str | None = None,
) -> dict[str, object]:
    changes: dict[str, object] = {"person_id": int(rec.person_id)}
    updates: dict[str, object] = {}
    if leader_tendency_at_least:
        before = rec.person.leader_tendency
        if _reputation_rank(before) < _reputation_rank(leader_tendency_at_least):
            updates["leader_tendency"] = leader_tendency_at_least
            changes["leader_tendency_before"] = before
            changes["leader_tendency_after"] = leader_tendency_at_least
    if status_tendency_at_least:
        before = rec.person.status_tendency
        if _reputation_rank(before) < _reputation_rank(status_tendency_at_least):
            updates["status_tendency"] = status_tendency_at_least
            changes["status_tendency_before"] = before
            changes["status_tendency_after"] = status_tendency_at_least
    if updates:
        rec.person = replace(rec.person, **updates)
    return changes if len(changes) > 1 else {}


def _apply_property_crime_consequences(
    ctx: "SimulationContext", year: int, incident: TheftFraudIncident
) -> dict[str, object]:
    loss_delta = -max(0.035, float(incident.loss_value) * 2.2)
    gain_delta = max(0.012, float(incident.loss_value) * 0.65)
    settlement = _apply_settlement_deltas(
        ctx,
        incident.settlement_id,
        prosperity_delta=-float(incident.loss_value) * 0.25,
        stability_delta=-(0.006 + float(incident.loss_value) * 0.06),
    )
    consequences: dict[str, object] = {
        "property_loss": {
            "target": _apply_household_prosperity_delta(
                ctx,
                incident.target,
                year=int(year),
                delta=loss_delta,
            ),
            "perpetrator": _apply_household_prosperity_delta(
                ctx,
                incident.perpetrator,
                year=int(year),
                delta=gain_delta,
            ),
        },
        "settlement": settlement,
        "faction_memory": [
            _faction_memory_row(
                memory_type="property_grievance",
                principal=incident.target,
                opposing=incident.perpetrator,
                region_id=incident.region_id,
                settlement_id=incident.settlement_id,
                strength=0.18
                + float(incident.loss_value) * 2.0
                + float(incident.historical_importance) * 0.25,
                polarity="negative",
                year=int(year),
                duration_years=10,
                source_role="property_crime_grievance",
                incident_kind=incident.incident_kind,
            )
        ],
    }
    if incident.outlaw_case_key:
        consequences["outlaw_case"] = _refresh_existing_outlaw_case_after_property_crime(
            ctx, int(year), incident
        )
    else:
        outlaw_case = outlaw_case_from_property_crime(ctx, int(year), incident)
        if outlaw_case is not None:
            consequences["outlaw_case"] = outlaw_case
    return consequences


def _apply_affair_scandal_consequences(
    ctx: "SimulationContext", year: int, incident: AffairScandalIncident
) -> dict[str, object]:
    ended_paramour = False
    dissolved_couples: list[dict[str, object]] = []
    a_id = int(incident.accused.person_id)
    b_id = int(incident.paramour.person_id)
    pair_set = {a_id, b_id}
    if any({int(x), int(y)} == pair_set for x, y in ctx.paramours):
        payload = _relationship_update_at_year(
            ctx, int(year), "end_paramour_relationship", a_id, b_id
        )
        payload.update(
            {
                "end_reason": "affair_scandal",
                "end_reasons": ["affair_scandal"],
                "source_event": "affair_scandal",
            }
        )
        ended_paramour = True
    for betrayed_id in incident.betrayed_partner_ids:
        betrayed = ctx.id_to_record.get(int(betrayed_id))
        if betrayed is None:
            continue
        partner_id = betrayed.person.partner_person_id
        if partner_id is None or int(partner_id) not in pair_set:
            continue
        payload = _relationship_update_at_year(
            ctx, int(year), "dissolve_couple", int(betrayed_id), int(partner_id)
        )
        payload.update(
            {
                "breakup_reason": "affair_scandal",
                "breakup_reasons": ["affair_scandal"],
                "source_event": "affair_scandal",
            }
        )
        dissolved_couples.append(
            {
                "person_a_id": int(betrayed_id),
                "person_b_id": int(partner_id),
            }
        )
    settlement = _apply_settlement_deltas(
        ctx,
        incident.settlement_id,
        stability_delta=-(0.004 + float(incident.historical_importance) * 0.015),
    )
    consequences: dict[str, object] = {
        "ended_paramour": ended_paramour,
        "dissolved_couples": dissolved_couples,
        "settlement": settlement,
        "faction_memory": [
            _faction_memory_row(
                memory_type="household_scandal_memory",
                principal=incident.accused,
                opposing=incident.paramour,
                region_id=incident.region_id,
                settlement_id=incident.settlement_id,
                strength=0.20 + float(incident.historical_importance) * 0.55,
                polarity="negative",
                year=int(year),
                duration_years=16,
                source_role="affair_scandal_memory",
                incident_kind=incident.incident_kind,
            )
        ],
    }
    legal_fallout = _affair_scandal_legal_fallout(int(year), incident)
    if legal_fallout:
        consequences["legal_fallout"] = legal_fallout
    return consequences


def _affair_scandal_legal_fallout(
    year: int, incident: AffairScandalIncident
) -> list[dict[str, object]]:
    incident_kind = str(incident.incident_kind or "").strip()
    if incident_kind not in LEGAL_FALLOUT_SCANDAL_KINDS:
        return []
    accused_id = int(incident.accused.person_id)
    paramour_id = int(incident.paramour.person_id)
    betrayed_ids = [int(pid) for pid in incident.betrayed_partner_ids]
    opposing_id = betrayed_ids[0] if betrayed_ids else None
    severity = round(
        min(
            1.0,
            max(
                0.06,
                0.22
                + float(incident.historical_importance) * 0.75
                + min(0.15, 0.05 * len(betrayed_ids)),
            ),
        ),
        5,
    )
    if incident_kind == "heir_legitimacy_rumor":
        fallout_type = "heir_legitimacy_challenge"
        fallout_key = f"heir_legitimacy:{accused_id}:{paramour_id}:{opposing_id or 0}"
        duration_years = 18
    else:
        fallout_type = "inheritance_dispute"
        fallout_key = f"inheritance:{accused_id}:{opposing_id or 0}:{paramour_id}"
        duration_years = 8
    return [
        {
            "fallout_key": fallout_key,
            "fallout_type": fallout_type,
            "status": "active",
            "principal_person_id": accused_id,
            "opposing_person_id": opposing_id,
            "related_person_id": paramour_id,
            "severity": severity,
            "start_year": int(year),
            "expected_duration_years": duration_years,
            "settlement_id": incident.settlement_id,
            "region_id": incident.region_id,
            "source_role": "affair_scandal_legal_fallout",
            "incident_kind": incident_kind,
            "betrayed_partner_person_ids": betrayed_ids,
        }
    ]


def _apply_public_virtue_consequences(
    ctx: "SimulationContext", year: int, incident: PublicVirtueIncident
) -> dict[str, object]:
    relief = float(incident.relief_value)
    beneficiary_gain = max(0.04, relief * 1.8)
    benefactor_cost = -min(0.18, max(0.015, relief * 0.45))
    obligation_strength = round(min(1.0, max(0.04, relief * 1.4)), 5)
    settlement = _apply_settlement_deltas(
        ctx,
        incident.settlement_id,
        prosperity_delta=min(0.08, relief * 0.32),
        stability_delta=min(0.05, 0.008 + relief * 0.20),
        food_pressure_delta=-min(0.04, relief * 0.10),
    )
    return {
        "relief": {
            "beneficiary": _apply_household_prosperity_delta(
                ctx,
                incident.beneficiary,
                year=int(year),
                delta=beneficiary_gain,
            ),
            "benefactor": _apply_household_prosperity_delta(
                ctx,
                incident.benefactor,
                year=int(year),
                delta=benefactor_cost,
            ),
        },
        "public_reputation": _raise_person_reputation(
            incident.benefactor,
            leader_tendency_at_least="medium",
        ),
        "obligations": [
            {
                "obligation_key": "beneficiary_to_benefactor",
                "obligation_type": "relief_debt",
                "owed_by_person_id": int(incident.beneficiary.person_id),
                "owed_to_person_id": int(incident.benefactor.person_id),
                "strength": obligation_strength,
                "start_year": int(year),
                "expected_duration_years": 12,
                "settlement_id": incident.settlement_id,
                "region_id": incident.region_id,
                "source_role": "public_virtue_relief",
            }
        ],
        "settlement": settlement,
        "faction_memory": [
            _faction_memory_row(
                memory_type="public_trust",
                principal=incident.beneficiary,
                opposing=incident.benefactor,
                region_id=incident.region_id,
                settlement_id=incident.settlement_id,
                strength=0.12
                + float(incident.relief_value) * 1.4
                + float(incident.historical_importance) * 0.25,
                polarity="positive",
                year=int(year),
                duration_years=20,
                source_role="public_virtue_trust",
                incident_kind=incident.incident_kind,
            )
        ],
    }


def _knowledge_settlement_deltas(incident: KnowledgeCultureIncident) -> tuple[float, float]:
    novelty = float(incident.novelty_value)
    if incident.incident_kind in LEGAL_KNOWLEDGE_KINDS:
        return min(0.04, novelty * 0.08), min(0.06, 0.012 + novelty * 0.18)
    if incident.incident_kind in ART_KNOWLEDGE_KINDS:
        return min(0.07, novelty * 0.28), min(0.04, novelty * 0.10)
    return min(0.09, novelty * 0.35), min(0.035, novelty * 0.08)


def _knowledge_state_diffusion(
    ctx: "SimulationContext",
    year: int,
    incident: KnowledgeCultureIncident,
    primary_delta: float,
) -> list[dict[str, object]]:
    domain = str(incident.knowledge_domain or "").strip()
    if domain not in PORTABLE_MERCANTILE_DOMAINS:
        return []
    try:
        routes = [
            route
            for route in list_routes_from(
                incident.region_id,
                world=ctx.world,
                db_path=ctx.db_path,
                simulation_year=int(year),
            )
            if route.route_type.strip().lower() == "sea"
        ]
    except LookupError:
        return []
    routes.sort(key=lambda route: (float(route.friction), route.to_region_id))
    out: list[dict[str, object]] = []
    seen: set[str] = set()
    for route in routes:
        dest = str(route.to_region_id or "").strip()
        if not dest or dest == incident.region_id or dest in seen:
            continue
        seen.add(dest)
        friction = max(0.0, float(route.friction))
        delta = round(max(0.001, float(primary_delta) * (0.35 / (1.0 + friction * 0.05))), 5)
        if delta <= 0.0:
            continue
        out.append(
            {
                "region_id": dest,
                "domain": domain,
                "state_delta": delta,
                "state_key": f"{dest}:{domain}",
                "source_region_id": incident.region_id,
                "route_type": route.route_type,
                "route_friction": round(friction, 5),
            }
        )
        if len(out) >= 3:
            break
    return out


def _institution_row(
    *,
    institution_type: str,
    focus_domain: str,
    incident: KnowledgeCultureIncident,
    year: int,
    strength_delta: float,
    source_role: str,
) -> dict[str, object]:
    domain = str(focus_domain or incident.knowledge_domain or "knowledge").strip()
    inst_type = str(institution_type or "institution").strip()
    return {
        "institution_key": f"{incident.region_id}:{inst_type}:{domain}",
        "institution_type": inst_type,
        "status": "active",
        "focus_domain": domain,
        "strength_delta": round(max(0.001, float(strength_delta)), 5),
        "influence_delta": round(max(0.001, float(strength_delta) * 0.75), 5),
        "founder_person_id": int(incident.creator.person_id),
        "patron_person_id": (
            int(incident.patron.person_id) if incident.patron is not None else None
        ),
        "founded_year": int(year),
        "settlement_id": incident.settlement_id,
        "region_id": incident.region_id,
        "source_role": source_role,
        "incident_kind": incident.incident_kind,
        "knowledge_domain": incident.knowledge_domain,
    }


def _knowledge_institution_rows(
    year: int,
    incident: KnowledgeCultureIncident,
    primary_delta: float,
) -> list[dict[str, object]]:
    kind = str(incident.incident_kind or "").strip()
    domain = str(incident.knowledge_domain or "").strip()
    base = max(0.01, float(primary_delta))
    rows: list[dict[str, object]] = []
    if kind in SCHOLARLY_KNOWLEDGE_KINDS or domain in {
        "scholarship",
        "calendar",
        "medicine",
        "natural_history",
        "writing",
    }:
        rows.append(
            _institution_row(
                institution_type="school",
                focus_domain=domain,
                incident=incident,
                year=int(year),
                strength_delta=base * 0.85,
                source_role="knowledge_school",
            )
        )
    if kind in LEGAL_KNOWLEDGE_KINDS or domain in {"law", "trade_law", "calendar"}:
        rows.append(
            _institution_row(
                institution_type="doctrine",
                focus_domain=domain,
                incident=incident,
                year=int(year),
                strength_delta=base * 0.95,
                source_role="knowledge_doctrine",
            )
        )
    if (
        kind in MERCANTILE_KNOWLEDGE_KINDS
        or kind in PORTABLE_CRAFT_KNOWLEDGE_KINDS
        or kind in MARITIME_KNOWLEDGE_KINDS
        or domain in {"accounting", "trade_law", "navigation", "shipbuilding", "craft"}
    ):
        rows.append(
            _institution_row(
                institution_type="guild",
                focus_domain=domain,
                incident=incident,
                year=int(year),
                strength_delta=base * 0.80,
                source_role="knowledge_guild",
            )
        )
    if (
        kind in INVENTION_KNOWLEDGE_KINDS
        or kind in PORTABLE_CRAFT_KNOWLEDGE_KINDS
        or domain in {"craft", "toolmaking", "shipbuilding", "art"}
    ):
        rows.append(
            _institution_row(
                institution_type="craft_institution",
                focus_domain=domain,
                incident=incident,
                year=int(year),
                strength_delta=base * 0.90,
                source_role="knowledge_craft_institution",
            )
        )
    return rows


def _apply_knowledge_culture_consequences(
    ctx: "SimulationContext", year: int, incident: KnowledgeCultureIncident
) -> dict[str, object]:
    novelty = float(incident.novelty_value)
    prosperity_delta, stability_delta = _knowledge_settlement_deltas(incident)
    settlement = _apply_settlement_deltas(
        ctx,
        incident.settlement_id,
        prosperity_delta=prosperity_delta,
        stability_delta=stability_delta,
    )
    region = _apply_region_pool_delta(ctx, incident.region_id, min(0.08, novelty * 0.22))
    patronage: dict[str, object] = {}
    obligations: list[dict[str, object]] = []
    if incident.patron is not None:
        grant = max(0.025, novelty * 0.55)
        cost = -min(0.16, grant * 0.55)
        patronage = {
            "creator": _apply_household_prosperity_delta(
                ctx,
                incident.creator,
                year=int(year),
                delta=grant,
            ),
            "patron": _apply_household_prosperity_delta(
                ctx,
                incident.patron,
                year=int(year),
                delta=cost,
            ),
        }
        obligations.append(
            {
                "obligation_key": "creator_to_patron",
                "obligation_type": "patronage_debt",
                "owed_by_person_id": int(incident.creator.person_id),
                "owed_to_person_id": int(incident.patron.person_id),
                "strength": round(min(1.0, max(0.05, novelty * 1.8)), 5),
                "start_year": int(year),
                "expected_duration_years": 20,
                "settlement_id": incident.settlement_id,
                "region_id": incident.region_id,
                "source_role": "knowledge_patronage",
            }
        )
    primary_delta = round(max(0.01, novelty * 0.35), 5)
    return {
        "knowledge_state": {
            "domain": incident.knowledge_domain,
            "state_delta": primary_delta,
            "state_key": f"{incident.region_id}:{incident.knowledge_domain}",
        },
        "knowledge_state_diffusion": _knowledge_state_diffusion(
            ctx, int(year), incident, primary_delta
        ),
        "institutions": _knowledge_institution_rows(
            int(year), incident, primary_delta
        ),
        "patronage": patronage,
        "obligations": obligations,
        "public_reputation": _raise_person_reputation(
            incident.creator,
            status_tendency_at_least="middle-high",
        ),
        "settlement": settlement,
        "region": region,
    }


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


def _scandal_kind(
    ctx: "SimulationContext",
    motive: str,
    betrayed_partner_count: int,
    rng: random.Random,
) -> str:
    if betrayed_partner_count > 1:
        return _catalog_incident_kind(
            ctx,
            "affair_scandal",
            tags=("double_household", "household"),
            default="double_affair_exposed",
            rng=rng,
        )
    if motive == "confession":
        return _catalog_incident_kind(
            ctx,
            "affair_scandal",
            tags=("confession", "household"),
            default="confessed_affair",
            rng=rng,
        )
    if motive == "witnessed_meeting":
        return _catalog_incident_kind(
            ctx,
            "affair_scandal",
            tags=("witnessed", "household"),
            default="affair_witnessed",
            rng=rng,
        )
    tags = ("rumor", "household")
    if motive == "household_rumor":
        tags = (*tags, "succession")
    return _catalog_incident_kind(
        ctx,
        "affair_scandal",
        tags=tags,
        default="affair_exposed",
        rng=rng,
    )


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
    rate: IncidentRateParams | None = None,
    scoring_facts: IncidentScoringFacts | None = None,
) -> AffairScandalIncident | None:
    paramour_ids = _scandal_priority_ids(ctx)
    sampled = _incident_priority_pool(
        ctx,
        residents,
        year=year,
        settlement_id=settlement_id,
        event_key="affair_scandal",
        stream=SCANDAL_SAMPLE_STREAM,
        cap=SCANDAL_SETTLEMENT_SAMPLE_CAP,
        priority_score=lambda rec: _scandal_priority_score(rec, paramour_ids),
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
    facts = scoring_facts or _build_incident_scoring_facts(ctx, year)
    adult_records = list(adult_by_id.values())
    contexts = _incident_context_map(
        ctx,
        facts,
        year=year,
        settlement_id=settlement_id,
        records=adult_records,
        event_family="affair_scandal",
        pressure=pressure,
    )
    propensities = contextual_propensity_by_person_id(
        adult_records, scandal_exposure_propensity, contexts
    )
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
    raw_chance = (
        SCANDAL_BASE_SETTLEMENT_CHANCE
        * (0.45 + population_factor)
        * (1.0 + social_pressure * 1.5)
        * (0.45 + max_score * 2.4)
    )
    chance = min(
        _scaled_chance_cap(SCANDAL_SETTLEMENT_CHANCE_CAP, rate),
        raw_chance * _incident_chance_multiplier(rate),
    )
    if rng.random() >= chance:
        return None
    eligible: list[
        tuple["SimulationPersonRecord", "SimulationPersonRecord", tuple[int, ...]]
    ] = []
    eligible_scores: list[float] = []
    for item, score in zip(candidate_pairs, pair_scores):
        a, b, _betrayed_ids = item
        if score < SCANDAL_PROPENSITY_THRESHOLD:
            continue
        participant_score = max(
            propensities.get(int(a.person_id), 0.0),
            propensities.get(int(b.person_id), 0.0),
        )
        if participant_score < SCANDAL_PROPENSITY_THRESHOLD:
            continue
        eligible.append(item)
        eligible_scores.append(score)
    if not eligible:
        return None
    chosen = rng.choices(
        eligible,
        weights=threshold_excess_value_weights(
            eligible_scores, SCANDAL_PROPENSITY_THRESHOLD
        ),
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
        incident_kind=_scandal_kind(ctx, motive, len(betrayed_ids), rng),
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
                "generosity",
                "perception",
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
    rate: IncidentRateParams | None = None,
    scoring_facts: IncidentScoringFacts | None = None,
) -> PublicVirtueIncident | None:
    pressure = _settlement_pressure(ctx, year, settlement_id)
    hardship = _clamp((pressure - 0.45) / 1.0)
    chance_roll = rng.random()
    if chance_roll >= _scored_family_chance_from_propensity(
        base_chance=VIRTUE_BASE_SETTLEMENT_CHANCE,
        population_denominator=70.0,
        population_base=0.40,
        pressure_base=1.0,
        pressure_factor=hardship,
        pressure_weight=1.7,
        propensity_base=0.35,
        propensity_weight=2.2,
        max_propensity=1.0,
        adults_count=min(len(residents), VIRTUE_SETTLEMENT_SAMPLE_CAP),
        chance_cap=VIRTUE_SETTLEMENT_CHANCE_CAP,
        rate=rate,
    ):
        return None
    sampled = _incident_priority_pool(
        ctx,
        residents,
        year=year,
        settlement_id=settlement_id,
        event_key="public_virtue",
        stream=VIRTUE_SAMPLE_STREAM,
        cap=VIRTUE_SETTLEMENT_SAMPLE_CAP,
        priority_score=_virtue_priority_score,
    )
    adults = [rec for rec in sampled if _adult_alive(rec, year)]
    if len(adults) < 2:
        return None
    facts = scoring_facts or _build_incident_scoring_facts(ctx, year)
    contexts = _incident_context_map(
        ctx,
        facts,
        year=year,
        settlement_id=settlement_id,
        records=adults,
        event_family="public_virtue",
        pressure=pressure,
    )
    propensities = contextual_propensity_by_person_id(
        adults, public_virtue_propensity, contexts
    )
    max_propensity = max(propensities.values(), default=0.0)
    chance = _scored_family_chance_from_propensity(
        base_chance=VIRTUE_BASE_SETTLEMENT_CHANCE,
        population_denominator=70.0,
        population_base=0.40,
        pressure_base=1.0,
        pressure_factor=hardship,
        pressure_weight=1.7,
        propensity_base=0.35,
        propensity_weight=2.2,
        max_propensity=max_propensity,
        adults_count=len(adults),
        chance_cap=VIRTUE_SETTLEMENT_CHANCE_CAP,
        rate=rate,
    )
    if chance_roll >= chance:
        return None
    candidates = eligible_records_by_threshold(
        adults, propensities, VIRTUE_PROPENSITY_THRESHOLD
    )
    if not candidates:
        return None
    benefactor = _weighted_choice(
        candidates,
        threshold_excess_weights(candidates, propensities, VIRTUE_PROPENSITY_THRESHOLD),
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
    incident_kind = _public_virtue_kind(
        ctx, benefactor, beneficiary, pressure, rng
    )
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
    rate: IncidentRateParams | None = None,
    scoring_facts: IncidentScoringFacts | None = None,
) -> KnowledgeCultureIncident | None:
    pressure = _settlement_pressure(ctx, year, settlement_id)
    prosperity_factor = max(0.0, 1.0 - min(1.4, pressure))
    chance_roll = rng.random()
    if chance_roll >= _scored_family_chance_from_propensity(
        base_chance=KNOWLEDGE_BASE_SETTLEMENT_CHANCE,
        population_denominator=90.0,
        population_base=0.35,
        pressure_base=0.75,
        pressure_factor=prosperity_factor,
        pressure_weight=0.25,
        propensity_base=0.35,
        propensity_weight=2.5,
        max_propensity=1.0,
        adults_count=min(len(residents), KNOWLEDGE_SETTLEMENT_SAMPLE_CAP),
        chance_cap=KNOWLEDGE_SETTLEMENT_CHANCE_CAP,
        rate=rate,
    ):
        return None
    sampled = _incident_priority_pool(
        ctx,
        residents,
        year=year,
        settlement_id=settlement_id,
        event_key="knowledge_culture",
        stream=KNOWLEDGE_SAMPLE_STREAM,
        cap=KNOWLEDGE_SETTLEMENT_SAMPLE_CAP,
        priority_score=_knowledge_priority_score,
    )
    adults = [rec for rec in sampled if _adult_alive(rec, year)]
    if len(adults) < 2:
        return None
    facts = scoring_facts or _build_incident_scoring_facts(ctx, year)
    contexts = _incident_context_map(
        ctx,
        facts,
        year=year,
        settlement_id=settlement_id,
        records=adults,
        event_family="knowledge_culture",
        pressure=pressure,
    )
    propensities = contextual_propensity_by_person_id(
        adults, knowledge_culture_propensity, contexts
    )
    max_propensity = max(propensities.values(), default=0.0)
    chance = _scored_family_chance_from_propensity(
        base_chance=KNOWLEDGE_BASE_SETTLEMENT_CHANCE,
        population_denominator=90.0,
        population_base=0.35,
        pressure_base=0.75,
        pressure_factor=prosperity_factor,
        pressure_weight=0.25,
        propensity_base=0.35,
        propensity_weight=2.5,
        max_propensity=max_propensity,
        adults_count=len(adults),
        chance_cap=KNOWLEDGE_SETTLEMENT_CHANCE_CAP,
        rate=rate,
    )
    if chance_roll >= chance:
        return None
    candidates = eligible_records_by_threshold(
        adults, propensities, KNOWLEDGE_PROPENSITY_THRESHOLD
    )
    if not candidates:
        return None
    creator = _weighted_choice(
        candidates,
        threshold_excess_weights(
            candidates, propensities, KNOWLEDGE_PROPENSITY_THRESHOLD
        ),
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
    kind = _knowledge_culture_kind(ctx, creator, rng)
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
    rate: IncidentRateParams | None = None,
    scoring_facts: IncidentScoringFacts | None = None,
) -> TheftFraudIncident | None:
    pressure = _settlement_pressure(ctx, year, settlement_id)
    scarcity = _clamp((pressure - 0.65) / 0.85)
    chance_roll = rng.random()
    if chance_roll >= _scored_family_chance_from_propensity(
        base_chance=THEFT_BASE_SETTLEMENT_CHANCE,
        population_denominator=60.0,
        population_base=0.45,
        pressure_base=1.0,
        pressure_factor=scarcity,
        pressure_weight=2.2,
        propensity_base=0.35,
        propensity_weight=2.4,
        max_propensity=1.0,
        adults_count=min(len(residents), THEFT_SETTLEMENT_SAMPLE_CAP),
        chance_cap=THEFT_SETTLEMENT_CHANCE_CAP,
        rate=rate,
    ):
        return None
    sampled = _incident_priority_pool(
        ctx,
        residents,
        year=year,
        settlement_id=settlement_id,
        event_key="property_crime",
        stream=THEFT_SAMPLE_STREAM,
        cap=THEFT_SETTLEMENT_SAMPLE_CAP,
        priority_score=_property_crime_priority_score,
    )
    adults = [rec for rec in sampled if _adult_alive(rec, year)]
    if len(adults) < 2:
        return None
    facts = scoring_facts or _build_incident_scoring_facts(ctx, year)
    contexts = _incident_context_map(
        ctx,
        facts,
        year=year,
        settlement_id=settlement_id,
        records=adults,
        event_family="property_crime",
        pressure=pressure,
    )
    propensities = contextual_propensity_by_person_id(
        adults, property_crime_propensity, contexts
    )
    max_propensity = max(propensities.values(), default=0.0)
    chance = _scored_family_chance_from_propensity(
        base_chance=THEFT_BASE_SETTLEMENT_CHANCE,
        population_denominator=60.0,
        population_base=0.45,
        pressure_base=1.0,
        pressure_factor=scarcity,
        pressure_weight=2.2,
        propensity_base=0.35,
        propensity_weight=2.4,
        max_propensity=max_propensity,
        adults_count=len(adults),
        chance_cap=THEFT_SETTLEMENT_CHANCE_CAP,
        rate=rate,
    )
    if chance_roll >= chance:
        return None
    candidates = eligible_records_by_threshold(
        adults, propensities, THEFT_PROPENSITY_THRESHOLD
    )
    if not candidates:
        return None
    perpetrator = _weighted_choice(
        candidates,
        threshold_excess_weights(candidates, propensities, THEFT_PROPENSITY_THRESHOLD),
        rng,
    )
    if perpetrator is None:
        return None
    target_pool = [rec for rec in adults if rec.person_id != perpetrator.person_id]
    target_weights = [
        1.0
        + float(rec.person.job_prosperity_01 or 0.35) * 0.75
        + min(1.0, float(rec.person.household_prosperity or 0.0) / 5.0) * 0.65
        + float(rec.person.social_standing_01 or 0.0) * 0.45
        + _ideal_strength(rec, "perception") * 0.15
        for rec in target_pool
    ]
    target = _weighted_choice(target_pool, target_weights, rng)
    if target is None:
        return None
    motive = _property_crime_motive(perpetrator, pressure)
    incident_kind = _property_crime_kind(ctx, perpetrator, target, motive, rng)
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
                "generosity",
                "neurochemical",
                "adaptability",
            ),
        ),
    )


def _eligible_outlaw_property_crime_actor(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    year: int,
) -> bool:
    if rec.person.deathyear is not None and int(rec.person.deathyear) <= int(year):
        return False
    if int(year) - int(rec.person.birthyear) < INCIDENT_ADULT_MIN_AGE:
        return False
    status = str(rec.person.outlaw_status or "").strip().lower()
    if status not in {OUTLAW_STATUS_WANTED, OUTLAW_STATUS_FUGITIVE}:
        return False
    if str(rec.person.outlaw_custody_status or "").strip():
        return False
    case_key = str(rec.person.outlaw_case_key or "").strip()
    if not case_key:
        return False
    case = getattr(ctx, "outlaw_cases", {}).get(case_key)
    if case is None or str(case.status or "").strip().lower() != "active":
        return False
    return True


def _outlaw_property_crime_settlement_id(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
) -> str | None:
    status = str(rec.person.outlaw_status or "").strip().lower()
    case = getattr(ctx, "outlaw_cases", {}).get(str(rec.person.outlaw_case_key or ""))
    candidates: list[str | None] = []
    if status == OUTLAW_STATUS_FUGITIVE:
        refuge = getattr(ctx, "outlaw_refuges", {}).get(
            rec.person.outlaw_refuge_id or getattr(case, "refuge_id", None)
        )
        candidates.extend(
            [
                getattr(refuge, "near_settlement_id", None) if refuge is not None else None,
                rec.person.last_free_settlement_id,
                getattr(case, "settlement_id", None) if case is not None else None,
            ]
        )
    else:
        candidates.extend(
            [
                rec.person.current_settlement_id,
                rec.person.last_free_settlement_id,
                getattr(case, "settlement_id", None) if case is not None else None,
            ]
        )
    for raw in candidates:
        sid = str(raw or "").strip()
        if sid and sid in ctx.settlements_by_id:
            return sid
    return None


def _outlaw_property_crime_status_multiplier(status: str) -> float:
    s = str(status or "").strip().lower()
    if s == OUTLAW_STATUS_FUGITIVE:
        return 4.0
    if s == OUTLAW_STATUS_WANTED:
        return 2.0
    return 1.0


def _outlaw_property_crime_attempt_chance(
    actor_propensity: float,
    *,
    outlaw_status: str,
    pressure: float,
    rate: IncidentRateParams | None = None,
) -> float:
    scarcity = _clamp((float(pressure) - 0.65) / 0.85)
    status_multiplier = _outlaw_property_crime_status_multiplier(outlaw_status)
    raw = (
        THEFT_BASE_SETTLEMENT_CHANCE
        * status_multiplier
        * (1.35 + scarcity * 2.5)
        * (0.45 + _clamp(actor_propensity) * 2.3)
    )
    cap = 0.34 if str(outlaw_status or "").strip().lower() == OUTLAW_STATUS_FUGITIVE else 0.20
    return min(_scaled_chance_cap(cap, rate), raw * _incident_chance_multiplier(rate))


def _outlaw_property_crime_event_limit(
    outlaw_count: int,
    rate: IncidentRateParams | None = None,
) -> int:
    if int(outlaw_count) <= 0:
        return 0
    base = max(1, min(8, (int(outlaw_count) + 14) // 15))
    return max(1, _annual_event_limit(base, rate))


def _outlaw_survival_motive(rec: "SimulationPersonRecord") -> bool:
    return (
        str(rec.person.employment_status or "").strip().lower() == "outlaw"
        or str(rec.person.housing_status or "").strip().lower() == "outlaw_refuge"
        or str(rec.person.household_role or "").strip().lower() == "fugitive"
    )


def _refresh_existing_outlaw_case_after_property_crime(
    ctx: "SimulationContext",
    year: int,
    incident: TheftFraudIncident,
) -> dict[str, object]:
    case_key = str(incident.outlaw_case_key or "").strip()
    case = getattr(ctx, "outlaw_cases", {}).get(case_key)
    if case is None:
        return {
            "case_key": case_key,
            "existing_case": True,
            "outlaw_status": incident.outlaw_status,
        }
    expected = max(int(case.expected_forget_year or year + 3), int(year) + 3)
    case = replace(
        case,
        last_seen_year=int(year),
        expected_forget_year=expected,
        details={
            **(case.details or {}),
            "last_property_crime_year": int(year),
            "last_property_crime_kind": incident.incident_kind,
        },
    )
    ctx.outlaw_cases[case.case_key] = case
    refuge_id = str(case.refuge_id or incident.perpetrator.person.outlaw_refuge_id or "").strip()
    if refuge_id:
        refuge = getattr(ctx, "outlaw_refuges", {}).get(refuge_id)
        if refuge is not None:
            ctx.outlaw_refuges[refuge_id] = replace(refuge, last_activity_year=int(year))
    return {
        "case_key": case.case_key,
        "existing_case": True,
        "outlaw_status": incident.outlaw_status,
        "last_seen_year": int(year),
    }


def _maybe_outlaw_property_crime(
    ctx: "SimulationContext",
    year: int,
    rec: "SimulationPersonRecord",
    *,
    rng: random.Random,
    rate: IncidentRateParams | None = None,
    scoring_facts: IncidentScoringFacts | None = None,
    already_dead: set[int] | None = None,
) -> TheftFraudIncident | None:
    if already_dead and int(rec.person_id) in already_dead:
        return None
    if not _eligible_outlaw_property_crime_actor(ctx, rec, year):
        return None
    settlement_id = _outlaw_property_crime_settlement_id(ctx, rec)
    if not settlement_id:
        return None
    residents = ctx.current_people_by_settlement().get(settlement_id, [])
    target_pool = [
        r
        for r in residents
        if int(r.person_id) != int(rec.person_id)
        and (not already_dead or int(r.person_id) not in already_dead)
        and _adult_alive(r, year)
    ]
    if not target_pool:
        return None
    pressure = _settlement_pressure(ctx, year, settlement_id)
    facts = scoring_facts or _build_incident_scoring_facts(ctx, year)
    records = [rec, *target_pool]
    contexts = _incident_context_map(
        ctx,
        facts,
        year=year,
        settlement_id=settlement_id,
        records=records,
        event_family="property_crime",
        pressure=pressure,
    )
    propensities = contextual_propensity_by_person_id(
        records, property_crime_propensity, contexts
    )
    status = str(rec.person.outlaw_status or "").strip().lower()
    raw_propensity = float(propensities.get(int(rec.person_id), 0.0))
    actor_propensity = _clamp(
        raw_propensity * _outlaw_property_crime_status_multiplier(status)
    )
    if rng.random() >= _outlaw_property_crime_attempt_chance(
        raw_propensity,
        outlaw_status=status,
        pressure=pressure,
        rate=rate,
    ):
        return None
    target_weights = [
        1.0
        + float(target.person.job_prosperity_01 or 0.35) * 0.75
        + min(1.0, float(target.person.household_prosperity or 0.0) / 5.0) * 0.65
        + float(target.person.social_standing_01 or 0.0) * 0.45
        + _ideal_strength(target, "perception") * 0.15
        for target in target_pool
    ]
    target = _weighted_choice(target_pool, target_weights, rng)
    if target is None:
        return None
    motive = "survival" if _outlaw_survival_motive(rec) else _property_crime_motive(rec, pressure)
    incident_kind = _property_crime_kind(ctx, rec, target, motive, rng)
    witness_ids = _choose_witnesses(
        target_pool,
        actor_id=int(rec.person_id),
        target_id=int(target.person_id),
        rng=rng,
    )
    st = ctx.settlements_by_id.get(settlement_id)
    region_id = (
        str(getattr(st, "region_id", "") or "").strip()
        or str(rec.person.birthplace_region_id or "").strip()
    )
    loss_value = _property_crime_loss(rec, target, rng)
    return TheftFraudIncident(
        perpetrator=rec,
        target=target,
        incident_kind=incident_kind,
        motive=motive,
        witness_person_ids=witness_ids,
        settlement_id=settlement_id,
        region_id=region_id,
        actor_propensity=actor_propensity,
        resource_pressure=pressure,
        historical_importance=_property_crime_importance(
            rec,
            target,
            len(witness_ids),
            loss_value,
        ),
        loss_value=loss_value,
        genome_signals=_genome_signal_payload(
            rec,
            (
                "justice",
                "honesty",
                "empathy",
                "persuasion",
                "perception",
                "ambition",
                "frugality",
                "generosity",
                "neurochemical",
                "adaptability",
            ),
        ),
        outlaw_case_key=str(rec.person.outlaw_case_key or "").strip() or None,
        outlaw_status=status,
    )


def _record_murder_incident(
    ctx: "SimulationContext", year: int, incident: MurderIncident
) -> None:
    consequences = _apply_murder_consequences(ctx, int(year), incident)
    crime_context = (
        incident.crime_context.payload()
        if incident.crime_context is not None
        else None
    )
    is_predatory = str(incident.incident_kind or "") == "predatory_murder"
    hidden_linked_kills = (
        int(incident.previous_predatory_murder_count) + 1 if is_predatory else 0
    )
    pattern_recognition_score = 0.0
    suspected_linked_kills = 0
    serial_pattern_status = "none"
    if is_predatory:
        public_suspicion = (
            float(crime_context.get("public_suspicion_score", 0.0) or 0.0)
            if crime_context is not None
            else 0.0
        )
        evidence_strength = (
            float(crime_context.get("evidence_strength", 0.0) or 0.0)
            if crime_context is not None
            else 0.0
        )
        identity_confidence = (
            float(crime_context.get("offender_identity_confidence", 0.0) or 0.0)
            if crime_context is not None
            else 0.0
        )
        pattern_recognition_score = _clamp(
            public_suspicion * 0.45
            + evidence_strength * 0.25
            + min(1.0, hidden_linked_kills / 3.0) * 0.20
            + identity_confidence * 0.10
        )
        if hidden_linked_kills >= 3:
            serial_pattern_status = "internal_serial_murderer"
        elif hidden_linked_kills == 2:
            serial_pattern_status = "linked_pattern_possible"
        if hidden_linked_kills >= 2 and public_suspicion >= 0.45:
            suspected_linked_kills = hidden_linked_kills
    public_pattern_recognized = bool(
        is_predatory
        and hidden_linked_kills >= 3
        and pattern_recognition_score >= 0.65
    )
    if public_pattern_recognized:
        suspected_linked_kills = hidden_linked_kills
    if crime_context is not None:
        crime_context["pattern_recognized"] = public_pattern_recognized
    motive_detail = (
        crime_context.get("motive_detail")
        if crime_context is not None
        else None
    )
    serious_crime_category = murder_taxonomy_category(
        incident_kind=incident.incident_kind,
        motive=incident.motive,
        motive_detail=motive_detail,
        hidden_linked_kill_count=hidden_linked_kills,
        serial_murder_classification=bool(hidden_linked_kills >= 3 and is_predatory),
    )
    serious_crime_label = serious_crime_category_label(serious_crime_category)
    justice_response = _build_murder_justice_response(
        crime_context=crime_context,
        pattern_recognition_score=pattern_recognition_score,
        public_pattern_recognized=public_pattern_recognized,
        hidden_linked_kills=hidden_linked_kills,
    )
    consequences["justice_response"] = justice_response
    legal_fallout = _murder_justice_legal_fallout(
        int(year), incident, justice_response, crime_context
    )
    if legal_fallout:
        consequences.setdefault("legal_fallout", []).extend(legal_fallout)
    reputation_marks = _murder_reputation_marks(
        int(year),
        incident,
        justice_response,
        crime_context,
        public_pattern_recognized=public_pattern_recognized,
    )
    if reputation_marks:
        consequences.setdefault("reputation_marks", []).extend(reputation_marks)
    _append_murder_pattern_memory(
        consequences,
        incident=incident,
        year=int(year),
        justice_response=justice_response,
        public_pattern_recognized=public_pattern_recognized,
    )
    payload = {
        "year": int(year),
        "event_type": "murder",
        "incident_kind": incident.incident_kind,
        "murder_taxonomy": serious_crime_category,
        "serious_crime_category": serious_crime_category,
        "serious_crime_category_label": serious_crime_label,
        "serial_classification_eligible": serial_classification_eligible_category(
            serious_crime_category
        ),
        "motive": incident.motive,
        "killer_person_id": int(incident.killer.person_id),
        "victim_person_id": int(incident.victim.person_id),
        "witness_person_ids": list(incident.witness_person_ids),
        "settlement_id": incident.settlement_id,
        "region_id": incident.region_id,
        "actor_violent_propensity": round(incident.actor_propensity, 5),
        "serial_predator_propensity": round(
            incident.serial_predator_propensity, 5
        ),
        "serial_predation_opportunity_score": round(
            incident.serial_predation_opportunity_score, 5
        ),
        "serial_predation_lane": incident.serial_predation_lane,
        "serial_predation_candidate_status": incident.serial_predation_candidate_status,
        "previous_murder_count": int(incident.previous_murder_count),
        "previous_predatory_murder_count": int(incident.previous_predatory_murder_count),
        "serial_predation_candidate": bool(
            float(incident.serial_predator_propensity) > 0.0
            and incident.serial_predation_candidate_status == "active"
        ),
        "serial_predator_candidate": bool(
            float(incident.serial_predator_propensity) > 0.0
            and incident.serial_predation_candidate_status == "active"
        ),
        "hidden_linked_kill_count": int(hidden_linked_kills),
        "suspected_linked_kill_count": int(suspected_linked_kills),
        "serial_pattern_status": serial_pattern_status,
        "internal_serial_murderer": bool(hidden_linked_kills >= 3 and is_predatory),
        "public_pattern_recognized": public_pattern_recognized,
        "pattern_recognized": public_pattern_recognized,
        "pattern_recognition_score": round(float(pattern_recognition_score), 5),
        "serial_murder_classification": bool(hidden_linked_kills >= 3 and is_predatory),
        "justice_response_level": justice_response["response_level"],
        "public_case_status": justice_response["public_case_status"],
        "pursuit_pressure_score": justice_response["pursuit_pressure_score"],
        "accusation_pressure_score": justice_response["accusation_pressure_score"],
        "kin_vengeance_pressure": justice_response["kin_vengeance_pressure"],
        "offender_panic_pressure": justice_response["offender_panic_pressure"],
        "resource_pressure": round(incident.resource_pressure, 5),
        "historical_importance": round(incident.historical_importance, 5),
        "consequences": consequences,
        "genome_signals": incident.genome_signals,
    }
    if crime_context is not None:
        payload.update(
            {
                "crime_context": crime_context,
                "motive_category": crime_context["motive_category"],
                "motive_detail": crime_context["motive_detail"],
                "motive_prose": crime_context["motive_prose"],
                "justice_pressure_score": crime_context["justice_pressure_score"],
                "retaliation_risk_score": crime_context["retaliation_risk_score"],
                "seen_identified": crime_context["seen_identified"],
                "discovered": crime_context["discovered"],
                "body_or_evidence_found": crime_context["body_or_evidence_found"],
                "offender_seen": crime_context["offender_seen"],
                "offender_identified": crime_context["offender_identified"],
                "witness_status_score": crime_context["witness_status_score"],
                "victim_kin_pressure": crime_context["victim_kin_pressure"],
                "authority_capacity": crime_context["authority_capacity"],
                "evidence_strength": crime_context["evidence_strength"],
                "public_suspicion_score": crime_context["public_suspicion_score"],
                "offender_identity_confidence": crime_context[
                    "offender_identity_confidence"
                ],
                "pattern_recognized": crime_context["pattern_recognized"],
            }
        )
    candidates = getattr(ctx, "serial_predation_candidates", {}) or {}
    if int(incident.killer.person_id) not in candidates:
        _refresh_serial_predation_candidates(
            ctx,
            (incident.killer,),
            year=int(year),
            trigger="successful_serious_crime",
        )
        candidates = getattr(ctx, "serial_predation_candidates", {}) or {}
    row = candidates.get(int(incident.killer.person_id))
    if isinstance(row, dict):
        row = dict(row)
        row["last_serious_crime_year"] = int(year)
        row["hidden_linked_kill_count"] = max(
            int(row.get("hidden_linked_kill_count", 0) or 0),
            int(hidden_linked_kills),
        )
        row["suspected_linked_kill_count"] = max(
            int(row.get("suspected_linked_kill_count", 0) or 0),
            int(suspected_linked_kills),
        )
        row["public_suspicion_score"] = max(
            float(row.get("public_suspicion_score", 0.0) or 0.0),
            float(payload.get("public_suspicion_score", 0.0) or 0.0),
        )
        row["pattern_recognized"] = bool(row.get("pattern_recognized")) or public_pattern_recognized
        row["offender_identity_confidence"] = max(
            float(row.get("offender_identity_confidence", 0.0) or 0.0),
            float(payload.get("offender_identity_confidence", 0.0) or 0.0),
        )
        details = dict(row.get("details") or {}) if isinstance(row.get("details"), dict) else {}
        details["last_serious_crime_trigger"] = "murder"
        details["last_serious_crime_year"] = int(year)
        details["last_opportunity_score"] = round(
            float(incident.serial_predation_opportunity_score or 0.0), 5
        )
        hidden_heat = _clamp(
            float(details.get("hidden_heat_score", 0.0) or 0.0)
            + float(justice_response.get("pursuit_pressure_score", 0.0) or 0.0)
            * 0.24
            + float(justice_response.get("pattern_recognition_score", 0.0) or 0.0)
            * 0.18
            + (0.14 if public_pattern_recognized else 0.0)
        )
        details["hidden_heat_score"] = round(hidden_heat, 5)
        details["justice_response_level"] = justice_response["response_level"]
        details["public_case_status"] = justice_response["public_case_status"]
        details["pursuit_pressure_score"] = justice_response["pursuit_pressure_score"]
        details["offender_panic_pressure"] = justice_response["offender_panic_pressure"]
        details["pattern_status"] = justice_response["pattern_status"]
        details["last_pattern_recognition_score"] = justice_response[
            "pattern_recognition_score"
        ]
        if public_pattern_recognized:
            details["violet_marginalia"] = "public_pattern_recognized"
        row["details"] = details
        candidates[int(incident.killer.person_id)] = row
        ctx.serial_predation_candidates = candidates
    ctx._record_simulation_event(
        int(year),
        "murder",
        payload,
    )


def _record_background_murder_incident(
    ctx: "SimulationContext",
    year: int,
    *,
    settlement_id: str,
    ordinal: int,
) -> None:
    st = getattr(ctx, "settlements_by_id", {}).get(settlement_id)
    ctx._record_simulation_event(
        int(year),
        "murder",
        {
            "year": int(year),
            "event_type": "murder",
            "incident_kind": "background_murder",
            "murder_taxonomy": "ordinary_murder",
            "serious_crime_category": "ordinary_murder",
            "serious_crime_category_label": serious_crime_category_label(
                "ordinary_murder"
            ),
            "serial_classification_eligible": False,
            "background_population_event": True,
            "population_backend": "non_detailed",
            "killer_population": "non_detailed_or_unknown",
            "victim_population": "non_detailed_or_unknown",
            "settlement_id": settlement_id,
            "region_id": getattr(st, "region_id", None) if st is not None else None,
            "background_murder_ordinal": int(ordinal),
            "serial_predator_candidate": False,
            "historical_importance": 0.08,
        },
    )


def _record_property_crime_incident(
    ctx: "SimulationContext", year: int, incident: TheftFraudIncident
) -> None:
    consequences = _apply_property_crime_consequences(ctx, int(year), incident)
    payload = {
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
        "consequences": consequences,
        "genome_signals": incident.genome_signals,
    }
    if incident.outlaw_case_key:
        payload["outlaw_case_key"] = incident.outlaw_case_key
    if incident.outlaw_status:
        payload["outlaw_status"] = incident.outlaw_status
    ctx._record_simulation_event(int(year), "property_crime", payload)
    _refresh_serial_predation_candidates(
        ctx,
        (incident.perpetrator,),
        year=int(year),
        trigger="successful_serious_crime",
    )


def _record_affair_scandal_incident(
    ctx: "SimulationContext", year: int, incident: AffairScandalIncident
) -> None:
    consequences = _apply_affair_scandal_consequences(ctx, int(year), incident)
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
        "consequences": consequences,
        "genome_signals": incident.genome_signals,
    }
    ctx._record_simulation_event(int(year), "affair_scandal", payload)


def _record_public_virtue_incident(
    ctx: "SimulationContext", year: int, incident: PublicVirtueIncident
) -> None:
    consequences = _apply_public_virtue_consequences(ctx, int(year), incident)
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
            "consequences": consequences,
            "genome_signals": incident.genome_signals,
        },
    )


def _record_knowledge_culture_incident(
    ctx: "SimulationContext", year: int, incident: KnowledgeCultureIncident
) -> None:
    consequences = _apply_knowledge_culture_consequences(ctx, int(year), incident)
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
        "consequences": consequences,
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
    background_murder_rng = random.Random(
        y * MURDER_BACKGROUND_RNG_STREAM
        + int(getattr(ctx, "placename_rng_salt", 0))
        + 983
    )
    theft_rng = random.Random(
        y * THEFT_RNG_STREAM + int(getattr(ctx, "placename_rng_salt", 0)) + 1171
    )
    outlaw_theft_rng = random.Random(
        y * (THEFT_RNG_STREAM + 17) + int(getattr(ctx, "placename_rng_salt", 0)) + 1291
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
    outlaw_property_crime_count = 0
    property_crime_actor_ids: set[int] = set()
    scandal_count = 0
    public_virtue_count = 0
    knowledge_culture_count = 0
    settlements = sorted(ctx.current_people_by_settlement().items())
    background_population_by_settlement = ctx.passive_population_counts_by_settlement()
    murder_population_by_settlement = {
        str(settlement_id): len(residents)
        + int(background_population_by_settlement.get(str(settlement_id), 0))
        for settlement_id, residents in settlements
    }
    historical_year = ctx.get_historical_year(y)
    murder_rate = incident_rate_for_year(
        db_path=ctx.db_path,
        world=ctx.world,
        incident_key="murder",
        historical_year=historical_year,
    )
    property_crime_rate = incident_rate_for_year(
        db_path=ctx.db_path,
        world=ctx.world,
        incident_key="property_crime",
        historical_year=historical_year,
    )
    scandal_rate = incident_rate_for_year(
        db_path=ctx.db_path,
        world=ctx.world,
        incident_key="affair_scandal",
        historical_year=historical_year,
    )
    public_virtue_rate = incident_rate_for_year(
        db_path=ctx.db_path,
        world=ctx.world,
        incident_key="public_virtue",
        historical_year=historical_year,
    )
    knowledge_culture_rate = incident_rate_for_year(
        db_path=ctx.db_path,
        world=ctx.world,
        incident_key="knowledge_culture",
        historical_year=historical_year,
    )
    murder_event_limit = _murder_annual_event_cap(
        settlements,
        murder_rate,
        population_by_settlement=murder_population_by_settlement,
    )
    property_crime_event_limit = _annual_event_limit(
        THEFT_MAX_EVENTS_PER_YEAR, property_crime_rate
    )
    scandal_event_limit = _annual_event_limit(SCANDAL_MAX_EVENTS_PER_YEAR, scandal_rate)
    public_virtue_event_limit = _annual_event_limit(
        VIRTUE_MAX_EVENTS_PER_YEAR, public_virtue_rate
    )
    knowledge_culture_event_limit = _annual_event_limit(
        KNOWLEDGE_MAX_EVENTS_PER_YEAR, knowledge_culture_rate
    )
    scoring_facts = _build_incident_scoring_facts(ctx, y)
    current_records = [rec for _settlement_id, residents in settlements for rec in residents]
    serial_trigger_checks = _refresh_serial_predation_event_triggers(
        ctx,
        year=y,
        current_records=current_records,
    )
    if prof:
        simulation_timing.accumulate("incidents.setup", tpc() - t0)
        simulation_timing.record_gauge(
            y,
            "incidents",
            "serial_candidate_trigger_checks",
            len(serial_trigger_checks),
        )
        t0 = tpc()
    for settlement_id, residents in settlements:
        murder_population = int(
            murder_population_by_settlement.get(str(settlement_id), len(residents))
        )
        for _trial in range(
            _murder_settlement_trial_count(
                residents,
                population_count=murder_population,
            )
        ):
            if murder_count >= murder_event_limit:
                break
            incident = _maybe_murder_in_settlement(
                ctx,
                y,
                settlement_id,
                residents,
                rng=rng,
                already_dead=dead_ids,
                rate=murder_rate,
                scoring_facts=scoring_facts,
                population_count=murder_population,
            )
            if incident is None:
                continue
            _record_murder_incident(ctx, y, incident)
            dead_ids.add(int(incident.victim.person_id))
            murder_count += 1
        if property_crime_count < property_crime_event_limit:
            property_crime = _maybe_property_crime_in_settlement(
                ctx,
                y,
                settlement_id,
                residents,
                rng=theft_rng,
                rate=property_crime_rate,
                scoring_facts=scoring_facts,
            )
            if property_crime is not None:
                _record_property_crime_incident(ctx, y, property_crime)
                property_crime_count += 1
                property_crime_actor_ids.add(
                    int(property_crime.perpetrator.person_id)
                )
        if scandal_count < scandal_event_limit:
            scandal = _maybe_affair_scandal_in_settlement(
                ctx,
                y,
                settlement_id,
                residents,
                rng=scandal_rng,
                rate=scandal_rate,
                scoring_facts=scoring_facts,
            )
            if scandal is not None:
                _record_affair_scandal_incident(ctx, y, scandal)
                scandal_count += 1
        if public_virtue_count < public_virtue_event_limit:
            virtue = _maybe_public_virtue_in_settlement(
                ctx,
                y,
                settlement_id,
                residents,
                rng=virtue_rng,
                rate=public_virtue_rate,
                scoring_facts=scoring_facts,
            )
            if virtue is not None:
                _record_public_virtue_incident(ctx, y, virtue)
                public_virtue_count += 1
        if knowledge_culture_count < knowledge_culture_event_limit:
            knowledge = _maybe_knowledge_culture_in_settlement(
                ctx,
                y,
                settlement_id,
                residents,
                rng=knowledge_rng,
                rate=knowledge_culture_rate,
                scoring_facts=scoring_facts,
            )
            if knowledge is not None:
                _record_knowledge_culture_incident(ctx, y, knowledge)
                knowledge_culture_count += 1
    outlaw_crime_candidates = [
        rec
        for rec in ctx.iter_current_people(sorted_by_id=True)
        if int(rec.person_id) not in property_crime_actor_ids
        and _eligible_outlaw_property_crime_actor(ctx, rec, y)
    ]
    outlaw_crime_limit = _outlaw_property_crime_event_limit(
        len(outlaw_crime_candidates), property_crime_rate
    )
    for rec in outlaw_crime_candidates:
        if (
            property_crime_count >= property_crime_event_limit
            or outlaw_property_crime_count >= outlaw_crime_limit
        ):
            break
        property_crime = _maybe_outlaw_property_crime(
            ctx,
            y,
            rec,
            rng=outlaw_theft_rng,
            rate=property_crime_rate,
            scoring_facts=scoring_facts,
            already_dead=dead_ids,
        )
        if property_crime is None:
            continue
        _record_property_crime_incident(ctx, y, property_crime)
        property_crime_count += 1
        outlaw_property_crime_count += 1
        property_crime_actor_ids.add(int(property_crime.perpetrator.person_id))
    detailed_murder_count = murder_count
    population_items = [
        (sid, max(0, int(population)))
        for sid, population in sorted(murder_population_by_settlement.items())
        if int(population) > 0
    ]
    worldwide_murder_target = _murder_worldwide_target_count(
        sum(population for _sid, population in population_items),
        murder_rate,
        background_murder_rng,
    )
    background_murder_count = max(0, int(worldwide_murder_target) - detailed_murder_count)
    if background_murder_count > 0 and population_items:
        settlement_ids = [sid for sid, _population in population_items]
        weights = [population for _sid, population in population_items]
        for ordinal, settlement_id in enumerate(
            background_murder_rng.choices(
                settlement_ids,
                weights=weights,
                k=background_murder_count,
            ),
            start=1,
        ):
            _record_background_murder_incident(
                ctx,
                y,
                settlement_id=settlement_id,
                ordinal=ordinal,
            )
        murder_count += background_murder_count
    if dead_ids:
        ctx.mark_dead(dead_ids, deathyear=y)
    if prof:
        simulation_timing.accumulate("incidents.generate", tpc() - t0)
        simulation_timing.record_gauge(y, "incidents", "murder_events", murder_count)
        simulation_timing.record_gauge(
            y, "incidents", "detailed_murder_events", detailed_murder_count
        )
        simulation_timing.record_gauge(
            y, "incidents", "background_murder_events", background_murder_count
        )
        simulation_timing.record_gauge(
            y, "incidents", "property_crime_events", property_crime_count
        )
        simulation_timing.record_gauge(
            y, "incidents", "outlaw_property_crime_events", outlaw_property_crime_count
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
