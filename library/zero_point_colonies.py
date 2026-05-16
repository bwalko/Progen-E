"""Zero-point multi-colony layout, founder seeding, and ordered yearly simulation slices."""

from __future__ import annotations

import heapq
import math
import random
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

from library.generator import generate_person_random
from library.geography import list_regions
from library.geography import _get_route_edges_by_origin
from library.person import Person
from library.reproduction import (
    annual_conception_probability,
    conception_rng,
    having_sex_birth_event,
)
from library.simulation_careers import resource_pressure_for_person
from library.simulation_context import SimulationContext, SimulationPersonRecord
from library.simulation_mortality import apply_annual_mortality

DEFAULT_FOUNDATION_COLONY_COUNT = 3
COUPLES_PER_FOUNDATION_COLONY = 10


@dataclass(frozen=True)
class FoundationColonySpec:
    region_id: str
    species: str
    ethnic: str


def default_foundation_specs(region_ids: Sequence[str]) -> list[FoundationColonySpec]:
    """Fixed species/ethnic pairing for each colony (length must match regions)."""
    triples = (
        ("Human", "Middle English"),
        ("Dwarf", "Old Norse"),
        ("Gnome", "Old English"),
    )
    if len(region_ids) > len(triples):
        raise ValueError("default_foundation_specs supports at most 3 colonies")
    return [
        FoundationColonySpec(region_ids[i], triples[i][0], triples[i][1])
        for i in range(len(region_ids))
    ]


def _weighted_adjacency(*, world: str, db_path: Path) -> dict[str, dict[str, float]]:
    by_origin = _get_route_edges_by_origin(world.strip(), db_path)
    adj: dict[str, dict[str, float]] = defaultdict(dict)
    for u, edges in by_origin.items():
        for edge in edges:
            v = edge.to_region_id
            wt = float(edge.friction)
            if v not in adj[u] or wt < adj[u][v]:
                adj[u][v] = wt
            if u not in adj[v] or wt < adj[v][u]:
                adj[v][u] = wt
    return adj


def _shortest_distance_to_targets(
    adj: dict[str, dict[str, float]],
    start: str,
    targets: set[str],
) -> float:
    if start in targets:
        return 0.0
    dist: dict[str, float] = {start: 0.0}
    pq: list[tuple[float, str]] = [(0.0, start)]
    while pq:
        d_u, u = heapq.heappop(pq)
        if d_u > dist[u]:
            continue
        if u in targets:
            return d_u
        for v, wt in adj.get(u, {}).items():
            nd = d_u + wt
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                heapq.heappush(pq, (nd, v))
    return math.inf


def pick_far_coastal_region_ids(
    *,
    world: str,
    db_path: Path,
    count: int = DEFAULT_FOUNDATION_COLONY_COUNT,
) -> list[str]:
    """Pick coast/riverland regions: first one per continent, then farthest-from-chosen greedy."""
    w = world.strip()
    regions = list_regions(world=w, db_path=db_path)
    coastal_sorted = sorted(
        (r for r in regions if r.terrain.strip().lower() in ("coast", "riverland")),
        key=lambda r: r.region_id,
    )
    if len(coastal_sorted) < count:
        raise LookupError(
            f"need {count} coast/riverland regions but only found {len(coastal_sorted)} for world={world!r}"
        )

    by_cont: dict[str, list[str]] = defaultdict(list)
    for r in coastal_sorted:
        cid = str(r.continent_id or "").strip()
        by_cont[cid].append(r.region_id)

    chosen: list[str] = []
    taken: set[str] = set()

    for cid in sorted(by_cont.keys()):
        if len(chosen) >= count:
            break
        bucket = sorted(by_cont[cid])
        for rid in bucket:
            if rid in taken:
                continue
            chosen.append(rid)
            taken.add(rid)
            break

    remain = sorted(
        rid for rid in (r.region_id for r in coastal_sorted) if rid not in taken
    )
    adj = _weighted_adjacency(world=w, db_path=db_path)
    while len(chosen) < count and remain:
        ch_set = set(chosen)
        best_rid: str | None = None
        best_key: tuple[float, str] = (-math.inf, "")
        for cand in sorted(remain):
            md = min(
                (_shortest_distance_to_targets(adj, cand, {s}) for s in chosen),
                default=math.inf,
            )
            sep = md if not math.isinf(md) else -1e9
            key = (sep, cand)
            if best_rid is None or key > best_key:
                best_key = key
                best_rid = cand
        assert best_rid is not None
        chosen.append(best_rid)
        taken.add(best_rid)
        remain.remove(best_rid)

    if len(chosen) < count:
        raise LookupError(f"could not pick {count} coastal colonies for world={world!r}")
    return chosen[:count]


def _is_mature(rec: SimulationPersonRecord, year: int) -> bool:
    if rec.person.deathyear is not None and int(rec.person.deathyear) <= int(year):
        return False
    age = year - int(rec.person.birthyear)
    min_fertility_age = rec.person.min_fertility_age
    if min_fertility_age is None:
        return True
    return age >= int(min_fertility_age)


def _eligible_for_birth(rec: SimulationPersonRecord, year: int) -> bool:
    if rec.person.deathyear is not None and int(rec.person.deathyear) <= int(year):
        return False
    age = year - int(rec.person.birthyear)
    if not _is_mature(rec, year):
        return False
    max_fertility_age = rec.person.max_fertility_age
    if max_fertility_age is not None and age > int(max_fertility_age):
        return False
    return True


def _in_colony(rec: SimulationPersonRecord, region_id: str, ctx: SimulationContext) -> bool:
    rid = ctx._residence_region_id(rec)
    return (rid or "").strip() == region_id.strip()


def generate_founder_person(
    ctx: SimulationContext,
    *,
    species: str,
    ethnic: str,
    gender: str,
    region_id: str,
    rng: random.Random,
) -> Person | None:
    """Draw a fertile founder with at least ten fertile simulation years remaining."""
    if ctx.current_year is None:
        raise ValueError("ctx.current_year required for founders")
    sim_year = int(ctx.current_year)
    settle = ctx.ensure_active_settlement_for_region(region_id)
    bp_name = settle.display_name or settle.region_display_name or region_id
    sid = settle.settlement_id
    # Allow plausible adult ages without forcing birthyear unrealistically recent.
    oldest = min(130, max(35, sim_year - 870))
    for _ in range(320):
        age_guess = rng.randint(18, oldest)
        p = generate_person_random(
            species=species,
            ethnic=ethnic,
            gender=gender,
            age=age_guess,
            simulation_year=sim_year,
            birthplace=bp_name,
            birthplace_region_id=region_id,
            birthplace_settlement_id=sid,
            simulation_context=ctx,
        )
        yrs = sim_year - int(p.birthyear)
        mf = p.min_fertility_age
        if mf is not None and yrs < int(mf):
            continue
        xf = p.max_fertility_age
        if xf is not None and yrs > int(xf) - 10:
            continue
        return p
    return None


def seed_foundation_colonies(
    ctx: SimulationContext,
    specs: Sequence[FoundationColonySpec],
    *,
    couples_per_colony: int = COUPLES_PER_FOUNDATION_COLONY,
    rng: random.Random | None = None,
) -> None:
    if rng is None:
        rng = random.Random()

    for spec in specs:
        for _ in range(couples_per_colony):
            male = generate_founder_person(
                ctx,
                species=spec.species,
                ethnic=spec.ethnic,
                gender="Male",
                region_id=spec.region_id,
                rng=rng,
            )
            female = generate_founder_person(
                ctx,
                species=spec.species,
                ethnic=spec.ethnic,
                gender="Female",
                region_id=spec.region_id,
                rng=rng,
            )
            if male is None or female is None:
                raise RuntimeError(f"founder RNG failed for region={spec.region_id!r}")

            mr = ctx.add_person(person=male, is_founder=True)
            fr = ctx.add_person(person=female, is_founder=True)
            ctx.add_couple(mr.person_id, fr.person_id)


def births_and_couples_for_region(
    ctx: SimulationContext,
    year: int,
    region_id: str,
    *,
    people_by_region: dict[str, list[SimulationPersonRecord]] | None = None,
) -> int:
    """Pair unpaired non-founders and produce births for couples anchored in ``region_id``."""
    rid = region_id.strip()
    records = (
        people_by_region.get(rid, [])
        if people_by_region is not None
        else ctx.current_people_by_region().get(rid, [])
    )
    paired_ids = {pid for pair in ctx.couples for pid in pair}
    eligible_males = [
        r
        for r in records
        if (not r.is_founder)
        and r.person.gender == "Male"
        and r.person_id not in paired_ids
        and _is_mature(r, year)
    ]
    eligible_females = [
        r
        for r in records
        if (not r.is_founder)
        and r.person.gender == "Female"
        and r.person_id not in paired_ids
        and _is_mature(r, year)
    ]
    eligible_males.sort(key=lambda r: r.person_id)
    eligible_females.sort(key=lambda r: r.person_id)
    pair_count = min(len(eligible_males), len(eligible_females))
    for i in range(pair_count):
        ctx.add_couple(eligible_males[i].person_id, eligible_females[i].person_id)

    births = 0
    rid_seed = sum((i + 1) * ord(ch) for i, ch in enumerate(rid))
    rng = random.Random(int(year) * 100_003 + rid_seed)
    for rec in records:
        if (rec.person.gender or "").strip() != "Female":
            continue
        if not _eligible_for_birth(rec, year):
            continue
        if rec.person.last_birth_event_year == year:
            continue
        candidates: list[int] = []
        pid = rec.person.partner_person_id
        mid = rec.person.paramour_person_id
        if pid is not None and ctx.is_alive(pid):
            pr = ctx.id_to_record.get(pid)
            if (
                pr is not None
                and _eligible_for_birth(pr, year)
                and _in_colony(pr, rid, ctx)
            ):
                candidates.append(pid)
        if mid is not None and ctx.is_alive(mid):
            mr = ctx.id_to_record.get(mid)
            if (
                mr is not None
                and _eligible_for_birth(mr, year)
                and _in_colony(mr, rid, ctx)
            ):
                candidates.append(mid)
        if not candidates:
            continue
        father_id = rng.choice(candidates)
        father = ctx.id_to_record[father_id]
        pressure = resource_pressure_for_person(ctx, rec)
        p_try = annual_conception_probability(rec.person, father.person, pressure=pressure)
        crng = conception_rng(year, rid_seed, rec.person_id, father_id)
        if crng.random() >= p_try:
            continue
        children = having_sex_birth_event(
            father.person,
            rec.person,
            simulation_year=year,
            rng=rng,
            birthyear=year,
            age=0,
            life_stage="child",
            birthplace=rec.person.birthplace or "Placeholder",
            birthplace_region_id=rec.person.birthplace_region_id,
            birthplace_settlement_id=(
                rec.person.current_settlement_id or rec.person.birthplace_settlement_id
            ),
            simulation_context=ctx,
            mother_person_id=rec.person_id,
        )
        if not children:
            continue
        rec.person = replace(rec.person, last_birth_event_year=year)
        for child in children:
            ctx.add_person(
                person=child,
                is_founder=False,
                father_id=father.person_id,
                mother_id=rec.person_id,
            )
            births += 1
    return births


def simulate_calendar_year_ordered_settlements(
    ctx: SimulationContext,
    *,
    year: int,
    colony_region_order: Sequence[str],
) -> dict[str, float]:
    ctx.current_year = year
    total_births = 0
    people_by_region = ctx.current_people_by_region()
    for rid in colony_region_order:
        total_births += births_and_couples_for_region(
            ctx, year, rid, people_by_region=people_by_region
        )
    mortality_rates = apply_annual_mortality(ctx, year)
    ctx.evolve_settlements_one_year()
    ctx.record_year_summary(
        year=year,
        births_count=total_births,
        deaths_count=int(mortality_rates["deaths_count"]),
        mortality_rates=mortality_rates,
        evolve_settlements_this_tick=False,
    )
    return mortality_rates
