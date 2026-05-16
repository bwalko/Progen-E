"""Resource-pressure migration: fluctuating effective regional caps and neighbor moves."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from library.geography import list_regions, list_routes_from

if TYPE_CHECKING:
    from library.simulation_context import SimulationContext

# Ratio of census / effective_cap above which out-migration is considered.
MIGRATION_PRESSURE_THRESHOLD = 0.88
# Trials scale with excess pressure; capped as a share of regional population per year.
MIGRATION_MAX_OUTFLOW_SHARE = 0.045
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
        cap = ctx.effective_regional_population_cap(dst)
        pop = ctx.count_alive_in_region(dst)
        headroom = max(1.0, float(cap - pop))
        w = headroom / (1.0 + max(0.0, float(route.friction)))
        dest_ids.append(dst)
        weights.append(max(1e-6, w))
    if not dest_ids:
        return None
    return rng.choices(dest_ids, weights=weights, k=1)[0]


def _pick_least_loaded_settlement(ctx: "SimulationContext", region_id: str):
    act = ctx.active_settlements_in_region(region_id)
    if not act:
        return ctx.ensure_active_settlement_for_region(region_id)
    return min(act, key=lambda s: ctx.count_alive_in_settlement(s.settlement_id))


def _eligible_migrant_pool(ctx: "SimulationContext", origin_rid: str, year: int) -> list[int]:
    """Adults 18+ in ``origin_rid``; one id per couple (``min`` id) so partners migrate together."""
    out: list[int] = []
    for pid in ctx.current_people_ids:
        rec = ctx.id_to_record.get(pid)
        if rec is None:
            continue
        if (ctx._residence_region_id(rec) or "") != origin_rid:
            continue
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
) -> None:
    """Move ``person_id`` then cohabiting partner if still in ``origin_rid``."""
    rec = ctx.id_to_record.get(person_id)
    if rec is None:
        return
    partner_id = rec.person.partner_person_id
    ctx.move_person_to_settlement(
        person_id, dest_settlement_id, move_reason="resource_pressure_migration"
    )
    if partner_id is None or partner_id not in ctx.current_people_ids:
        return
    p2 = ctx.id_to_record.get(partner_id)
    if p2 is None:
        return
    if (ctx._residence_region_id(p2) or "") != origin_rid:
        return
    try:
        ctx.move_person_to_settlement(
            partner_id, dest_settlement_id, move_reason="resource_pressure_migration"
        )
    except (ValueError, LookupError):
        pass


def simulation_migration_annual_tick(ctx: "SimulationContext", year: int) -> None:
    """Move people from high-pressure regions toward neighbor headroom; then drift caps for next year."""
    rng = random.Random(
        int(year) * 500_011 + int(ctx.placename_rng_salt) + 4241
    )

    for r in list_regions(world=ctx.world, db_path=ctx.db_path):
        rid = (r.region_id or "").strip()
        if not rid:
            continue
        cap = ctx.effective_regional_population_cap(rid)
        pop = ctx.count_alive_in_region(rid)
        if cap <= 0 or pop <= 0:
            continue
        pressure = float(pop) / float(cap)
        if pressure < MIGRATION_PRESSURE_THRESHOLD:
            continue
        excess = max(0.0, pressure - MIGRATION_PRESSURE_THRESHOLD)
        trial_cap = max(1, int(pop * MIGRATION_MAX_OUTFLOW_SHARE))
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
            dst_rid = _pick_destination_region(ctx, rid, year, rng)
            if dst_rid is None:
                continue
            try:
                st = _pick_least_loaded_settlement(ctx, dst_rid)
                _move_migrant_and_coresident_partner(ctx, pid, rid, st.settlement_id)
            except (ValueError, LookupError):
                continue

    tick_region_effective_cap_multipliers(ctx, rng)
