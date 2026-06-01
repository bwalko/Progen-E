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
from library.job_market import JobMarketCatalog, JobMarketParams
from library.mind_body import attractiveness_01, ensure_full_mind_body, work_trait_values
from library.personality_interpreter import interpret_genome_personality
from library.geography import list_routes_from
from library.random_traits import _as_int, _connect

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

JOB_SEEKER_MIGRATION_MAX_PROB = 0.35

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
    saturation_score: float = 1.0
    desperation_score: float = 0.0


@dataclass(frozen=True)
class CareerFitness:
    score: float
    near_perfect_traits: tuple[str, ...]
    high_deviation_traits: tuple[str, ...]
    weighted_near_perfect_count: float
    weighted_high_deviation_count: float


@dataclass(frozen=True)
class CareerJobEntry:
    title: str
    tier: Literal["common", "premium"]
    restriction: str | None
    job_key: str
    economics: JobEconomicsParams
    market: JobMarketParams
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


def _primary_childcare_pull(person: "Person", childcare_duty_factor: float) -> float:
    """How strongly this person should stay in the implicit home-childcare role."""
    duty = _clamp(float(childcare_duty_factor or 0.0), 0.0, 1.0)
    if duty < PRIMARY_CHILDCARE_DUTY_THRESHOLD:
        return 0.0
    if (person.gender or "").strip().lower() != "female":
        return 0.0
    if (person.gender_mind or "").strip().lower() == "masculine":
        return 0.0
    return _clamp(duty / CAREER_CHILD_DUTY_FACTOR_CAP, 0.0, 1.0)


def _job_home_childcare_compatible(job_title: str | None, job_family: str | None = None) -> bool:
    """True for roles plausibly done in or around one's own household."""
    jk = normalize_job_catalog_key(job_title or "")
    if not jk:
        return False
    fam = (job_family or "").strip().lower()
    if fam == "domestic":
        return True
    home_parts = (
        "parent",
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
            out.append(
                CareerJobEntry(
                    title=title,
                    tier=tier,
                    restriction=restriction,
                    job_key=normalize_job_catalog_key(title),
                    economics=economics_catalog.lookup(title, era_key, tier=tier),
                    market=market,
                    home_compatible=_job_home_childcare_compatible(
                        title, market.job_family
                    ),
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
    trait_values: dict[str, float] | None = None,
) -> CareerAssignment | None:
    """Pick the best available job for the person's skill, market, and desperation."""
    prof = simulation_timing.active_for_year(year)
    tpc = time.perf_counter
    if prof:
        t0 = tpc()
    path = Path(db_path)
    db_path_s = str(path.resolve())
    era_key = (era or "").strip().lower()
    options = _career_job_options_for_era(db_path_s, era_key)
    if not options:
        return None
    traits = trait_values if trait_values is not None else work_trait_values(person)
    allowed_by_restriction = _job_restriction_allowance(person, traits)
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
        common_entries = tuple(
            entry
            for entry in option.common_entries
            if allowed_by_restriction.get(entry.restriction, True)
        )
        premium_entries = tuple(
            entry
            for entry in option.premium_entries
            if allowed_by_restriction.get(entry.restriction, True)
        )
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
            float,
        ]
    ] = []
    desperation = career_desperation_score(
        resource_pressure=resource_pressure,
        unemployment_years=unemployment_years,
        household_prosperity=household_prosperity,
    )
    primary_care_pull = _primary_childcare_pull(person, childcare_duty_factor)
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
                if primary_care_pull > 0.0 and not entry.home_compatible:
                    continue
                job_count = int((current_job_counts or {}).get(entry.job_key, 0))
                family_count = int(
                    (current_family_counts or {}).get(entry.market.job_family, 0)
                )
                saturation = _saturation_multiplier(
                    current_job_count=job_count,
                    current_family_count=family_count,
                    settlement_resident_count=settlement_resident_count,
                    market=entry.market,
                )
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
                weight = _assignment_weight(
                    trait_match=trait_score,
                    wage_yield=entry.economics.wage_yield,
                    market_demand=market_demand,
                    selfish_desperate=selfish_desperate,
                    desperation=desperation,
                )
                if primary_care_pull > 0.0 and entry.home_compatible:
                    weight *= 1.0 + PRIMARY_CHILDCARE_HOME_JOB_WEIGHT * primary_care_pull
                scored_jobs.append(
                    (
                        option,
                        trait_score,
                        entry,
                        market_demand,
                        _clamp(float(entry.economics.wage_yield) / 1.45, 0.0, 1.0),
                        selfish_desperate,
                        saturation,
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
            -item[7],
            item[2].title,
            item[0].trait,
            item[0].deviation_band,
        ),
    )
    weights = [item[7] for item in top]
    (
        option,
        trait_score,
        entry,
        market_demand,
        prosperity_score,
        selfish_desperate,
        saturation,
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
        saturation_score=round(saturation, 4),
        desperation_score=desperation,
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
        trait_values=traits,
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
            "job_saturation_score": assignment.saturation_score,
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
    primary_care_pull = _primary_childcare_pull(rec.person, duty)
    out_of_home_primary_care_conflict = False
    if primary_care_pull > 0.0:
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
        if _primary_childcare_pull(rec.person, duty) > 0.0
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
