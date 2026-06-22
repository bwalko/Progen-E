"""Annual career assignment driven by genome_jobs config."""

from __future__ import annotations

import heapq
import random
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from library import simulation_timing
from library.genome_composites import significant_composite_names_for_traits
from library.job_economics import (
    JobEconomicsCatalog,
    JobEconomicsParams,
    normalize_job_catalog_key,
)
from library.job_archetypes import JobArchetypeCatalog, JobArchetypeParams
from library.job_market import JobMarketCatalog, JobMarketParams
from library.status_echelons import StatusEchelonCatalog
from library.mind_body import attractiveness_01, ensure_full_mind_body, work_trait_values
from library.personality_interpreter import interpret_genome_personality
from library.geography import list_routes_from
from library.random_traits import _as_int, _connect
from library.simulation_outlaws import (
    is_outlaw_absent,
    normalize_outlaw_labor_state,
    outlaw_blocks_normal_career,
)
from library.work_body_fit import (
    BodyDemandFit,
    body_demand_fit_for_person,
    magic_physical_leveling_for_world,
)

if TYPE_CHECKING:
    from library.person import Person
    from library.simulation_context import SimulationContext
    from library.simulation_context import SimulationPersonRecord

IGNORED_GENOME_JOB_COLUMNS: frozenset[str] = frozenset(
    {"strong_pairings", "role_cluster", "overlap_notes", "design_notes"}
)

ERA_JOB_COLUMNS: dict[str, str] = {
    "prehistoric": "prehistoric_jobs",
    "bronze_age": "bronze_age_jobs",
    "iron_age": "iron_age_jobs",
    "medieval": "medieval_jobs",
    "modern": "modern_jobs",
}

ERA_PREMIUM_COLUMNS: dict[str, str] = {
    "prehistoric": "prehistoric_premium_jobs",
    "bronze_age": "bronze_age_premium_jobs",
    "iron_age": "iron_age_premium_jobs",
    "medieval": "medieval_premium_jobs",
    "modern": "modern_premium_jobs",
}

PREMIUM_JOB_FITNESS_THRESHOLD = 0.75
PREMIUM_JOB_MAX_PROB = 0.28

HIGH_WEIGHT_FITNESS_TRAITS: frozenset[str] = frozenset(
    {
        "physical",
        "intellect",
        "symmetry",
        "neurochemical",
        "ambition",
        "persuasion",
        "empathy",
    }
)
MEDIUM_WEIGHT_FITNESS_TRAITS: frozenset[str] = frozenset(
    {"discipline", "focus", "resilience", "adaptability", "honesty", "courage"}
)
HIGH_TRAIT_WEIGHT = 2.25
MEDIUM_TRAIT_WEIGHT = 1.55
DEFAULT_TRAIT_WEIGHT = 1.0

NEAR_PERFECT_MAGNITUDE = 20.0
HIGH_DEVIATION_MAGNITUDE = 75.0

JOB_LOSS_BASE_PROB = 0.002
JOB_LOSS_FITNESS_SCALE = 0.25
JOB_LOSS_PRESSURE_SCALE = 0.18
JOB_LOSS_HIGH_FITNESS_DAMPING = 0.25
JOB_LOSS_MAX_PROB = 0.65

REHIRE_BASE_PROB = 0.10
REHIRE_FITNESS_SCALE = 0.75
REHIRE_DURATION_BONUS_PER_YEAR = 0.03
REHIRE_PRESSURE_PENALTY = 0.25
REHIRE_MIN_PROB = 0.03
REHIRE_MAX_PROB = 0.95

# Caregiver-duty penalty applied to rehire and (mildly) to job-loss draws.
# See ``library.simulation_household_care.childcare_duty_factor`` for the source.
JOB_CHILD_DUTY_REHIRE_WEIGHT = 0.65
JOB_CHILD_DUTY_LOSS_WEIGHT = 0.30
CAREER_CHILD_DUTY_FACTOR_CAP = 0.85
PRIMARY_CHILDCARE_DUTY_THRESHOLD = 0.25
PRIMARY_CHILDCARE_HOME_JOB_WEIGHT = 2.8
PRIMARY_CHILDCARE_OUT_OF_HOME_LOSS_FLOOR = 0.72
PRIMARY_CHILDCARE_KIN_SIGNAL_WEIGHT = 0.44
PRIMARY_CHILDCARE_KIN_PULL_WEIGHT = 0.35

JOB_SEEKER_MIGRATION_MAX_PROB = 0.35

ADULT_HOUSING_MIN_AGE = 18
HOUSEHOLD_CARE_MIN_DUTY = 0.35
SERVICE_HOUSEHOLD_PROSPERITY_THRESHOLD = 2.15
SERVICE_HIGH_STANDING_THRESHOLD = 0.68
STREET_PRECARITY_PRESSURE_THRESHOLD = 0.62
VICE_DESPERATION_THRESHOLD = 0.58
PRESTIGE_MOBILITY_MIN_AGE = 22
PRESTIGE_PATRONAGE_SCORE_THRESHOLD = 0.62
PRESTIGE_HIGH_CONFIDENCE_SCORE = 0.88
PRESTIGE_MAX_PROMOTIONS_PER_SETTLEMENT = 4
PRESTIGE_MIN_SETTLEMENT_POPULATION = 8
PRESTIGE_FALL_STANDING_THRESHOLD = 0.62
PRESTIGE_BANKRUPTCY_PROSPERITY_THRESHOLD = 0.16

# Sex-restricted jobs: tokens may end with `` [M]`` (male-only) or `` [F]`` (female-only).
# Cross-gender exception: opposite ``gender_mind``, low ``mating drive`` genome, physical gate.
CROSS_GENDER_MATING_DRIVE_THRESHOLD = 35.0
CROSS_GENDER_FEMALE_PHYS_MIN = -40.0
CROSS_GENDER_MALE_PHYS_MAX = -30.0
MATING_DRIVE_GENOME_KEY = "mating drive"

_JOB_SEX_TAG = re.compile(r"^(.+?)\s*\[([mMfF])\]\s*$")


@dataclass(frozen=True)
class CareerAssignment:
    job: str
    job_tier: Literal["common", "premium"]
    job_era: str
    trait: str
    deviation_band: str
    descriptor: str
    status_tendency: str
    leader_quality: str
    leader_tendency: str
    job_sex_restriction: str | None = None
    cross_gender_job_exception: bool = False
    society_need: float = 0.5
    selfish_desperate: float = 0.0
    job_trait_match_score: float = 0.0
    job_market_demand_score: float = 0.0
    job_prosperity_score: float = 0.0
    job_family: str = "labor"
    job_market_type: str = "settlement_market"
    role_family: str = "labor"
    social_class_band: str = "commoner"
    social_standing_01: float = 0.35
    societal_impact_01: float = 0.42
    perceived_worth_01: float = 0.38
    care_intensity_01: float = 0.0
    saturation_score: float = 1.0
    desperation_score: float = 0.0
    physical_demand_01: float = 0.0
    effective_physical_demand_01: float = 0.0
    body_power_01: float = 0.0
    physical_demand_multiplier: float = 1.0


@dataclass(frozen=True)
class CareerFitness:
    score: float
    near_perfect_traits: tuple[str, ...]
    high_deviation_traits: tuple[str, ...]
    weighted_near_perfect_count: float
    weighted_high_deviation_count: float


@dataclass(frozen=True)
class PrestigeTarget:
    job: str
    source_tokens: tuple[str, ...]
    min_score: float
    min_population: int = PRESTIGE_MIN_SETTLEMENT_POPULATION
    min_market_pull: float = 0.0
    min_household_prosperity: float = 0.0
    event_type: str = "elite_job_promoted"


PRESTIGE_TARGETS: tuple[PrestigeTarget, ...] = (
    PrestigeTarget(
        "merchant",
        ("trader", "peddler", "market", "scribe", "accountant", "clerk"),
        0.70,
        min_population=12,
        min_market_pull=0.05,
    ),
    PrestigeTarget(
        "caravan master",
        ("caravan", "trader", "merchant", "sailor", "ferry", "route", "dock"),
        0.74,
        min_population=14,
        min_market_pull=0.08,
    ),
    PrestigeTarget(
        "shipowner",
        ("sailor", "ferry", "dock", "ship", "merchant", "caravan master"),
        0.80,
        min_population=24,
        min_market_pull=0.18,
        min_household_prosperity=1.8,
    ),
    PrestigeTarget(
        "guild master",
        ("smith", "mason", "carpenter", "artisan", "guild", "workshop", "master"),
        0.80,
        min_population=30,
        min_market_pull=0.10,
        event_type="guild_admission",
    ),
    PrestigeTarget(
        "treasurer",
        ("accountant", "scribe", "clerk", "tax", "steward", "record"),
        0.76,
        min_population=18,
        min_market_pull=0.08,
    ),
    PrestigeTarget(
        "estate steward",
        ("steward", "farmer", "household head", "administrator", "manager"),
        0.74,
        min_population=14,
        min_household_prosperity=0.9,
    ),
    PrestigeTarget(
        "landholder",
        ("farmer", "herder", "estate steward", "household head", "village elder"),
        0.82,
        min_population=16,
        min_household_prosperity=2.2,
    ),
    PrestigeTarget(
        "scholar",
        ("scribe", "teacher", "engineer", "architect", "philosopher", "inventor"),
        0.76,
        min_population=16,
    ),
    PrestigeTarget(
        "physician",
        ("healer", "midwife", "surgeon", "physician assistant", "care"),
        0.74,
        min_population=10,
    ),
    PrestigeTarget(
        "priest",
        ("temple", "priest", "monk", "nun", "oracle", "prophet", "ritual"),
        0.72,
        min_population=10,
    ),
    PrestigeTarget(
        "courtier",
        ("diplomat", "envoy", "official", "retainer", "court", "performer"),
        0.76,
        min_population=24,
        min_market_pull=0.10,
    ),
    PrestigeTarget(
        "banker",
        ("merchant", "treasurer", "accountant", "moneylender", "guild treasurer"),
        0.84,
        min_population=36,
        min_market_pull=0.18,
        min_household_prosperity=2.0,
    ),
)


@dataclass(frozen=True)
class CareerJobEntry:
    title: str
    tier: Literal["common", "premium"]
    restriction: str | None
    job_key: str
    economics: JobEconomicsParams
    market: JobMarketParams
    archetype: JobArchetypeParams
    home_compatible: bool


@dataclass(frozen=True)
class CareerGenomeJobOption:
    trait: str
    deviation_band: str
    descriptor: str
    status_tendency: str
    leader_quality: str
    leader_tendency: str
    society_need: float
    selfish_desperate: float
    common_entries: tuple[CareerJobEntry, ...]
    premium_entries: tuple[CareerJobEntry, ...]


@dataclass
class SettlementJobMarketSnapshot:
    settlement_pop: int | None
    market_pull: float
    stability: float
    job_counts: dict[str, int]
    family_counts: dict[str, int]


@dataclass
class YearJobMarketSnapshots:
    """Reusable job-market census for one career-assignment pass."""

    catalog: JobMarketCatalog
    by_settlement: dict[str, SettlementJobMarketSnapshot]

    @classmethod
    def build(cls, ctx: "SimulationContext") -> "YearJobMarketSnapshots":
        catalog = JobMarketCatalog.load(ctx.db_path)
        by_settlement: dict[str, SettlementJobMarketSnapshot] = {}
        for sid, records in ctx.current_people_by_settlement().items():
            st = ctx.settlements_by_id.get(sid)
            snapshot = SettlementJobMarketSnapshot(
                settlement_pop=(
                    int(getattr(st, "resident_count", 0) or 0)
                    if st is not None
                    else len(records)
                ),
                market_pull=(
                    float(getattr(st, "market_pull", 0.0) or 0.0)
                    if st is not None
                    else 0.0
                ),
                stability=(
                    float(getattr(st, "stability", 0.5) or 0.5)
                    if st is not None
                    else 0.5
                ),
                job_counts={},
                family_counts={},
            )
            for other in records:
                if (other.person.employment_status or "").strip().lower() != "employed":
                    continue
                market_type = (other.person.job_market_type or "settlement_market").strip().lower()
                if market_type not in {"settlement_market", "office"}:
                    continue
                job = (other.person.job or "").strip()
                if not job:
                    continue
                jk = normalize_job_catalog_key(job)
                if not jk:
                    continue
                snapshot.job_counts[jk] = snapshot.job_counts.get(jk, 0) + 1
                fam = catalog.lookup(job).job_family
                snapshot.family_counts[fam] = snapshot.family_counts.get(fam, 0) + 1
            by_settlement[sid] = snapshot
        return cls(catalog=catalog, by_settlement=by_settlement)

    def snapshot_for(
        self, ctx: "SimulationContext", settlement_id: str
    ) -> SettlementJobMarketSnapshot:
        sid = (settlement_id or "").strip()
        if sid not in self.by_settlement:
            st = ctx.settlements_by_id.get(sid)
            self.by_settlement[sid] = SettlementJobMarketSnapshot(
                settlement_pop=(
                    int(getattr(st, "resident_count", 0) or 0)
                    if st is not None
                    else None
                ),
                market_pull=(
                    float(getattr(st, "market_pull", 0.0) or 0.0)
                    if st is not None
                    else 0.0
                ),
                stability=(
                    float(getattr(st, "stability", 0.5) or 0.5)
                    if st is not None
                    else 0.5
                ),
                job_counts={},
                family_counts={},
            )
        return self.by_settlement[sid]

    def add_assigned_worker(self, rec: "SimulationPersonRecord") -> None:
        sid = _residence_settlement_id(rec)
        if not sid:
            return
        job = (rec.person.job or "").strip()
        if not job or (rec.person.employment_status or "").strip().lower() != "employed":
            return
        market_type = (rec.person.job_market_type or "settlement_market").strip().lower()
        if market_type not in {"settlement_market", "office"}:
            return
        snap = self.by_settlement.get(sid)
        if snap is None:
            return
        jk = normalize_job_catalog_key(job)
        if jk:
            snap.job_counts[jk] = snap.job_counts.get(jk, 0) + 1
        fam = self.catalog.lookup(job).job_family
        snap.family_counts[fam] = snap.family_counts.get(fam, 0) + 1


@dataclass
class YearResourceFacts:
    """Reusable settlement/region pressure facts for one annual tick."""

    settlement_food_pressure: dict[str, float]
    region_population: dict[str, int]
    region_cap: dict[str, int]
    pressure_by_person_id: dict[int, float] = field(default_factory=dict)

    @classmethod
    def build(cls, ctx: "SimulationContext") -> "YearResourceFacts":
        settlement_food_pressure = {
            sid: float(getattr(st, "food_pressure", 0.0) or 0.0)
            for sid, st in ctx.settlements_by_id.items()
        }
        census = ctx.alive_census_cache()
        region_population = dict(census.count_by_region)
        region_ids = set(region_population)
        for st in ctx.settlements_by_id.values():
            rid = (st.region_id or "").strip()
            if rid:
                region_ids.add(rid)
        region_cap: dict[str, int] = {}
        for rid in region_ids:
            try:
                region_cap[rid] = int(ctx.effective_regional_population_cap(rid))
            except (LookupError, ValueError):
                continue
        return cls(
            settlement_food_pressure=settlement_food_pressure,
            region_population=region_population,
            region_cap=region_cap,
        )

    def pressure_for(
        self, ctx: "SimulationContext", rec: "SimulationPersonRecord"
    ) -> float:
        pid = int(rec.person_id)
        if pid not in self.pressure_by_person_id:
            self.pressure_by_person_id[pid] = _resource_pressure_from_facts(
                ctx, rec, self
            )
        return self.pressure_by_person_id[pid]


@dataclass
class YearCareerFacts:
    """Reusable per-person facts for one career tick."""

    pressure_by_person_id: dict[int, float]
    duty_by_person_id: dict[int, float]
    kinship_bonus_by_person_id: dict[int, float]
    resource_facts: YearResourceFacts
    care_indexes: object | None = None

    @classmethod
    def build(
        cls,
        ctx: "SimulationContext",
        year: int,
        records: list["SimulationPersonRecord"],
    ) -> "YearCareerFacts":
        resource_facts = ctx.annual_resource_facts(year)
        pressure_by_person_id = {
            int(rec.person_id): resource_facts.pressure_for(ctx, rec)
            for rec in records
        }
        care_indexes = ctx.annual_care_indexes(year)
        duty_by_adult = getattr(care_indexes, "childcare_duty_factor_by_adult", {})

        duty_by_person_id = {
            int(rec.person_id): float(duty_by_adult.get(int(rec.person_id), 0.0))
            for rec in records
        }
        return cls(
            pressure_by_person_id=pressure_by_person_id,
            duty_by_person_id=duty_by_person_id,
            kinship_bonus_by_person_id={},
            resource_facts=resource_facts,
            care_indexes=care_indexes,
        )

    def pressure_for(
        self, ctx: "SimulationContext", rec: "SimulationPersonRecord"
    ) -> float:
        pid = int(rec.person_id)
        if pid not in self.pressure_by_person_id:
            self.pressure_by_person_id[pid] = self.resource_facts.pressure_for(ctx, rec)
        return self.pressure_by_person_id[pid]

    def duty_for(
        self, ctx: "SimulationContext", rec: "SimulationPersonRecord", year: int
    ) -> float:
        pid = int(rec.person_id)
        if pid not in self.duty_by_person_id:
            from library.simulation_household_care import childcare_duty_factor

            self.duty_by_person_id[pid] = float(
                childcare_duty_factor(ctx, rec, year, indexes=self.care_indexes)
            )
        return self.duty_by_person_id[pid]

    def kinship_bonus_for(
        self, ctx: "SimulationContext", rec: "SimulationPersonRecord", year: int
    ) -> float:
        pid = int(rec.person_id)
        if pid not in self.kinship_bonus_by_person_id:
            from library.simulation_household_care import childcare_kinship_bonus_01

            self.kinship_bonus_by_person_id[pid] = float(
                childcare_kinship_bonus_01(ctx, rec, year, indexes=self.care_indexes)
            )
        return self.kinship_bonus_by_person_id[pid]


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _trait_fitness_weight(trait: str) -> float:
    key = (trait or "").strip().lower()
    if key in HIGH_WEIGHT_FITNESS_TRAITS:
        return HIGH_TRAIT_WEIGHT
    if key in MEDIUM_WEIGHT_FITNESS_TRAITS:
        return MEDIUM_TRAIT_WEIGHT
    return DEFAULT_TRAIT_WEIGHT


def career_fitness(person: "Person") -> CareerFitness:
    """Overall 0..1 work fitness from weighted mind/body distance to ideal."""
    traits = work_trait_values(person)
    return _career_fitness_from_traits(traits)


def _career_fitness_from_traits(traits: dict[str, float]) -> CareerFitness:
    """Overall 0..1 work fitness from precomputed work-trait values."""
    if not traits:
        return CareerFitness(0.5, (), (), 0.0, 0.0)

    weighted_total = 0.0
    weight_sum = 0.0
    near: list[str] = []
    high: list[str] = []
    near_w = 0.0
    high_w = 0.0
    for trait, raw_value in traits.items():
        try:
            magnitude = min(100.0, abs(float(raw_value)))
        except (TypeError, ValueError):
            continue
        weight = _trait_fitness_weight(str(trait))
        trait_score = max(0.0, 1.0 - (magnitude / 100.0))
        if magnitude <= NEAR_PERFECT_MAGNITUDE:
            trait_score = min(1.0, trait_score + 0.10)
            near.append(str(trait))
            near_w += weight
        elif magnitude >= HIGH_DEVIATION_MAGNITUDE:
            trait_score = max(0.0, trait_score - 0.10)
            high.append(str(trait))
            high_w += weight
        weighted_total += trait_score * weight
        weight_sum += weight

    if weight_sum <= 0:
        return CareerFitness(0.5, (), (), 0.0, 0.0)
    return CareerFitness(
        score=round(_clamp(weighted_total / weight_sum, 0.0, 1.0), 4),
        near_perfect_traits=tuple(sorted(near)),
        high_deviation_traits=tuple(sorted(high)),
        weighted_near_perfect_count=round(near_w, 3),
        weighted_high_deviation_count=round(high_w, 3),
    )


def career_fitness_score(person: "Person") -> float:
    """Convenience wrapper for the normalized work-fitness score."""
    return career_fitness(person).score


def materialize_adult_profile(
    ctx: "SimulationContext", rec: "SimulationPersonRecord", year: int
) -> None:
    """Initialize adult-facing phenotype caches once a person enters job eligibility."""
    p = rec.person
    if p.mind_body and p.attractiveness_01 is not None:
        return
    p2 = p
    if not p2.mind_body:
        p2 = replace(p2, mind_body=ensure_full_mind_body(p2))
    if p2.attractiveness_01 is None:
        p2 = replace(p2, attractiveness_01=round(attractiveness_01(p2, int(year)), 5))
    if p2 is not p:
        rec.person = p2
        ctx.invalidate_alive_columns_cache()


def resolve_job_era(historical_year: int) -> str:
    """Map historical year to the job-list era columns in ``genome_jobs``."""
    hy = int(historical_year)
    if hy < -3300:
        return "prehistoric"
    if hy < -1200:
        return "bronze_age"
    if hy < 500:
        return "iron_age"
    if hy < 1500:
        return "medieval"
    return "modern"


def job_eligibility_age(maturity_age: int, era: str) -> int:
    """Earlier eras assign work before maturity; modern waits until maturity."""
    m = max(1, int(maturity_age))
    e = (era or "").strip().lower()
    if e == "prehistoric":
        return max(1, int(round(m * 0.5)))
    if e == "bronze_age":
        return max(1, int(round(m * (2.0 / 3.0))))
    if e == "iron_age":
        return max(1, int(round(m * 0.75)))
    if e == "medieval":
        return max(1, int(round(m * (5.0 / 6.0))))
    return m


def score_genome_job_row(genome_value: float, deviation_band: str) -> float:
    """Return a 0..1 tendency score for one signed genome trait against a row band."""
    v = max(-100.0, min(100.0, float(genome_value)))
    band = (deviation_band or "").strip().lower()
    if band == "optimal":
        return max(0.0, 1.0 - (abs(v) / 50.0))
    if band == "deficient":
        return max(0.0, 1.0 - (abs(v + 50.0) / 50.0))
    if band == "excess":
        return max(0.0, 1.0 - (abs(v - 50.0) / 50.0))
    return 0.0


def job_category_fitness_score(
    person: "Person",
    *,
    career_score: float,
    trait: str,
    deviation_band: str,
    trait_values: dict[str, float] | None = None,
) -> tuple[float, float]:
    """Blend broad work fitness with the selected trait-band match for job events."""
    try:
        general = _clamp(float(career_score), 0.0, 1.0)
    except (TypeError, ValueError):
        general = career_fitness_score(person)
    key = (trait or "").strip()
    traits = trait_values if trait_values is not None else work_trait_values(person)
    raw = traits.get(key)
    trait_score = 0.0
    if raw is not None:
        try:
            trait_score = score_genome_job_row(float(raw), deviation_band)
        except (TypeError, ValueError):
            trait_score = 0.0
    return round(_clamp((general + trait_score) / 2.0, 0.0, 1.0), 4), round(
        _clamp(trait_score, 0.0, 1.0), 4
    )


def job_category_fitness_for_title(
    person: "Person",
    *,
    career_score: float,
    job_title: str | None,
    era: str | None,
    db_path: Path | str,
) -> tuple[float, float, str | None, str | None]:
    """Best current category fit for an existing job title in the era catalog."""
    general = _clamp(float(career_score), 0.0, 1.0)
    job_key = normalize_job_catalog_key(job_title or "")
    era_key = (era or "").strip().lower()
    if not job_key or not era_key:
        return round(general, 4), 0.0, None, None
    cols = [ERA_JOB_COLUMNS.get(era_key), ERA_PREMIUM_COLUMNS.get(era_key)]
    cols = [c for c in cols if c]
    if not cols:
        return round(general, 4), 0.0, None, None

    traits = work_trait_values(person)
    best: tuple[float, str, str] | None = None
    for row in _genome_job_rows(str(Path(db_path).resolve())):
        trait = str(row.get("trait") or "").strip()
        if trait not in traits:
            continue
        matched = False
        for col in cols:
            for token in _split_jobs(row.get(col)):
                if normalize_job_catalog_key(token) == job_key:
                    matched = True
                    break
            if matched:
                break
        if not matched:
            continue
        band = str(row.get("deviation_band") or "").strip().lower()
        score = score_genome_job_row(float(traits[trait]), band)
        if best is None or score > best[0]:
            best = (score, trait, band)

    if best is None:
        return round(general, 4), 0.0, None, None
    trait_score, trait, band = best
    fit = _clamp((general + trait_score) / 2.0, 0.0, 1.0)
    return round(fit, 4), round(_clamp(trait_score, 0.0, 1.0), 4), trait, band


def _split_jobs(cell: object) -> tuple[str, ...]:
    if cell is None:
        return ()
    return tuple(p.strip() for p in str(cell).split(";") if p.strip())


def _parse_job_token(raw: str) -> tuple[str, str | None]:
    """Split ``title [M|F]`` into display title and ``male`` / ``female`` restriction."""
    s = (raw or "").strip()
    if not s:
        return "", None
    m = _JOB_SEX_TAG.match(s)
    if not m:
        return s, None
    title = (m.group(1) or "").strip()
    if not title:
        return s, None
    tag = (m.group(2) or "").upper()
    if tag == "M":
        return title, "male"
    if tag == "F":
        return title, "female"
    return s, None


def _work_trait_float(person: "Person", key: str) -> float | None:
    v = work_trait_values(person).get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _trait_float_from_values(
    trait_values: dict[str, float], key: str
) -> float | None:
    v = trait_values.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _female_exception_for_male_only_job(person: "Person") -> bool:
    return _female_exception_for_male_only_job_with_traits(
        person, work_trait_values(person)
    )


def _female_exception_for_male_only_job_with_traits(
    person: "Person", trait_values: dict[str, float]
) -> bool:
    if (person.gender_mind or "").strip().lower() != "masculine":
        return False
    md = _trait_float_from_values(trait_values, MATING_DRIVE_GENOME_KEY)
    ph = _trait_float_from_values(trait_values, "physical")
    if md is None or ph is None:
        return False
    if md > -CROSS_GENDER_MATING_DRIVE_THRESHOLD:
        return False
    return ph > CROSS_GENDER_FEMALE_PHYS_MIN


def _male_exception_for_female_only_job(person: "Person") -> bool:
    return _male_exception_for_female_only_job_with_traits(
        person, work_trait_values(person)
    )


def _male_exception_for_female_only_job_with_traits(
    person: "Person", trait_values: dict[str, float]
) -> bool:
    if (person.gender_mind or "").strip().lower() != "feminine":
        return False
    md = _trait_float_from_values(trait_values, MATING_DRIVE_GENOME_KEY)
    ph = _trait_float_from_values(trait_values, "physical")
    if md is None or ph is None:
        return False
    if md > -CROSS_GENDER_MATING_DRIVE_THRESHOLD:
        return False
    return ph < CROSS_GENDER_MALE_PHYS_MAX


def _job_allowed_for_person(person: "Person", restriction: str | None) -> bool:
    return _job_allowed_for_person_with_traits(
        person, restriction, work_trait_values(person)
    )


def _job_allowed_for_person_with_traits(
    person: "Person",
    restriction: str | None,
    trait_values: dict[str, float],
) -> bool:
    if restriction is None:
        return True
    g = (person.gender or "").strip().lower()
    if restriction == "male":
        if g == "male":
            return True
        if g == "female":
            return _female_exception_for_male_only_job_with_traits(person, trait_values)
        return False
    if restriction == "female":
        if g == "female":
            return True
        if g == "male":
            return _male_exception_for_female_only_job_with_traits(person, trait_values)
        return False
    return True


def _job_restriction_allowance(
    person: "Person", trait_values: dict[str, float]
) -> dict[str | None, bool]:
    g = (person.gender or "").strip().lower()
    female_male_job_exception = (
        g == "female" and _female_exception_for_male_only_job_with_traits(person, trait_values)
    )
    male_female_job_exception = (
        g == "male" and _male_exception_for_female_only_job_with_traits(person, trait_values)
    )
    return {
        None: True,
        "male": g == "male" or female_male_job_exception,
        "female": g == "female" or male_female_job_exception,
    }


def _filter_job_entries_for_person(
    person: "Person", raw_jobs: tuple[str, ...]
) -> tuple[tuple[str, str | None], ...]:
    """Parse CSV tokens and keep only jobs this person may hold (titles stripped of tags)."""
    out: list[tuple[str, str | None]] = []
    for raw in raw_jobs:
        title, rest = _parse_job_token(raw)
        if not title:
            continue
        if not _job_allowed_for_person(person, rest):
            continue
        out.append((title, rest))
    return tuple(out)


def _primary_childcare_pull(
    person: "Person",
    childcare_duty_factor: float,
    childcare_kinship_bonus_01: float = 0.0,
) -> float:
    """How strongly this person should stay in the implicit home-childcare role."""
    duty = _clamp(float(childcare_duty_factor or 0.0), 0.0, 1.0)
    kin_bonus = _clamp(float(childcare_kinship_bonus_01 or 0.0), 0.0, 1.0)
    kin_signal = kin_bonus * PRIMARY_CHILDCARE_KIN_SIGNAL_WEIGHT
    if max(duty, kin_signal) < PRIMARY_CHILDCARE_DUTY_THRESHOLD:
        return 0.0
    if (person.gender or "").strip().lower() != "female":
        return 0.0
    if (person.gender_mind or "").strip().lower() == "masculine":
        return 0.0
    return _clamp(
        duty / CAREER_CHILD_DUTY_FACTOR_CAP
        + kin_bonus * PRIMARY_CHILDCARE_KIN_PULL_WEIGHT,
        0.0,
        1.0,
    )


def _job_home_childcare_compatible(job_title: str | None, job_family: str | None = None) -> bool:
    """True for roles plausibly done in or around one's own household."""
    jk = normalize_job_catalog_key(job_title or "")
    if not jk:
        return False
    fam = (job_family or "").strip().lower()
    if fam == "household_care":
        return True
    if fam == "domestic":
        return True
    home_parts = (
        "parent",
        "child rearer",
        "child watcher",
        "caretaker",
        "caregiver",
        "household",
        "hearth",
        "camp cook",
        "gatherer near camp",
        "stores keeper",
        "spinner",
        "weaver",
        "seamstress",
        "tailor",
        "basket maker",
        "bead stringer",
    )
    return any(part in jk for part in home_parts)


def _person_age(person: "Person", year: int) -> int:
    return int(year) - int(person.birthyear)


def _trait_ideal_strength(trait_values: dict[str, float], key: str, default: float = 0.5) -> float:
    raw = trait_values.get(key)
    if raw is None:
        return float(default)
    try:
        return _clamp(1.0 - abs(float(raw)) / 100.0, 0.0, 1.0)
    except (TypeError, ValueError):
        return float(default)


def _trait_positive_strength(trait_values: dict[str, float], key: str) -> float:
    raw = trait_values.get(key)
    if raw is None:
        return 0.0
    try:
        return _clamp(float(raw) / 100.0, 0.0, 1.0)
    except (TypeError, ValueError):
        return 0.0


def _class_band_from_archetype(archetype: JobArchetypeParams) -> str:
    return (archetype.class_band or "commoner").strip().lower() or "commoner"


def _social_standing_from_archetype(archetype: JobArchetypeParams) -> float:
    return round(
        _clamp(
            0.55 * float(archetype.public_prestige_01)
            + 0.45 * float(archetype.perceived_worth_01),
            0.0,
            1.0,
        ),
        4,
    )


def _default_housing_status(
    ctx: "SimulationContext", rec: "SimulationPersonRecord", year: int
) -> str:
    current = (rec.person.housing_status or "").strip().lower()
    if current:
        return current
    if rec.person.employer_person_id is not None:
        return "employer_household"
    if rec.person.partner_person_id is not None:
        return "own_household"
    if _living_parent_records_same_settlement(ctx, rec):
        return "family_home"
    if _household_dependent_minor_count(ctx, rec, year) > 0:
        return "own_household"
    return "own_household"


def _apply_job_archetype_state(
    person: "Person",
    *,
    job: str,
    archetype: JobArchetypeParams,
    housing_status: str | None = None,
    household_role: str | None = None,
    host_person_id: int | None = None,
    employer_person_id: int | None = None,
    status_tendency: str | None = None,
    job_prosperity_01: float | None = None,
) -> "Person":
    return replace(
        person,
        job_market_type=archetype.job_market_type,
        housing_status=housing_status or person.housing_status,
        household_role=household_role,
        host_person_id=host_person_id,
        employer_person_id=employer_person_id,
        social_class_band=_class_band_from_archetype(archetype),
        social_standing_01=_social_standing_from_archetype(archetype),
        societal_impact_01=round(float(archetype.societal_impact_01), 4),
        perceived_worth_01=round(float(archetype.perceived_worth_01), 4),
        status_tendency=status_tendency if status_tendency is not None else person.status_tendency,
        job_prosperity_01=(
            round(float(job_prosperity_01), 5)
            if job_prosperity_01 is not None
            else person.job_prosperity_01
        ),
    )


def _holds_formal_government_office(ctx: "SimulationContext", person_id: int) -> bool:
    for seat in getattr(ctx, "gov_office_seats", {}).values():
        if getattr(seat, "holder_person_id", None) == int(person_id):
            return True
    return False


def _patronage_strength_for_client(ctx: "SimulationContext", person_id: int) -> float:
    strength = 0.0
    for tie in getattr(ctx, "patronage_ties", {}).values():
        if int(getattr(tie, "client_person_id", 0) or 0) != int(person_id):
            continue
        if str(getattr(tie, "status", "active") or "active").strip().lower() != "active":
            continue
        strength = max(strength, float(getattr(tie, "strength_01", 0.0) or 0.0))
    return _clamp(strength, 0.0, 1.0)


def _residence_settlement_state(
    ctx: "SimulationContext", rec: "SimulationPersonRecord"
):
    sid = _residence_settlement_id(rec)
    if not sid:
        return None
    return ctx.settlements_by_id.get(sid)


def _prestige_local_opportunity(ctx: "SimulationContext", rec: "SimulationPersonRecord") -> float:
    st = _residence_settlement_state(ctx, rec)
    if st is None:
        return 0.0
    pop = max(0, int(getattr(st, "resident_count", 0) or 0))
    prosperity = _clamp(float(getattr(st, "prosperity_pool", 0.0) or 0.0) / 2.5, 0.0, 1.0)
    market = _clamp(float(getattr(st, "market_pull", 0.0) or 0.0), 0.0, 1.0)
    stability = _clamp(float(getattr(st, "stability", 0.5) or 0.5), 0.0, 1.0)
    scale = _clamp(pop / 80.0, 0.0, 1.0)
    network_bonus = 0.06 if getattr(st, "trade_network_id", None) else 0.0
    return _clamp(
        scale * 0.30 + prosperity * 0.30 + market * 0.24 + stability * 0.10 + network_bonus,
        0.0,
        1.0,
    )


def _prestige_target_match(
    target: PrestigeTarget,
    rec: "SimulationPersonRecord",
    archetypes: JobArchetypeCatalog,
) -> float:
    job_key = normalize_job_catalog_key(rec.person.job or "")
    if not job_key:
        return 0.0
    if job_key == normalize_job_catalog_key(target.job):
        return 0.0
    score = 0.0
    if any(token in job_key for token in target.source_tokens):
        score += 0.34
    archetype = archetypes.lookup(rec.person.job)
    role = (getattr(archetype, "role_family", "") or "").strip().lower()
    if role and any(token in role for token in target.source_tokens):
        score += 0.14
    if not score and target.job in {"merchant", "scholar", "physician", "priest"}:
        score = 0.08
    return _clamp(score, 0.0, 1.0)


def _prestige_candidate_score(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    *,
    fitness: CareerFitness,
    pressure: float,
    trait_values: dict[str, float],
    patronage_strength: float,
    status_catalog: StatusEchelonCatalog,
) -> float:
    echelon = status_catalog.echelon_for_person(rec.person)
    standing = _clamp(float(rec.person.social_standing_01 or 0.0), 0.0, 1.0)
    household = _clamp(float(rec.person.household_prosperity or 0.0) / 5.0, 0.0, 1.0)
    job_success = _clamp(float(rec.person.job_prosperity_01 or 0.0), 0.0, 1.0)
    local = _prestige_local_opportunity(ctx, rec)
    trait_push = _clamp(
        _trait_positive_strength(trait_values, "ambition") * 0.26
        + _trait_positive_strength(trait_values, "persuasion") * 0.18
        + _trait_ideal_strength(trait_values, "discipline") * 0.20
        + _trait_ideal_strength(trait_values, "focus") * 0.16
        + _trait_ideal_strength(trait_values, "honesty") * 0.10
        + _trait_ideal_strength(trait_values, "civics") * 0.10,
        0.0,
        1.0,
    )
    score = (
        float(fitness.score) * 0.30
        + standing * 0.16
        + household * 0.13
        + job_success * 0.10
        + local * 0.13
        + patronage_strength * 0.11
        + trait_push * 0.15
    )
    score *= _clamp(float(echelon.prestige_access_multiplier), 0.35, 2.1)
    score -= _clamp(float(pressure), 0.0, 2.0) * 0.05
    return _clamp(score, 0.0, 1.0)


def _patron_power(
    status_catalog: StatusEchelonCatalog, rec: "SimulationPersonRecord"
) -> float:
    echelon = status_catalog.echelon_for_person(rec.person)
    standing = _clamp(float(rec.person.social_standing_01 or 0.0), 0.0, 1.0)
    prosperity = _clamp(float(rec.person.household_prosperity or 0.0) / 5.0, 0.0, 1.0)
    office = 0.12 if (rec.person.job_market_type or "").strip().lower() == "office" else 0.0
    return _clamp(float(echelon.patronage_power_01) + standing * 0.20 + prosperity * 0.18 + office, 0.0, 1.0)


def _patrons_by_settlement(
    ctx: "SimulationContext", status_catalog: StatusEchelonCatalog
) -> dict[str, list["SimulationPersonRecord"]]:
    patrons: dict[str, list[SimulationPersonRecord]] = {}
    for rec in ctx.iter_current_people(sorted_by_id=True):
        if rec.person_id not in ctx.current_people_ids:
            continue
        sid = _residence_settlement_id(rec)
        if not sid:
            continue
        if _patron_power(status_catalog, rec) < 0.34:
            continue
        patrons.setdefault(sid, []).append(rec)
    for bucket in patrons.values():
        bucket.sort(
            key=lambda r: (
                -_patron_power(status_catalog, r),
                -float(r.person.social_standing_01 or 0.0),
                int(r.person_id),
            )
        )
    return patrons


def _best_patron_for_candidate(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    *,
    status_catalog: StatusEchelonCatalog,
    patrons_by_sid: dict[str, list["SimulationPersonRecord"]],
    trait_values: dict[str, float],
) -> tuple["SimulationPersonRecord", float] | None:
    sid = _residence_settlement_id(rec)
    if not sid:
        return None
    candidates = patrons_by_sid.get(sid, ())
    if not candidates:
        return None
    persuasion = _trait_positive_strength(trait_values, "persuasion")
    loyalty = _trait_ideal_strength(trait_values, "loyalty")
    honesty = _trait_ideal_strength(trait_values, "honesty")
    best: tuple[float, SimulationPersonRecord] | None = None
    for patron in candidates:
        if patron.person_id == rec.person_id:
            continue
        power = _patron_power(status_catalog, patron)
        if power < 0.34:
            continue
        score = _clamp(power * 0.72 + persuasion * 0.12 + loyalty * 0.08 + honesty * 0.08, 0.0, 1.0)
        cand = (score, patron)
        if best is None or (cand[0], -int(cand[1].person_id)) > (best[0], -int(best[1].person_id)):
            best = cand
    if best is None:
        return None
    return best[1], best[0]


def _grant_patronage_tie(
    ctx: "SimulationContext",
    *,
    patron: "SimulationPersonRecord",
    client: "SimulationPersonRecord",
    year: int,
    strength: float,
    target_job: str | None,
) -> None:
    from library.simulation_context import SimulationPatronageTie

    sid = _residence_settlement_id(client)
    key = (int(patron.person_id), int(client.person_id), "elite_advancement")
    existing = getattr(ctx, "patronage_ties", {}).get(key)
    old_strength = float(getattr(existing, "strength_01", 0.0) or 0.0) if existing else 0.0
    new_strength = _clamp(max(old_strength, float(strength)), 0.0, 1.0)
    ctx.patronage_ties[key] = SimulationPatronageTie(
        patron_person_id=int(patron.person_id),
        client_person_id=int(client.person_id),
        tie_kind="elite_advancement",
        strength_01=round(new_strength, 5),
        status="active",
        start_year=(
            int(getattr(existing, "start_year"))
            if existing is not None and getattr(existing, "start_year", None) is not None
            else int(year)
        ),
        settlement_id=sid,
        updated_year=int(year),
    )
    if existing is not None and old_strength >= new_strength:
        return
    ctx._record_simulation_event(
        int(year),
        "patronage_granted",
        {
            "year": int(year),
            "patron_person_id": int(patron.person_id),
            "client_person_id": int(client.person_id),
            "settlement_id": sid,
            "tie_kind": "elite_advancement",
            "strength_01": round(new_strength, 5),
            "target_job": target_job,
            "details": (
                f"{patron.person.full_name} extended patronage to "
                f"{client.person.full_name}."
            ),
        },
    )


def _select_prestige_target(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    *,
    score: float,
    patronage_strength: float,
    archetypes: JobArchetypeCatalog,
) -> PrestigeTarget | None:
    st = _residence_settlement_state(ctx, rec)
    if st is None:
        return None
    pop = int(getattr(st, "resident_count", 0) or 0)
    market = float(getattr(st, "market_pull", 0.0) or 0.0)
    household = float(rec.person.household_prosperity or 0.0)
    matches: list[tuple[float, PrestigeTarget]] = []
    for target in PRESTIGE_TARGETS:
        if pop < int(target.min_population):
            continue
        if market < float(target.min_market_pull):
            continue
        if household + patronage_strength * 2.0 < float(target.min_household_prosperity):
            continue
        if score < float(target.min_score):
            continue
        match = _prestige_target_match(target, rec, archetypes)
        if match <= 0.0:
            continue
        matches.append((match + score - target.min_score, target))
    if not matches:
        return None
    matches.sort(key=lambda item: (-item[0], item[1].job))
    return matches[0][1]


def _promote_to_prestige_job(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    *,
    year: int,
    target: PrestigeTarget,
    score: float,
    patron: "SimulationPersonRecord" | None,
    archetypes: JobArchetypeCatalog,
) -> bool:
    if _holds_formal_government_office(ctx, int(rec.person_id)):
        return False
    previous_job = rec.person.job
    previous_standing = float(rec.person.social_standing_01 or 0.0)
    archetype = archetypes.lookup(target.job)
    rec.person = replace(
        rec.person,
        job=target.job,
        job_assigned_year=int(year),
        job_tier="premium",
        employment_status="employed",
        unemployment_started_year=None,
        last_job=previous_job,
        status_tendency="high",
    )
    rec.person = _apply_job_archetype_state(
        rec.person,
        job=target.job,
        archetype=archetype,
        housing_status=_default_housing_status(ctx, rec, year),
        household_role=archetype.role_family if archetype.job_market_type == "office" else rec.person.household_role,
        job_prosperity_01=max(float(rec.person.job_prosperity_01 or 0.0), float(archetype.personal_prosperity_01)),
    )
    new_standing = max(
        _social_standing_from_archetype(archetype),
        previous_standing + 0.045,
        min(0.94, float(score) * 0.92),
    )
    rec.person = replace(
        rec.person,
        social_standing_01=round(_clamp(new_standing, 0.0, 1.0), 4),
        social_class_band=archetype.class_band,
    )
    sid = _residence_settlement_id(rec)
    event_payload = {
        "year": int(year),
        "person_id": int(rec.person_id),
        "previous_job": previous_job,
        "new_job": target.job,
        "settlement_id": sid,
        "score": round(float(score), 5),
        "previous_social_standing_01": round(previous_standing, 5),
        "new_social_standing_01": rec.person.social_standing_01,
        "social_class_band": rec.person.social_class_band,
        "patron_person_id": int(patron.person_id) if patron is not None else None,
    }
    ctx._record_simulation_event(int(year), target.event_type, event_payload)
    ctx._record_simulation_event(
        int(year),
        "status_rise",
        {
            **event_payload,
            "event_reason": "prestige_mobility",
            "details": f"{rec.person.full_name} rose into {target.job}.",
        },
    )
    return True


def _prestige_mobility_pass(
    ctx: "SimulationContext",
    year: int,
    eligible: list[tuple["SimulationPersonRecord", CareerFitness, float, dict[str, float]]],
) -> tuple[int, int, int]:
    status_catalog = StatusEchelonCatalog.load(ctx.db_path)
    archetypes = JobArchetypeCatalog.load(ctx.db_path)
    patrons_by_sid = _patrons_by_settlement(ctx, status_catalog)
    considered: list[
        tuple[
            float,
            "SimulationPersonRecord",
            CareerFitness,
            float,
            dict[str, float],
            float,
        ]
    ] = []
    for rec, fitness, pressure, traits in eligible:
        if rec.person_id not in ctx.current_people_ids:
            continue
        if int(year) - int(rec.person.birthyear) < PRESTIGE_MOBILITY_MIN_AGE:
            continue
        if not rec.person.job or (rec.person.employment_status or "").strip().lower() != "employed":
            continue
        market_type = (rec.person.job_market_type or "settlement_market").strip().lower()
        if market_type in {"household_care", "vice", "criminal"}:
            continue
        patronage = _patronage_strength_for_client(ctx, int(rec.person_id))
        score = _prestige_candidate_score(
            ctx,
            rec,
            fitness=fitness,
            pressure=pressure,
            trait_values=traits,
            patronage_strength=patronage,
            status_catalog=status_catalog,
        )
        if score >= PRESTIGE_PATRONAGE_SCORE_THRESHOLD:
            considered.append((score, rec, fitness, pressure, traits, patronage))
    considered.sort(key=lambda item: (-item[0], int(item[1].person_id)))

    promotions_by_sid: dict[str, int] = {}
    promotions = 0
    patronages = 0
    falls = 0
    for score, rec, _fitness, _pressure, traits, patronage in considered:
        sid = _residence_settlement_id(rec)
        if not sid:
            continue
        st = ctx.settlements_by_id.get(sid)
        pop = int(getattr(st, "resident_count", 0) or 0) if st is not None else 0
        cap = min(
            PRESTIGE_MAX_PROMOTIONS_PER_SETTLEMENT,
            max(1, 1 + pop // 80),
        )
        if promotions_by_sid.get(sid, 0) >= cap:
            continue
        patron_pair = _best_patron_for_candidate(
            ctx,
            rec,
            status_catalog=status_catalog,
            patrons_by_sid=patrons_by_sid,
            trait_values=traits,
        )
        patron = patron_pair[0] if patron_pair is not None else None
        patron_strength = patron_pair[1] if patron_pair is not None else 0.0
        if patron is not None and score >= PRESTIGE_PATRONAGE_SCORE_THRESHOLD:
            before = _patronage_strength_for_client(ctx, int(rec.person_id))
            _grant_patronage_tie(
                ctx,
                patron=patron,
                client=rec,
                year=year,
                strength=patron_strength,
                target_job=None,
            )
            if _patronage_strength_for_client(ctx, int(rec.person_id)) > before:
                patronages += 1
            patronage = max(patronage, patron_strength)
            score = _prestige_candidate_score(
                ctx,
                rec,
                fitness=_fitness,
                pressure=_pressure,
                trait_values=traits,
                patronage_strength=patronage,
                status_catalog=status_catalog,
            )
        target = _select_prestige_target(
            ctx,
            rec,
            score=score,
            patronage_strength=patronage,
            archetypes=archetypes,
        )
        if target is None:
            continue
        rng = random.Random(
            int(year) * 1_000_003
            + int(rec.person_id) * 97
            + int(ctx.placename_rng_salt)
            + 42_013
        )
        probability = _clamp(0.08 + (score - target.min_score) * 0.75, 0.0, 0.46)
        if score < PRESTIGE_HIGH_CONFIDENCE_SCORE and rng.random() > probability:
            continue
        if patron is not None:
            _grant_patronage_tie(
                ctx,
                patron=patron,
                client=rec,
                year=year,
                strength=max(patron_strength, patronage),
                target_job=target.job,
            )
        if _promote_to_prestige_job(
            ctx,
            rec,
            year=year,
            target=target,
            score=score,
            patron=patron,
            archetypes=archetypes,
        ):
            promotions += 1
            promotions_by_sid[sid] = promotions_by_sid.get(sid, 0) + 1

    for rec, _fitness, pressure, _traits in eligible:
        standing = float(rec.person.social_standing_01 or 0.0)
        prosperity = float(rec.person.household_prosperity or 0.0)
        if standing < PRESTIGE_FALL_STANDING_THRESHOLD:
            continue
        if prosperity >= PRESTIGE_BANKRUPTCY_PROSPERITY_THRESHOLD and pressure < 1.05:
            continue
        severity = _clamp((PRESTIGE_BANKRUPTCY_PROSPERITY_THRESHOLD - prosperity) * 0.12 + pressure * 0.015, 0.015, 0.08)
        rec.person = replace(
            rec.person,
            social_standing_01=round(_clamp(standing - severity, 0.0, 1.0), 4),
        )
        event_type = "bankruptcy" if prosperity < PRESTIGE_BANKRUPTCY_PROSPERITY_THRESHOLD else "status_fall"
        ctx._record_simulation_event(
            int(year),
            event_type,
            {
                "year": int(year),
                "person_id": int(rec.person_id),
                "settlement_id": _residence_settlement_id(rec),
                "previous_social_standing_01": round(standing, 5),
                "new_social_standing_01": rec.person.social_standing_01,
                "household_prosperity": round(prosperity, 5),
                "resource_pressure": round(float(pressure), 5),
            },
        )
        if event_type != "status_fall":
            ctx._record_simulation_event(
                int(year),
                "status_fall",
                {
                    "year": int(year),
                    "person_id": int(rec.person_id),
                    "settlement_id": _residence_settlement_id(rec),
                    "fall_reason": event_type,
                    "previous_social_standing_01": round(standing, 5),
                    "new_social_standing_01": rec.person.social_standing_01,
                },
            )
        falls += 1
    return promotions, patronages, falls


def _household_dependent_minor_count(
    ctx: "SimulationContext", rec: "SimulationPersonRecord", year: int, indexes: object | None = None
) -> int:
    if indexes is not None:
        counts = getattr(indexes, "dependent_minor_count_by_adult", {})
        cached = counts.get(int(rec.person_id))
        if cached is not None:
            return int(cached)
    try:
        from library.simulation_household_care import dependent_minors_in_implicit_household

        return int(dependent_minors_in_implicit_household(ctx, rec, year, indexes=indexes))
    except Exception:
        return 0


def _living_parent_records_same_settlement(
    ctx: "SimulationContext", rec: "SimulationPersonRecord"
) -> tuple["SimulationPersonRecord", ...]:
    sid = _residence_settlement_id(rec)
    if not sid:
        return ()
    out: list[SimulationPersonRecord] = []
    for pid in (rec.father_id, rec.mother_id):
        if pid is None or int(pid) not in ctx.current_people_ids:
            continue
        parent = ctx.id_to_record.get(int(pid))
        if parent is not None and _residence_settlement_id(parent) == sid:
            out.append(parent)
    return tuple(out)


def _adult_child_support_score(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    parents: tuple["SimulationPersonRecord", ...],
    *,
    pressure: float,
    trait_values: dict[str, float],
    care_contribution: float,
) -> float:
    if not parents:
        return 0.0
    parent_traits = [work_trait_values(parent.person) for parent in parents]
    parent_care_values: list[float] = []
    parent_frugality_values: list[float] = []
    for traits in parent_traits:
        for key in ("nurturance", "empathy", "generosity", "loyalty"):
            parent_care_values.append(_trait_ideal_strength(traits, key))
        parent_frugality_values.append(_trait_positive_strength(traits, "frugality"))
    parent_care = (
        sum(parent_care_values) / len(parent_care_values)
        if parent_care_values
        else 0.5
    )
    parent_frugality = (
        sum(parent_frugality_values) / len(parent_frugality_values)
        if parent_frugality_values
        else 0.0
    )
    household_prosperity = _clamp(
        float(rec.person.household_prosperity or 0.0) / 2.5,
        0.0,
        1.0,
    )
    manipulation = _clamp(
        _trait_positive_strength(trait_values, "persuasion") * 0.48
        + (1.0 - _trait_ideal_strength(trait_values, "honesty")) * 0.28
        + (1.0 - _trait_ideal_strength(trait_values, "empathy")) * 0.18,
        0.0,
        1.0,
    )
    contribution = _clamp(
        care_contribution
        + float(rec.person.job_prosperity_01 or 0.0) * 0.7
        + (0.25 if rec.person.job else 0.0),
        0.0,
        1.0,
    )
    score = (
        parent_care * 0.34
        + household_prosperity * 0.22
        + manipulation * 0.22
        + contribution * 0.22
        - _clamp(float(pressure), 0.0, 2.0) * 0.10
        - parent_frugality * 0.09
    )
    return _clamp(score, 0.0, 1.0)


def _record_housing_event(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    year: int,
    event_type: str,
    *,
    housing_status: str,
    pressure: float,
    support_score: float | None = None,
    details: str | None = None,
) -> None:
    ctx._record_simulation_event(
        int(year),
        event_type,
        {
            "year": int(year),
            "person_id": int(rec.person_id),
            "housing_status": housing_status,
            "resource_pressure": round(float(pressure), 4),
            "support_score": (
                round(float(support_score), 4) if support_score is not None else None
            ),
            "details": details
            or f"{rec.person.full_name} entered {housing_status.replace('_', ' ')}.",
        },
    )


def _cross_gender_job_exception(
    person: "Person",
    restriction: str | None,
    trait_values: dict[str, float] | None = None,
) -> bool:
    if restriction is None:
        return False
    g = (person.gender or "").strip().lower()
    traits = trait_values if trait_values is not None else work_trait_values(person)
    if restriction == "male" and g == "female":
        return _female_exception_for_male_only_job_with_traits(person, traits)
    if restriction == "female" and g == "male":
        return _male_exception_for_female_only_job_with_traits(person, traits)
    return False


def premium_job_roll_probability(fitness_score: float | None) -> float:
    """Scaled 0..PREMIUM_JOB_MAX_PROB when fitness is at or above the threshold."""
    if fitness_score is None:
        return 0.0
    fs = _clamp(float(fitness_score), 0.0, 1.0)
    if fs < PREMIUM_JOB_FITNESS_THRESHOLD:
        return 0.0
    span = 1.0 - PREMIUM_JOB_FITNESS_THRESHOLD
    if span <= 0:
        return PREMIUM_JOB_MAX_PROB
    return ((fs - PREMIUM_JOB_FITNESS_THRESHOLD) / span) * PREMIUM_JOB_MAX_PROB


def _pick_job_tier_and_title(
    *,
    rng: random.Random,
    common_entries: tuple[tuple[str, str | None], ...],
    premium_entries: tuple[tuple[str, str | None], ...],
    fitness_score: float | None,
) -> tuple[str, Literal["common", "premium"], str | None]:
    if not common_entries and premium_entries:
        title, rest = rng.choice(list(premium_entries))
        return title, "premium", rest
    if not common_entries:
        return "", "common", None
    if not premium_entries:
        title, rest = rng.choice(list(common_entries))
        return title, "common", rest
    p_roll = premium_job_roll_probability(fitness_score)
    if p_roll > 0.0 and rng.random() < p_roll:
        title, rest = rng.choice(list(premium_entries))
        return title, "premium", rest
    title, rest = rng.choice(list(common_entries))
    return title, "common", rest


@lru_cache(maxsize=8)
def _genome_job_rows(db_path_s: str) -> tuple[dict[str, Any], ...]:
    path = Path(db_path_s)
    with closing(_connect(path)) as conn:
        try:
            raw = conn.execute("SELECT * FROM genome_jobs").fetchall()
        except sqlite3.OperationalError:
            return ()
    return tuple({k: r[k] for k in r.keys()} for r in raw)


@lru_cache(maxsize=40)
def _career_job_options_for_era(
    db_path_s: str, era_key: str
) -> tuple[CareerGenomeJobOption, ...]:
    rows = _genome_job_rows(db_path_s)
    job_col = ERA_JOB_COLUMNS.get(era_key)
    if not rows or job_col is None:
        return ()
    path = Path(db_path_s)
    economics_catalog = JobEconomicsCatalog.load(path)
    market_catalog = JobMarketCatalog.load(path)
    archetype_catalog = JobArchetypeCatalog.load(path)
    premium_col = ERA_PREMIUM_COLUMNS.get(era_key)

    def build_entries(
        raw_jobs: object, tier: Literal["common", "premium"]
    ) -> tuple[CareerJobEntry, ...]:
        out: list[CareerJobEntry] = []
        for raw in _split_jobs(raw_jobs):
            title, restriction = _parse_job_token(raw)
            if not title:
                continue
            market = market_catalog.lookup(title)
            archetype = archetype_catalog.lookup(title)
            out.append(
                CareerJobEntry(
                    title=title,
                    tier=tier,
                    restriction=restriction,
                    job_key=normalize_job_catalog_key(title),
                    economics=economics_catalog.lookup(title, era_key, tier=tier),
                    market=market,
                    archetype=archetype,
                    home_compatible=_job_home_childcare_compatible(
                        title, market.job_family
                    )
                    or archetype.home_compatible,
                )
            )
        return tuple(out)

    options: list[CareerGenomeJobOption] = []
    for row in rows:
        trait = str(row.get("trait") or "").strip()
        if not trait:
            continue
        common_entries = build_entries(row.get(job_col), "common")
        premium_entries = build_entries(row.get(premium_col), "premium") if premium_col else ()
        if not common_entries and not premium_entries:
            continue
        options.append(
            CareerGenomeJobOption(
                trait=trait,
                deviation_band=str(row.get("deviation_band") or "").strip().lower(),
                descriptor=str(row.get("descriptor") or "").strip(),
                status_tendency=str(row.get("status_tendency") or "").strip(),
                leader_quality=str(row.get("leader_quality") or "").strip(),
                leader_tendency=str(row.get("leader_tendency") or "").strip(),
                society_need=_row_float(row, "society_need", 0.5),
                selfish_desperate=_row_float(row, "selfish_desperate", 0.0),
                common_entries=common_entries,
                premium_entries=premium_entries,
            )
        )
    return tuple(options)


@lru_cache(maxsize=160)
def _career_job_options_for_era_and_allowance(
    db_path_s: str,
    era_key: str,
    allow_male_restricted: bool,
    allow_female_restricted: bool,
) -> tuple[CareerGenomeJobOption, ...]:
    def allowed(restriction: str | None) -> bool:
        if restriction == "male":
            return bool(allow_male_restricted)
        if restriction == "female":
            return bool(allow_female_restricted)
        return True

    out: list[CareerGenomeJobOption] = []
    for option in _career_job_options_for_era(db_path_s, era_key):
        common_entries = tuple(e for e in option.common_entries if allowed(e.restriction))
        premium_entries = tuple(e for e in option.premium_entries if allowed(e.restriction))
        if not common_entries and not premium_entries:
            continue
        out.append(
            replace(
                option,
                common_entries=common_entries,
                premium_entries=premium_entries,
            )
        )
    return tuple(out)


@lru_cache(maxsize=8)
def _genome_composite_rows(db_path_s: str) -> tuple[dict[str, Any], ...]:
    path = Path(db_path_s)
    with closing(_connect(path)) as conn:
        try:
            raw = conn.execute("SELECT * FROM genome_composites").fetchall()
        except sqlite3.OperationalError:
            return ()
    return tuple({k: r[k] for k in r.keys()} for r in raw)


@lru_cache(maxsize=32)
def _species_maturity_lookup(db_path_s: str, species: str, ethnic: str) -> int | None:
    path = Path(db_path_s)
    with closing(_connect(path)) as conn:
        try:
            row = conn.execute(
                """
                SELECT maturity FROM species
                WHERE species = ? AND ethnic = ?
                LIMIT 1
                """,
                (species.strip(), ethnic.strip()),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
    if row is None:
        return None
    maturity = _as_int(row["maturity"], 0)
    return maturity if maturity > 0 else None


def _person_maturity_age(person: "Person", db_path: Path | str) -> int:
    if person.min_fertility_age is not None:
        return max(1, int(person.min_fertility_age))
    path_s = str(Path(db_path).resolve())
    maturity = _species_maturity_lookup(path_s, person.species, person.ethnic)
    return maturity if maturity is not None else 18


def _eligible_for_job(person: "Person", db_path: Path | str, year: int, era: str) -> bool:
    age = int(year) - int(person.birthyear)
    return age >= job_eligibility_age(_person_maturity_age(person, db_path), era)


def _career_assignment_seed(
    *, year: int, person_id: int, salt: int, historical_year: int
) -> int:
    return (
        int(year) * 700_001
        + int(historical_year) * 101
        + int(person_id) * 9_176
        + int(salt)
        + 31_337
    )


def _event_seed(*, year: int, person_id: int, salt: int, stream: int) -> int:
    return int(year) * 900_001 + int(person_id) * 12_289 + int(salt) + int(stream)


def _person_residence_region(ctx: "SimulationContext", rec: "SimulationPersonRecord") -> str | None:
    return ctx._residence_region_id(rec)


def _resource_pressure_from_facts(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    resource_facts: YearResourceFacts,
) -> float:
    sid = (
        rec.person.current_settlement_id
        or rec.person.birthplace_settlement_id
        or ""
    ).strip()
    pressure = 0.0
    if sid:
        pressure = max(
            pressure,
            float(resource_facts.settlement_food_pressure.get(sid, 0.0)),
        )
    rid = _person_residence_region(ctx, rec)
    if rid:
        try:
            cap = int(resource_facts.region_cap.get(rid, 0))
            pop = int(resource_facts.region_population.get(rid, 0))
            if cap > 0:
                pressure = max(pressure, float(pop) / float(cap))
        except (LookupError, ValueError):
            pass
    return round(_clamp(pressure, 0.0, 2.0), 4)


def resource_pressure_for_person(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    resource_facts: YearResourceFacts | None = None,
) -> float:
    """Return local pressure where 1.0 is around capacity and >1 is scarce."""
    if resource_facts is not None:
        return resource_facts.pressure_for(ctx, rec)
    sid = (
        rec.person.current_settlement_id
        or rec.person.birthplace_settlement_id
        or ""
    ).strip()
    pressure = 0.0
    if sid:
        st = ctx.settlements_by_id.get(sid)
        if st is not None:
            try:
                pressure = max(pressure, float(st.food_pressure))
            except (TypeError, ValueError):
                pass
    rid = _person_residence_region(ctx, rec)
    if rid:
        try:
            cap = ctx.effective_regional_population_cap(rid)
            pop = ctx.count_alive_in_region(rid)
            if cap > 0:
                pressure = max(pressure, float(pop) / float(cap))
        except (LookupError, ValueError):
            pass
    return round(_clamp(pressure, 0.0, 2.0), 4)


def _settlement_job_market_snapshot(
    ctx: "SimulationContext",
    settlement_id: str,
    market_snapshots: YearJobMarketSnapshots | None = None,
) -> tuple[int | None, float, float, dict[str, int], dict[str, int]]:
    sid = (settlement_id or "").strip()
    if not sid:
        return None, 0.0, 0.5, {}, {}
    if market_snapshots is not None:
        snap = market_snapshots.snapshot_for(ctx, sid)
        return (
            snap.settlement_pop,
            snap.market_pull,
            snap.stability,
            dict(snap.job_counts),
            dict(snap.family_counts),
        )
    st = ctx.settlements_by_id.get(sid)
    settlement_pop = int(getattr(st, "resident_count", 0) or 0) if st is not None else None
    market_pull = float(getattr(st, "market_pull", 0.0) or 0.0) if st is not None else 0.0
    stability = float(getattr(st, "stability", 0.5) or 0.5) if st is not None else 0.5
    catalog = JobMarketCatalog.load(ctx.db_path)
    job_counts: dict[str, int] = {}
    family_counts: dict[str, int] = {}
    for other in ctx.iter_current_people(sorted_by_id=False):
        if (other.person.current_settlement_id or other.person.birthplace_settlement_id or "").strip() != sid:
            continue
        if (other.person.employment_status or "").strip().lower() != "employed":
            continue
        job = (other.person.job or "").strip()
        if not job:
            continue
        jk = normalize_job_catalog_key(job)
        if not jk:
            continue
        job_counts[jk] = job_counts.get(jk, 0) + 1
        fam = catalog.lookup(job).job_family
        family_counts[fam] = family_counts.get(fam, 0) + 1
    return settlement_pop, market_pull, stability, job_counts, family_counts


def _fitness_event_payload(
    rec: "SimulationPersonRecord",
    fitness: CareerFitness,
    pressure: float,
) -> dict[str, Any]:
    return {
        "person_id": rec.person_id,
        "fitness_score": fitness.score,
        "resource_pressure": pressure,
        "near_perfect_traits": list(fitness.near_perfect_traits),
        "high_deviation_traits": list(fitness.high_deviation_traits),
        "weighted_near_perfect_count": fitness.weighted_near_perfect_count,
        "weighted_high_deviation_count": fitness.weighted_high_deviation_count,
    }


def refresh_career_fitness(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    year: int,
    *,
    pressure: float | None = None,
    trait_values: dict[str, float] | None = None,
) -> CareerFitness:
    """Persist current fitness score and log first/material fitness snapshots."""
    fitness = (
        _career_fitness_from_traits(trait_values)
        if trait_values is not None
        else career_fitness(rec.person)
    )
    p = resource_pressure_for_person(ctx, rec) if pressure is None else float(pressure)
    prev = rec.person.career_fitness_score
    changed = prev is None or abs(float(prev) - fitness.score) >= 0.005
    if changed:
        rec.person = replace(rec.person, career_fitness_score=fitness.score)
        payload = {"year": int(year), **_fitness_event_payload(rec, fitness, p)}
        ctx._record_simulation_event(int(year), "career_fitness_updated", payload)
    elif rec.person.career_fitness_score != fitness.score:
        rec.person = replace(rec.person, career_fitness_score=fitness.score)
    return fitness


def job_loss_probability(fitness_score: float, resource_pressure: float) -> float:
    scarcity = max(0.0, float(resource_pressure) - 0.75)
    low_fit = 1.0 - _clamp(float(fitness_score), 0.0, 1.0)
    p = (
        JOB_LOSS_BASE_PROB
        + (low_fit * low_fit * JOB_LOSS_FITNESS_SCALE)
        + (scarcity * JOB_LOSS_PRESSURE_SCALE)
    )
    if fitness_score >= 0.8:
        p *= JOB_LOSS_HIGH_FITNESS_DAMPING
    return round(_clamp(p, 0.0, JOB_LOSS_MAX_PROB), 5)


def rehire_probability(
    fitness_score: float, resource_pressure: float, unemployment_years: int
) -> float:
    scarcity = max(0.0, float(resource_pressure) - 0.75)
    p = (
        REHIRE_BASE_PROB
        + (_clamp(float(fitness_score), 0.0, 1.0) * REHIRE_FITNESS_SCALE)
        + (min(max(0, int(unemployment_years)), 5) * REHIRE_DURATION_BONUS_PER_YEAR)
        - (scarcity * REHIRE_PRESSURE_PENALTY)
    )
    return round(_clamp(p, REHIRE_MIN_PROB, REHIRE_MAX_PROB), 5)


def _job_loss_reason(fitness_score: float, resource_pressure: float) -> str:
    low_fit = fitness_score < 0.45
    scarce = resource_pressure > 0.9
    if low_fit and scarce:
        return "mixed_pressure"
    if low_fit:
        return "low_fitness"
    if scarce:
        return "resource_scarcity"
    return "job_market_churn"


def _unemployment_years(person: "Person", year: int) -> int:
    if person.unemployment_started_year is None:
        return 0
    return max(0, int(year) - int(person.unemployment_started_year))


def _row_float(row: dict[str, Any], key: str, default: float) -> float:
    try:
        return _clamp(float(row.get(key)), 0.0, 1.0)
    except (TypeError, ValueError):
        return float(default)


def _settlement_market_scale(settlement_resident_count: int | None) -> float:
    """0..1 scale where hamlets favor simple jobs and cities can support specialists."""
    if settlement_resident_count is None:
        return 0.55
    pop = max(0, int(settlement_resident_count))
    if pop >= 20_000:
        return 1.0
    if pop >= 2_500:
        return 0.78
    if pop >= 250:
        return 0.48
    return 0.28


def _saturation_multiplier(
    *,
    current_job_count: int,
    current_family_count: int,
    settlement_resident_count: int | None,
    market: JobMarketParams,
) -> float:
    pop = max(1, int(settlement_resident_count or 1))
    scale = _settlement_market_scale(settlement_resident_count)
    curve = (market.saturation_curve or "medium").strip().lower()
    if curve == "flat":
        job_slots = max(2.0, pop / 18.0)
        family_slots = max(4.0, pop / 7.5)
        sharpness = 0.55
    elif curve == "steep":
        job_slots = max(1.0, pop / 95.0 * (0.55 + scale))
        family_slots = max(2.0, pop / 25.0 * (0.55 + scale))
        sharpness = 1.35
    else:
        job_slots = max(1.0, pop / 45.0 * (0.70 + scale))
        family_slots = max(3.0, pop / 14.0 * (0.70 + scale))
        sharpness = 0.95
    load = 0.62 * (max(0, current_job_count) / job_slots) + 0.38 * (
        max(0, current_family_count) / family_slots
    )
    return round(_clamp(1.0 / (1.0 + sharpness * load), 0.08, 1.0), 4)


def _job_market_demand_score(
    *,
    society_need: float,
    selfish_desperate: float,
    job_tier: str,
    settlement_resident_count: int | None,
    resource_pressure: float = 0.0,
    market_pull: float = 0.0,
    stability: float = 0.5,
    current_job_count: int = 0,
    current_family_count: int = 0,
    market: JobMarketParams | None = None,
    saturation: float | None = None,
) -> float:
    jm = market or JobMarketParams(
        "labor", 0.55, 0.20, 0.25, 0.55, "medium", 0.0, 0.0, 0.0, 0.0, 0.60
    )
    scale = _settlement_market_scale(settlement_resident_count)
    need = _clamp(society_need, 0.0, 1.0)
    selfish = _clamp(selfish_desperate, 0.0, 1.0)
    scarcity = _clamp(float(resource_pressure), 0.0, 2.0) / 2.0
    urban_fit = _clamp(1.0 - max(0.0, jm.urban_scale - scale), 0.0, 1.0)
    essential = jm.essential_need * (0.65 + 0.35 * jm.scarcity_resilience * scarcity)
    luxury = jm.luxury_need * (0.35 + 0.65 * _clamp(market_pull, 0.0, 1.0)) * (1.0 - 0.45 * scarcity)
    stability_fit = 1.0
    if jm.job_family in {"trade", "admin", "knowledge", "prestige", "entertainment"}:
        stability_fit = 0.55 + 0.45 * _clamp(stability, 0.0, 1.0)
    market_need = _clamp(max(need, essential) * 0.72 + luxury * 0.28, 0.0, 1.0)
    saturation_score = (
        _clamp(float(saturation), 0.08, 1.0)
        if saturation is not None
        else _saturation_multiplier(
            current_job_count=current_job_count,
            current_family_count=current_family_count,
            settlement_resident_count=settlement_resident_count,
            market=jm,
        )
    )
    # Simple, high-need roles exist everywhere. Specialist, prestige, or selfish
    # roles need enough local population and surplus to become a real market.
    simple_floor = 0.42 + 0.58 * scale
    complexity_penalty = (1.0 - scale) * (0.18 + 0.36 * (1.0 - need + selfish) / 2.0)
    tier_penalty = (1.0 - scale) * (0.26 if job_tier == "premium" else 0.0)
    score = (
        market_need * simple_floor * urban_fit * stability_fit * saturation_score
        - complexity_penalty
        - tier_penalty
    )
    return round(_clamp(score, 0.0, 1.0), 4)


def career_desperation_score(
    *,
    resource_pressure: float,
    unemployment_years: int,
    household_prosperity: float | None = None,
) -> float:
    scarcity = max(0.0, float(resource_pressure) - 0.72) / 1.28
    unemployment = min(1.0, max(0, int(unemployment_years)) / 6.0)
    if household_prosperity is None:
        savings_gap = 0.35
    else:
        savings_gap = 1.0 - _clamp(float(household_prosperity) / 4.0, 0.0, 1.0)
    return round(_clamp(0.46 * scarcity + 0.34 * unemployment + 0.20 * savings_gap, 0.0, 1.0), 4)


def _assignment_weight(
    *,
    trait_match: float,
    wage_yield: float,
    market_demand: float,
    selfish_desperate: float,
    desperation: float,
) -> float:
    demand = _clamp(market_demand, 0.0, 1.0)
    prosper = _clamp(float(wage_yield) / 1.45, 0.0, 1.0) * (
        0.35 + 0.65 * (demand ** 1.1)
    )
    skill_weight = 0.52 - 0.24 * desperation
    selfish_weight = 0.04 + 0.24 * desperation
    wage_weight = 0.24 + 0.10 * desperation
    demand_weight = 1.0 - skill_weight - selfish_weight - wage_weight
    score = (
        skill_weight * _clamp(trait_match, 0.0, 1.0)
        + wage_weight * prosper
        + demand_weight * demand
        + selfish_weight * _clamp(selfish_desperate, 0.0, 1.0)
    )
    return max(0.001, float(score))


def choose_career_assignment(
    person: "Person",
    *,
    person_id: int,
    db_path: Path | str,
    era: str,
    year: int,
    historical_year: int,
    salt: int = 0,
    top_n: int = 5,
    fitness_score: float | None = None,
    settlement_resident_count: int | None = None,
    resource_pressure: float = 0.0,
    market_pull: float = 0.0,
    settlement_stability: float = 0.5,
    current_job_counts: dict[str, int] | None = None,
    current_family_counts: dict[str, int] | None = None,
    unemployment_years: int = 0,
    household_prosperity: float | None = None,
    childcare_duty_factor: float = 0.0,
    childcare_kinship_bonus_01: float = 0.0,
    trait_values: dict[str, float] | None = None,
    world: str = "default",
    magic_physical_leveling_01: float | None = None,
) -> CareerAssignment | None:
    """Pick the best available job for the person's skill, market, and desperation."""
    prof = simulation_timing.active_for_year(year)
    tpc = time.perf_counter
    if prof:
        t0 = tpc()
    path = Path(db_path)
    db_path_s = str(path.resolve())
    era_key = (era or "").strip().lower()
    traits = trait_values if trait_values is not None else work_trait_values(person)
    magic_leveling = (
        magic_physical_leveling_for_world(world, path)
        if magic_physical_leveling_01 is None
        else _clamp(float(magic_physical_leveling_01), 0.0, 1.0)
    )
    allowed_by_restriction = _job_restriction_allowance(person, traits)
    options = _career_job_options_for_era_and_allowance(
        db_path_s,
        era_key,
        bool(allowed_by_restriction.get("male", False)),
        bool(allowed_by_restriction.get("female", False)),
    )
    if not options:
        return None
    if prof:
        simulation_timing.accumulate("careers.assignment_setup", tpc() - t0)
        t0 = tpc()

    candidates: list[
        tuple[
            CareerGenomeJobOption,
            float,
            tuple[CareerJobEntry, ...],
            tuple[CareerJobEntry, ...],
        ]
    ] = []
    for option in options:
        if option.trait not in traits:
            continue
        common_entries = option.common_entries
        premium_entries = option.premium_entries
        if not common_entries and not premium_entries:
            continue
        score = score_genome_job_row(
            float(traits[option.trait]), option.deviation_band
        )
        if score <= 0.0:
            continue
        candidates.append((option, score, common_entries, premium_entries))

    if not candidates:
        if prof:
            simulation_timing.accumulate("careers.assignment_candidates", tpc() - t0)
        return None
    if prof:
        simulation_timing.accumulate("careers.assignment_candidates", tpc() - t0)
        t0 = tpc()
    rng = random.Random(
        _career_assignment_seed(
            year=year,
            person_id=person_id,
            salt=salt,
            historical_year=historical_year,
        )
    )
    premium_roll = premium_job_roll_probability(fitness_score)
    prefer_premium = premium_roll > 0.0 and rng.random() < premium_roll

    scored_jobs: list[
        tuple[
            CareerGenomeJobOption,
            float,
            CareerJobEntry,
            float,
            float,
            float,
            float,
            BodyDemandFit,
            float,
        ]
    ] = []
    desperation = career_desperation_score(
        resource_pressure=resource_pressure,
        unemployment_years=unemployment_years,
        household_prosperity=household_prosperity,
    )
    primary_care_pull = _primary_childcare_pull(
        person, childcare_duty_factor, childcare_kinship_bonus_01
    )
    job_counts = current_job_counts or {}
    family_counts = current_family_counts or {}
    saturation_cache: dict[tuple[str, str, str], float] = {}
    market_demand_cache: dict[tuple[str, str, float, float], float] = {}
    prosperity_cache: dict[str, float] = {}
    for option, trait_score, common_entries, premium_entries in candidates:
        society_need = option.society_need
        selfish_desperate = option.selfish_desperate
        tier_entries: tuple[tuple[CareerJobEntry, ...], ...]
        if (prefer_premium and premium_entries) or (not common_entries and premium_entries):
            tier_entries = (premium_entries,)
        else:
            tier_entries = (common_entries,)
        for entries in tier_entries:
            for entry in entries:
                if entry.archetype.adult_only and (
                    int(year) - int(person.birthyear) < ADULT_HOUSING_MIN_AGE
                ):
                    continue
                if primary_care_pull > 0.0 and not entry.home_compatible:
                    continue
                saturation_key = (
                    entry.job_key,
                    entry.market.job_family,
                    entry.market.saturation_curve,
                )
                saturation = saturation_cache.get(saturation_key)
                if saturation is None:
                    job_count = int(job_counts.get(entry.job_key, 0))
                    family_count = int(family_counts.get(entry.market.job_family, 0))
                    saturation = _saturation_multiplier(
                        current_job_count=job_count,
                        current_family_count=family_count,
                        settlement_resident_count=settlement_resident_count,
                        market=entry.market,
                    )
                    saturation_cache[saturation_key] = saturation
                demand_key = (
                    entry.job_key,
                    entry.tier,
                    float(society_need),
                    float(selfish_desperate),
                )
                market_demand = market_demand_cache.get(demand_key)
                if market_demand is None:
                    job_count = int(job_counts.get(entry.job_key, 0))
                    family_count = int(family_counts.get(entry.market.job_family, 0))
                    market_demand = _job_market_demand_score(
                        society_need=society_need,
                        selfish_desperate=selfish_desperate,
                        job_tier=entry.tier,
                        settlement_resident_count=settlement_resident_count,
                        resource_pressure=resource_pressure,
                        market_pull=market_pull,
                        stability=settlement_stability,
                        current_job_count=job_count,
                        current_family_count=family_count,
                        market=entry.market,
                        saturation=saturation,
                    )
                    market_demand_cache[demand_key] = market_demand
                prosperity_score = prosperity_cache.get(entry.job_key)
                if prosperity_score is None:
                    prosperity_score = _clamp(
                        float(entry.economics.wage_yield) / 1.45, 0.0, 1.0
                    )
                    prosperity_cache[entry.job_key] = prosperity_score
                weight = _assignment_weight(
                    trait_match=trait_score,
                    wage_yield=entry.economics.wage_yield,
                    market_demand=market_demand,
                    selfish_desperate=selfish_desperate,
                    desperation=desperation,
                )
                if primary_care_pull > 0.0 and entry.home_compatible:
                    weight *= 1.0 + PRIMARY_CHILDCARE_HOME_JOB_WEIGHT * primary_care_pull
                body_fit = body_demand_fit_for_person(
                    person,
                    physical_demand_01=entry.archetype.physical_demand_01,
                    leveling_affinity_01=entry.archetype.leveling_affinity_01,
                    era=era_key,
                    magic_leveling_01=magic_leveling,
                    trait_values=traits,
                )
                weight *= body_fit.physical_demand_multiplier
                scored_jobs.append(
                    (
                        option,
                        trait_score,
                        entry,
                        market_demand,
                        prosperity_score,
                        selfish_desperate,
                        saturation,
                        body_fit,
                        weight,
                    )
                )

    if not scored_jobs:
        if prof:
            simulation_timing.accumulate("careers.assignment_score_jobs", tpc() - t0)
        return None
    if prof:
        simulation_timing.accumulate("careers.assignment_score_jobs", tpc() - t0)
        t0 = tpc()
    top = heapq.nsmallest(
        max(1, int(top_n)),
        scored_jobs,
        key=lambda item: (
            -item[8],
            item[2].title,
            item[0].trait,
            item[0].deviation_band,
        ),
    )
    weights = [item[8] for item in top]
    (
        option,
        trait_score,
        entry,
        market_demand,
        prosperity_score,
        selfish_desperate,
        saturation,
        body_fit,
        _weight,
    ) = rng.choices(top, weights=weights, k=1)[0]
    if not entry.title:
        return None
    if prof:
        simulation_timing.accumulate("careers.assignment_top_pick", tpc() - t0)
    return CareerAssignment(
        job=entry.title,
        job_tier=entry.tier,
        job_era=era_key,
        trait=option.trait,
        deviation_band=option.deviation_band,
        descriptor=option.descriptor,
        status_tendency=option.status_tendency,
        leader_quality=option.leader_quality,
        leader_tendency=option.leader_tendency,
        job_sex_restriction=entry.restriction,
        cross_gender_job_exception=_cross_gender_job_exception(
            person, entry.restriction, trait_values=traits
        ),
        society_need=option.society_need,
        selfish_desperate=selfish_desperate,
        job_trait_match_score=round(_clamp(trait_score, 0.0, 1.0), 4),
        job_market_demand_score=market_demand,
        job_prosperity_score=round(prosperity_score, 4),
        job_family=entry.market.job_family,
        job_market_type=entry.archetype.job_market_type,
        role_family=entry.archetype.role_family,
        social_class_band=entry.archetype.class_band,
        social_standing_01=round(
            _clamp(
                0.55 * float(entry.archetype.public_prestige_01)
                + 0.45 * float(entry.archetype.perceived_worth_01),
                0.0,
                1.0,
            ),
            4,
        ),
        societal_impact_01=round(float(entry.archetype.societal_impact_01), 4),
        perceived_worth_01=round(float(entry.archetype.perceived_worth_01), 4),
        care_intensity_01=round(float(entry.archetype.care_intensity_01), 4),
        saturation_score=round(saturation, 4),
        desperation_score=desperation,
        physical_demand_01=round(float(entry.archetype.physical_demand_01), 4),
        effective_physical_demand_01=round(body_fit.effective_physical_demand_01, 4),
        body_power_01=round(body_fit.body_power_01, 4),
        physical_demand_multiplier=round(body_fit.physical_demand_multiplier, 4),
    )


def assign_career_if_eligible(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    year: int,
    *,
    market_snapshots: YearJobMarketSnapshots | None = None,
    pressure: float | None = None,
    fitness: CareerFitness | None = None,
    childcare_duty_factor: float | None = None,
    known_eligible: bool = False,
    historical_year: int | None = None,
    era: str | None = None,
    trait_values: dict[str, float] | None = None,
) -> CareerAssignment | None:
    """Assign or rehire an eligible person, returning details if changed."""
    if outlaw_blocks_normal_career(rec.person):
        normalize_outlaw_labor_state(ctx, rec, year)
        return None
    if rec.person.job:
        return None
    historical_year = (
        int(historical_year)
        if historical_year is not None
        else ctx.get_historical_year(year)
    )
    era = (era or resolve_job_era(historical_year)).strip().lower()
    if not known_eligible and not _eligible_for_job(rec.person, ctx.db_path, year, era):
        return None
    if not known_eligible:
        materialize_adult_profile(ctx, rec, year)
    pressure = (
        resource_pressure_for_person(ctx, rec) if pressure is None else float(pressure)
    )
    traits = trait_values if trait_values is not None else work_trait_values(rec.person)
    fitness = (
        refresh_career_fitness(ctx, rec, year, pressure=pressure, trait_values=traits)
        if fitness is None
        else fitness
    )
    duty = (
        _childcare_duty_factor_safe(ctx, rec, year)
        if childcare_duty_factor is None
        else float(childcare_duty_factor)
    )
    kinship_bonus = _childcare_kinship_bonus_safe(ctx, rec, year) if duty > 0.0 else 0.0
    prof = simulation_timing.active_for_year(year)
    tpc = time.perf_counter
    if prof:
        t0 = tpc()
    sid = _residence_settlement_id(rec)
    (
        settlement_pop,
        market_pull,
        settlement_stability,
        current_job_counts,
        current_family_counts,
    ) = _settlement_job_market_snapshot(ctx, sid, market_snapshots)
    if prof:
        simulation_timing.accumulate("careers.assignment_market_snapshot", tpc() - t0)
        t0 = tpc()
    assignment = choose_career_assignment(
        rec.person,
        person_id=rec.person_id,
        db_path=ctx.db_path,
        era=era,
        year=year,
        historical_year=historical_year,
        salt=int(ctx.placename_rng_salt),
        fitness_score=fitness.score,
        settlement_resident_count=settlement_pop,
        resource_pressure=pressure,
        market_pull=market_pull,
        settlement_stability=settlement_stability,
        current_job_counts=current_job_counts,
        current_family_counts=current_family_counts,
        unemployment_years=_unemployment_years(rec.person, year),
        household_prosperity=rec.person.household_prosperity,
        childcare_duty_factor=duty,
        childcare_kinship_bonus_01=kinship_bonus,
        trait_values=traits,
        world=getattr(ctx, "world", "default"),
    )
    if assignment is None:
        if prof:
            simulation_timing.accumulate("careers.assignment_choose", tpc() - t0)
        return None
    if prof:
        simulation_timing.accumulate("careers.assignment_choose", tpc() - t0)
        t0 = tpc()
    previous_job = rec.person.last_job
    was_unemployed = rec.person.employment_status == "unemployed"
    unemployment_started = rec.person.unemployment_started_year
    unemployment_years = _unemployment_years(rec.person, year)
    comp_labels = tuple(rec.person.genome_composite_names or ())
    if not comp_labels:
        comp_rows = _genome_composite_rows(str(Path(ctx.db_path).resolve()))
        comp_labels = significant_composite_names_for_traits(
            traits, comp_rows
        )
    trait_phrases = tuple(rec.person.genome_trait_phrases or ())
    if not trait_phrases:
        trait_notes = interpret_genome_personality(rec.person, db_path=ctx.db_path)
        trait_phrases = tuple(n.phrase for n in trait_notes if n.phrase)
    job_fit_score, job_trait_match_score = job_category_fitness_score(
        rec.person,
        career_score=fitness.score,
        trait=assignment.trait,
        deviation_band=assignment.deviation_band,
        trait_values=traits,
    )
    rec.person = replace(
        rec.person,
        job=assignment.job,
        job_assigned_year=int(year),
        job_era=assignment.job_era,
        job_tier=assignment.job_tier,
        status_tendency=assignment.status_tendency,
        leader_quality=assignment.leader_quality,
        leader_tendency=assignment.leader_tendency,
        employment_status="employed",
        unemployment_started_year=None,
        career_fitness_score=fitness.score,
        genome_composite_names=comp_labels,
        genome_trait_phrases=trait_phrases,
    )
    archetype = JobArchetypeCatalog.load(ctx.db_path).lookup(assignment.job)
    rec.person = _apply_job_archetype_state(
        rec.person,
        job=assignment.job,
        archetype=archetype,
        housing_status=_default_housing_status(ctx, rec, year),
        household_role=assignment.role_family if assignment.job_market_type != "settlement_market" else None,
    )
    ctx._record_simulation_event(
        int(year),
        "job_assigned",
        {
            "year": int(year),
            "person_id": rec.person_id,
            "job": assignment.job,
            "job_tier": assignment.job_tier,
            "job_era": assignment.job_era,
            "trait": assignment.trait,
            "deviation_band": assignment.deviation_band,
            "descriptor": assignment.descriptor,
            "status_tendency": assignment.status_tendency,
            "leader_quality": assignment.leader_quality,
            "leader_tendency": assignment.leader_tendency,
            "job_sex_restriction": assignment.job_sex_restriction,
            "cross_gender_job_exception": assignment.cross_gender_job_exception,
            "previous_job": previous_job,
            "rehire": bool(was_unemployed),
            "fitness_score": job_fit_score,
            "career_fitness_score": fitness.score,
            "job_trait_match_score": job_trait_match_score,
            "job_market_demand_score": assignment.job_market_demand_score,
            "job_prosperity_score": assignment.job_prosperity_score,
            "job_family": assignment.job_family,
            "job_market_type": assignment.job_market_type,
            "role_family": assignment.role_family,
            "social_class_band": assignment.social_class_band,
            "social_standing_01": assignment.social_standing_01,
            "societal_impact_01": assignment.societal_impact_01,
            "perceived_worth_01": assignment.perceived_worth_01,
            "care_intensity_01": assignment.care_intensity_01,
            "job_saturation_score": assignment.saturation_score,
            "physical_demand_01": assignment.physical_demand_01,
            "effective_physical_demand_01": assignment.effective_physical_demand_01,
            "body_power_01": assignment.body_power_01,
            "physical_demand_multiplier": assignment.physical_demand_multiplier,
            "society_need": assignment.society_need,
            "selfish_desperate": assignment.selfish_desperate,
            "desperation_score": assignment.desperation_score,
            "resource_pressure": pressure,
            "childcare_duty_factor": round(_clamp(duty, 0.0, 1.0), 4),
            "genome_composite_names": list(comp_labels),
            "genome_trait_phrases": list(trait_phrases),
        },
    )
    if was_unemployed:
        ctx._record_simulation_event(
            int(year),
            "unemployment_ended",
            {
                "year": int(year),
                "person_id": rec.person_id,
                "new_job": assignment.job,
                "previous_job": previous_job,
                "unemployment_started_year": unemployment_started,
                "unemployment_years": unemployment_years,
                "fitness_score": job_fit_score,
                "career_fitness_score": fitness.score,
                "job_trait_match_score": job_trait_match_score,
                "trait": assignment.trait,
                "deviation_band": assignment.deviation_band,
                "resource_pressure": pressure,
        },
    )
    if market_snapshots is not None:
        market_snapshots.add_assigned_worker(rec)
    if prof:
        simulation_timing.accumulate("careers.assignment_apply", tpc() - t0)
    return assignment


def mark_unemployed(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    year: int,
    *,
    reason: str,
    pressure: float,
    fitness: CareerFitness,
) -> None:
    """Put a no-job eligible person into unemployment and log the transition once."""
    if rec.person.employment_status == "unemployed":
        return
    last_job = rec.person.last_job or rec.person.job
    job_fit_score, job_trait_match_score, job_trait, job_band = (
        job_category_fitness_for_title(
            rec.person,
            career_score=fitness.score,
            job_title=last_job,
            era=rec.person.job_era,
            db_path=ctx.db_path,
        )
    )
    rec.person = replace(
        rec.person,
        employment_status="unemployed",
        job_market_type="none",
        household_role=None,
        host_person_id=None,
        employer_person_id=None,
        social_class_band=None if not rec.person.job else rec.person.social_class_band,
        social_standing_01=None if not rec.person.job else rec.person.social_standing_01,
        societal_impact_01=None if not rec.person.job else rec.person.societal_impact_01,
        perceived_worth_01=None if not rec.person.job else rec.person.perceived_worth_01,
        unemployment_started_year=int(year),
        last_job=last_job,
        career_fitness_score=fitness.score,
    )
    ctx._record_simulation_event(
        int(year),
        "unemployment_started",
        {
            "year": int(year),
            "person_id": rec.person_id,
            "last_job": last_job,
            "reason": reason,
            "fitness_score": job_fit_score,
            "career_fitness_score": fitness.score,
            "job_trait_match_score": job_trait_match_score,
            "trait": job_trait,
            "deviation_band": job_band,
            "resource_pressure": pressure,
        },
    )


def lose_job(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    year: int,
    *,
    reason: str,
    pressure: float,
    fitness: CareerFitness,
) -> None:
    """Clear current job, update unemployment fields, and log job loss."""
    old_job = rec.person.job
    if not old_job:
        mark_unemployed(
            ctx, rec, year, reason=reason, pressure=pressure, fitness=fitness
        )
        return
    old_era = rec.person.job_era
    job_fit_score, job_trait_match_score, job_trait, job_band = (
        job_category_fitness_for_title(
            rec.person,
            career_score=fitness.score,
            job_title=old_job,
            era=old_era,
            db_path=ctx.db_path,
        )
    )
    rec.person = replace(
        rec.person,
        job=None,
        job_era=None,
        job_tier=None,
        job_market_type="none",
        household_role=None,
        host_person_id=None,
        employer_person_id=None,
        social_class_band=None,
        social_standing_01=None,
        societal_impact_01=None,
        perceived_worth_01=None,
        status_tendency=None,
        leader_quality=None,
        leader_tendency=None,
        employment_status="unemployed",
        job_lost_year=int(year),
        unemployment_started_year=int(year),
        last_job=old_job,
        career_fitness_score=fitness.score,
    )
    ctx._record_simulation_event(
        int(year),
        "job_lost",
        {
            "year": int(year),
            "person_id": rec.person_id,
            "old_job": old_job,
            "old_job_era": old_era,
            "fitness_score": job_fit_score,
            "career_fitness_score": fitness.score,
            "job_trait_match_score": job_trait_match_score,
            "trait": job_trait,
            "deviation_band": job_band,
            "resource_pressure": pressure,
            "reason": reason,
        },
    )
    ctx._record_simulation_event(
        int(year),
        "unemployment_started",
        {
            "year": int(year),
            "person_id": rec.person_id,
            "last_job": old_job,
            "reason": reason,
            "fitness_score": job_fit_score,
            "career_fitness_score": fitness.score,
            "job_trait_match_score": job_trait_match_score,
            "trait": job_trait,
            "deviation_band": job_band,
            "resource_pressure": pressure,
        },
    )


def maybe_lose_job(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    year: int,
    *,
    fitness: CareerFitness,
    pressure: float,
    career_facts: YearCareerFacts | None = None,
) -> bool:
    if not rec.person.job:
        return False
    p = job_loss_probability(fitness.score, pressure)
    duty = (
        career_facts.duty_for(ctx, rec, year)
        if career_facts is not None
        else _childcare_duty_factor_safe(ctx, rec, year)
    )
    if duty > 0.0:
        p = _clamp(
            p * (1.0 + JOB_CHILD_DUTY_LOSS_WEIGHT * duty),
            0.0,
            JOB_LOSS_MAX_PROB,
        )
    kinship_bonus = _childcare_kinship_bonus_safe(ctx, rec, year) if duty > 0.0 else 0.0
    primary_care_pull = _primary_childcare_pull(rec.person, duty, kinship_bonus)
    out_of_home_primary_care_conflict = False
    if primary_care_pull > 0.0:
        job_family = str(rec.person.job_market_type or "").strip().lower()
        if not job_family:
            market_catalog = JobMarketCatalog.load(ctx.db_path)
            job_family = market_catalog.lookup(rec.person.job).job_family
        if not _job_home_childcare_compatible(rec.person.job, job_family):
            out_of_home_primary_care_conflict = True
            p = max(
                p,
                PRIMARY_CHILDCARE_OUT_OF_HOME_LOSS_FLOOR * primary_care_pull,
            )
    rng = random.Random(
        _event_seed(
            year=year,
            person_id=rec.person_id,
            salt=int(ctx.placename_rng_salt),
            stream=71_001,
        )
    )
    if rng.random() >= p:
        if rec.person.employment_status != "employed":
            rec.person = replace(rec.person, employment_status="employed")
        return False
    lose_job(
        ctx,
        rec,
        year,
        reason=(
            "primary_childcare"
            if out_of_home_primary_care_conflict
            else _job_loss_reason(fitness.score, pressure)
        ),
        pressure=pressure,
        fitness=fitness,
    )
    return True


def _childcare_duty_factor_safe(
    ctx: "SimulationContext", rec: "SimulationPersonRecord", year: int
) -> float:
    """Look up caregiver duty without taking a hard dep on household_care at import."""
    from library.simulation_household_care import childcare_duty_factor

    return float(childcare_duty_factor(ctx, rec, year, indexes=ctx.annual_care_indexes(year)))


def _childcare_kinship_bonus_safe(
    ctx: "SimulationContext", rec: "SimulationPersonRecord", year: int
) -> float:
    """Look up childcare kinship pull without taking a hard dep at import."""
    from library.simulation_household_care import childcare_kinship_bonus_01

    return float(
        childcare_kinship_bonus_01(ctx, rec, year, indexes=ctx.annual_care_indexes(year))
    )


def _assign_special_household_job(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    year: int,
    *,
    job: str,
    archetype: JobArchetypeParams,
    reason: str,
    pressure: float,
    fitness: CareerFitness | None,
    housing_status: str,
    household_role: str,
    host_person_id: int | None = None,
    employer_person_id: int | None = None,
    trait_values: dict[str, float] | None = None,
) -> bool:
    if outlaw_blocks_normal_career(rec.person):
        normalize_outlaw_labor_state(ctx, rec, year)
        return False
    if rec.person.job:
        return False
    previous_job = rec.person.last_job
    was_unemployed = rec.person.employment_status == "unemployed"
    unemployment_started = rec.person.unemployment_started_year
    unemployment_years = _unemployment_years(rec.person, year)
    traits = trait_values if trait_values is not None else work_trait_values(rec.person)
    comp_labels = tuple(rec.person.genome_composite_names or ())
    if not comp_labels:
        comp_labels = significant_composite_names_for_traits(
            traits,
            _genome_composite_rows(str(Path(ctx.db_path).resolve())),
        )
    trait_phrases = tuple(rec.person.genome_trait_phrases or ())
    if not trait_phrases:
        trait_notes = interpret_genome_personality(rec.person, db_path=ctx.db_path)
        trait_phrases = tuple(n.phrase for n in trait_notes if n.phrase)
    era = resolve_job_era(ctx.get_historical_year(year))
    fitness_score = fitness.score if fitness is not None else career_fitness_score(rec.person)
    body_fit = body_demand_fit_for_person(
        rec.person,
        physical_demand_01=archetype.physical_demand_01,
        leveling_affinity_01=archetype.leveling_affinity_01,
        era=era,
        magic_leveling_01=magic_physical_leveling_for_world(
            getattr(ctx, "world", "default"), ctx.db_path
        ),
        trait_values=traits,
    )
    rec.person = replace(
        rec.person,
        job=job,
        job_assigned_year=int(year),
        job_era=era,
        job_tier="common",
        employment_status="employed",
        unemployment_started_year=None,
        career_fitness_score=fitness_score,
        genome_composite_names=comp_labels,
        genome_trait_phrases=trait_phrases,
    )
    rec.person = _apply_job_archetype_state(
        rec.person,
        job=job,
        archetype=archetype,
        housing_status=housing_status,
        household_role=household_role,
        host_person_id=host_person_id,
        employer_person_id=employer_person_id,
        status_tendency=(
            "low"
            if archetype.job_market_type in {"vice", "criminal"}
            else rec.person.status_tendency
        ),
        job_prosperity_01=archetype.personal_prosperity_01,
    )
    ctx._record_simulation_event(
        int(year),
        "job_assigned",
        {
            "year": int(year),
            "person_id": rec.person_id,
            "job": job,
            "job_tier": "common",
            "job_era": era,
            "descriptor": reason.replace("_", " "),
            "previous_job": previous_job,
            "rehire": bool(was_unemployed),
            "career_fitness_score": round(float(fitness_score), 4),
            "job_market_type": archetype.job_market_type,
            "role_family": archetype.role_family,
            "household_role": household_role,
            "housing_status": housing_status,
            "host_person_id": host_person_id,
            "employer_person_id": employer_person_id,
            "social_class_band": archetype.class_band,
            "social_standing_01": _social_standing_from_archetype(archetype),
            "societal_impact_01": round(float(archetype.societal_impact_01), 4),
            "perceived_worth_01": round(float(archetype.perceived_worth_01), 4),
            "care_intensity_01": round(float(archetype.care_intensity_01), 4),
            "job_prosperity_score": round(float(archetype.personal_prosperity_01), 4),
            "physical_demand_01": round(float(archetype.physical_demand_01), 4),
            "effective_physical_demand_01": round(
                body_fit.effective_physical_demand_01, 4
            ),
            "body_power_01": round(body_fit.body_power_01, 4),
            "physical_demand_multiplier": round(
                body_fit.physical_demand_multiplier, 4
            ),
            "placement_reason": reason,
            "resource_pressure": pressure,
            "non_graphic": archetype.job_market_type == "vice",
            "genome_composite_names": list(comp_labels),
            "genome_trait_phrases": list(trait_phrases),
        },
    )
    if was_unemployed:
        ctx._record_simulation_event(
            int(year),
            "unemployment_ended",
            {
                "year": int(year),
                "person_id": rec.person_id,
                "new_job": job,
                "previous_job": previous_job,
                "unemployment_started_year": unemployment_started,
                "unemployment_years": unemployment_years,
                "resource_pressure": pressure,
                "placement_reason": reason,
            },
        )
    if archetype.job_market_type == "domestic_service":
        ctx._record_simulation_event(
            int(year),
            "household_service_started",
            {
                "year": int(year),
                "person_id": rec.person_id,
                "worker_person_id": rec.person_id,
                "employer_person_id": employer_person_id,
                "service_kind": archetype.domestic_service_kind or household_role,
                "board_included": housing_status == "employer_household",
                "cash_wage_01": round(float(archetype.personal_prosperity_01), 4),
                "details": f"{rec.person.full_name} entered household service as {job}.",
            },
        )
    if archetype.job_market_type == "vice":
        ctx._record_simulation_event(
            int(year),
            "street_vice_scandal",
            {
                "year": int(year),
                "person_id": rec.person_id,
                "incident_kind": "street_vice_scandal",
                "job": job,
                "housing_status": housing_status,
                "resource_pressure": round(float(pressure), 4),
                "non_graphic": True,
                "details": (
                    f"{rec.person.full_name}'s survival work became a local scandal."
                ),
            },
        )
    return True


def _service_demand_anchors(
    ctx: "SimulationContext",
    year: int,
    care_indexes: object | None,
) -> list[dict[str, object]]:
    existing_by_employer: dict[int, int] = {}
    for rec in ctx.iter_current_people(sorted_by_id=True):
        if (rec.person.job_market_type or "").strip().lower() != "domestic_service":
            continue
        employer = rec.person.employer_person_id
        if employer is not None:
            existing_by_employer[int(employer)] = existing_by_employer.get(int(employer), 0) + 1

    seen: set[frozenset[int]] = set()
    demands: list[dict[str, object]] = []
    for rec in ctx.iter_current_people(sorted_by_id=True):
        if ctx._person_is_dependent_minor(rec, year):
            continue
        hids = tuple(
            getattr(care_indexes, "household_ids_by_adult", {}).get(
                int(rec.person_id),
                (int(rec.person_id),),
            )
            if care_indexes is not None
            else _household_ids_for_job_move(ctx, rec, year, indexes=None, use_shared_index=False)
        )
        hkey = frozenset(int(x) for x in hids)
        if not hkey or hkey in seen:
            continue
        seen.add(hkey)
        minors = (
            getattr(care_indexes, "minor_ids_by_household", {}).get(hkey, frozenset())
            if care_indexes is not None
            else frozenset()
        )
        adults = [
            ctx.id_to_record[pid]
            for pid in sorted(hkey)
            if pid in ctx.id_to_record and pid in ctx.current_people_ids
        ]
        if not adults:
            continue
        prosperity = max(float(a.person.household_prosperity or 0.0) for a in adults)
        standing = max(float(a.person.social_standing_01 or 0.0) for a in adults)
        if prosperity < SERVICE_HOUSEHOLD_PROSPERITY_THRESHOLD and standing < SERVICE_HIGH_STANDING_THRESHOLD:
            continue
        anchor = None
        for a in adults:
            if a.person.household_purseholder_person_id in hkey:
                anchor = int(a.person.household_purseholder_person_id)
                break
        if anchor is None:
            anchor = int(max(adults, key=lambda a: float(a.person.household_prosperity or 0.0)).person_id)
        desired = 1
        if prosperity >= 4.0 or standing >= 0.82:
            desired = 2
        current = existing_by_employer.get(anchor, 0)
        if current >= desired:
            continue
        sid = _residence_settlement_id(ctx.id_to_record.get(anchor, rec))
        service_kind = "nanny" if minors else "servant"
        demands.append(
            {
                "employer_person_id": anchor,
                "settlement_id": sid,
                "service_kind": service_kind,
                "prosperity": prosperity,
                "standing": standing,
                "slots": desired - current,
            }
        )
    demands.sort(
        key=lambda d: (
            -float(d.get("prosperity") or 0.0),
            -float(d.get("standing") or 0.0),
            int(d.get("employer_person_id") or 0),
        )
    )
    return demands


def _domestic_service_candidate_score(
    person: "Person", archetype: JobArchetypeParams, trait_values: dict[str, float]
) -> float:
    gender = (person.gender or "").strip().lower()
    gender_mind = (person.gender_mind or "").strip().lower()
    score = 0.35
    if gender == "female":
        score += 0.24 * archetype.female_mindset_affinity_01
    if gender_mind == "feminine":
        score += 0.22 * archetype.female_mindset_affinity_01
    score += _trait_ideal_strength(trait_values, "empathy") * 0.12
    score += _trait_ideal_strength(trait_values, "nurturance") * 0.15
    score += _trait_ideal_strength(trait_values, "patience") * 0.08
    return _clamp(score, 0.0, 1.0)


def _take_service_demand(
    demands: list[dict[str, object]], settlement_id: str
) -> dict[str, object] | None:
    for demand in demands:
        if str(demand.get("settlement_id") or "") != settlement_id:
            continue
        slots = int(demand.get("slots") or 0)
        if slots <= 0:
            continue
        demand["slots"] = slots - 1
        return demand
    return None


def _maybe_assign_domestic_service(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    year: int,
    *,
    demands: list[dict[str, object]],
    archetypes: JobArchetypeCatalog,
    pressure: float,
    fitness: CareerFitness,
    trait_values: dict[str, float],
    care_indexes: object | None,
) -> bool:
    if outlaw_blocks_normal_career(rec.person):
        normalize_outlaw_labor_state(ctx, rec, year)
        return False
    if rec.person.job or rec.person.partner_person_id is not None:
        return False
    if _household_dependent_minor_count(ctx, rec, year, indexes=care_indexes) > 0:
        return False
    sid = _residence_settlement_id(rec)
    demand = _take_service_demand(demands, sid)
    if demand is None:
        return False
    kind = str(demand.get("service_kind") or "servant")
    job = "nanny" if kind == "nanny" else "servant"
    archetype = archetypes.lookup(job)
    score = _domestic_service_candidate_score(rec.person, archetype, trait_values)
    if score < 0.38 and pressure < 0.9:
        demand["slots"] = int(demand.get("slots") or 0) + 1
        return False
    return _assign_special_household_job(
        ctx,
        rec,
        year,
        job=job,
        archetype=archetype,
        reason="domestic_service_placement",
        pressure=pressure,
        fitness=fitness,
        housing_status="employer_household",
        household_role=archetype.domestic_service_kind or kind,
        host_person_id=int(demand["employer_person_id"]),
        employer_person_id=int(demand["employer_person_id"]),
        trait_values=trait_values,
    )


def _vice_job_choice(
    person: "Person", trait_values: dict[str, float], rng: random.Random
) -> str:
    attractiveness = float(person.attractiveness_01 or 0.0)
    persuasion = _trait_positive_strength(trait_values, "persuasion")
    ambition = _trait_positive_strength(trait_values, "ambition")
    if attractiveness > 0.68 and (persuasion + ambition) > 0.55 and rng.random() < 0.45:
        return "courtesan"
    if rng.random() < 0.35:
        return "brothel worker"
    return "prostitute"


def _maybe_assign_vice_work(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    year: int,
    *,
    archetypes: JobArchetypeCatalog,
    pressure: float,
    fitness: CareerFitness,
    trait_values: dict[str, float],
    desperation: float,
) -> bool:
    if outlaw_blocks_normal_career(rec.person):
        normalize_outlaw_labor_state(ctx, rec, year)
        return False
    if rec.person.job or _person_age(rec.person, year) < ADULT_HOUSING_MIN_AGE:
        return False
    housing = (rec.person.housing_status or "").strip().lower()
    if housing != "street" and pressure < 1.05:
        return False
    if desperation < VICE_DESPERATION_THRESHOLD:
        return False
    rng = random.Random(
        _event_seed(
            year=year,
            person_id=rec.person_id,
            salt=int(ctx.placename_rng_salt),
            stream=104_729,
        )
    )
    access = (
        0.16
        + desperation * 0.36
        + float(rec.person.attractiveness_01 or 0.0) * 0.12
        + _trait_positive_strength(trait_values, "mating drive") * 0.10
        + _trait_positive_strength(trait_values, "persuasion") * 0.08
        + _trait_positive_strength(trait_values, "ambition") * 0.06
        - _trait_ideal_strength(trait_values, "temperance") * 0.07
    )
    if rng.random() >= _clamp(access, 0.0, 0.72):
        return False
    job = _vice_job_choice(rec.person, trait_values, rng)
    return _assign_special_household_job(
        ctx,
        rec,
        year,
        job=job,
        archetype=archetypes.lookup(job),
        reason="street_precarity_vice_work",
        pressure=pressure,
        fitness=fitness,
        housing_status=housing or "street",
        household_role="survival_worker",
        trait_values=trait_values,
    )


def _resolve_adult_housing_pressure(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    year: int,
    *,
    pressure: float,
    fitness: CareerFitness,
    trait_values: dict[str, float],
    care_indexes: object | None,
    archetypes: JobArchetypeCatalog,
) -> None:
    if outlaw_blocks_normal_career(rec.person):
        normalize_outlaw_labor_state(ctx, rec, year)
        return
    if _person_age(rec.person, year) < ADULT_HOUSING_MIN_AGE:
        return
    if rec.person.job:
        if not rec.person.housing_status:
            rec.person = replace(
                rec.person,
                housing_status=_default_housing_status(ctx, rec, year),
            )
        return
    own_minor_count = _household_dependent_minor_count(
        ctx, rec, year, indexes=care_indexes
    )
    if rec.person.partner_person_id is not None:
        rec.person = replace(
            rec.person,
            housing_status=rec.person.housing_status or "own_household",
            household_role=rec.person.household_role or "household_adult",
        )
        return
    if own_minor_count > 0 and not _living_parent_records_same_settlement(ctx, rec):
        rec.person = replace(
            rec.person,
            housing_status=rec.person.housing_status or "own_household",
            household_role=rec.person.household_role or "caregiver",
        )
        return
    parents = _living_parent_records_same_settlement(ctx, rec)
    care_contribution = (
        0.35
        if own_minor_count > 0
        else 0.0
    )
    support_score = _adult_child_support_score(
        ctx,
        rec,
        parents,
        pressure=pressure,
        trait_values=trait_values,
        care_contribution=care_contribution,
    )
    if parents and support_score >= 0.42:
        rec.person = replace(
            rec.person,
            housing_status="family_home",
            household_role="adult_child",
            host_person_id=int(parents[0].person_id),
        )
        return
    if care_contribution > 0.0 and support_score >= 0.34:
        rec.person = replace(
            rec.person,
            housing_status="family_home" if parents else "kin_board",
            household_role="family_childcare_helper",
            host_person_id=int(parents[0].person_id) if parents else None,
        )
        return
    desperation = career_desperation_score(
        resource_pressure=pressure,
        unemployment_years=_unemployment_years(rec.person, year),
        household_prosperity=rec.person.household_prosperity,
    )
    if _maybe_assign_vice_work(
        ctx,
        rec,
        year,
        archetypes=archetypes,
        pressure=pressure,
        fitness=fitness,
        trait_values=trait_values,
        desperation=desperation,
    ):
        return
    if pressure < STREET_PRECARITY_PRESSURE_THRESHOLD and support_score >= 0.25:
        status = "kin_board" if parents else "charity_board"
        rec.person = replace(
            rec.person,
            housing_status=status,
            household_role="boarded_adult",
            host_person_id=int(parents[0].person_id) if parents else None,
        )
        return
    if (rec.person.housing_status or "").strip().lower() != "street":
        rec.person = replace(
            rec.person,
            housing_status="street",
            household_role="street_adult",
            host_person_id=None,
            employer_person_id=None,
        )
        _record_housing_event(
            ctx,
            rec,
            year,
            "vagrancy",
            housing_status="street",
            pressure=pressure,
            support_score=support_score,
            details=f"{rec.person.full_name} lost stable household support.",
        )
    if _unemployment_years(rec.person, year) >= 1 or pressure >= 1.0:
        _record_housing_event(
            ctx,
            rec,
            year,
            "begging",
            housing_status="street",
            pressure=pressure,
            support_score=support_score,
            details=f"{rec.person.full_name} relied on begging while without stable shelter.",
        )


def _household_labor_pre_assignment_pass(
    ctx: "SimulationContext",
    year: int,
    eligible: list[tuple["SimulationPersonRecord", CareerFitness, float, dict[str, float]]],
    *,
    career_facts: YearCareerFacts,
) -> int:
    archetypes = JobArchetypeCatalog.load(ctx.db_path)
    care_indexes = career_facts.care_indexes
    assigned = 0
    for rec, fitness, pressure, traits in eligible:
        if rec.person.job:
            continue
        duty = career_facts.duty_for(ctx, rec, year)
        kin_bonus = career_facts.kinship_bonus_for(ctx, rec, year)
        care_pull = _primary_childcare_pull(rec.person, duty, kin_bonus)
        if (
            care_pull > 0.0
            and (duty >= HOUSEHOLD_CARE_MIN_DUTY or kin_bonus >= 0.60)
        ):
            if _assign_special_household_job(
                ctx,
                rec,
                year,
                job="child rearer",
                archetype=archetypes.lookup("child rearer"),
                reason="primary_child_rearing",
                pressure=pressure,
                fitness=fitness,
                housing_status=_default_housing_status(ctx, rec, year),
                household_role="primary_child_rearer",
                trait_values=traits,
            ):
                assigned += 1

    demands = _service_demand_anchors(ctx, year, care_indexes)
    for rec, fitness, pressure, traits in eligible:
        if rec.person.job:
            continue
        if _maybe_assign_domestic_service(
            ctx,
            rec,
            year,
            demands=demands,
            archetypes=archetypes,
            pressure=pressure,
            fitness=fitness,
            trait_values=traits,
            care_indexes=care_indexes,
        ):
            assigned += 1

    for rec, fitness, pressure, traits in eligible:
        _resolve_adult_housing_pressure(
            ctx,
            rec,
            year,
            pressure=pressure,
            fitness=fitness,
            trait_values=traits,
            care_indexes=care_indexes,
            archetypes=archetypes,
        )
    return assigned


def maybe_assign_or_rehire(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    year: int,
    *,
    fitness: CareerFitness,
    pressure: float,
    market_snapshots: YearJobMarketSnapshots | None = None,
    career_facts: YearCareerFacts | None = None,
    known_eligible: bool = False,
    historical_year: int | None = None,
    era: str | None = None,
    trait_values: dict[str, float] | None = None,
) -> bool:
    if outlaw_blocks_normal_career(rec.person):
        normalize_outlaw_labor_state(ctx, rec, year)
        return False
    if rec.person.job:
        return False
    historical_year = (
        int(historical_year)
        if historical_year is not None
        else ctx.get_historical_year(year)
    )
    era = (era or resolve_job_era(historical_year)).strip().lower()
    if not known_eligible and not _eligible_for_job(rec.person, ctx.db_path, year, era):
        return False
    unemployment_years = _unemployment_years(rec.person, year)
    p = rehire_probability(fitness.score, pressure, unemployment_years)
    duty = (
        career_facts.duty_for(ctx, rec, year)
        if career_facts is not None
        else _childcare_duty_factor_safe(ctx, rec, year)
    )
    if duty > 0.0:
        p = _clamp(
            p * (1.0 - JOB_CHILD_DUTY_REHIRE_WEIGHT * duty),
            REHIRE_MIN_PROB,
            REHIRE_MAX_PROB,
        )
    placement_failure_reason = (
        "primary_childcare"
        if _primary_childcare_pull(
            rec.person,
            duty,
            _childcare_kinship_bonus_safe(ctx, rec, year) if duty > 0.0 else 0.0,
        )
        > 0.0
        else "placement_failed"
    )
    rng = random.Random(
        _event_seed(
            year=year,
            person_id=rec.person_id,
            salt=int(ctx.placename_rng_salt),
            stream=83_003,
        )
    )
    if rng.random() < p:
        assigned = assign_career_if_eligible(
            ctx,
            rec,
            year,
            market_snapshots=market_snapshots,
            pressure=pressure,
            fitness=fitness,
            childcare_duty_factor=duty,
            known_eligible=known_eligible,
            historical_year=historical_year,
            era=era,
            trait_values=trait_values,
        )
        if assigned is not None:
            return True
        mark_unemployed(
            ctx,
            rec,
            year,
            reason=placement_failure_reason,
            pressure=pressure,
            fitness=fitness,
        )
        return False
    mark_unemployed(
        ctx,
        rec,
        year,
        reason=placement_failure_reason,
        pressure=pressure,
        fitness=fitness,
    )
    return False


def _residence_settlement_id(rec: "SimulationPersonRecord") -> str:
    if is_outlaw_absent(rec.person):
        return ""
    return (
        rec.person.current_settlement_id
        or rec.person.birthplace_settlement_id
        or ""
    ).strip()


def _household_ids_for_job_move(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    year: int,
    indexes: object | None = None,
    use_shared_index: bool = True,
) -> list[int]:
    """Worker, co-resident partner, and dependent children who share that home."""
    worker_id = int(rec.person_id)
    if indexes is None and use_shared_index and hasattr(ctx, "annual_care_indexes"):
        indexes = ctx.annual_care_indexes(year)
    if indexes is not None and hasattr(indexes, "household_ids_by_adult"):
        hids = getattr(indexes, "household_ids_by_adult").get(worker_id)
        if hids is not None:
            return list(hids)
    origin_sid = _residence_settlement_id(rec)
    if not origin_sid:
        return [worker_id]
    ids: list[int] = [worker_id]
    partner_id = rec.person.partner_person_id
    partner_rec = ctx.id_to_record.get(partner_id) if partner_id is not None else None
    if (
        partner_id is not None
        and partner_id in ctx.current_people_ids
        and partner_rec is not None
        and _residence_settlement_id(partner_rec) == origin_sid
    ):
        ids.append(int(partner_id))

    parent_set = {worker_id}
    if partner_id is not None:
        parent_set.add(int(partner_id))
    for child_id in sorted(ctx.current_people_ids):
        if child_id in parent_set:
            continue
        child = ctx.id_to_record.get(child_id)
        if child is None or _residence_settlement_id(child) != origin_sid:
            continue
        child_parents = {x for x in (child.father_id, child.mother_id) if x is not None}
        if not child_parents:
            continue
        if not child_parents.issubset(parent_set):
            continue
        if ctx._person_is_dependent_minor(child, year):
            ids.append(int(child_id))
    return sorted(set(ids))


def _pick_job_seeker_destination(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    year: int,
    rng: random.Random,
    resource_facts: YearResourceFacts | None = None,
) -> str | None:
    origin_rid = _person_residence_region(ctx, rec)
    if not origin_rid:
        return None
    routes = list_routes_from(
        origin_rid,
        world=ctx.world,
        db_path=ctx.db_path,
        simulation_year=year,
    )
    dest_ids: list[str] = []
    weights: list[float] = []
    for route in routes:
        rid = (route.to_region_id or "").strip()
        if not rid or rid == origin_rid:
            continue
        try:
            if resource_facts is not None:
                cap = int(resource_facts.region_cap.get(rid, 0))
                pop = int(resource_facts.region_population.get(rid, 0))
            else:
                cap = ctx.effective_regional_population_cap(rid)
                pop = ctx.count_alive_in_region(rid)
        except (LookupError, ValueError):
            continue
        headroom = max(1.0, float(cap - pop))
        w = headroom / (1.0 + max(0.0, float(route.friction)))
        dest_ids.append(rid)
        weights.append(max(1e-6, w))
    if not dest_ids:
        return None
    dest_rid = rng.choices(dest_ids, weights=weights, k=1)[0]
    active = ctx.active_settlements_in_region(dest_rid)
    if active:
        st = min(active, key=lambda s: ctx.count_alive_in_settlement(s.settlement_id))
    else:
        st = ctx.ensure_active_settlement_for_region(dest_rid)
    return st.settlement_id


def job_seeker_migration_probability(
    fitness_score: float, resource_pressure: float, unemployment_years: int
) -> float:
    if unemployment_years <= 0 and resource_pressure < 0.9:
        return 0.0
    p = (
        0.02
        + min(max(0, int(unemployment_years)), 8) * 0.04
        + max(0.0, float(resource_pressure) - 0.8) * 0.25
        - _clamp(float(fitness_score), 0.0, 1.0) * 0.03
    )
    return round(_clamp(p, 0.0, JOB_SEEKER_MIGRATION_MAX_PROB), 5)


def maybe_migrate_job_seeker_household(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    year: int,
    *,
    fitness: CareerFitness,
    pressure: float,
    career_facts: YearCareerFacts | None = None,
) -> bool:
    if outlaw_blocks_normal_career(rec.person):
        normalize_outlaw_labor_state(ctx, rec, year)
        return False
    if rec.person.job or rec.person.employment_status != "unemployed":
        return False
    unemployment_years = _unemployment_years(rec.person, year)
    p = job_seeker_migration_probability(fitness.score, pressure, unemployment_years)
    if p <= 0:
        return False
    rng = random.Random(
        _event_seed(
            year=year,
            person_id=rec.person_id,
            salt=int(ctx.placename_rng_salt),
            stream=97_009,
        )
    )
    if rng.random() >= p:
        return False
    dest_sid = _pick_job_seeker_destination(
        ctx,
        rec,
        year,
        rng,
        resource_facts=career_facts.resource_facts if career_facts is not None else None,
    )
    if not dest_sid:
        return False
    origin_sid = _residence_settlement_id(rec)
    origin_rid = _person_residence_region(ctx, rec)
    moved_ids: list[int] = []
    group_id = f"job_seeker:{rec.person_id}:{int(year)}"
    for pid in _household_ids_for_job_move(
        ctx,
        rec,
        year,
        indexes=career_facts.care_indexes if career_facts is not None else None,
    ):
        try:
            if ctx.queue_person_move_to_settlement(
                pid,
                dest_sid,
                move_reason="job_seeker_migration",
                requested_year=int(year),
                apply_year=int(year) + 1,
                source_event="job_seeker_migration",
                group_id=group_id,
            ):
                moved_ids.append(pid)
        except (LookupError, ValueError):
            continue
    if not moved_ids:
        return False
    dest_state = ctx.settlements_by_id.get(dest_sid)
    ctx._record_simulation_event(
        int(year),
        "job_seeker_migration",
        {
            "year": int(year),
            "person_id": rec.person_id,
            "moved_person_ids": moved_ids,
            "from_settlement_id": origin_sid,
            "to_settlement_id": dest_sid,
            "from_region_id": origin_rid,
            "to_region_id": dest_state.region_id if dest_state is not None else None,
            "unemployment_years": unemployment_years,
            "fitness_score": fitness.score,
            "resource_pressure": pressure,
            "move_reason": "job_seeker_migration",
        },
    )
    return True


def simulation_careers_annual_tick(ctx: "SimulationContext", year: int) -> None:
    """Update annual employment state for living people at job-eligible ages."""
    eligible: list[tuple[SimulationPersonRecord, CareerFitness, float, dict[str, float]]] = []
    prof = simulation_timing.active_for_year(year)
    tpc = time.perf_counter
    if prof:
        t0 = tpc()
    historical_year = ctx.get_historical_year(year)
    era = resolve_job_era(historical_year)
    job_age_cache: dict[tuple[str, str, int | None], int] = {}
    potentially_eligible: list[SimulationPersonRecord] = []
    current_alive = 0
    for rec in ctx.iter_current_people(sorted_by_id=True):
        current_alive += 1
        key = (
            (rec.person.species or "").strip(),
            (rec.person.ethnic or "").strip(),
            (
                int(rec.person.min_fertility_age)
                if rec.person.min_fertility_age is not None
                else None
            ),
        )
        min_age = job_age_cache.get(key)
        if min_age is None:
            min_age = job_eligibility_age(
                _person_maturity_age(rec.person, ctx.db_path), era
            )
            job_age_cache[key] = min_age
        if int(year) - int(rec.person.birthyear) < min_age:
            continue
        if outlaw_blocks_normal_career(rec.person):
            normalize_outlaw_labor_state(ctx, rec, year)
            continue
        materialize_adult_profile(ctx, rec, year)
        potentially_eligible.append(rec)
    if prof:
        simulation_timing.accumulate("careers.scan_eligible", tpc() - t0)
        simulation_timing.record_gauge(year, "careers", "current_alive", current_alive)
        simulation_timing.record_gauge(
            year, "careers", "potentially_eligible", len(potentially_eligible)
        )
        t0 = tpc()

    career_facts = YearCareerFacts.build(ctx, year, potentially_eligible)
    if prof:
        simulation_timing.accumulate("careers.build_facts", tpc() - t0)
        t0 = tpc()
    for rec in potentially_eligible:
        pressure = career_facts.pressure_for(ctx, rec)
        traits = work_trait_values(rec.person)
        fitness = refresh_career_fitness(
            ctx, rec, year, pressure=pressure, trait_values=traits
        )
        eligible.append((rec, fitness, pressure, traits))
    if prof:
        simulation_timing.accumulate("careers.refresh_fitness", tpc() - t0)
        simulation_timing.record_gauge(year, "careers", "eligible", len(eligible))
        simulation_timing.record_gauge(
            year,
            "careers",
            "eligible_with_job_before_loss",
            sum(1 for rec, _fitness, _pressure, _traits in eligible if bool(rec.person.job)),
        )
        t0 = tpc()

    lost_count = 0
    for rec, fitness, pressure, _traits in eligible:
        if maybe_lose_job(
            ctx,
            rec,
            year,
            fitness=fitness,
            pressure=pressure,
            career_facts=career_facts,
        ):
            lost_count += 1
    if prof:
        simulation_timing.accumulate("careers.job_loss", tpc() - t0)
        simulation_timing.record_gauge(year, "careers", "job_losses", lost_count)
        t0 = tpc()

    household_labor_assigned = _household_labor_pre_assignment_pass(
        ctx,
        year,
        eligible,
        career_facts=career_facts,
    )
    if prof:
        simulation_timing.accumulate("careers.household_labor", tpc() - t0)
        simulation_timing.record_gauge(
            year,
            "careers",
            "household_labor_assignments",
            household_labor_assigned,
        )
        t0 = tpc()

    market_snapshots = YearJobMarketSnapshots.build(ctx)
    if prof:
        simulation_timing.accumulate("careers.market_snapshot", tpc() - t0)
        t0 = tpc()
    assigned_count = 0
    assign_skipped_job_lost = 0
    assign_skipped_employed = 0
    assign_considered = 0
    for rec, fitness, pressure, traits in eligible:
        if rec.person.job_lost_year == int(year):
            assign_skipped_job_lost += 1
            continue
        if rec.person.job:
            assign_skipped_employed += 1
            continue
        assign_considered += 1
        if maybe_assign_or_rehire(
            ctx,
            rec,
            year,
            fitness=fitness,
            pressure=pressure,
            market_snapshots=market_snapshots,
            career_facts=career_facts,
            known_eligible=True,
            historical_year=historical_year,
            era=era,
            trait_values=traits,
        ):
            assigned_count += 1
    if prof:
        simulation_timing.accumulate("careers.assign_rehire", tpc() - t0)
        simulation_timing.record_gauge(
            year, "careers", "assign_rehire_considered", assign_considered
        )
        simulation_timing.record_gauge(
            year, "careers", "assign_rehire_skipped_employed", assign_skipped_employed
        )
        simulation_timing.record_gauge(
            year, "careers", "assign_rehire_skipped_job_lost", assign_skipped_job_lost
        )
        simulation_timing.record_gauge(year, "careers", "assignments", assigned_count)
        t0 = tpc()

    prestige_promotions, patronage_ties, prestige_falls = _prestige_mobility_pass(
        ctx,
        year,
        eligible,
    )
    if prof:
        simulation_timing.accumulate("careers.prestige_mobility", tpc() - t0)
        simulation_timing.record_gauge(
            year, "careers", "prestige_promotions", prestige_promotions
        )
        simulation_timing.record_gauge(
            year, "careers", "patronage_ties", patronage_ties
        )
        simulation_timing.record_gauge(
            year, "careers", "prestige_falls", prestige_falls
        )
        t0 = tpc()

    migrated_count = 0
    for rec, fitness, pressure, _traits in eligible:
        if maybe_migrate_job_seeker_household(
            ctx,
            rec,
            year,
            fitness=fitness,
            pressure=pressure,
            career_facts=career_facts,
        ):
            migrated_count += 1
    if prof:
        simulation_timing.accumulate("careers.job_migration", tpc() - t0)
        simulation_timing.record_gauge(year, "careers", "job_migrations", migrated_count)
        simulation_timing.record_gauge(
            year,
            "careers",
            "employed_after_tick",
            sum(
                1
                for rec, _fitness, _pressure, _traits in eligible
                if (rec.person.employment_status or "").strip().lower() == "employed"
            ),
        )
