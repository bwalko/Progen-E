"""Resource-pressure migration: fluctuating effective regional caps and neighbor moves."""

from __future__ import annotations

import math
import random
import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

from library.geography import list_regions, list_routes_from, region_connectivity_score
from library.nondetailed_population import run_nondetailed_sql_migration
from library.settlements import settlement_attraction_score
from library.world_save import ensure_checkpoint_schema

if TYPE_CHECKING:
    from library.simulation_context import SimulationContext

# Ratio of census / effective_cap above which out-migration is considered.
MIGRATION_PRESSURE_THRESHOLD = 0.88
# Trials scale with excess pressure; capped as a share of regional population per year.
MIGRATION_MAX_OUTFLOW_SHARE = 0.08
MIGRATION_TRIALS_PER_EXCESS_PRESSURE = 3.2
# Random walk on effective-cap multiplier (applied to config carrying_capacity).
_CAP_DRIFT_LOW = 0.993
_CAP_DRIFT_HIGH = 1.007
_CAP_MULT_FLOOR = 0.58
_CAP_MULT_CEIL = 1.48


def tick_region_effective_cap_multipliers(ctx: "SimulationContext", rng: random.Random) -> None:
    """Slowly drift each region's effective population cap multiplier (not a hard census cap)."""
    for r in list_regions(world=ctx.world, db_path=ctx.db_path):
        rid = (r.region_id or "").strip()
        if not rid:
            continue
        if rid not in ctx.region_effective_cap_multiplier:
            ctx.region_effective_cap_multiplier[rid] = 1.0
        m = float(ctx.region_effective_cap_multiplier[rid])
        m *= rng.uniform(_CAP_DRIFT_LOW, _CAP_DRIFT_HIGH)
        m = max(_CAP_MULT_FLOOR, min(_CAP_MULT_CEIL, m))
        ctx.region_effective_cap_multiplier[rid] = m


def _pick_destination_region(
    ctx: "SimulationContext",
    origin_rid: str,
    year: int,
    rng: random.Random,
    resource_facts=None,
) -> str | None:
    routes = list_routes_from(
        origin_rid,
        world=ctx.world,
        db_path=ctx.db_path,
        simulation_year=year,
    )
    if not routes:
        return None
    dest_ids: list[str] = []
    weights: list[float] = []
    for route in routes:
        dst = (route.to_region_id or "").strip()
        if not dst or dst == origin_rid:
            continue
        if resource_facts is not None:
            cap = int(resource_facts.region_cap.get(dst, 0))
            pop = int(resource_facts.region_population.get(dst, 0))
            if cap <= 0:
                cap = ctx.effective_regional_population_cap(dst)
        else:
            cap = ctx.effective_regional_population_cap(dst)
            mixed = getattr(ctx, "mixed_population_count_in_region", None)
            pop = int(mixed(dst)) if callable(mixed) else ctx.count_alive_in_region(dst)
        headroom = max(1.0, float(cap - pop))
        w = headroom / (1.0 + max(0.0, float(route.friction)))
        dest_ids.append(dst)
        weights.append(max(1e-6, w))
    if not dest_ids:
        return None
    return rng.choices(dest_ids, weights=weights, k=1)[0]


def _pick_attractive_settlement(
    ctx: "SimulationContext",
    region_id: str,
    *,
    year: int,
    rng: random.Random,
):
    act = ctx.active_settlements_in_region(region_id)
    if not act:
        return ctx.ensure_active_settlement_for_region(region_id)
    try:
        connectivity = region_connectivity_score(
            region_id,
            world=ctx.world,
            db_path=ctx.db_path,
            simulation_year=year,
        )
    except Exception:
        connectivity = 0.0
    scored = [
        (settlement_attraction_score(st, connectivity_score=connectivity), st)
        for st in act
    ]
    scored = [(score, st) for score, st in scored if score > 0.0]
    if not scored:
        return act[0]
    scored.sort(key=lambda item: (-item[0], item[1].settlement_id))
    top = scored[: min(6, len(scored))]
    return rng.choices([st for _score, st in top], weights=[score for score, _st in top], k=1)[0]


def _eligible_migrant_pool(ctx: "SimulationContext", origin_rid: str, year: int) -> list[int]:
    """Adults 18+ in ``origin_rid``; one id per couple (``min`` id) so partners migrate together."""
    out: list[int] = []
    for rec in ctx.current_people_by_region().get(origin_rid, ()):
        pid = int(rec.person_id)
        age = int(year) - int(rec.person.birthyear)
        if age < 18:
            continue
        partner_id = rec.person.partner_person_id
        if partner_id is not None and partner_id in ctx.current_people_ids:
            rep = min(int(pid), int(partner_id))
            if int(pid) != rep:
                continue
        out.append(int(pid))
    return out


def _move_migrant_and_coresident_partner(
    ctx: "SimulationContext",
    person_id: int,
    origin_rid: str,
    dest_settlement_id: str,
    year: int,
) -> None:
    """Queue ``person_id`` then cohabiting partner if still in ``origin_rid``."""
    rec = ctx.id_to_record.get(person_id)
    if rec is None:
        return
    partner_id = rec.person.partner_person_id
    group_id = f"resource_pressure:{origin_rid}:{person_id}:{int(year)}"
    ctx.queue_person_move_to_settlement(
        person_id,
        dest_settlement_id,
        move_reason="resource_pressure_migration",
        requested_year=int(year),
        apply_year=int(year) + 1,
        source_event="resource_pressure_migration",
        group_id=group_id,
    )
    if partner_id is None or partner_id not in ctx.current_people_ids:
        return
    p2 = ctx.id_to_record.get(partner_id)
    if p2 is None:
        return
    if (ctx._residence_region_id(p2) or "") != origin_rid:
        return
    try:
        ctx.queue_person_move_to_settlement(
            partner_id,
            dest_settlement_id,
            move_reason="resource_pressure_migration",
            requested_year=int(year),
            apply_year=int(year) + 1,
            source_event="resource_pressure_migration",
            group_id=group_id,
        )
    except (ValueError, LookupError):
        pass


def simulation_migration_annual_tick(ctx: "SimulationContext", year: int) -> None:
    """Move people from high-pressure regions toward neighbor headroom; then drift caps for next year."""
    rng = random.Random(
        int(year) * 500_011 + int(ctx.placename_rng_salt) + 4241
    )
    resource_facts = ctx.annual_resource_facts(year)

    for r in list_regions(world=ctx.world, db_path=ctx.db_path):
        rid = (r.region_id or "").strip()
        if not rid:
            continue
        cap = int(resource_facts.region_cap.get(rid, 0))
        pop = int(resource_facts.region_population.get(rid, 0))
        if cap <= 0 or pop <= 0:
            continue
        pressure = float(pop) / float(cap)
        if pressure < MIGRATION_PRESSURE_THRESHOLD:
            continue
        excess = max(0.0, pressure - MIGRATION_PRESSURE_THRESHOLD)
        surplus_above_threshold = max(
            1,
            int(math.ceil(float(pop) - float(cap) * MIGRATION_PRESSURE_THRESHOLD)),
        )
        trial_cap = min(
            surplus_above_threshold,
            max(1, int(pop * MIGRATION_MAX_OUTFLOW_SHARE)),
        )
        trials = min(
            trial_cap,
            max(0, int(pop * excess * MIGRATION_TRIALS_PER_EXCESS_PRESSURE)),
        )
        if trials <= 0:
            continue
        pool = _eligible_migrant_pool(ctx, rid, year)
        if not pool:
            continue
        rng.shuffle(pool)
        for pid in pool[:trials]:
            dst_rid = _pick_destination_region(
                ctx, rid, year, rng, resource_facts=resource_facts
            )
            if dst_rid is None:
                continue
            try:
                st = _pick_attractive_settlement(ctx, dst_rid, year=year, rng=rng)
                _move_migrant_and_coresident_partner(ctx, pid, rid, st.settlement_id, year)
            except (ValueError, LookupError):
                continue

    if int(getattr(ctx, "_nondetailed_sql_migration_year", -1)) != int(year):
        try:
            with closing(sqlite3.connect(ctx.save_db_path)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                nd_migration = run_nondetailed_sql_migration(conn, ctx, year=year)
                conn.commit()
            ctx._nondetailed_sql_migration_year = int(year)
            if nd_migration.moved and hasattr(ctx, "sync_settlement_resident_counts"):
                if hasattr(ctx, "invalidate_mixed_population_cache"):
                    ctx.invalidate_mixed_population_cache()
                ctx.sync_settlement_resident_counts()
        except sqlite3.Error:
            pass

    tick_region_effective_cap_multipliers(ctx, rng)
    ctx.invalidate_annual_indexes()
