"""Annual career assignment driven by genome_jobs config."""

from __future__ import annotations

import random
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from library.genome_composites import significant_composite_names
from library.job_economics import JobEconomicsCatalog, normalize_job_catalog_key
from library.job_market import JobMarketCatalog, JobMarketParams
from library.mind_body import work_trait_values
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
class YearCareerFacts:
    """Reusable per-person facts for one career tick."""

    pressure_by_person_id: dict[int, float]
    duty_by_person_id: dict[int, float]

    @classmethod
    def build(
        cls,
        ctx: "SimulationContext",
        year: int,
        records: list["SimulationPersonRecord"],
    ) -> "YearCareerFacts":
        pressure_by_person_id = {
            int(rec.person_id): resource_pressure_for_person(ctx, rec)
            for rec in records
        }
        from library.simulation_household_care import childcare_duty_factor

        duty_by_person_id = {
            int(rec.person_id): float(childcare_duty_factor(ctx, rec, year))
            for rec in records
        }
        return cls(
            pressure_by_person_id=pressure_by_person_id,
            duty_by_person_id=duty_by_person_id,
        )

    def pressure_for(
        self, ctx: "SimulationContext", rec: "SimulationPersonRecord"
    ) -> float:
        pid = int(rec.person_id)
        if pid not in self.pressure_by_person_id:
            self.pressure_by_person_id[pid] = resource_pressure_for_person(ctx, rec)
        return self.pressure_by_person_id[pid]

    def duty_for(
        self, ctx: "SimulationContext", rec: "SimulationPersonRecord", year: int
    ) -> float:
        pid = int(rec.person_id)
        if pid not in self.duty_by_person_id:
            from library.simulation_household_care import childcare_duty_factor

            self.duty_by_person_id[pid] = float(childcare_duty_factor(ctx, rec, year))
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
) -> tuple[float, float]:
    """Blend broad work fitness with the selected trait-band match for job events."""
    try:
        general = _clamp(float(career_score), 0.0, 1.0)
    except (TypeError, ValueError):
        general = career_fitness_score(person)
    key = (trait or "").strip()
    raw = work_trait_values(person).get(key)
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


def _female_exception_for_male_only_job(person: "Person") -> bool:
    if (person.gender_mind or "").strip().lower() != "masculine":
        return False
    md = _work_trait_float(person, MATING_DRIVE_GENOME_KEY)
    ph = _work_trait_float(person, "physical")
    if md is None or ph is None:
        return False
    if md > -CROSS_GENDER_MATING_DRIVE_THRESHOLD:
        return False
    return ph > CROSS_GENDER_FEMALE_PHYS_MIN


def _male_exception_for_female_only_job(person: "Person") -> bool:
    if (person.gender_mind or "").strip().lower() != "feminine":
        return False
    md = _work_trait_float(person, MATING_DRIVE_GENOME_KEY)
    ph = _work_trait_float(person, "physical")
    if md is None or ph is None:
        return False
    if md > -CROSS_GENDER_MATING_DRIVE_THRESHOLD:
        return False
    return ph < CROSS_GENDER_MALE_PHYS_MAX


def _job_allowed_for_person(person: "Person", restriction: str | None) -> bool:
    if restriction is None:
        return True
    g = (person.gender or "").strip().lower()
    if restriction == "male":
        if g == "male":
            return True
        if g == "female":
            return _female_exception_for_male_only_job(person)
        return False
    if restriction == "female":
        if g == "female":
            return True
        if g == "male":
            return _male_exception_for_female_only_job(person)
        return False
    return True


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


def _cross_gender_job_exception(person: "Person", restriction: str | None) -> bool:
    if restriction is None:
        return False
    g = (person.gender or "").strip().lower()
    if restriction == "male" and g == "female":
        return _female_exception_for_male_only_job(person)
    if restriction == "female" and g == "male":
        return _male_exception_for_female_only_job(person)
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


def resource_pressure_for_person(
    ctx: "SimulationContext", rec: "SimulationPersonRecord"
) -> float:
    """Return local pressure where 1.0 is around capacity and >1 is scarce."""
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
) -> CareerFitness:
    """Persist current fitness score and log first/material fitness snapshots."""
    fitness = career_fitness(rec.person)
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
    saturation = _saturation_multiplier(
        current_job_count=current_job_count,
        current_family_count=current_family_count,
        settlement_resident_count=settlement_resident_count,
        market=jm,
    )
    # Simple, high-need roles exist everywhere. Specialist, prestige, or selfish
    # roles need enough local population and surplus to become a real market.
    simple_floor = 0.42 + 0.58 * scale
    complexity_penalty = (1.0 - scale) * (0.18 + 0.36 * (1.0 - need + selfish) / 2.0)
    tier_penalty = (1.0 - scale) * (0.26 if job_tier == "premium" else 0.0)
    score = (
        market_need * simple_floor * urban_fit * stability_fit * saturation
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
) -> CareerAssignment | None:
    """Pick the best available job for the person's skill, market, and desperation."""
    path = Path(db_path)
    rows = _genome_job_rows(str(path.resolve()))
    if not rows:
        return None
    catalog = JobEconomicsCatalog.load(path)
    market_catalog = JobMarketCatalog.load(path)
    traits = work_trait_values(person)
    era_key = (era or "").strip().lower()
    job_col = ERA_JOB_COLUMNS.get(era_key)
    premium_col = ERA_PREMIUM_COLUMNS.get(era_key)
    if job_col is None:
        return None

    candidates: list[
        tuple[
            dict[str, Any],
            float,
            tuple[tuple[str, str | None], ...],
            tuple[tuple[str, str | None], ...],
        ]
    ] = []
    for row in rows:
        trait = str(row.get("trait") or "").strip()
        if not trait or trait not in traits:
            continue
        common_entries = _filter_job_entries_for_person(
            person, _split_jobs(row.get(job_col))
        )
        premium_entries = _filter_job_entries_for_person(
            person,
            _split_jobs(row.get(premium_col)) if premium_col else (),
        )
        if not common_entries and not premium_entries:
            continue
        score = score_genome_job_row(
            float(traits[trait]), str(row.get("deviation_band") or "")
        )
        if score <= 0.0:
            continue
        candidates.append((row, score, common_entries, premium_entries))

    if not candidates:
        return None
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
            dict[str, Any],
            float,
            str,
            Literal["common", "premium"],
            str | None,
            float,
            float,
            float,
            str,
            float,
            float,
        ]
    ] = []
    desperation = career_desperation_score(
        resource_pressure=resource_pressure,
        unemployment_years=unemployment_years,
        household_prosperity=household_prosperity,
    )
    for row, trait_score, common_entries, premium_entries in candidates:
        society_need = _row_float(row, "society_need", 0.5)
        selfish_desperate = _row_float(row, "selfish_desperate", 0.0)
        tier_entries: tuple[tuple[str, tuple[tuple[str, str | None], ...]], ...]
        if (prefer_premium and premium_entries) or (not common_entries and premium_entries):
            tier_entries = (("premium", premium_entries),)
        else:
            tier_entries = (("common", common_entries),)
        for tier, entries in tier_entries:
            for job_title, job_restriction in entries:
                je = catalog.lookup(job_title, era_key, tier=tier)
                jm = market_catalog.lookup(job_title)
                job_key = normalize_job_catalog_key(job_title)
                job_count = int((current_job_counts or {}).get(job_key, 0))
                family_count = int((current_family_counts or {}).get(jm.job_family, 0))
                market_demand = _job_market_demand_score(
                    society_need=society_need,
                    selfish_desperate=selfish_desperate,
                    job_tier=tier,
                    settlement_resident_count=settlement_resident_count,
                    resource_pressure=resource_pressure,
                    market_pull=market_pull,
                    stability=settlement_stability,
                    current_job_count=job_count,
                    current_family_count=family_count,
                    market=jm,
                )
                saturation = _saturation_multiplier(
                    current_job_count=job_count,
                    current_family_count=family_count,
                    settlement_resident_count=settlement_resident_count,
                    market=jm,
                )
                weight = _assignment_weight(
                    trait_match=trait_score,
                    wage_yield=je.wage_yield,
                    market_demand=market_demand,
                    selfish_desperate=selfish_desperate,
                    desperation=desperation,
                )
                scored_jobs.append(
                    (
                        row,
                        trait_score,
                        job_title,
                        tier,
                        job_restriction,
                        market_demand,
                        _clamp(float(je.wage_yield) / 1.45, 0.0, 1.0),
                        selfish_desperate,
                        jm.job_family,
                        saturation,
                        weight,
                    )
                )

    if not scored_jobs:
        return None
    scored_jobs.sort(
        key=lambda item: (
            -item[10],
            str(item[2]),
            str(item[0].get("trait") or ""),
            str(item[0].get("deviation_band") or ""),
        )
    )
    top = scored_jobs[: max(1, int(top_n))]
    weights = [item[10] for item in top]
    (
        row,
        trait_score,
        job,
        job_tier,
        job_restriction,
        market_demand,
        prosperity_score,
        selfish_desperate,
        job_family,
        saturation,
        _weight,
    ) = rng.choices(top, weights=weights, k=1)[0]
    if not job:
        return None
    return CareerAssignment(
        job=job,
        job_tier=job_tier,
        job_era=era_key,
        trait=str(row.get("trait") or "").strip(),
        deviation_band=str(row.get("deviation_band") or "").strip().lower(),
        descriptor=str(row.get("descriptor") or "").strip(),
        status_tendency=str(row.get("status_tendency") or "").strip(),
        leader_quality=str(row.get("leader_quality") or "").strip(),
        leader_tendency=str(row.get("leader_tendency") or "").strip(),
        job_sex_restriction=job_restriction,
        cross_gender_job_exception=_cross_gender_job_exception(
            person, job_restriction
        ),
        society_need=_row_float(row, "society_need", 0.5),
        selfish_desperate=selfish_desperate,
        job_trait_match_score=round(_clamp(trait_score, 0.0, 1.0), 4),
        job_market_demand_score=market_demand,
        job_prosperity_score=round(prosperity_score, 4),
        job_family=job_family,
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
) -> CareerAssignment | None:
    """Assign or rehire an eligible person, returning details if changed."""
    if rec.person.job:
        return None
    historical_year = ctx.get_historical_year(year)
    era = resolve_job_era(historical_year)
    if not _eligible_for_job(rec.person, ctx.db_path, year, era):
        return None
    pressure = (
        resource_pressure_for_person(ctx, rec) if pressure is None else float(pressure)
    )
    fitness = (
        refresh_career_fitness(ctx, rec, year, pressure=pressure)
        if fitness is None
        else fitness
    )
    sid = _residence_settlement_id(rec)
    (
        settlement_pop,
        market_pull,
        settlement_stability,
        current_job_counts,
        current_family_counts,
    ) = _settlement_job_market_snapshot(ctx, sid, market_snapshots)
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
    )
    if assignment is None:
        return None
    previous_job = rec.person.last_job
    was_unemployed = rec.person.employment_status == "unemployed"
    unemployment_started = rec.person.unemployment_started_year
    unemployment_years = _unemployment_years(rec.person, year)
    comp_rows = _genome_composite_rows(str(Path(ctx.db_path).resolve()))
    comp_labels = significant_composite_names(rec.person, comp_rows)
    trait_notes = interpret_genome_personality(rec.person, db_path=ctx.db_path)
    trait_phrases = tuple(n.phrase for n in trait_notes if n.phrase)
    job_fit_score, job_trait_match_score = job_category_fitness_score(
        rec.person,
        career_score=fitness.score,
        trait=assignment.trait,
        deviation_band=assignment.deviation_band,
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
        reason=_job_loss_reason(fitness.score, pressure),
        pressure=pressure,
        fitness=fitness,
    )
    return True


def _childcare_duty_factor_safe(
    ctx: "SimulationContext", rec: "SimulationPersonRecord", year: int
) -> float:
    """Look up caregiver duty without taking a hard dep on household_care at import."""
    from library.simulation_household_care import childcare_duty_factor

    return float(childcare_duty_factor(ctx, rec, year))


def maybe_assign_or_rehire(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    year: int,
    *,
    fitness: CareerFitness,
    pressure: float,
    market_snapshots: YearJobMarketSnapshots | None = None,
    career_facts: YearCareerFacts | None = None,
) -> bool:
    if rec.person.job:
        return False
    historical_year = ctx.get_historical_year(year)
    era = resolve_job_era(historical_year)
    if not _eligible_for_job(rec.person, ctx.db_path, year, era):
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
    rng = random.Random(
        _event_seed(
            year=year,
            person_id=rec.person_id,
            salt=int(ctx.placename_rng_salt),
            stream=83_003,
        )
    )
    if rng.random() < p:
        return (
            assign_career_if_eligible(
                ctx,
                rec,
                year,
                market_snapshots=market_snapshots,
                pressure=pressure,
                fitness=fitness,
            )
            is not None
        )
    mark_unemployed(
        ctx,
        rec,
        year,
        reason="placement_failed",
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
    ctx: "SimulationContext", rec: "SimulationPersonRecord", year: int
) -> list[int]:
    """Worker, co-resident partner, and dependent children who share that home."""
    worker_id = int(rec.person_id)
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
    dest_sid = _pick_job_seeker_destination(ctx, rec, year, rng)
    if not dest_sid:
        return False
    origin_sid = _residence_settlement_id(rec)
    origin_rid = _person_residence_region(ctx, rec)
    moved_ids: list[int] = []
    group_id = f"job_seeker:{rec.person_id}:{int(year)}"
    for pid in _household_ids_for_job_move(ctx, rec, year):
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
    eligible: list[tuple[SimulationPersonRecord, CareerFitness, float]] = []
    historical_year = ctx.get_historical_year(year)
    era = resolve_job_era(historical_year)
    job_age_cache: dict[tuple[str, str, int | None], int] = {}
    potentially_eligible: list[SimulationPersonRecord] = []
    for rec in ctx.iter_current_people(sorted_by_id=True):
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
        potentially_eligible.append(rec)

    career_facts = YearCareerFacts.build(ctx, year, potentially_eligible)
    for rec in potentially_eligible:
        pressure = career_facts.pressure_for(ctx, rec)
        fitness = refresh_career_fitness(ctx, rec, year, pressure=pressure)
        eligible.append((rec, fitness, pressure))

    for rec, fitness, pressure in eligible:
        maybe_lose_job(
            ctx,
            rec,
            year,
            fitness=fitness,
            pressure=pressure,
            career_facts=career_facts,
        )

    market_snapshots = YearJobMarketSnapshots.build(ctx)
    for rec, fitness, pressure in eligible:
        if rec.person.job_lost_year == int(year):
            continue
        maybe_assign_or_rehire(
            ctx,
            rec,
            year,
            fitness=fitness,
            pressure=pressure,
            market_snapshots=market_snapshots,
            career_facts=career_facts,
        )

    for rec, fitness, pressure in eligible:
        maybe_migrate_job_seeker_household(
            ctx, rec, year, fitness=fitness, pressure=pressure
        )
