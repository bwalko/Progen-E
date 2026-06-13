"""Annual pooled prosperity, per-job draws, wages, taxes, and regional treasury."""

from __future__ import annotations

import random
import time
from dataclasses import replace
from typing import TYPE_CHECKING

from library import simulation_timing
from library.job_economics import JobEconomicsCatalog, JobEconomicsParams, JobTier
from library.job_archetypes import JobArchetypeCatalog
from library.job_market import JobMarketCatalog, JobMarketParams
from library.settlements import SettlementState
from library.simulation_careers import (
    _household_ids_for_job_move,
    resolve_job_era,
    resource_pressure_for_person,
)

if TYPE_CHECKING:
    from library.simulation_context import SimulationContext, SimulationPersonRecord

PROSPERITY_POOL_MIN = 0.0
PROSPERITY_POOL_MAX = 2.5
REGION_POOL_MIN = 0.0
REGION_POOL_MAX = 2.5
POOL_TOWARD_BASELINE = 0.14
REGION_POOL_TOWARD_BASELINE = 0.11
VALUE_ADD_ON_DRAW_SCALE = 0.34
WAGE_BASE_SCALE = 0.72
TREASURY_INCOME_SCALE = 0.28
LEADER_SPEND_PROB = 0.12
LEADER_SPEND_FRAC_OF_TREASURY = 0.035
LEADER_SPEND_CAP = 0.22
STABILITY_BUMP_CAP = 0.04
SETTLEMENT_POOL_BUMP_CAP = 0.06
HOUSEHOLD_PROSPERITY_MIN = 0.0
HOUSEHOLD_PROSPERITY_MAX = 25.0
HOUSEHOLD_STARTING_PROSPERITY = 1.0
HOUSEHOLD_JOB_INCOME_SCALE = 0.32
HOUSEHOLD_BASE_EXPENSE_PER_PERSON = 0.045
HOUSEHOLD_DEPENDENT_EXPENSE = 0.028
HOUSEHOLD_UNEMPLOYED_ADULT_EXPENSE = 0.034
HOUSEHOLD_LOW_STATUS_JOB_EXPENSE = 0.012

# Above this caregiver-duty value, an informal (non-officeholding) leader is
# excluded from the treasury leader pool. Officeholders (treasury seats) bypass
# the gate so seated rulers continue to direct spend regardless.
LEADER_CHILD_DUTY_EXCLUSION_THRESHOLD = 0.55


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _government_treasury_holder_ids(ctx: "SimulationContext") -> set[int]:
    """Current officeholders allowed to direct treasury spend."""
    try:
        from library.government_catalog import load_government_catalog
        from library.simulation_government import init_government_state

        init_government_state(ctx)
        catalog = load_government_catalog(ctx.db_path)
    except Exception:
        return set()

    holder_ids: set[int] = set()
    for seat in getattr(ctx, "gov_office_seats", {}).values():
        holder_id = seat.holder_person_id
        if holder_id is None:
            continue
        try:
            title = catalog.title_by_id(seat.title_id)
        except Exception:
            continue
        role = (title.role or "").strip().lower() if title is not None else ""
        if role in ("head", "court"):
            holder_ids.add(int(holder_id))
    return holder_ids


def _leader_candidate(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    *,
    year: int | None = None,
    care_indexes: object | None = None,
    treasury_holder_ids: set[int] | None = None,
) -> bool:
    from library.simulation_household_care import childcare_duty_factor

    pid = int(rec.person_id)
    if treasury_holder_ids is not None:
        if pid in treasury_holder_ids:
            return True
    else:
        from library.simulation_government import person_holds_government_treasury_seat

        if person_holds_government_treasury_seat(ctx, pid):
            return True
    lq = (rec.person.leader_quality or "").strip().lower()
    lt = (rec.person.leader_tendency or "").strip().lower()
    if lq not in ("strong", "high") and lt not in ("high", "strong"):
        return False
    y = int(year if year is not None else ctx.current_year or 0)
    if care_indexes is None:
        care_indexes = ctx.annual_care_indexes(y)
    duty = childcare_duty_factor(ctx, rec, y, indexes=care_indexes)
    return duty <= LEADER_CHILD_DUTY_EXCLUSION_THRESHOLD


def _infer_job_tier() -> JobTier:
    return "common"


def _job_tier_for_person(rec: "SimulationPersonRecord") -> JobTier:
    tier = (rec.person.job_tier or "common").strip().lower()
    return "premium" if tier == "premium" else "common"


def _trait_value(rec: "SimulationPersonRecord", key: str, default: float = 0.0) -> float:
    try:
        return float(rec.person.mind_body.get(key, rec.person.genome.get(key, default)))
    except (AttributeError, TypeError, ValueError):
        return float(default)


def household_purseholder_id(
    ctx: "SimulationContext", member_ids: set[int] | frozenset[int]
) -> int | None:
    """Assertiveness holder for the main partnership/household purse."""
    best: tuple[float, int] | None = None
    for pid in sorted(member_ids):
        rec = ctx.id_to_record.get(pid)
        if rec is None or pid not in ctx.current_people_ids:
            continue
        score = _trait_value(rec, "assertiveness")
        cand = (score, -int(pid))
        if best is None or cand > best:
            best = cand
    return -best[1] if best is not None else None


def preferred_child_caretaker_id(
    ctx: "SimulationContext", member_ids: set[int] | frozenset[int]
) -> int | None:
    """Nurturance holder used by future household split logic for dependent children."""
    best: tuple[float, int] | None = None
    for pid in sorted(member_ids):
        rec = ctx.id_to_record.get(pid)
        if rec is None or pid not in ctx.current_people_ids:
            continue
        score = -abs(_trait_value(rec, "nurturance"))
        cand = (score, -int(pid))
        if best is None or cand > best:
            best = cand
    return -best[1] if best is not None else None


def _frugality_expense_multiplier(ctx: "SimulationContext", hkey: frozenset[int]) -> float:
    vals: list[float] = []
    for pid in hkey:
        rec = ctx.id_to_record.get(pid)
        if rec is None or pid not in ctx.current_people_ids:
            continue
        vals.append(_trait_value(rec, "frugality"))
    if not vals:
        return 1.0
    avg = sum(vals) / len(vals)
    # Positive frugality conserves resources; negative wastefulness spends faster.
    # Extreme miserly values still conserve but with diminishing returns.
    return _clamp(1.0 - 0.28 * (avg / 100.0), 0.72, 1.28)


def _household_current_savings(ctx: "SimulationContext", hkey: frozenset[int]) -> float:
    vals: list[float] = []
    for pid in hkey:
        rec = ctx.id_to_record.get(pid)
        if rec is None:
            continue
        v = rec.person.household_prosperity
        if v is not None:
            vals.append(float(v))
    return vals[0] if vals else HOUSEHOLD_STARTING_PROSPERITY


def _update_household_prosperity(
    ctx: "SimulationContext",
    year: int,
    *,
    care_indexes: object | None = None,
) -> None:
    """Accumulate annual household savings from wages minus household expenses."""
    seen: set[frozenset[int]] = set()
    for rec in ctx.iter_current_people(sorted_by_id=True):
        if rec.person_id not in ctx.current_people_ids:
            continue
        if ctx._person_is_dependent_minor(rec, year):
            continue
        hkey = frozenset(_household_ids_for_job_move(ctx, rec, year, indexes=care_indexes))
        if not hkey or hkey in seen:
            continue
        seen.add(hkey)
        alive_members = [
            ctx.id_to_record[pid]
            for pid in sorted(hkey)
            if pid in ctx.current_people_ids and pid in ctx.id_to_record
        ]
        if not alive_members:
            continue
        income = 0.0
        unemployed_adults = 0
        low_status_workers = 0
        dependents = 0
        for hr in alive_members:
            if ctx._person_is_dependent_minor(hr, year):
                dependents += 1
                continue
            es = (hr.person.employment_status or "").strip().lower()
            if es == "employed" and (hr.person.job or "").strip():
                market_type = (hr.person.job_market_type or "settlement_market").strip().lower()
                if market_type == "household_care":
                    pass
                elif market_type == "domestic_service":
                    wage = float(hr.person.job_prosperity_01 or 0.0)
                    if hr.person.employer_person_id in hkey:
                        income += wage * HOUSEHOLD_JOB_INCOME_SCALE * 0.15
                        expenses += 0.018 + wage * 0.055
                    else:
                        income += wage * HOUSEHOLD_JOB_INCOME_SCALE
                    low_status_workers += 1
                elif market_type in {"vice", "criminal"}:
                    income += float(hr.person.job_prosperity_01 or 0.0) * HOUSEHOLD_JOB_INCOME_SCALE * 0.55
                    low_status_workers += 1
                else:
                    income += float(hr.person.job_prosperity_01 or 0.0) * HOUSEHOLD_JOB_INCOME_SCALE
                st = (hr.person.status_tendency or "").strip().lower()
                if st.startswith("low"):
                    low_status_workers += 1
            else:
                unemployed_adults += 1
        expenses = (
            len(alive_members) * HOUSEHOLD_BASE_EXPENSE_PER_PERSON
            + dependents * HOUSEHOLD_DEPENDENT_EXPENSE
            + unemployed_adults * HOUSEHOLD_UNEMPLOYED_ADULT_EXPENSE
            + low_status_workers * HOUSEHOLD_LOW_STATUS_JOB_EXPENSE
        )
        expenses *= _frugality_expense_multiplier(ctx, hkey)
        old = _household_current_savings(ctx, hkey)
        new = _clamp(
            old + income - expenses,
            HOUSEHOLD_PROSPERITY_MIN,
            HOUSEHOLD_PROSPERITY_MAX,
        )
        purseholder = household_purseholder_id(ctx, hkey)
        for hr in alive_members:
            hr.person = replace(
                hr.person,
                household_prosperity=round(new, 5),
                household_purseholder_person_id=purseholder,
            )
        if old >= 0.2 and new < 0.2:
            ctx._record_simulation_event(
                int(year),
                "household_prosperity_crisis",
                {
                    "year": int(year),
                    "household_member_ids": sorted(hkey),
                    "purseholder_person_id": purseholder,
                    "prosperity_before": round(old, 5),
                    "prosperity_after": round(new, 5),
                    "income": round(income, 5),
                    "expenses": round(expenses, 5),
                    "unemployed_adults": unemployed_adults,
                    "dependent_minors": dependents,
                },
            )


def simulation_economy_annual_tick(ctx: "SimulationContext", year: int) -> None:
    """After careers and household care: pool draws, wages, value-add, tax, leader spend."""
    prof = simulation_timing.active_for_year(year)
    tpc = time.perf_counter
    if prof:
        t0 = tpc()
    catalog = JobEconomicsCatalog.load(ctx.db_path)
    archetype_catalog = JobArchetypeCatalog.load(ctx.db_path)
    market_catalog = JobMarketCatalog.load(ctx.db_path)
    y = int(year)
    hist = ctx.get_historical_year(y)
    resource_facts = ctx.annual_resource_facts(y)
    care_indexes = ctx.annual_care_indexes(y)
    if prof:
        simulation_timing.accumulate("economy.setup", tpc() - t0)
        t0 = tpc()

    # --- Pull pools toward baselines supported by settlement / region pressure ---
    next_settlements: dict[str, SettlementState] = {}
    try:
        from library.simulation_innovation import innovation_wealth_bonus_for_settlement
    except Exception:
        innovation_wealth_bonus_for_settlement = None
    for sid, st in ctx.settlements_by_id.items():
        if (st.status or "").strip().lower() != "active":
            next_settlements[sid] = st
            continue
        fp = float(st.food_pressure or 0.0)
        mp = float(st.market_pull or 0.0)
        innovation_bonus = (
            innovation_wealth_bonus_for_settlement(ctx, sid)
            if innovation_wealth_bonus_for_settlement is not None
            else 0.0
        )
        settle_target = _clamp(
            (1.0 - min(1.0, fp)) * 1.05 + mp * 0.38 + innovation_bonus,
            PROSPERITY_POOL_MIN + 0.02,
            PROSPERITY_POOL_MAX,
        )
        pp = float(getattr(st, "prosperity_pool", 1.0) or 0.0)
        pp = _lerp(pp, settle_target, POOL_TOWARD_BASELINE)
        pp = _clamp(pp, PROSPERITY_POOL_MIN, PROSPERITY_POOL_MAX)
        next_settlements[sid] = replace(st, prosperity_pool=pp)
    ctx.settlements_by_id = next_settlements

    rid_all: set[str] = set()
    for st in ctx.settlements_by_id.values():
        rid = (st.region_id or "").strip()
        if rid:
            rid_all.add(rid)
    if prof:
        simulation_timing.accumulate("economy.settlement_pools", tpc() - t0)
        t0 = tpc()

    for rid in rid_all:
        cap = max(1, int(resource_facts.region_cap.get(rid, 0) or ctx.effective_regional_population_cap(rid)))
        pop = int(resource_facts.region_population.get(rid, 0))
        rp = float(pop) / float(cap)
        region_target = _clamp(
            1.0 - min(1.0, rp) / 2.0 + 0.18,
            REGION_POOL_MIN + 0.02,
            REGION_POOL_MAX,
        )
        cur = float(ctx.region_prosperity_pool.get(rid, region_target))
        cur = _lerp(cur, region_target, REGION_POOL_TOWARD_BASELINE)
        ctx.region_prosperity_pool[rid] = _clamp(cur, REGION_POOL_MIN, REGION_POOL_MAX)
    if prof:
        simulation_timing.accumulate("economy.region_pools", tpc() - t0)
        t0 = tpc()

    # --- Unemployed / no job: baseline wage prosperity for conception hooks ---
    for rec in ctx.iter_current_people(sorted_by_id=True):
        es = (rec.person.employment_status or "").strip().lower()
        job = (rec.person.job or "").strip()
        if es != "employed" or not job:
            rec.person = replace(rec.person, job_prosperity_01=0.08)
            continue
        market_type = (rec.person.job_market_type or "settlement_market").strip().lower()
        if market_type in {"household_care", "domestic_service", "vice", "criminal"}:
            archetype = archetype_catalog.lookup(job)
            cash_score = float(archetype.personal_prosperity_01) * max(
                0.0, float(archetype.cash_wage_multiplier)
            )
            if market_type == "household_care":
                cash_score = min(cash_score, 0.04)
            if market_type == "domestic_service":
                cash_score += float(archetype.board_compensation_01) * 0.06
            rec.person = replace(
                rec.person,
                job_prosperity_01=round(_clamp01(cash_score), 5),
            )
    if prof:
        simulation_timing.accumulate("economy.unemployed_baseline", tpc() - t0)
        t0 = tpc()

    # Group employed workers by settlement
    by_sid: dict[str, list[SimulationPersonRecord]] = {}
    for rec in ctx.iter_current_people(sorted_by_id=True):
        if (rec.person.employment_status or "").strip().lower() != "employed":
            continue
        if not (rec.person.job or "").strip():
            continue
        market_type = (rec.person.job_market_type or "settlement_market").strip().lower()
        if market_type not in {"settlement_market", "office"}:
            continue
        sid = (
            (rec.person.current_settlement_id or rec.person.birthplace_settlement_id or "")
            .strip()
        )
        if not sid or sid not in ctx.settlements_by_id:
            continue
        st = ctx.settlements_by_id[sid]
        if (st.status or "").strip().lower() != "active":
            continue
        by_sid.setdefault(sid, []).append(rec)
    if prof:
        simulation_timing.accumulate("economy.group_workers", tpc() - t0)
        t0 = tpc()

    for sid, workers in by_sid.items():
        st = ctx.settlements_by_id[sid]
        rid = (st.region_id or "").strip()
        era_default = resolve_job_era(hist)
        weights: list[tuple["SimulationPersonRecord", float, JobEconomicsParams, JobMarketParams]] = []
        for rec in workers:
            je = catalog.lookup(
                rec.person.job,
                rec.person.job_era or era_default,
                tier=_job_tier_for_person(rec),
            )
            jm = market_catalog.lookup(rec.person.job)
            scarcity = _clamp(float(st.food_pressure or 0.0), 0.0, 2.0) / 2.0
            essential_boost = 1.0 + 0.30 * float(jm.essential_need) * float(jm.scarcity_resilience) * scarcity
            w = max(0.0, float(je.pool_draw) * essential_boost)
            weights.append((rec, w, je, jm))
        total_w = sum(t[1] for t in weights)
        pool_here = max(0.0, float(getattr(st, "prosperity_pool", 0.0) or 0.0))
        region_slack = float(ctx.region_prosperity_pool.get(rid, 1.0) or 0.0) / max(
            REGION_POOL_MAX, 1e-6
        )

        draw_budget = pool_here * (0.55 + 0.45 * _clamp(region_slack, 0.0, 1.0))
        total_draw = 0.0
        value_back = 0.0
        tax_total = 0.0

        family_effects = {
            "food": 0.0,
            "stability": 0.0,
            "care": 0.0,
            "capacity": 0.0,
        }
        for rec, w_i, je, jm in weights:
            share = (w_i / total_w) if total_w > 0 else 0.0
            draw_i = draw_budget * share
            total_draw += draw_i
            value_back += VALUE_ADD_ON_DRAW_SCALE * float(je.value_add) * (draw_i + 0.02)
            wage_unit = float(je.wage_yield) * WAGE_BASE_SCALE
            pressure = resource_pressure_for_person(ctx, rec, resource_facts=resource_facts)
            press_rel = _clamp(1.0 - 0.42 * pressure, 0.25, 1.0)
            market_rel = 0.72 + 0.28 * float(jm.taxability)
            wage_score = _clamp01(
                wage_unit
                * press_rel
                * market_rel
                * (0.38 + 0.62 * _clamp(draw_i * 2.5 / max(draw_budget, 1e-6), 0.0, 1.0))
                * (0.55 + 0.45 * region_slack)
            )
            rec.person = replace(rec.person, job_prosperity_01=round(wage_score, 5))
            tax_total += float(je.tax_rate) * float(jm.taxability) * wage_unit * TREASURY_INCOME_SCALE
            family_effects["food"] += float(jm.food_delta) * max(0.0, wage_score) * 0.010
            family_effects["stability"] += float(jm.stability_delta) * max(0.0, wage_score) * 0.006
            family_effects["care"] += float(jm.care_delta) * max(0.0, wage_score) * 0.004
            family_effects["capacity"] += float(jm.capacity_delta) * max(0.0, wage_score) * 0.006

        new_pool = _clamp(
            pool_here - total_draw + value_back,
            PROSPERITY_POOL_MIN,
            PROSPERITY_POOL_MAX,
        )
        food_pressure = _clamp(
            float(st.food_pressure or 0.0) - min(0.08, family_effects["food"]),
            0.0,
            2.0,
        )
        stability = _clamp(
            float(st.stability or 0.0) + _clamp(family_effects["stability"], -0.05, 0.05),
            0.0,
            1.0,
        )
        ctx.settlements_by_id[sid] = replace(
            st,
            prosperity_pool=new_pool,
            food_pressure=food_pressure,
            stability=stability,
        )
        if (
            abs(food_pressure - float(st.food_pressure or 0.0)) >= 0.001
            or abs(stability - float(st.stability or 0.0)) >= 0.001
            or family_effects["care"] >= 0.004
            or family_effects["capacity"] >= 0.004
        ):
            ctx._record_simulation_event(
                y,
                "settlement_job_market_effect",
                {
                    "settlement_id": sid,
                    "region_id": rid,
                    "food_pressure_delta": round(food_pressure - float(st.food_pressure or 0.0), 5),
                    "stability_delta": round(stability - float(st.stability or 0.0), 5),
                    "care_support": round(family_effects["care"], 5),
                    "capacity_support": round(family_effects["capacity"], 5),
                    "worker_count": len(workers),
                },
            )

        if rid and tax_total > 0.0:
            ctx.region_treasury_balance[rid] = float(
                ctx.region_treasury_balance.get(rid, 0.0)
            ) + float(tax_total)

        # Regional pool slowly tracks settlement activity (value-add spill)
        if rid:
            spill = min(0.06, value_back * 0.08)
            ctx.region_prosperity_pool[rid] = _clamp(
                float(ctx.region_prosperity_pool.get(rid, 1.0)) + spill,
                REGION_POOL_MIN,
                REGION_POOL_MAX,
            )
    if prof:
        simulation_timing.accumulate("economy.worker_markets", tpc() - t0)
        t0 = tpc()

    _update_household_prosperity(ctx, y, care_indexes=care_indexes)
    if prof:
        simulation_timing.accumulate("economy.household_prosperity", tpc() - t0)
        t0 = tpc()

    # --- Leader spending from treasury ---
    treasury_holder_ids = _government_treasury_holder_ids(ctx)
    for rid in rid_all:
        treasury = float(ctx.region_treasury_balance.get(rid, 0.0))
        if treasury < 0.35:
            continue
        leaders = [
            rec
            for rec in ctx.decision_sample_people_in_region(
                rid,
                year=y,
                stream=30_001,
            )
            if _leader_candidate(
                ctx,
                rec,
                year=y,
                care_indexes=care_indexes,
                treasury_holder_ids=treasury_holder_ids,
            )
        ]
        if not leaders:
            continue
        rng = random.Random(
            y * 1_009_003
            + hash(rid) % 1_000_003
            + int(ctx.placename_rng_salt)
            + 77_777
        )
        if rng.random() > LEADER_SPEND_PROB:
            continue
        spend = min(
            treasury * LEADER_SPEND_FRAC_OF_TREASURY,
            LEADER_SPEND_CAP,
            treasury * 0.5,
        )
        if spend <= 0.0:
            continue
        ctx.region_treasury_balance[rid] = treasury - spend

        act = ctx.active_settlements_in_region(rid)
        if not act:
            continue
        pick = act[int(rng.random() * len(act))]
        bump_s = min(STABILITY_BUMP_CAP, spend * 0.35)
        bump_p = min(SETTLEMENT_POOL_BUMP_CAP, spend * 0.5)
        ctx.settlements_by_id[pick.settlement_id] = replace(
            pick,
            stability=_clamp(float(pick.stability) + bump_s, 0.0, 1.0),
            prosperity_pool=_clamp(
                float(getattr(pick, "prosperity_pool", 1.0)) + bump_p,
                PROSPERITY_POOL_MIN,
                PROSPERITY_POOL_MAX,
            ),
        )
        ctx._record_simulation_event(
            y,
            "treasury_leader_spend",
            {
                "region_id": rid,
                "settlement_id": pick.settlement_id,
                "spend": round(spend, 5),
                "stability_bump": round(bump_s, 5),
                "prosperity_pool_bump": round(bump_p, 5),
                "leader_count": len(leaders),
            },
        )
    if prof:
        simulation_timing.accumulate("economy.leader_spend", tpc() - t0)


def _clamp01(x: float) -> float:
    return _clamp(x, 0.0, 1.0)
