"""Lightweight population records for hybrid-scale simulation."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
import random
from typing import Any

from library.generator import generate_person_random
from library.person import Person
from library.random_names import choose_random_first_last


@dataclass(frozen=True)
class PassivePerson:
    """Minimal persisted facts for a real person outside detailed annual simulation."""

    name: str
    birthyear: int
    deathyear: int | None = None
    gender: str = ""
    species: str | None = None
    ethnic: str | None = None
    birthplace_region_id: str | None = None
    birthplace_settlement_id: str | None = None
    current_settlement_id: str | None = None
    job_family: str | None = None
    partner_person_id: int | None = None
    partner_name: str | None = None
    partner_birthyear: int | None = None
    partner_deathyear: int | None = None
    partnership_start_year: int | None = None
    partnership_end_year: int | None = None
    father_id: int | None = None
    mother_id: int | None = None
    child_count: int = 0
    child_person_ids: tuple[int, ...] = ()
    child_birthyears: tuple[int, ...] = ()
    status_bucket: str | None = None
    prosperity_bucket: str | None = None


@dataclass(frozen=True)
class PassivePersonRecord:
    """Runtime wrapper for a passive person row sharing the global person id space."""

    person_id: int
    person: PassivePerson


@dataclass(frozen=True)
class PassiveCohort:
    """Aggregate demographic bucket for people too numerous to materialize one by one."""

    sim_year: int
    population_count: int
    birth_count: int = 0
    death_count: int = 0
    region_id: str | None = None
    settlement_id: str | None = None
    age_band: str = ""
    gender: str = ""
    species: str = ""
    culture: str = ""
    job_family: str = ""
    status_bucket: str = ""


PassiveMarriageCandidateIndex = dict[tuple[str, str], list[tuple[int, int, PassiveCohort]]]
PassiveOfficeCandidateIndex = dict[tuple[str, str], list[tuple[int, int, PassiveCohort]]]


def normalize_passive_gender(gender: str | None, *, fallback: str = "Male") -> str:
    raw = (gender or "").strip().lower()
    if raw.startswith("f"):
        return "Female"
    if raw.startswith("m"):
        return "Male"
    return fallback


def passive_person_to_detailed_person(
    passive: PassivePerson,
    *,
    simulation_context,
    simulation_year: int,
) -> Person:
    """Generate full mutable ``Person`` state from persisted low-detail facts."""
    age = max(0, int(simulation_year) - int(passive.birthyear))
    gender = normalize_passive_gender(passive.gender)
    generated = generate_person_random(
        species=passive.species,
        ethnic=passive.ethnic,
        gender=gender,
        age=age,
        birthyear=int(passive.birthyear),
        simulation_year=int(simulation_year),
        birthplace_region_id=passive.birthplace_region_id,
        birthplace_settlement_id=passive.birthplace_settlement_id,
        simulation_context=simulation_context,
        world=simulation_context.world,
        db_path=simulation_context.db_path,
    )
    first, _, last = (passive.name or "").strip().partition(" ")
    if not first:
        first = generated.first_name
    if not last:
        last = generated.last_name
    return replace(
        generated,
        first_name=first,
        last_name=last,
        deathyear=passive.deathyear,
        current_settlement_id=(
            passive.current_settlement_id
            or passive.birthplace_settlement_id
            or generated.current_settlement_id
        ),
        partner_person_id=passive.partner_person_id,
        job_tier=passive.status_bucket,
        household_prosperity=_prosperity_bucket_value(passive.prosperity_bucket),
    )


def _prosperity_bucket_value(bucket: str | None) -> float | None:
    b = (bucket or "").strip().lower()
    if b in ("poor", "lean"):
        return 0.25
    if b in ("modest", "common", "middling"):
        return 0.55
    if b in ("wealthy", "elite"):
        return 0.82
    return None


def promote_passive_candidate_for_office(
    ctx: Any,
    *,
    year: int,
    settlement_id: str | None = None,
    region_id: str | None = None,
    min_age: int = 16,
    reason: str = "office_selection",
    source: dict[str, Any] | None = None,
    candidate_index: PassiveOfficeCandidateIndex | None = None,
) -> Any | None:
    """Promote one adult from the latest aggregate cohort snapshot for an office."""
    sid = (settlement_id or "").strip()
    rid = (region_id or "").strip()
    candidates: list[tuple[int, int, PassiveCohort]] = []
    if candidate_index is not None:
        if sid:
            source_candidates = candidate_index.get(("settlement", sid), ())
        elif rid:
            source_candidates = candidate_index.get(("region", rid), ())
        else:
            source_candidates = candidate_index.get(("all", ""), ())
        latest_year = int(
            getattr(ctx, "_passive_office_candidate_index_latest_year", int(year))
        )
        for age, idx, cohort in source_candidates:
            if int(cohort.population_count) <= 0:
                continue
            if sid and (cohort.settlement_id or "").strip() != sid:
                continue
            if rid and (cohort.region_id or "").strip() != rid:
                continue
            if age < int(min_age):
                continue
            candidates.append((age, idx, cohort))
    else:
        latest_year = max((int(c.sim_year) for c in ctx.passive_cohorts), default=None)
        if latest_year is None:
            return None
        for idx, cohort in enumerate(ctx.passive_cohorts):
            if int(cohort.sim_year) != latest_year:
                continue
            if int(cohort.population_count) <= 0:
                continue
            if sid and (cohort.settlement_id or "").strip() != sid:
                continue
            if rid and (cohort.region_id or "").strip() != rid:
                continue
            age = _age_from_band(cohort.age_band)
            if age < int(min_age):
                continue
            candidates.append((age, idx, cohort))
    if not candidates:
        return None
    seed = _stable_seed(
        "|".join(
            (
                str(getattr(ctx, "world", "")),
                str(getattr(ctx, "placename_rng_salt", 0)),
                str(year),
                sid,
                rid,
                str(reason),
            )
        )
    )
    rng = random.Random(seed)
    age, cohort_index, cohort = rng.choice(candidates)
    updated = replace(cohort, population_count=int(cohort.population_count) - 1)
    ctx.passive_cohorts[cohort_index] = updated
    if candidate_index is not None:
        _update_passive_office_candidate_index(
            candidate_index,
            cohort_index=cohort_index,
            age=age,
            cohort=cohort,
            updated=updated,
        )
    birthyear = int(year) - int(age)
    family_facts = _synthesize_family_facts_for_cohort(
        ctx,
        cohort=cohort,
        birthyear=birthyear,
        year=int(year),
        rng=rng,
    )
    person = PassivePerson(
        name="",
        birthyear=birthyear,
        gender=normalize_passive_gender(cohort.gender, fallback=rng.choice(["Male", "Female"])),
        species=(cohort.species or None),
        ethnic=(cohort.culture or None),
        birthplace_region_id=cohort.region_id,
        birthplace_settlement_id=cohort.settlement_id,
        current_settlement_id=cohort.settlement_id,
        job_family=cohort.job_family,
        status_bucket=cohort.status_bucket,
        prosperity_bucket=_prosperity_from_status(cohort.status_bucket),
        **family_facts,
    )
    passive = ctx.add_passive_person(person)
    return ctx.promote_passive_person(
        passive.person_id,
        year=int(year),
        reason=reason,
        source={
            **(source or {}),
            "source_cohort_year": int(latest_year),
            "source_age_band": cohort.age_band,
            "source_population_before": int(cohort.population_count),
        },
    )


def build_passive_office_candidate_index(ctx: Any) -> PassiveOfficeCandidateIndex:
    """Index latest passive cohorts for repeated office-promotion lookups."""
    latest_year = max((int(c.sim_year) for c in ctx.passive_cohorts), default=None)
    if latest_year is None:
        return {}
    setattr(ctx, "_passive_office_candidate_index_latest_year", int(latest_year))
    out: PassiveOfficeCandidateIndex = {}
    for idx, cohort in enumerate(ctx.passive_cohorts):
        if int(cohort.sim_year) != latest_year:
            continue
        if int(cohort.population_count) <= 0:
            continue
        age = _age_from_band(cohort.age_band)
        entry = (age, idx, cohort)
        out.setdefault(("all", ""), []).append(entry)
        rid = (cohort.region_id or "").strip()
        if rid:
            out.setdefault(("region", rid), []).append(entry)
        sid = (cohort.settlement_id or "").strip()
        if sid:
            out.setdefault(("settlement", sid), []).append(entry)
    return out


def _update_passive_office_candidate_index(
    candidate_index: PassiveOfficeCandidateIndex,
    *,
    cohort_index: int,
    age: int,
    cohort: PassiveCohort,
    updated: PassiveCohort,
) -> None:
    keys = [("all", "")]
    rid = (cohort.region_id or "").strip()
    if rid:
        keys.append(("region", rid))
    sid = (cohort.settlement_id or "").strip()
    if sid:
        keys.append(("settlement", sid))
    for key in keys:
        bucket = candidate_index.get(key)
        if bucket is None:
            continue
        for pos, (_age, idx, _cohort) in enumerate(bucket):
            if idx != int(cohort_index):
                continue
            if int(updated.population_count) > 0:
                bucket[pos] = (int(age), int(cohort_index), updated)
            else:
                bucket.pop(pos)
            break


def build_passive_marriage_candidate_index(ctx: Any) -> PassiveMarriageCandidateIndex:
    """Index latest single adult passive cohorts by settlement and normalized gender."""
    latest_year = max((int(c.sim_year) for c in ctx.passive_cohorts), default=None)
    if latest_year is None:
        return {}
    setattr(ctx, "_passive_marriage_candidate_index_latest_year", int(latest_year))
    out: PassiveMarriageCandidateIndex = {}
    for idx, cohort in enumerate(ctx.passive_cohorts):
        if int(cohort.sim_year) != latest_year:
            continue
        if int(cohort.population_count) <= 0:
            continue
        status = (cohort.status_bucket or "").strip().lower()
        if status in {"child", "partnered"}:
            continue
        age = _age_from_band(cohort.age_band)
        sid = (cohort.settlement_id or "").strip()
        if not sid:
            continue
        gender = normalize_passive_gender(cohort.gender, fallback="")
        if not gender:
            continue
        out.setdefault((sid, gender), []).append((age, idx, cohort))
    return out


def promote_passive_candidate_for_marriage(
    ctx: Any,
    *,
    year: int,
    gender: str,
    settlement_id: str | None = None,
    region_id: str | None = None,
    min_age: int = 16,
    reason: str = "marriage_into_detailed_family",
    source: dict[str, Any] | None = None,
    candidate_index: PassiveMarriageCandidateIndex | None = None,
) -> Any | None:
    """Promote one single adult from aggregate cohorts as a detailed spouse."""
    wanted_gender = normalize_passive_gender(gender)
    sid = (settlement_id or "").strip()
    rid = (region_id or "").strip()
    candidates: list[tuple[int, int, PassiveCohort]] = []
    if candidate_index is not None and sid:
        for age, idx, cohort in candidate_index.get((sid, wanted_gender), ()):
            if int(cohort.population_count) <= 0:
                continue
            if rid and (cohort.region_id or "").strip() != rid:
                continue
            if age < int(min_age):
                continue
            candidates.append((age, idx, cohort))
        latest_year = int(
            getattr(ctx, "_passive_marriage_candidate_index_latest_year", int(year))
        )
    else:
        latest_year = max((int(c.sim_year) for c in ctx.passive_cohorts), default=None)
        if latest_year is None:
            return None
        for idx, cohort in enumerate(ctx.passive_cohorts):
            if int(cohort.sim_year) != latest_year:
                continue
            if int(cohort.population_count) <= 0:
                continue
            if sid and (cohort.settlement_id or "").strip() != sid:
                continue
            if rid and (cohort.region_id or "").strip() != rid:
                continue
            if normalize_passive_gender(cohort.gender, fallback="") != wanted_gender:
                continue
            status = (cohort.status_bucket or "").strip().lower()
            if status in {"child", "partnered"}:
                continue
            age = _age_from_band(cohort.age_band)
            if age < int(min_age):
                continue
            candidates.append((age, idx, cohort))
    if not candidates:
        return None
    seed = _stable_seed(
        "|".join(
            (
                str(getattr(ctx, "world", "")),
                str(getattr(ctx, "placename_rng_salt", 0)),
                str(year),
                sid,
                rid,
                wanted_gender,
                str(reason),
                str((source or {}).get("detailed_person_id", "")),
            )
        )
    )
    rng = random.Random(seed)
    age, cohort_index, cohort = rng.choice(candidates)
    updated = replace(cohort, population_count=int(cohort.population_count) - 1)
    ctx.passive_cohorts[cohort_index] = updated
    if candidate_index is not None and sid:
        bucket = candidate_index.get((sid, wanted_gender))
        if bucket is not None:
            for pos, (_age, idx, _cohort) in enumerate(bucket):
                if idx != cohort_index:
                    continue
                if int(updated.population_count) > 0:
                    bucket[pos] = (age, cohort_index, updated)
                else:
                    bucket.pop(pos)
                break
    birthyear = int(year) - int(age)
    person = PassivePerson(
        name="",
        birthyear=birthyear,
        gender=wanted_gender,
        species=(cohort.species or None),
        ethnic=(cohort.culture or None),
        birthplace_region_id=cohort.region_id,
        birthplace_settlement_id=cohort.settlement_id,
        current_settlement_id=cohort.settlement_id,
        job_family=cohort.job_family,
        status_bucket=cohort.status_bucket,
        prosperity_bucket=_prosperity_from_status(cohort.status_bucket),
    )
    passive = ctx.add_passive_person(person)
    return ctx.promote_passive_person(
        passive.person_id,
        year=int(year),
        reason=reason,
        source={
            **(source or {}),
            "source_cohort_year": int(latest_year),
            "source_age_band": cohort.age_band,
            "source_population_before": int(cohort.population_count),
        },
    )


def _age_from_band(age_band: str) -> int:
    raw = (age_band or "").strip()
    if "-" in raw:
        raw = raw.split("-", 1)[0]
    try:
        return max(0, int(raw))
    except ValueError:
        return 30


def _stable_seed(text: str) -> int:
    h = 14_695_981_039_346_656_037
    for ch in text:
        h ^= ord(ch)
        h = (h * 1_099_511_628_211) & 0xFFFFFFFFFFFFFFFF
    return h


def _prosperity_from_status(status: str | None) -> str | None:
    s = (status or "").strip().lower()
    if s in ("elite", "wealthy"):
        return "wealthy"
    if s in ("modest", "partnered"):
        return "modest"
    if s:
        return "common"
    return None


def _synthesize_family_facts_for_cohort(
    ctx: Any,
    *,
    cohort: PassiveCohort,
    birthyear: int,
    year: int,
    rng: random.Random,
) -> dict[str, object]:
    age = max(0, int(year) - int(birthyear))
    partnered = (cohort.status_bucket or "").strip().lower() == "partnered"
    if not partnered or age < 16:
        return {}
    start_age = min(age, rng.randint(18, 28))
    partnership_start_year = int(birthyear) + int(start_age)
    partner_birthyear = int(birthyear) + rng.randint(-5, 5)
    partner_gender = (
        "Female"
        if normalize_passive_gender(cohort.gender, fallback="Male") == "Male"
        else "Male"
    )
    partner_name = _passive_related_name(
        ctx,
        gender=partner_gender,
        ethnic=(cohort.culture or None),
        region_id=cohort.region_id,
        settlement_id=cohort.settlement_id,
    )
    child_birthyears = _synthesize_child_birthyears(
        birthyear=int(birthyear),
        partnership_start_year=partnership_start_year,
        year=int(year),
        rng=rng,
    )
    return {
        "partner_name": partner_name,
        "partner_birthyear": partner_birthyear,
        "partnership_start_year": partnership_start_year,
        "child_count": len(child_birthyears),
        "child_birthyears": tuple(child_birthyears),
    }


def _synthesize_child_birthyears(
    *,
    birthyear: int,
    partnership_start_year: int,
    year: int,
    rng: random.Random,
) -> tuple[int, ...]:
    latest = min(int(year), int(birthyear) + 42)
    first = max(int(partnership_start_year) + rng.randint(0, 2), int(birthyear) + 18)
    if first > latest:
        return ()
    out: list[int] = []
    cur = first
    while cur <= latest and len(out) < 8:
        out.append(cur)
        cur += rng.randint(2, 5)
        if rng.random() < 0.18:
            cur += rng.randint(1, 3)
    return tuple(out)


def _passive_related_name(
    ctx: Any,
    *,
    gender: str,
    ethnic: str | None,
    region_id: str | None,
    settlement_id: str | None,
) -> str:
    try:
        first, last = choose_random_first_last(
            ethnic=ethnic,
            gender=gender,
            birthplace="Placeholder",
            db_path=ctx.db_path,
            birthplace_region_id=region_id,
            world=ctx.world,
            simulation_context=ctx,
        )
        return f"{first} {last}".strip()
    except Exception:
        return "Unknown Partner"
