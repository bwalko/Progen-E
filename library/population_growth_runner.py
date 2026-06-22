"""Population-growth scenario: founder couples, births, pairing, mortality, yearly summaries."""

from __future__ import annotations

import json
import os
import random
import secrets
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable
from zlib import crc32

import numpy as np

from library.generator import generate_person_random
from library.detailed_population_variance import apply_detailed_selection_variance
from library.passive_population import (
    NONDETAILED_MARRIAGE_PROMOTION_MAX_AGE,
    PassiveCohort,
    build_passive_marriage_candidate_index,
    build_passive_office_candidate_index,
    promote_passive_candidate_for_marriage,
    promote_passive_candidate_for_office,
    promote_passive_candidate_for_settlement_context,
)
from library.nondetailed_population import (
    apply_nondetailed_job_family_economy_effects,
    run_nondetailed_sql_annual_tick_for_save,
    run_nondetailed_sql_migration,
    seed_nondetailed_from_active_settlements,
)
from library.random_names import choose_random_first_last
from library.settlements import SettlementState
from library.reproduction import (
    annual_conception_probability,
    conception_rng,
    having_sex_birth_event,
)
from library.relationship_attraction import (
    deterministic_pair_rng,
    partner_formation_probability_01,
    person_prosperity_01,
    person_relationship_desirability_01,
    relationship_pair_score_01,
    relationship_trait_data_available,
)
from library.simulation_careers import resource_pressure_for_person
from library import simulation_timing
from library.simulation_context import SimulationContext, SimulationPersonRecord
from library.simulation_export import people_export_payload, settlements_geo_export_payload
from library.simulation_mortality import apply_annual_mortality
from library.trait_impacts import trait_category_benefit, trait_category_pressure
from library.world_save import ensure_checkpoint_schema

KIN_PAIR_PARENT_CHILD_PROB = 0.000001
KIN_PAIR_GRANDPARENT_GRANDCHILD_PROB = 0.000002
KIN_PAIR_FULL_SIBLING_PROB = 0.000005
KIN_PAIR_HALF_SIBLING_PROB = 0.00002
KIN_PAIR_RNG_STREAM = 612_047
PARTNER_FORMATION_RNG_STREAM = 712_831
PASSIVE_MARRIAGE_OFFER_RNG_STREAM = 914_591
PAIRING_EXHAUSTIVE_PAIR_LIMIT = 25_000
PAIRING_CANDIDATE_ATTEMPTS_PER_PERSON = 8
PASSIVE_MARRIAGE_PROMOTION_CAP_PER_YEAR = 24
PASSIVE_MIGRATION_CONTEXT_PROMOTION_CAP_PER_YEAR = 12
PASSIVE_MIGRATION_CONTEXT_PROMOTION_CAP_PER_SETTLEMENT = 2
PASSIVE_MIGRATION_CONTEXT_REASONS: frozenset[str] = frozenset(
    {"resource_pressure_migration", "job_seeker_migration"}
)
MIN_DETAILED_RESIDENTS_PER_ACTIVE_SETTLEMENT = 2
PARTNER_FORMATION_MAX_AGE = 70
PARTNER_FORMATION_MAX_AGE_GAP = 35
BIRTH_RELATIONSHIP_SPOUSE = "spouse"
BIRTH_RELATIONSHIP_PARAMOUR = "paramour"
LEGITIMACY_STATUS_LEGITIMATE = "legitimate"
LEGITIMACY_STATUS_BASTARD = "bastard"
DEATH_CAUSE_CHILDBIRTH = "childbirth"
CHILDBIRTH_MORTALITY_BASE_PROBABILITY = 0.006
CHILDBIRTH_MORTALITY_MAX_PROBABILITY = 0.18
CHILDBIRTH_MORTALITY_RNG_STREAM = 827_119

_PASSIVE_JOB_FAMILY_SHARES: tuple[tuple[str, float], ...] = (
    ("farm", 0.46),
    ("labor", 0.24),
    ("craft", 0.12),
    ("trade", 0.08),
    ("service", 0.06),
    ("admin", 0.04),
)
_PASSIVE_AGE_MAX = 90
_PASSIVE_BIRTH_RATE = 0.165
_PASSIVE_PARTNERSHIP_ADULT_SHARE = 0.72
_PASSIVE_PARTNERSHIP_ELDER_SHARE = 0.55


@dataclass(frozen=True)
class ChildbirthMortalityAssessment:
    probability: float
    maternal_age: int
    prior_births: int
    litter_size: int
    resource_pressure: float
    settlement_care_01: float
    prosperity_01: float
    health_pressure_01: float
    health_benefit_01: float
    age_factor: float
    prior_birth_factor: float
    litter_factor: float
    care_factor: float
    prosperity_factor: float
    pressure_factor: float
    health_factor: float


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def resolve_population_sim_seed() -> int:
    raw = os.environ.get("POPULATION_GROWTH_SIM_SEED")
    if raw is not None and str(raw).strip() != "":
        return int(raw)
    s = secrets.randbelow(2_147_483_647)
    os.environ["POPULATION_GROWTH_SIM_SEED"] = str(s)
    return s


def _is_mature(partner: SimulationPersonRecord, year: int) -> bool:
    if partner.person.deathyear is not None and int(partner.person.deathyear) <= int(year):
        return False
    age = year - int(partner.person.birthyear)
    min_fertility_age = partner.person.min_fertility_age
    if min_fertility_age is None:
        return True
    return age >= int(min_fertility_age)


def _partner_formation_age(partner: SimulationPersonRecord, year: int) -> int:
    return int(year) - int(partner.person.birthyear)


def _eligible_for_partner_formation(partner: SimulationPersonRecord, year: int) -> bool:
    if not _is_mature(partner, int(year)):
        return False
    return _partner_formation_age(partner, int(year)) <= PARTNER_FORMATION_MAX_AGE


def _partner_age_gap_allowed(
    a: SimulationPersonRecord,
    b: SimulationPersonRecord,
    year: int,
) -> bool:
    return (
        abs(_partner_formation_age(a, int(year)) - _partner_formation_age(b, int(year)))
        <= PARTNER_FORMATION_MAX_AGE_GAP
    )


def _eligible_for_birth(partner: SimulationPersonRecord, year: int) -> bool:
    if partner.person.deathyear is not None and int(partner.person.deathyear) <= int(year):
        return False
    age = year - int(partner.person.birthyear)
    if not _is_mature(partner, year):
        return False
    max_fertility_age = partner.person.max_fertility_age
    if max_fertility_age is not None and age > int(max_fertility_age):
        return False
    return True


def _mother_prior_birth_count(
    ctx: SimulationContext,
    mother_person_id: int,
    *,
    before_year: int,
) -> int:
    mid = int(mother_person_id)
    y = int(before_year)
    return sum(
        1
        for child in ctx.people
        if child.mother_id == mid and int(child.person.birthyear) < y
    )


def _settlement_birth_care_01(
    ctx: SimulationContext,
    mother: SimulationPersonRecord,
    settlement_id: str | None,
) -> float:
    sid = (
        (settlement_id or "")
        or mother.person.current_settlement_id
        or mother.person.birthplace_settlement_id
        or ""
    ).strip()
    if not sid:
        return 0.45
    st = ctx.settlements_by_id.get(sid)
    if st is None:
        return 0.45
    try:
        live_count = int(ctx.alive_census_cache().count_by_settlement.get(sid, 0))
    except Exception:
        live_count = 0
    residents = max(0, int(getattr(st, "resident_count", 0) or 0), live_count)
    households = max(0, int(getattr(st, "household_cap", 0) or 0))
    population_component = _clamp01(residents / 150.0)
    household_component = _clamp01(households / 25.0)
    stability_component = _clamp01(float(getattr(st, "stability", 0.5) or 0.0))
    prosperity_component = _clamp01(float(getattr(st, "prosperity_pool", 0.5) or 0.0))
    return _clamp01(
        population_component * 0.28
        + household_component * 0.24
        + stability_component * 0.24
        + prosperity_component * 0.24
    )


def _childbirth_maternal_age_factor(age: int) -> float:
    a = int(age)
    factor = 1.0
    if a < 18:
        factor += min(1.2, (18 - a) * 0.16)
    elif a < 22:
        factor += (22 - a) * 0.035
    if a > 35:
        factor += min(1.6, (a - 35) * 0.08)
    return max(0.6, factor)


def _childbirth_prior_birth_factor(prior_births: int) -> float:
    n = max(0, int(prior_births))
    factor = 1.0
    if n == 0:
        factor += 0.08
    if n >= 5:
        factor += min(0.75, (n - 4) * 0.08)
    return factor


def _childbirth_litter_factor(litter_size: int) -> float:
    return 1.0 + max(0, int(litter_size) - 1) * 0.35


def assess_childbirth_mortality(
    ctx: SimulationContext,
    mother: SimulationPersonRecord,
    *,
    year: int,
    resource_pressure: float | None,
    litter_size: int,
    settlement_id: str | None = None,
) -> ChildbirthMortalityAssessment:
    age = int(year) - int(mother.person.birthyear)
    prior_births = _mother_prior_birth_count(
        ctx, mother.person_id, before_year=int(year)
    )
    pressure = max(0.0, float(resource_pressure or 0.0))
    care = _settlement_birth_care_01(ctx, mother, settlement_id)
    prosperity = person_prosperity_01(
        mother.person,
        resource_pressure=pressure,
        default=0.35,
    )
    health_pressure = trait_category_pressure(mother.person, "mortality_health")
    health_benefit = trait_category_benefit(mother.person, "mortality_health")
    age_factor = _childbirth_maternal_age_factor(age)
    prior_factor = _childbirth_prior_birth_factor(prior_births)
    litter_factor = _childbirth_litter_factor(litter_size)
    care_factor = 1.35 - 0.45 * care
    prosperity_factor = 1.25 - 0.35 * prosperity
    pressure_factor = 1.0 + 0.35 * _clamp01((pressure - 0.65) / 1.35)
    health_factor = max(0.35, 1.0 + health_pressure * 1.15 - health_benefit * 0.35)
    probability = CHILDBIRTH_MORTALITY_BASE_PROBABILITY
    for factor in (
        age_factor,
        prior_factor,
        litter_factor,
        care_factor,
        prosperity_factor,
        pressure_factor,
        health_factor,
    ):
        probability *= factor
    probability = min(CHILDBIRTH_MORTALITY_MAX_PROBABILITY, max(0.0, probability))
    return ChildbirthMortalityAssessment(
        probability=probability,
        maternal_age=age,
        prior_births=prior_births,
        litter_size=max(1, int(litter_size)),
        resource_pressure=round(pressure, 5),
        settlement_care_01=round(care, 5),
        prosperity_01=round(float(prosperity), 5),
        health_pressure_01=round(float(health_pressure), 5),
        health_benefit_01=round(float(health_benefit), 5),
        age_factor=round(age_factor, 5),
        prior_birth_factor=round(prior_factor, 5),
        litter_factor=round(litter_factor, 5),
        care_factor=round(care_factor, 5),
        prosperity_factor=round(prosperity_factor, 5),
        pressure_factor=round(pressure_factor, 5),
        health_factor=round(health_factor, 5),
    )


def childbirth_mortality_probability(
    ctx: SimulationContext,
    mother: SimulationPersonRecord,
    *,
    year: int,
    resource_pressure: float | None,
    litter_size: int,
    settlement_id: str | None = None,
) -> float:
    return assess_childbirth_mortality(
        ctx,
        mother,
        year=year,
        resource_pressure=resource_pressure,
        litter_size=litter_size,
        settlement_id=settlement_id,
    ).probability


def childbirth_mortality_rng(
    *,
    year: int,
    sim_seed: int,
    mother_person_id: int,
    father_person_id: int,
) -> random.Random:
    return random.Random(
        int(year) * CHILDBIRTH_MORTALITY_RNG_STREAM
        + int(sim_seed) * 29
        + int(mother_person_id) * 1009
        + int(father_person_id) * 917
        + 41_003
    )


def _maybe_apply_childbirth_mortality(
    ctx: SimulationContext,
    mother: SimulationPersonRecord,
    *,
    father_id: int,
    year: int,
    sim_seed: int,
    resource_pressure: float | None,
    litter_size: int,
    settlement_id: str | None,
    child_ids: list[int],
    birth_relationship_type: str,
    newborn_outcome: str,
) -> ChildbirthMortalityAssessment:
    assessment = assess_childbirth_mortality(
        ctx,
        mother,
        year=year,
        resource_pressure=resource_pressure,
        litter_size=litter_size,
        settlement_id=settlement_id,
    )
    roll = childbirth_mortality_rng(
        year=year,
        sim_seed=sim_seed,
        mother_person_id=mother.person_id,
        father_person_id=father_id,
    ).random()
    if roll >= assessment.probability:
        return assessment
    related_child_id = child_ids[0] if child_ids else None
    ctx.mark_dead(
        {int(mother.person_id)},
        deathyear=int(year),
        cause=DEATH_CAUSE_CHILDBIRTH,
        details="died from childbirth",
        event_payload={
            "mother_person_id": int(mother.person_id),
            "father_person_id": int(father_id),
            "related_child_id": related_child_id,
            "related_child_ids": list(child_ids),
            "birth_relationship_type": birth_relationship_type,
            "newborn_outcome": newborn_outcome,
            "childbirth_mortality_probability": round(assessment.probability, 6),
            "childbirth_mortality_roll": round(float(roll), 6),
            "maternal_age": assessment.maternal_age,
            "prior_births": assessment.prior_births,
            "litter_size": assessment.litter_size,
            "resource_pressure": assessment.resource_pressure,
            "settlement_care_01": assessment.settlement_care_01,
            "maternal_prosperity_01": assessment.prosperity_01,
            "maternal_health_pressure_01": assessment.health_pressure_01,
            "maternal_health_benefit_01": assessment.health_benefit_01,
        },
    )
    ctx.last_childbirth_maternal_deaths_count = (
        int(ctx.last_childbirth_maternal_deaths_count) + 1
    )
    return assessment


def _active_relationship_pair(
    pairs: list[tuple[int, int]] | tuple[tuple[int, int], ...],
    person_a_id: int,
    person_b_id: int,
) -> bool:
    pair_set = {int(person_a_id), int(person_b_id)}
    return any({int(a), int(b)} == pair_set for a, b in pairs)


def _birth_father_candidates(
    ctx: SimulationContext,
    mother: SimulationPersonRecord,
    year: int,
) -> list[tuple[int, str]]:
    """Eligible male fathers for one mother-year, labeled by relationship kind."""
    mother_id = int(mother.person_id)
    candidates: list[tuple[int, str]] = []

    def add_candidate(raw_id: int | None, relationship_type: str) -> None:
        if raw_id is None:
            return
        try:
            father_id = int(raw_id)
        except (TypeError, ValueError):
            return
        if father_id == mother_id or not ctx.is_alive(father_id):
            return
        father = ctx.id_to_record.get(father_id)
        if father is None or not _eligible_for_birth(father, year):
            return
        if (father.person.gender or "").strip().lower() != "male":
            return
        if relationship_type == BIRTH_RELATIONSHIP_SPOUSE:
            if not _active_relationship_pair(ctx.couples, mother_id, father_id):
                return
            if int(father.person.partner_person_id or 0) != mother_id:
                return
        elif relationship_type == BIRTH_RELATIONSHIP_PARAMOUR:
            if not _active_relationship_pair(ctx.paramours, mother_id, father_id):
                return
            if int(father.person.paramour_person_id or 0) != mother_id:
                return
        else:
            return
        candidates.append((father_id, relationship_type))

    add_candidate(mother.person.partner_person_id, BIRTH_RELATIONSHIP_SPOUSE)
    add_candidate(mother.person.paramour_person_id, BIRTH_RELATIONSHIP_PARAMOUR)
    return candidates


def _parents_of(rec: SimulationPersonRecord) -> set[int]:
    return {int(pid) for pid in (rec.father_id, rec.mother_id) if pid is not None}


def _is_parent_child(a: SimulationPersonRecord, b: SimulationPersonRecord) -> bool:
    return int(a.person_id) in _parents_of(b) or int(b.person_id) in _parents_of(a)


def _is_grandparent_grandchild(
    ctx: SimulationContext, a: SimulationPersonRecord, b: SimulationPersonRecord
) -> bool:
    def grandparents_of(rec: SimulationPersonRecord) -> set[int]:
        out: set[int] = set()
        for parent_id in _parents_of(rec):
            parent = ctx.id_to_record.get(parent_id)
            if parent is not None:
                out.update(_parents_of(parent))
        return out

    return int(a.person_id) in grandparents_of(b) or int(b.person_id) in grandparents_of(a)


def _sibling_kind(a: SimulationPersonRecord, b: SimulationPersonRecord) -> str | None:
    shared = _parents_of(a).intersection(_parents_of(b))
    if len(shared) >= 2:
        return "full_sibling"
    if len(shared) == 1:
        return "half_sibling"
    return None


def _close_kin_pairing_exception_probability(
    ctx: SimulationContext, a: SimulationPersonRecord, b: SimulationPersonRecord
) -> tuple[str | None, float]:
    if _is_parent_child(a, b):
        return "parent_child", KIN_PAIR_PARENT_CHILD_PROB
    if _is_grandparent_grandchild(ctx, a, b):
        return "grandparent_grandchild", KIN_PAIR_GRANDPARENT_GRANDCHILD_PROB
    sibling = _sibling_kind(a, b)
    if sibling == "full_sibling":
        return sibling, KIN_PAIR_FULL_SIBLING_PROB
    if sibling == "half_sibling":
        return sibling, KIN_PAIR_HALF_SIBLING_PROB
    return None, 1.0


def _kin_pairing_rng(ctx: SimulationContext, year: int, a_id: int, b_id: int) -> random.Random:
    lo, hi = sorted((int(a_id), int(b_id)))
    return random.Random(
        int(year) * KIN_PAIR_RNG_STREAM
        + int(getattr(ctx, "placename_rng_salt", 0)) * 37
        + lo * 10_033
        + hi
    )


def _pairing_allowed_by_kinship(
    ctx: SimulationContext, year: int, a: SimulationPersonRecord, b: SimulationPersonRecord
) -> tuple[bool, str | None, float | None]:
    relation, probability = _close_kin_pairing_exception_probability(ctx, a, b)
    if relation is None:
        return True, None, None
    rng = _kin_pairing_rng(ctx, year, int(a.person_id), int(b.person_id))
    return rng.random() < probability, relation, probability


def _record_prosperity_01(
    ctx: SimulationContext,
    rec: SimulationPersonRecord,
    *,
    resource_facts=None,
) -> float:
    try:
        pressure = resource_pressure_for_person(
            ctx, rec, resource_facts=resource_facts
        )
    except (FileNotFoundError, LookupError):
        pressure = None
    return person_prosperity_01(
        rec.person,
        resource_pressure=pressure,
    )


def _partner_pair_attraction_score_01(
    ctx: SimulationContext,
    year: int,
    a: SimulationPersonRecord,
    b: SimulationPersonRecord,
    *,
    resource_facts=None,
) -> float:
    return relationship_pair_score_01(
        a.person,
        b.person,
        int(year),
        prosperity_a_01=_record_prosperity_01(ctx, a, resource_facts=resource_facts),
        prosperity_b_01=_record_prosperity_01(ctx, b, resource_facts=resource_facts),
    )


def _partner_pair_formation_probability(
    ctx: SimulationContext,
    year: int,
    a: SimulationPersonRecord,
    b: SimulationPersonRecord,
    *,
    resource_facts=None,
) -> tuple[float, float]:
    if (
        not relationship_trait_data_available(a.person)
        and not relationship_trait_data_available(b.person)
    ):
        return 1.0, 0.68
    score = _partner_pair_attraction_score_01(
        ctx, int(year), a, b, resource_facts=resource_facts
    )
    return partner_formation_probability_01(score), score


def _passive_marriage_offer_probability(
    ctx: SimulationContext,
    year: int,
    rec: SimulationPersonRecord,
    *,
    resource_facts=None,
) -> tuple[float, float]:
    if not relationship_trait_data_available(rec.person):
        return 1.0, 0.68
    prosperity = _record_prosperity_01(ctx, rec, resource_facts=resource_facts)
    score = person_relationship_desirability_01(
        rec.person,
        int(year),
        prosperity_01=prosperity,
    )
    return partner_formation_probability_01(score), score


def _format_government_report_appendix(ctx: SimulationContext) -> list[str]:
    """Short polity / ruler / campaign summary from end-of-run RAM (optional report block)."""
    pols = getattr(ctx, "gov_polities", None) or {}
    seats = getattr(ctx, "gov_office_seats", None) or {}
    terr = getattr(ctx, "gov_territory_rows", None) or []
    camps = getattr(ctx, "gov_campaigns", None) or []
    lines: list[str] = [
        "",
        "Government (end-of-run RAM state)",
        "----------------------------",
        f"Polities loaded: {len(pols)}",
        f"Open territory rows: {len(terr)}",
        f"Office seats: {len(seats)}",
        f"Campaigns (all outcomes): {len(camps)}",
    ]
    active_pol = [
        p for p in pols.values() if (getattr(p, "status", "") or "").strip().lower() == "active"
    ]
    lines.append(f"Active polities: {len(active_pol)}")
    for pol in sorted(active_pol, key=lambda p: int(p.polity_id)):
        lines.append(
            " | ".join(
                (
                    f"polity_id={pol.polity_id}",
                    f"name={pol.name}",
                    f"type={pol.polity_type_id}",
                    f"parent={pol.parent_polity_id}",
                    f"founded={pol.founded_sim_year}",
                )
            )
        )
    held = [s for s in seats.values() if s.holder_person_id is not None]
    lines.append(f"Seats with holder: {len(held)}")
    for s in sorted(held, key=lambda x: int(x.seat_id)):
        rec = ctx.id_to_record.get(int(s.holder_person_id or 0))
        nm = rec.person.full_name if rec is not None else "?"
        lines.append(
            " | ".join(
                (
                    f"seat_id={s.seat_id}",
                    f"polity_id={s.polity_id}",
                    f"title_id={s.title_id}",
                    f"holder_id={s.holder_person_id}",
                    f"holder_name={nm}",
                )
            )
        )
    ongoing = [
        c for c in camps if (getattr(c, "outcome", "") or "").strip().lower() == "ongoing"
    ]
    lines.append(f"Campaigns ongoing: {len(ongoing)}")
    for c in sorted(ongoing, key=lambda x: int(x.campaign_id)):
        lines.append(
            " | ".join(
                (
                    f"campaign_id={c.campaign_id}",
                    f"kind={c.kind}",
                    f"attacker={c.attacker_polity_id}",
                    f"defender={c.defender_polity_id}",
                    f"started={c.start_sim_year}",
                )
            )
        )
    return lines


def _fertile_founder_age_bounds(person, fallback_age: int = 18) -> tuple[int, int]:
    p = person
    lo = int(p.min_fertility_age) if p.min_fertility_age is not None else fallback_age
    hi = int(p.max_fertility_age) if p.max_fertility_age is not None else max(lo, fallback_age)
    if hi < lo:
        hi = lo
    return lo, hi


def _founder_parent_name(
    ctx: SimulationContext,
    *,
    ethnic: str,
    gender: str,
    birthplace: str,
    birthplace_region_id: str | None,
) -> str:
    first, last = choose_random_first_last(
        ethnic=ethnic,
        gender=gender,
        birthplace=birthplace,
        db_path=ctx.db_path,
        birthplace_region_id=birthplace_region_id,
        world=ctx.world,
        simulation_context=ctx,
    )
    return f"{first} {last}".strip()


def _with_founder_parent_names(
    ctx: SimulationContext,
    person,
) -> object:
    return replace(
        person,
        father_name=_founder_parent_name(
            ctx,
            ethnic=person.ethnic,
            gender="Male",
            birthplace=person.birthplace,
            birthplace_region_id=person.birthplace_region_id,
        ),
        mother_name=_founder_parent_name(
            ctx,
            ethnic=person.ethnic,
            gender="Female",
            birthplace=person.birthplace,
            birthplace_region_id=person.birthplace_region_id,
        ),
    )


def generate_population_founder(
    ctx: SimulationContext,
    *,
    gender: str,
    simulation_year: int,
    rng: random.Random,
    person_id_seed: int = 0,
):
    """Generate a founder with age inside that person's fertility window."""
    def selected_founder(person):
        with_parent_names = _with_founder_parent_names(ctx, person)
        return apply_detailed_selection_variance(
            with_parent_names,
            person_id=int(person_id_seed),
            year=int(simulation_year),
            reason="founder",
            source={
                "source_kind": "founder",
                "gender": gender,
                "birthyear": int(with_parent_names.birthyear),
                "settlement_id": with_parent_names.current_settlement_id,
                "name": with_parent_names.full_name,
            },
        )

    probe = generate_person_random(
        gender=gender,
        age=18,
        simulation_year=simulation_year,
        simulation_context=ctx,
    )
    lo, hi = _fertile_founder_age_bounds(probe)
    for _ in range(64):
        age = rng.randint(lo, hi)
        founder = generate_person_random(
            species=probe.species,
            ethnic=probe.ethnic,
            gender=gender,
            age=age,
            simulation_year=simulation_year,
            simulation_context=ctx,
        )
        f_lo, f_hi = _fertile_founder_age_bounds(founder)
        actual_age = int(simulation_year) - int(founder.birthyear)
        if f_lo <= actual_age <= f_hi:
            return selected_founder(founder)
    fallback_age = max(lo, min(hi, int(probe.min_fertility_age or lo)))
    founder = generate_person_random(
        species=probe.species,
        ethnic=probe.ethnic,
        gender=gender,
        age=fallback_age,
        simulation_year=simulation_year,
        simulation_context=ctx,
    )
    return selected_founder(founder)


def _geo_summary(local_geography_json: str | None) -> str:
    if not local_geography_json:
        return ""
    try:
        d = json.loads(local_geography_json)
        nf = len(d.get("features") or [])
        ns = len(d.get("settlements") or [])
        ne = len(d.get("edges") or [])
        return f" abstract_geo[features={nf} pins={ns} edges={ne}]"
    except json.JSONDecodeError:
        return " abstract_geo[invalid_json]"


def build_population_growth_report(
    people: list[SimulationPersonRecord],
    couples: list[tuple[int, int]],
    settlements_by_id: dict[str, SettlementState],
    *,
    random_seed: int,
    start_year: int,
    duration_years: int,
    ctx: SimulationContext | None = None,
) -> str:
    couples_by_member: dict[int, int] = {}
    for a_id, b_id in couples:
        couples_by_member[a_id] = b_id
        couples_by_member[b_id] = a_id

    def cur_residence_region(rec: SimulationPersonRecord) -> str:
        p = rec.person
        sid = (p.current_settlement_id or p.birthplace_settlement_id or "").strip()
        if not sid:
            return ""
        st = settlements_by_id.get(sid)
        if st is not None:
            return (st.region_id or "").strip()
        if ":" in sid:
            return sid.split(":")[0].strip()
        return ""

    lines: list[str] = []
    lines.append("Population Growth Simulation Report")
    lines.append(f"Random seed: {random_seed}")
    lines.append(f"Start year: {start_year}")
    lines.append(f"Duration: {duration_years} years")
    lines.append(f"Total people: {len(people)}")
    lines.append(f"Total couples: {len(couples)}")
    if people:
        end_year = start_year + duration_years - 1
        alive = [
            rec
            for rec in people
            if rec.person.deathyear is None or int(rec.person.deathyear) > end_year
        ]
        dead = [
            rec
            for rec in people
            if rec.person.deathyear is not None and int(rec.person.deathyear) <= end_year
        ]
        lines.append(f"Alive at end year: {len(alive)}")
        lines.append(f"Dead by end year: {len(dead)}")
        if alive:
            alive_cross = sum(
                1
                for rec in alive
                if (cur_residence_region(rec) or "").strip()
                != (rec.person.birthplace_region_id or "").strip()
            )
            lines.append(
                f"Alive with residence region != birth region (incl. migration): {alive_cross}"
            )
            alive_avg = sum(end_year - int(rec.person.birthyear) for rec in alive) / len(alive)
            lines.append(f"Average age alive at end year: {alive_avg:.2f}")
        if dead:
            dead_avg = (
                sum(int(rec.person.deathyear or 0) - int(rec.person.birthyear) for rec in dead)
                / len(dead)
            )
            lines.append(f"Average age at death: {dead_avg:.2f}")
    lines.append("")
    lines.append("People")
    lines.append("------")

    for rec in people:
        p = rec.person
        partner_id = couples_by_member.get(rec.person_id)
        cur_reg = cur_residence_region(rec)
        lines.append(
            " | ".join(
                (
                    f"id={rec.person_id}",
                    f"name={p.full_name}",
                    f"gender={p.gender}",
                    f"species={p.species}",
                    f"ethnic={p.ethnic}",
                    f"birthyear={p.birthyear}",
                    f"deathyear={p.deathyear}",
                    f"birthplace={p.birthplace}",
                    f"birth_region={p.birthplace_region_id}",
                    f"birth_settlement={p.birthplace_settlement_id}",
                    f"current_region={cur_reg}",
                    f"current_settlement={p.current_settlement_id}",
                    f"founder={rec.is_founder}",
                    f"father_id={rec.father_id}",
                    f"mother_id={rec.mother_id}",
                    f"father_name={p.father_name}",
                    f"mother_name={p.mother_name}",
                    f"partner_id={partner_id}",
                )
            )
        )
    lines.append("")
    lines.append("Places (generated settlement names + abstract local geography)")
    lines.append("--------------------------------------------------------------")
    for sid in sorted(settlements_by_id.keys()):
        st = settlements_by_id[sid]
        rid = st.region_id
        dn = st.display_name or ""
        et = st.etymology or ""
        geo = _geo_summary(st.local_geography_json)
        lines.append(
            " | ".join(
                (
                    f"settlement_id={sid}",
                    f"region_id={rid}",
                    f"region_display_name={st.region_display_name}",
                    f"display_name={dn}",
                    f"level={st.level}",
                    f"resident_count={st.resident_count}",
                    f"household_cap={st.household_cap}",
                    f"name_category_primary={st.name_category_primary}",
                    f"name_category_secondary={st.name_category_secondary}",
                    f"name_culture_primary={st.name_culture_primary}",
                    f"name_culture_secondary={st.name_culture_secondary}",
                    f"etymology={et}",
                )
            )
            + geo
        )
    if ctx is not None:
        lines.extend(_format_government_report_appendix(ctx))
    return "\n".join(lines) + "\n"


def _pair_from_records(
    ctx: SimulationContext,
    records: list[SimulationPersonRecord],
    year: int,
    paired_ids: set[int],
    resource_facts=None,
) -> None:
    prof = simulation_timing.active_for_year(year)
    tpc = time.perf_counter
    if prof:
        t0 = tpc()
    eligible_males = [
        r
        for r in records
        if (not r.is_founder)
        and r.person.gender == "Male"
        and r.person_id not in paired_ids
        and _eligible_for_partner_formation(r, year)
    ]
    eligible_females = [
        r
        for r in records
        if (not r.is_founder)
        and r.person.gender == "Female"
        and r.person_id not in paired_ids
        and _eligible_for_partner_formation(r, year)
    ]
    if prof:
        simulation_timing.accumulate("pairing.collect_eligible", tpc() - t0)
        t0 = tpc()
    remaining_females = list(eligible_females)
    pair_count = len(eligible_males) * len(remaining_females)
    bounded = pair_count > PAIRING_EXHAUSTIVE_PAIR_LIMIT
    rng = random.Random(
        int(year) * 1_300_003
        + int(getattr(ctx, "placename_rng_salt", 0)) * 43
        + len(eligible_males) * 101
        + len(remaining_females)
    )
    for male in eligible_males:
        chosen: tuple[
            SimulationPersonRecord,
            str | None,
            float | None,
            float,
            float,
        ] | None = None
        if bounded:
            attempts = min(PAIRING_CANDIDATE_ATTEMPTS_PER_PERSON, len(remaining_females))
            candidates = rng.sample(remaining_females, attempts) if attempts else []
        else:
            candidates = remaining_females
        candidate_scores: list[
            tuple[float, float, int, SimulationPersonRecord, str | None, float | None]
        ] = []
        for female in candidates:
            if not _partner_age_gap_allowed(male, female, int(year)):
                continue
            allowed, relation, probability = _pairing_allowed_by_kinship(
                ctx, year, male, female
            )
            if not allowed:
                continue
            formation_probability, attraction_score = _partner_pair_formation_probability(
                ctx,
                int(year),
                male,
                female,
                resource_facts=resource_facts,
            )
            candidate_scores.append(
                (
                    attraction_score,
                    formation_probability,
                    -int(female.person_id),
                    female,
                    relation,
                    probability,
                )
            )
        if candidate_scores:
            candidate_scores.sort(reverse=True)
            attraction_score, formation_probability, _, female, relation, probability = (
                candidate_scores[0]
            )
            prng = deterministic_pair_rng(
                int(year),
                int(getattr(ctx, "placename_rng_salt", 0)),
                int(male.person_id),
                int(female.person_id),
                stream=PARTNER_FORMATION_RNG_STREAM,
            )
            if prng.random() >= formation_probability:
                continue
            chosen = (
                female,
                relation,
                probability,
                attraction_score,
                formation_probability,
            )
        if chosen is None:
            continue
        female, relation, probability, attraction_score, formation_probability = chosen
        a_id = male.person_id
        b_id = female.person_id
        ctx.add_couple(a_id, b_id)
        ctx._pending_simulation_events[-1][2].update(
            {
                "attraction_fit_score": round(attraction_score, 4),
                "formation_probability": round(formation_probability, 4),
            }
        )
        if relation is not None:
            ctx._pending_simulation_events[-1][2].update(
                {
                    "kinship_exception": relation,
                    "kinship_exception_probability": probability,
                }
            )
        paired_ids.add(a_id)
        paired_ids.add(b_id)
        remaining_females.remove(female)
    if prof:
        simulation_timing.accumulate("pairing.match_loop", tpc() - t0)


def _opposite_binary_gender(gender: str | None) -> str | None:
    raw = (gender or "").strip().lower()
    if raw.startswith("m"):
        return "Female"
    if raw.startswith("f"):
        return "Male"
    return None


def _promote_passive_spouses_for_unpaired_detailed(
    ctx: SimulationContext,
    year: int,
    by_settlement: dict[str, list[SimulationPersonRecord]],
    paired_ids: set[int],
    resource_facts=None,
) -> None:
    prof = simulation_timing.active_for_year(year)
    tpc = time.perf_counter
    if prof:
        t0 = tpc()
    candidate_index = build_passive_marriage_candidate_index(ctx)
    if prof:
        simulation_timing.accumulate("pairing.passive_promote.index", tpc() - t0)
        t0 = tpc()
    promotions = 0
    for sid in sorted(by_settlement.keys()):
        st = ctx.settlements_by_id.get(sid)
        rid = (st.region_id or "").strip() if st is not None else ""
        records = sorted(by_settlement[sid], key=lambda rec: int(rec.person_id))
        for rec in records:
            if promotions >= PASSIVE_MARRIAGE_PROMOTION_CAP_PER_YEAR:
                if prof:
                    simulation_timing.accumulate("pairing.passive_promote.scan", tpc() - t0)
                return
            if (
                rec.is_founder
                or rec.person_id in paired_ids
                or not _eligible_for_partner_formation(rec, year)
            ):
                continue
            offer_probability, attraction_score = _passive_marriage_offer_probability(
                ctx,
                int(year),
                rec,
                resource_facts=resource_facts,
            )
            offer_rng = deterministic_pair_rng(
                int(year),
                int(getattr(ctx, "placename_rng_salt", 0)),
                int(rec.person_id),
                0,
                stream=PASSIVE_MARRIAGE_OFFER_RNG_STREAM,
            )
            if offer_rng.random() >= offer_probability:
                continue
            spouse_gender = _opposite_binary_gender(rec.person.gender)
            if spouse_gender is None:
                continue
            min_age = int(rec.person.min_fertility_age or 16)
            source_age = _partner_formation_age(rec, int(year))
            spouse_max_age = min(
                NONDETAILED_MARRIAGE_PROMOTION_MAX_AGE,
                source_age + PARTNER_FORMATION_MAX_AGE_GAP,
            )
            promoted = promote_passive_candidate_for_marriage(
                ctx,
                year=int(year),
                gender=spouse_gender,
                settlement_id=sid,
                region_id=rid or None,
                min_age=min_age,
                max_age=spouse_max_age,
                source={
                    "detailed_person_id": int(rec.person_id),
                    "detailed_settlement_id": sid,
                },
                candidate_index=candidate_index,
            )
            if promoted is None:
                continue
            ctx.add_couple(rec.person_id, promoted.person_id)
            ctx._pending_simulation_events[-1][2].update(
                {
                    "passive_promotion_reason": "marriage_into_detailed_family",
                    "attraction_fit_score": round(attraction_score, 4),
                    "formation_probability": round(offer_probability, 4),
                }
            )
            paired_ids.add(rec.person_id)
            paired_ids.add(promoted.person_id)
            promotions += 1
    if prof:
        simulation_timing.accumulate("pairing.passive_promote.scan", tpc() - t0)


def pair_people_by_settlement_then_region(
    ctx: SimulationContext,
    year: int,
    by_settlement: dict[str, list[SimulationPersonRecord]],
) -> None:
    """Pair local residents first, then use same-region fallback without world-global lists.

    New pairs are opposite-sex only (one mature male, one mature female per pair).
    Same-sex official couples are formed in ``library.simulation_social`` instead.
    """
    prof = simulation_timing.active_for_year(year)
    tpc = time.perf_counter
    if prof:
        t0 = tpc()
    paired_ids = {pid for pair in ctx.couples for pid in pair}
    if prof:
        simulation_timing.accumulate("pairing.paired_ids", tpc() - t0)
        t0 = tpc()
    for sid in sorted(by_settlement.keys()):
        _pair_from_records(ctx, by_settlement[sid], year, paired_ids)
    if prof:
        simulation_timing.accumulate("pairing.settlement_phase", tpc() - t0)
        t0 = tpc()

    by_region: dict[str, list[SimulationPersonRecord]] = {}
    for sid in sorted(by_settlement.keys()):
        records = by_settlement[sid]
        rid = ""
        st = ctx.settlements_by_id.get(sid)
        if st is not None:
            rid = (st.region_id or "").strip()
        if not rid and records:
            rid = (ctx._residence_region_id(records[0]) or "").strip()
        if not rid:
            continue
        for rec in records:
            if rec.person_id not in paired_ids:
                by_region.setdefault(rid, []).append(rec)
    if prof:
        simulation_timing.accumulate("pairing.region_build", tpc() - t0)
        t0 = tpc()

    for rid in sorted(by_region.keys()):
        _pair_from_records(ctx, by_region[rid], year, paired_ids)
    if prof:
        simulation_timing.accumulate("pairing.region_phase", tpc() - t0)
        t0 = tpc()

    _promote_passive_spouses_for_unpaired_detailed(ctx, year, by_settlement, paired_ids)
    if prof:
        simulation_timing.accumulate("pairing.passive_promote", tpc() - t0)


def births_by_settlement(
    ctx: SimulationContext,
    year: int,
    *,
    sim_seed: int,
    by_settlement: dict[str, list[SimulationPersonRecord]],
    resource_facts=None,
    detailed_active_soft_cap: int | None = None,
    passive_births_by_place: dict[tuple[str, str, str, str], int] | None = None,
) -> int:
    """Run birth attempts by settlement id, then mother person id.

    Genetic births require a **female** mother with a **male** ``partner_person_id`` or
    ``paramour_person_id`` who passes the same fertility gate. Same-sex marriages
    (``add_couple``) do **not** supply a male genetic parent between spouses; a female
    spouse alone does not enable conception without a separate male partner/paramour.
    """
    ctx.last_childbirth_maternal_deaths_count = 0
    births_count = 0
    cap = int(detailed_active_soft_cap) if detailed_active_soft_cap else None
    rng = random.Random(year * 1_000_003 + sim_seed)
    prof = simulation_timing.active_for_year(year)
    tpc = time.perf_counter
    if prof:
        t0 = tpc()
    mothers_by_settlement: list[tuple[str, list[SimulationPersonRecord]]] = []
    candidate_mothers = 0
    for sid in sorted(by_settlement.keys()):
        mothers_this_year = [
            r
            for r in by_settlement[sid]
            if (r.person.gender or "").strip() == "Female"
        ]
        candidate_mothers += len(mothers_this_year)
        mothers_by_settlement.append((sid, mothers_this_year))
    if prof:
        simulation_timing.accumulate("births.scan_mothers", tpc() - t0)
        simulation_timing.record_gauge(
            year, "births", "candidate_mothers", candidate_mothers
        )
    eligible_mothers = 0
    partnered_mothers = 0
    conception_successes = 0
    passive_births = 0
    detailed_births = 0
    for sid, mothers_this_year in mothers_by_settlement:
        for rec in mothers_this_year:
            if prof:
                t0 = tpc()
            if not _eligible_for_birth(rec, year):
                if prof:
                    simulation_timing.accumulate("births.eligibility_partner", tpc() - t0)
                continue
            if rec.person.last_birth_event_year == year:
                if prof:
                    simulation_timing.accumulate("births.eligibility_partner", tpc() - t0)
                continue
            eligible_mothers += 1
            candidates = _birth_father_candidates(ctx, rec, year)
            if not candidates:
                if prof:
                    simulation_timing.accumulate("births.eligibility_partner", tpc() - t0)
                continue
            partnered_mothers += 1
            father_id, birth_relationship_type = rng.choice(candidates)
            father = ctx.id_to_record[father_id]
            if prof:
                simulation_timing.accumulate("births.eligibility_partner", tpc() - t0)
                t0 = tpc()
            pressure = resource_pressure_for_person(
                ctx, rec, resource_facts=resource_facts
            )
            if prof:
                simulation_timing.accumulate("births.resource_pressure", tpc() - t0)
                t0 = tpc()
            p_try = annual_conception_probability(
                rec.person,
                father.person,
                pressure=pressure,
                simulation_year=year,
            )
            crng = conception_rng(year, sim_seed, rec.person_id, father_id)
            if crng.random() >= p_try:
                if prof:
                    simulation_timing.accumulate("births.conception_roll", tpc() - t0)
                continue
            conception_successes += 1
            if prof:
                simulation_timing.accumulate("births.conception_roll", tpc() - t0)
                t0 = tpc()
            if cap is not None and len(ctx.current_people_ids) + births_count >= cap:
                sid_birth = rec.person.current_settlement_id or rec.person.birthplace_settlement_id or sid
                st = ctx.settlements_by_id.get(str(sid_birth))
                rid_birth = (
                    (st.region_id if st is not None else None)
                    or rec.person.birthplace_region_id
                    or ""
                )
                key = (
                    str(rid_birth),
                    str(sid_birth),
                    str(rec.person.species or father.person.species or ""),
                    str(rec.person.ethnic or father.person.ethnic or ""),
                )
                if passive_births_by_place is not None:
                    passive_births_by_place[key] = passive_births_by_place.get(key, 0) + 1
                rec.person = replace(rec.person, last_birth_event_year=year)
                passive_births += 1
                _maybe_apply_childbirth_mortality(
                    ctx,
                    rec,
                    father_id=father.person_id,
                    year=year,
                    sim_seed=sim_seed,
                    resource_pressure=pressure,
                    litter_size=1,
                    settlement_id=str(sid_birth),
                    child_ids=[],
                    birth_relationship_type=birth_relationship_type,
                    newborn_outcome="passive_birth_recorded",
                )
                if prof:
                    simulation_timing.accumulate("births.passive_births", tpc() - t0)
                continue
            surname_convention = ctx.surname_convention_for_parents(
                father.person_id, rec.person_id
            )
            if prof:
                simulation_timing.accumulate("births.surname_convention", tpc() - t0)
                t0 = tpc()
            children = having_sex_birth_event(
                father.person,
                rec.person,
                simulation_year=year,
                rng=rng,
                birthyear=year,
                age=0,
                life_stage="child",
                birthplace=rec.person.birthplace or "Placeholder",
                simulation_context=ctx,
                mother_person_id=rec.person_id,
                surname_convention=surname_convention,
            )
            if prof:
                simulation_timing.accumulate("births.generate_children", tpc() - t0)
            if not children:
                continue
            rec.person = replace(rec.person, last_birth_event_year=year)
            if prof:
                t0 = tpc()
            born_out_of_wedlock = (
                birth_relationship_type == BIRTH_RELATIONSHIP_PARAMOUR
            )
            legitimacy_status = (
                LEGITIMACY_STATUS_BASTARD
                if born_out_of_wedlock
                else LEGITIMACY_STATUS_LEGITIMATE
            )
            created_child_ids: list[int] = []
            for child in children:
                child = replace(
                    child,
                    birth_relationship_type=birth_relationship_type,
                    born_out_of_wedlock=born_out_of_wedlock,
                    legitimacy_status=legitimacy_status,
                )
                child_rec = ctx.add_person(
                    person=child,
                    is_founder=False,
                    father_id=father.person_id,
                    mother_id=rec.person_id,
                )
                created_child_ids.append(int(child_rec.person_id))
                births_count += 1
                detailed_births += 1
            sid_birth = rec.person.current_settlement_id or rec.person.birthplace_settlement_id or sid
            _maybe_apply_childbirth_mortality(
                ctx,
                rec,
                father_id=father.person_id,
                year=year,
                sim_seed=sim_seed,
                resource_pressure=pressure,
                litter_size=len(children),
                settlement_id=str(sid_birth),
                child_ids=created_child_ids,
                birth_relationship_type=birth_relationship_type,
                newborn_outcome="detailed_child_records_created",
            )
            if prof:
                simulation_timing.accumulate("births.add_person", tpc() - t0)
    if prof:
        simulation_timing.record_gauge(year, "births", "eligible_mothers", eligible_mothers)
        simulation_timing.record_gauge(year, "births", "partnered_mothers", partnered_mothers)
        simulation_timing.record_gauge(
            year, "births", "conception_successes", conception_successes
        )
        simulation_timing.record_gauge(year, "births", "passive_births", passive_births)
        simulation_timing.record_gauge(year, "births", "detailed_births", detailed_births)
        simulation_timing.record_gauge(
            year,
            "births",
            "childbirth_maternal_deaths",
            int(ctx.last_childbirth_maternal_deaths_count),
        )
    return births_count


def _partition_count(total: int, shares: tuple[tuple[str, float], ...]) -> list[tuple[str, int]]:
    n = max(0, int(total))
    if n <= 0:
        return []
    assigned = 0
    out: list[tuple[str, int]] = []
    for i, (label, share) in enumerate(shares):
        if i == len(shares) - 1:
            count = n - assigned
        else:
            count = int(round(n * float(share)))
            count = max(0, min(n - assigned, count))
        assigned += count
        if count > 0:
            out.append((label, count))
    return out


def _passive_age_band(age: int) -> str:
    return str(max(0, min(_PASSIVE_AGE_MAX, int(age))))


def _passive_age_from_band(age_band: str) -> int:
    raw = (age_band or "").strip()
    if "-" in raw:
        raw = raw.split("-", 1)[0]
    try:
        return max(0, min(_PASSIVE_AGE_MAX, int(raw)))
    except ValueError:
        return 30


def _passive_death_rate(age: int) -> float:
    a = max(0, int(age))
    if a <= 1:
        return 0.035
    if a < 5:
        return 0.012
    if a < 15:
        return 0.004
    if a < 45:
        return 0.006 + max(0, a - 15) * 0.00035
    if a < 65:
        return 0.020 + max(0, a - 45) * 0.0016
    return min(0.45, 0.060 + max(0, a - 65) * 0.014)


def _passive_partner_share(age: int) -> float:
    if age < 16:
        return 0.0
    if age < 22:
        return 0.30 + (age - 16) * 0.06
    if age < 55:
        return _PASSIVE_PARTNERSHIP_ADULT_SHARE
    return _PASSIVE_PARTNERSHIP_ELDER_SHARE


def _passive_birth_rate_for_age(age: int) -> float:
    if age < 17 or age > 42:
        return 0.0
    peak = 1.0 - abs(age - 26) / 18.0
    return _PASSIVE_BIRTH_RATE * max(0.18, peak)


def _scaled_round(count: int, rate: float, key: str) -> int:
    exact = max(0.0, float(count) * float(rate))
    base = int(exact)
    frac = exact - base
    if frac <= 0.0:
        return base
    return base + (1 if _stable_unit_interval(key) < frac else 0)


def _passive_species_culture_for_settlement(
    ctx: SimulationContext, settlement_id: str
) -> tuple[str, str]:
    records = ctx.current_people_by_settlement().get(settlement_id, ())
    if records:
        p = records[0].person
        return (p.species or "", p.ethnic or "")
    latest_year = max((int(c.sim_year) for c in ctx.passive_cohorts), default=None)
    for cohort in ctx.passive_cohorts:
        if latest_year is None or int(cohort.sim_year) != int(latest_year):
            continue
        if (cohort.settlement_id or "").strip() == (settlement_id or "").strip():
            return (cohort.species or "", cohort.culture or "")
    return "", ""


def _generate_detail_floor_person(
    ctx: SimulationContext,
    *,
    year: int,
    settlement_id: str,
    gender: str,
    ordinal: int,
) -> SimulationPersonRecord:
    st = ctx.settlements_by_id[settlement_id]
    species, culture = _passive_species_culture_for_settlement(ctx, settlement_id)
    rng = random.Random(
        int(year) * 1_000_003
        + int(getattr(ctx, "placename_rng_salt", 0)) * 53
        + crc32(f"{settlement_id}|{gender}|{ordinal}".encode("utf-8"))
    )
    age = rng.randint(18, 35)
    person = generate_person_random(
        species=species or None,
        ethnic=culture or None,
        gender=gender,
        age=age,
        simulation_year=int(year),
        birthplace_region_id=st.region_id,
        birthplace_settlement_id=settlement_id,
        simulation_context=ctx,
        world=ctx.world,
        db_path=ctx.db_path,
    )
    person = _with_founder_parent_names(ctx, person)
    return ctx.add_person(person=person, is_founder=False)


def ensure_detailed_floor_for_active_settlements(
    ctx: SimulationContext,
    year: int,
    *,
    minimum: int = MIN_DETAILED_RESIDENTS_PER_ACTIVE_SETTLEMENT,
) -> int:
    """Keep active cohort settlements from losing all detailed representation."""
    minimum = max(0, int(minimum))
    if minimum <= 0:
        return 0
    promoted_or_created = 0
    by_settlement = ctx.current_people_by_settlement()
    for sid, st in sorted(ctx.settlements_by_id.items()):
        if (st.status or "").strip().lower() != "active":
            continue
        needed = minimum - len(by_settlement.get(sid, ()))
        if needed <= 0:
            continue
        for i in range(needed):
            gender = "Male" if (i % 2 == 0) else "Female"
            promoted = promote_passive_candidate_for_office(
                ctx,
                year=int(year),
                settlement_id=sid,
                min_age=18,
                reason="settlement_detail_floor",
                source={"settlement_id": sid, "minimum": minimum},
            )
            if promoted is None:
                promoted = _generate_detail_floor_person(
                    ctx,
                    year=int(year),
                    settlement_id=sid,
                    gender=gender,
                    ordinal=i,
                )
                ctx._pending_simulation_events[-1][2].update(
                    {"reason": "settlement_detail_floor", "settlement_id": sid}
                )
            promoted_or_created += 1
        by_settlement = ctx.current_people_by_settlement()
    return promoted_or_created


def _migration_arrivals_by_settlement_from_events(
    events: list[tuple[int | None, str, dict]],
) -> dict[str, int]:
    arrivals: dict[str, int] = {}
    for _event_year, event_type, payload in events:
        if event_type != "settlement_moved":
            continue
        reason = str(payload.get("move_reason") or "").strip()
        if reason not in PASSIVE_MIGRATION_CONTEXT_REASONS:
            continue
        sid = str(payload.get("to_settlement_id") or "").strip()
        if not sid:
            continue
        arrivals[sid] = arrivals.get(sid, 0) + 1
    return arrivals


def _promote_passive_context_for_migration_arrivals(
    ctx: SimulationContext,
    year: int,
    arrivals_by_settlement: dict[str, int],
    *,
    detailed_active_soft_cap: int | None = None,
) -> int:
    if not arrivals_by_settlement:
        return 0
    cap = max(0, int(PASSIVE_MIGRATION_CONTEXT_PROMOTION_CAP_PER_YEAR))
    if cap <= 0:
        return 0
    soft_cap = int(detailed_active_soft_cap) if detailed_active_soft_cap else None
    candidate_index = build_passive_office_candidate_index(ctx)
    promotions = 0
    for sid, arrival_count in sorted(
        arrivals_by_settlement.items(), key=lambda item: (-int(item[1]), item[0])
    ):
        if promotions >= cap:
            break
        st = ctx.settlements_by_id.get(sid)
        if st is None or (st.status or "").strip().lower() != "active":
            continue
        per_settlement = min(
            int(arrival_count),
            PASSIVE_MIGRATION_CONTEXT_PROMOTION_CAP_PER_SETTLEMENT,
            cap - promotions,
        )
        for _ in range(max(0, per_settlement)):
            if soft_cap is not None and len(ctx.current_people_ids) >= soft_cap:
                return promotions
            promoted = promote_passive_candidate_for_settlement_context(
                ctx,
                year=int(year),
                settlement_id=sid,
                min_age=16,
                reason="migration_into_focal_settlement",
                source={
                    "settlement_id": sid,
                    "region_id": st.region_id,
                    "arrival_count": int(arrival_count),
                    "trigger_move_reasons": sorted(PASSIVE_MIGRATION_CONTEXT_REASONS),
                },
                candidate_index=candidate_index,
            )
            if promoted is None:
                break
            promotions += 1
    return promotions


def _initial_passive_age_count(total: int, age: int) -> int:
    if total <= 0:
        return 0
    weights = [max(0.02, (1.0 - a / 105.0) ** 2.35) for a in range(_PASSIVE_AGE_MAX + 1)]
    exact = total * weights[age] / sum(weights)
    return int(round(exact))


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _stable_unit_interval(text: str) -> float:
    return crc32(text.encode("utf-8")) / 0xFFFFFFFF


def _passive_settlement_weight(st: SettlementState, *, year: int) -> float:
    founded = st.founded_sim_year if st.founded_sim_year is not None else year
    age = max(0, int(year) - int(founded))
    age_factor = 1.0 + min(0.45, age / 200.0)
    site_factor = 1.0 / (max(1, int(st.site_slot)) ** 0.18)
    stability_factor = 0.82 + 0.36 * _clamp(float(st.stability or 0.0), 0.0, 1.0)
    market_factor = 0.94 + 0.20 * _clamp(float(st.market_pull or 0.0), 0.0, 1.0)
    prosperity_factor = 0.84 + 0.28 * _clamp(
        float(getattr(st, "prosperity_pool", 1.0) or 0.0), 0.0, 2.0
    ) / 2.0
    resident_factor = 1.0 + min(0.35, (max(0, int(st.resident_count)) ** 0.5) / 35.0)
    jitter = 0.82 + 0.36 * _stable_unit_interval(
        f"{st.region_id}|{st.settlement_id}|{st.site_slot}"
    )
    return max(
        0.01,
        age_factor
        * site_factor
        * stability_factor
        * market_factor
        * prosperity_factor
        * resident_factor
        * jitter,
    )


def _allocate_counts_by_weight(total: int, weighted_ids: list[tuple[str, float]]) -> dict[str, int]:
    n = max(0, int(total))
    if n <= 0 or not weighted_ids:
        return {sid: 0 for sid, _ in weighted_ids}
    positive = [(sid, max(0.0, float(weight))) for sid, weight in weighted_ids]
    weight_total = sum(weight for _, weight in positive)
    if weight_total <= 0.0:
        positive = [(sid, 1.0) for sid, _ in positive]
        weight_total = float(len(positive))

    allocations: dict[str, int] = {}
    remainders: list[tuple[float, str]] = []
    assigned = 0
    for sid, weight in positive:
        exact = n * weight / weight_total
        base = int(exact)
        allocations[sid] = base
        assigned += base
        remainders.append((exact - base, sid))

    for _, sid in sorted(remainders, key=lambda item: (-item[0], item[1]))[: n - assigned]:
        allocations[sid] = allocations.get(sid, 0) + 1
    return allocations


def refresh_passive_background_cohorts(
    ctx: SimulationContext,
    year: int,
    *,
    population_scale: float = 1.0,
    extra_newborns_by_place: dict[tuple[str, str, str, str], int] | None = None,
) -> int:
    """Evolve aggregate background cohorts with births, deaths, aging, and pairing.

    Cohorts count for demographics and place stats, but they do not enter detailed
    person event loops. The first year seeds each active settlement from regional
    carrying capacity; later years age the previous passive snapshot forward.
    """
    prof = simulation_timing.active_for_year(year)
    tpc = time.perf_counter
    if prof:
        t0 = tpc()
    scale = max(0.0, float(population_scale))
    extra_newborns_by_place = extra_newborns_by_place or {}
    if scale <= 0.0 and extra_newborns_by_place:
        next_cohorts: list[PassiveCohort] = []
        total = _append_passive_newborn_cohorts(
            ctx,
            year,
            extra_newborns_by_place,
            next_cohorts=next_cohorts,
        )
        ctx.passive_cohorts = next_cohorts
        return total
    if scale <= 0.0:
        ctx.passive_cohorts = []
        return 0
    if not ctx.settlements_by_id:
        return 0
    ctx.sync_settlement_resident_counts()
    active_by_region: dict[str, list[str]] = {}
    for sid, st in ctx.settlements_by_id.items():
        if (st.status or "").strip().lower() != "active":
            continue
        active_by_region.setdefault(st.region_id, []).append(sid)
    for sids in active_by_region.values():
        sids.sort()

    previous_year = max((int(c.sim_year) for c in ctx.passive_cohorts), default=None)
    if prof:
        simulation_timing.accumulate("passive.setup", tpc() - t0)
        t0 = tpc()
    if previous_year is not None:
        next_cohorts: list[PassiveCohort] = []
        total_background = 0
        births_by_place: dict[tuple[str, str, str, str], int] = {}
        previous = [c for c in ctx.passive_cohorts if int(c.sim_year) == previous_year]
        if prof:
            simulation_timing.accumulate("passive.previous_select", tpc() - t0)
            t0 = tpc()
        for cohort in previous:
            if (cohort.settlement_id or "") not in ctx.settlements_by_id:
                continue
            count = max(0, int(cohort.population_count))
            if count <= 0:
                continue
            age = _passive_age_from_band(cohort.age_band)
            deaths = _scaled_round(
                count,
                _passive_death_rate(age),
                f"death|{year}|{cohort.settlement_id}|{cohort.gender}|{age}|{cohort.job_family}",
            )
            survivors = max(0, count - deaths)
            if (cohort.gender or "").strip().lower().startswith("f"):
                births = _scaled_round(
                    survivors,
                    _passive_birth_rate_for_age(age)
                    * (0.30 + 0.70 * _passive_partner_share(age)),
                    f"birth|{year}|{cohort.settlement_id}|{age}|{cohort.job_family}",
                )
                key = (
                    str(cohort.region_id or ""),
                    str(cohort.settlement_id or ""),
                    str(cohort.species or ""),
                    str(cohort.culture or ""),
                )
                births_by_place[key] = births_by_place.get(key, 0) + births
            next_age = min(_PASSIVE_AGE_MAX, age + 1)
            partner_share = _passive_partner_share(next_age)
            status = "partnered" if partner_share >= 0.5 else (cohort.status_bucket or "common")
            if survivors:
                total_background += survivors
                next_cohorts.append(
                    PassiveCohort(
                        sim_year=int(year),
                        region_id=cohort.region_id,
                        settlement_id=cohort.settlement_id,
                        age_band=_passive_age_band(next_age),
                        gender=cohort.gender,
                        species=cohort.species,
                        culture=cohort.culture,
                        job_family=cohort.job_family,
                        status_bucket=status,
                        population_count=survivors,
                        death_count=deaths,
                    )
                )
        if prof:
            simulation_timing.accumulate("passive.evolve_existing", tpc() - t0)
            t0 = tpc()
        for (rid, sid, species, culture), births in sorted(births_by_place.items()):
            if births <= 0:
                continue
            total_background += births
            male_births = births // 2
            female_births = births - male_births
            for gender, count in (("Male", male_births), ("Female", female_births)):
                if count <= 0:
                    continue
                next_cohorts.append(
                    PassiveCohort(
                        sim_year=int(year),
                        region_id=rid,
                        settlement_id=sid,
                        age_band="0",
                        gender=gender,
                        species=species,
                        culture=culture,
                        job_family="dependent",
                        status_bucket="child",
                        population_count=count,
                        birth_count=count,
                    )
                )
        if prof:
            simulation_timing.accumulate("passive.background_births", tpc() - t0)
            t0 = tpc()
        ctx.passive_cohorts = next_cohorts
        total_background += _append_passive_newborn_cohorts(
            ctx,
            year,
            extra_newborns_by_place or {},
            next_cohorts=ctx.passive_cohorts,
        )
        if prof:
            simulation_timing.accumulate("passive.detailed_overflow_births", tpc() - t0)
        return total_background

    next_cohorts: list[PassiveCohort] = []
    total_background = 0
    if prof:
        t0 = tpc()
    for rid, settlement_ids in sorted(active_by_region.items()):
        if not settlement_ids:
            continue
        regional_target = int(round(ctx.effective_regional_population_cap(rid) * scale))
        background_target = max(0, regional_target)
        weights = [
            (sid, _passive_settlement_weight(ctx.settlements_by_id[sid], year=year))
            for sid in settlement_ids
        ]
        background_by_settlement = _allocate_counts_by_weight(background_target, weights)
        for sid in settlement_ids:
            background = background_by_settlement.get(sid, 0)
            species, culture = _passive_species_culture_for_settlement(ctx, sid)
            seeded = 0
            for age in range(_PASSIVE_AGE_MAX + 1):
                age_count = _initial_passive_age_count(background, age)
                if age == _PASSIVE_AGE_MAX:
                    age_count = max(0, background - seeded)
                seeded += age_count
                if age_count <= 0:
                    continue
                male_count = age_count // 2
                female_count = age_count - male_count
                for gender, gender_count in (("Male", male_count), ("Female", female_count)):
                    if gender_count <= 0:
                        continue
                    for job_family, count in _partition_count(
                        gender_count,
                        _PASSIVE_JOB_FAMILY_SHARES if age >= 14 else (("dependent", 1.0),),
                    ):
                        status_share = _passive_partner_share(age)
                        status = (
                            "child"
                            if age < 14
                            else "partnered"
                            if status_share >= 0.5
                            else "single"
                        )
                        total_background += count
                        next_cohorts.append(
                            PassiveCohort(
                                sim_year=int(year),
                                region_id=rid,
                                settlement_id=sid,
                                age_band=_passive_age_band(age),
                                gender=gender,
                                species=species,
                                culture=culture,
                                job_family=job_family,
                                status_bucket=status,
                                population_count=count,
                            )
                        )
    if prof:
        simulation_timing.accumulate("passive.seed_initial", tpc() - t0)
        t0 = tpc()
    ctx.passive_cohorts = next_cohorts
    if extra_newborns_by_place:
        total_background += _append_passive_newborn_cohorts(
            ctx,
            year,
            extra_newborns_by_place,
            next_cohorts=ctx.passive_cohorts,
        )
    if prof:
        simulation_timing.accumulate("passive.seed_newborns", tpc() - t0)
    return total_background


def _append_passive_newborn_cohorts(
    ctx: SimulationContext,
    year: int,
    newborns_by_place: dict[tuple[str, str, str, str], int],
    *,
    next_cohorts: list[PassiveCohort],
) -> int:
    total = 0
    positive_births = [
        (key, int(births))
        for key, births in sorted(newborns_by_place.items())
        if int(births) > 0
    ]
    if not positive_births:
        return 0

    prof = simulation_timing.active_for_year(year)
    tpc = time.perf_counter
    if prof:
        t0 = tpc()
    merge_index: dict[tuple[str, str, str, str, str], int] = {}
    for i, cohort in enumerate(next_cohorts):
        if (
            int(cohort.sim_year) == int(year)
            and str(cohort.age_band or "") == "0"
            and str(cohort.job_family or "") == "dependent"
            and str(cohort.status_bucket or "") == "child"
        ):
            key = (
                str(cohort.region_id or ""),
                str(cohort.settlement_id or ""),
                str(cohort.gender or ""),
                str(cohort.species or ""),
                str(cohort.culture or ""),
            )
            merge_index.setdefault(key, i)
    if prof:
        simulation_timing.accumulate("passive.newborn_merge_index", tpc() - t0)
        t0 = tpc()

    for (rid, sid, species, culture), births in positive_births:
        total += int(births)
        male_births = int(births) // 2
        female_births = int(births) - male_births
        for gender, count in (("Male", male_births), ("Female", female_births)):
            if count <= 0:
                continue
            key = (str(rid), str(sid), str(gender), str(species), str(culture))
            cohort_index = merge_index.get(key)
            if cohort_index is not None:
                cohort = next_cohorts[cohort_index]
                next_cohorts[cohort_index] = replace(
                    cohort,
                    population_count=int(cohort.population_count) + int(count),
                    birth_count=int(cohort.birth_count) + int(count),
                )
            else:
                merge_index[key] = len(next_cohorts)
                next_cohorts.append(
                    PassiveCohort(
                        sim_year=int(year),
                        region_id=rid,
                        settlement_id=sid,
                        age_band="0",
                        gender=gender,
                        species=species,
                        culture=culture,
                        job_family="dependent",
                        status_bucket="child",
                        population_count=count,
                        birth_count=count,
                    )
                )
    if prof:
        simulation_timing.accumulate("passive.newborn_merge_apply", tpc() - t0)
    return total


def _record_profile_scale_snapshot(
    ctx: SimulationContext,
    year: int,
    label: str,
) -> None:
    """Record scale counters alongside late-year phase timings."""
    if not simulation_timing.active_for_year(year):
        return
    latest_cohort_year = max((int(c.sim_year) for c in ctx.passive_cohorts), default=None)
    latest_cohort_rows = sum(
        1
        for c in ctx.passive_cohorts
        if latest_cohort_year is not None and int(c.sim_year) == latest_cohort_year
    )
    latest_cohort_alive = sum(
        int(c.population_count)
        for c in ctx.passive_cohorts
        if latest_cohort_year is not None and int(c.sim_year) == latest_cohort_year
    )
    try:
        save_size = int(Path(ctx.save_db_path).stat().st_size)
    except OSError:
        save_size = 0

    detailed_alive = len(ctx.current_people_ids)
    metrics = {
        "detailed_alive": detailed_alive,
        "detailed_ram_people": len(ctx.people),
        "detailed_ram_dead": max(0, len(ctx.people) - detailed_alive),
        "passive_people": len(ctx.passive_people),
        "passive_cohort_rows_ram": len(ctx.passive_cohorts),
        "latest_passive_cohort_year": latest_cohort_year or 0,
        "latest_passive_cohort_rows": latest_cohort_rows,
        "latest_passive_cohort_alive": latest_cohort_alive,
        "settlements": len(ctx.settlements_by_id),
        "couples": len(ctx.couples),
        "paramours": len(ctx.paramours),
        "pending_events": len(getattr(ctx, "_pending_simulation_events", ())),
        "save_size_bytes": save_size,
    }
    for metric, value in metrics.items():
        simulation_timing.record_gauge(year, label, metric, value)


def _run_population_growth_year_loop(
    ctx: SimulationContext,
    *,
    sim_seed: int,
    start_year: int,
    duration_years: int,
    passive_population_scale: float,
    detailed_active_soft_cap: int | None,
    use_nondetailed_directory: bool = False,
    progress_callback: Callable[[int], None] | None,
) -> None:
    end_exclusive = int(start_year) + int(duration_years)
    for year in range(int(start_year), end_exclusive):
        ctx.current_year = year
        move_event_start = len(ctx._pending_simulation_events)
        ctx.apply_pending_settlement_moves(year)
        migration_arrivals = _migration_arrivals_by_settlement_from_events(
            ctx._pending_simulation_events[move_event_start:]
        )
        births_count = 0
        passive_births_by_place: dict[tuple[str, str, str, str], int] = {}
        prof = simulation_timing.active_for_year(year)
        tpc = time.perf_counter

        if prof:
            t0 = tpc()
        promoted_for_migration = _promote_passive_context_for_migration_arrivals(
            ctx,
            year,
            migration_arrivals,
            detailed_active_soft_cap=detailed_active_soft_cap,
        )
        if prof:
            simulation_timing.accumulate("runner.migration_context_promote", tpc() - t0)
            simulation_timing.record_gauge(
                year,
                "passive_promotion",
                "migration_context_promotions",
                promoted_for_migration,
            )

        if prof:
            t0 = tpc()
        people_by_settlement = ctx.current_people_by_settlement()
        if prof:
            simulation_timing.accumulate("runner.group_current_by_settlement", tpc() - t0)
            t0 = tpc()
        pair_people_by_settlement_then_region(ctx, year, people_by_settlement)
        if prof:
            simulation_timing.accumulate("runner.pairing", tpc() - t0)

        if prof:
            t0 = tpc()
        resource_facts = ctx.annual_resource_facts(year)
        if prof:
            simulation_timing.accumulate("births.resource_facts", tpc() - t0)
            t0 = tpc()
        births_count = births_by_settlement(
            ctx,
            year,
            sim_seed=sim_seed,
            by_settlement=people_by_settlement,
            resource_facts=resource_facts,
            detailed_active_soft_cap=detailed_active_soft_cap,
            passive_births_by_place=passive_births_by_place,
        )
        # Births has its own exclusive inner profile phases.

        if prof:
            t0 = tpc()
        mortality_rates = apply_annual_mortality(ctx, year)
        if prof:
            simulation_timing.accumulate("runner.mortality", tpc() - t0)
        childbirth_maternal_deaths = int(ctx.last_childbirth_maternal_deaths_count)
        if childbirth_maternal_deaths:
            mortality_rates = dict(mortality_rates)
            mortality_rates["childbirth_maternal_deaths_count"] = float(
                childbirth_maternal_deaths
            )

        if prof:
            t0 = tpc()
        if use_nondetailed_directory:
            with closing(sqlite3.connect(ctx.save_db_path)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                seed_nondetailed_from_active_settlements(
                    conn,
                    ctx,
                    year=year,
                    population_scale=passive_population_scale,
                    start_person_id=ctx.next_person_id,
                )
                conn.commit()
            ctx.last_nondetailed_tick_result = run_nondetailed_sql_annual_tick_for_save(
                ctx.save_db_path,
                year=year,
            )
            with closing(sqlite3.connect(ctx.save_db_path)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                economy_result = apply_nondetailed_job_family_economy_effects(
                    conn,
                    ctx,
                    year=year,
                )
                migration_result = run_nondetailed_sql_migration(
                    conn,
                    ctx,
                    year=year,
                )
                conn.commit()
            if prof:
                simulation_timing.record_gauge(
                    year,
                    "nondetailed",
                    "economy_affected_settlements",
                    economy_result.affected_settlements,
                )
                simulation_timing.record_gauge(
                    year,
                    "nondetailed",
                    "migration_moved",
                    migration_result.moved,
                )
            with closing(sqlite3.connect(ctx.save_db_path)) as conn:
                row = conn.execute(
                    "SELECT COALESCE(MAX(person_id), 0) FROM simulation_people_nondetailed"
                ).fetchone()
            ctx.next_person_id = max(int(ctx.next_person_id), int(row[0] or 0) + 1)
            ensure_detailed_floor_for_active_settlements(ctx, year)
        else:
            refresh_passive_background_cohorts(
                ctx,
                year,
                population_scale=passive_population_scale,
                extra_newborns_by_place=passive_births_by_place,
            )
            ensure_detailed_floor_for_active_settlements(ctx, year)
        if prof:
            simulation_timing.accumulate(
                "runner.nondetailed_directory"
                if use_nondetailed_directory
                else "runner.passive_cohorts",
                tpc() - t0,
            )

        persist_to_save = ctx._should_checkpoint_snapshot(year)
        _record_profile_scale_snapshot(ctx, year, "before_summary")
        ctx.record_year_summary(
            year=year,
            births_count=births_count,
            deaths_count=int(mortality_rates["deaths_count"]) + childbirth_maternal_deaths,
            mortality_rates=mortality_rates,
            persist_to_save=persist_to_save,
        )
        _record_profile_scale_snapshot(ctx, year, "after_summary")
        if progress_callback is not None and (
            persist_to_save or year == end_exclusive - 1
        ):
            progress_callback(year)


def run_population_growth_simulation(
    ctx: SimulationContext,
    *,
    sim_seed: int,
    start_year: int,
    duration_years: int,
    starting_couples: int,
    passive_population_scale: float = 1.0,
    detailed_active_soft_cap: int | None = None,
    use_nondetailed_directory: bool = False,
    progress_callback: Callable[[int], None] | None = None,
    print_timing_report: bool = True,
) -> None:
    """Drive the canonical population-growth yearly loop until ``finalize_run`` (context exit)."""
    random.seed(sim_seed)
    np.random.seed(int(sim_seed) % (2**32))
    simulation_timing.configure_profile_window(
        start_year=start_year, duration_years=duration_years
    )

    founder_rng = random.Random(int(sim_seed) * 1_000_003 + int(start_year) + 71_009)
    for _ in range(starting_couples):
        male_person_id_seed = int(ctx.next_person_id)
        male = generate_population_founder(
            ctx,
            gender="Male",
            simulation_year=start_year,
            rng=founder_rng,
            person_id_seed=male_person_id_seed,
        )
        female_person_id_seed = int(ctx.next_person_id) + 1
        female = generate_population_founder(
            ctx,
            gender="Female",
            simulation_year=start_year,
            rng=founder_rng,
            person_id_seed=female_person_id_seed,
        )
        male_rec = ctx.add_person(person=male, is_founder=True)
        female_rec = ctx.add_person(person=female, is_founder=True)
        ctx.add_couple(male_rec.person_id, female_rec.person_id)

    _run_population_growth_year_loop(
        ctx,
        sim_seed=sim_seed,
        start_year=start_year,
        duration_years=duration_years,
        passive_population_scale=passive_population_scale,
        detailed_active_soft_cap=detailed_active_soft_cap,
        use_nondetailed_directory=bool(use_nondetailed_directory),
        progress_callback=progress_callback,
    )

    if print_timing_report:
        simulation_timing.print_report_if_configured()


def continue_population_growth_simulation(
    ctx: SimulationContext,
    *,
    sim_seed: int,
    duration_years: int,
    passive_population_scale: float = 1.0,
    detailed_active_soft_cap: int | None = None,
    use_nondetailed_directory: bool = False,
    progress_callback: Callable[[int], None] | None = None,
    print_timing_report: bool = True,
) -> int:
    """Continue an already-loaded save without adding founders.

    Returns the first simulation year processed by this continuation.
    """
    if (
        not ctx.people
        and not ctx.passive_people
        and not ctx.passive_cohorts
        and ctx.nondetailed_population_count() <= 0
    ):
        raise ValueError("Cannot resume population simulation without a loaded save.")
    current_year = (
        int(ctx.current_year)
        if ctx.current_year is not None
        else int(ctx.simulation_start_year)
    )
    resume_start_year = current_year + 1
    random.seed(sim_seed)
    np.random.seed(int(sim_seed) % (2**32))
    simulation_timing.configure_profile_window(
        start_year=resume_start_year, duration_years=duration_years
    )

    _run_population_growth_year_loop(
        ctx,
        sim_seed=sim_seed,
        start_year=resume_start_year,
        duration_years=duration_years,
        passive_population_scale=passive_population_scale,
        detailed_active_soft_cap=detailed_active_soft_cap,
        use_nondetailed_directory=bool(use_nondetailed_directory),
        progress_callback=progress_callback,
    )

    if print_timing_report:
        simulation_timing.print_report_if_configured()
    return resume_start_year


def write_population_growth_report_files(
    ctx: SimulationContext,
    *,
    sim_seed: int,
    start_year: int,
    duration_years: int,
    output_path: Path,
    people_json_path: Path,
    places_geo_path: Path,
) -> None:
    prof = simulation_timing.enabled()
    tpc = time.perf_counter
    if prof:
        t0 = tpc()
    report_text = build_population_growth_report(
        ctx.people,
        ctx.couples,
        ctx.settlements_by_id,
        random_seed=sim_seed,
        start_year=start_year,
        duration_years=duration_years,
        ctx=ctx,
    )
    if prof:
        simulation_timing.accumulate("report.build_text", tpc() - t0)
        t0 = tpc()
    output_path.write_text(
        report_text,
        encoding="utf-8",
    )
    if prof:
        simulation_timing.accumulate("report.write_text", tpc() - t0)
    end_exclusive = start_year + duration_years
    if prof:
        t0 = tpc()
    people_payload = people_export_payload(
        ctx.people,
        random_seed=sim_seed,
        simulation_start_year=start_year,
        simulation_end_year_exclusive=end_exclusive,
    )
    if prof:
        simulation_timing.accumulate("report.build_people_json", tpc() - t0)
        t0 = tpc()
    people_json_path.write_text(
        json.dumps(
            people_payload,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    if prof:
        simulation_timing.accumulate("report.write_people_json", tpc() - t0)
        t0 = tpc()
    places_payload = settlements_geo_export_payload(ctx.settlements_by_id)
    if prof:
        simulation_timing.accumulate("report.build_places_geo", tpc() - t0)
        t0 = tpc()
    places_geo_path.write_text(
        json.dumps(
            places_payload,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    if prof:
        simulation_timing.accumulate("report.write_places_geo", tpc() - t0)
