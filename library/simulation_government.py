"""Polities, office seats, succession, and annual government tick."""

from __future__ import annotations

import random
import sqlite3
import time
from heapq import nlargest
from dataclasses import replace
from typing import TYPE_CHECKING

from library import simulation_timing
from library import government_checkpoint as gov_ckpt
from library.event_scoring import clamp01, composite_score
from library.geography import population_scale_for_world
from library.government_catalog import (
    GovernmentCatalog,
    TitleRow,
    display_title_name,
    effective_max_population_before_split,
    effective_min_population_to_form,
    load_government_catalog,
    load_genome_composite_rows,
    pick_polity_type_for_region_population,
    pick_primary_polity_type,
    resolve_government_era,
)
from library.leadership import (
    government_candidate_composite_rows,
    leadership_and_military_indexes,
)
from library.place_namer import (
    REGION_RENAME_DOMINANT_CITY_HYSTERESIS_YEARS,
    REGION_RENAME_DOMINANT_CITY_RATIO,
    is_placeholder_polity_name,
    naming_threshold_for_world,
    placeholder_polity_name,
    placeholder_region_label,
    polity_geographic_label,
    region_geographic_label,
    region_label_after_dominant_city,
)
from library.passive_population import (
    build_passive_office_candidate_index,
    promote_passive_candidate_for_office,
)
from library.work_body_fit import (
    authority_force_fit_from_body_power,
    body_power_01,
    magic_physical_leveling_for_world,
)
from library.polity import (
    AllianceState,
    DynastyState,
    OfficeSeatState,
    PolityState,
    TerritoryOpenRow,
    polity_for_region,
    polity_for_settlement,
    polity_regions,
    polity_settlement_territory_ids,
    polities_in_region,
    primogeniture_candidates,
    realm_resident_decision_sample,
)
from library.world_save import _open_save

if TYPE_CHECKING:
    from library.simulation_context import SimulationContext


def _cfs_safe(p) -> float:
    v = p.career_fitness_score
    return float(v) if v is not None else 0.5


# Caregiver-duty penalty applied to merit / election scores and to the head-pick
# leadership score. See ``library.simulation_household_care.childcare_duty_factor``.
GOV_CHILD_DUTY_MERIT_WEIGHT = 0.55
GOV_CHILD_DUTY_HEAD_WEIGHT = 0.45
SETTLEMENT_MERIT_MAX_SEATS_PER_TITLE = 12


def _government_office_composite_multiplier(rec, title: TitleRow | None) -> float:
    force_authority = float(getattr(title, "force_authority_01", 0.0) or 0.0)
    insanity = composite_score(rec, "insanity")
    insanity_pressure = clamp01((insanity - 0.25) / 0.55)
    trust_penalty = (
        composite_score(rec, "psychopathy") * 0.18
        + composite_score(rec, "evil_done_desire") * 0.12
        + composite_score(rec, "ruthless_ambition") * 0.08
    )
    force_relief = clamp01(force_authority / 0.85) * 0.35
    penalty = 1.0 - insanity_pressure * 0.75 - trust_penalty * (1.0 - force_relief)
    boost = (
        composite_score(rec, "lead_others_ability") * 0.20
        + composite_score(rec, "practical_intellect") * 0.12
        + composite_score(rec, "honest_work_desire") * 0.08
        + composite_score(rec, "good_done_desire") * 0.06
    )
    return max(0.20, penalty) * (1.0 + min(0.35, boost))


def _gov_profile_count(
    ctx: "SimulationContext", year: int, metric: str, value: int | float = 1
) -> None:
    if not simulation_timing.active_for_year(year):
        return
    counts = getattr(ctx, "_gov_profile_counts", None)
    if counts is None:
        counts = {}
        setattr(ctx, "_gov_profile_counts", counts)
    counts[str(metric)] = float(counts.get(str(metric), 0.0)) + float(value)


def _childcare_duty_factor_safe(
    ctx: "SimulationContext", rec, year: int
) -> float:
    """Look up caregiver duty factor without taking a hard dep at module import."""
    y = int(year)
    indexes = getattr(ctx, "_gov_care_indexes", None)
    if indexes is None:
        indexes = ctx.annual_care_indexes(y)
    duty_by_adult = getattr(indexes, "childcare_duty_factor_by_adult", {})
    return float(duty_by_adult.get(int(rec.person_id), 0.0))


def _government_job_era_key(ctx: "SimulationContext", year: int) -> str:
    try:
        historical_year = ctx.get_historical_year(int(year))
    except Exception:
        historical_year = int(year)
    from library.simulation_careers import resolve_job_era

    return resolve_job_era(historical_year)


def _government_magic_leveling(ctx: "SimulationContext") -> float:
    return magic_physical_leveling_for_world(
        getattr(ctx, "world", "default"),
        getattr(ctx, "db_path", None),
    )


def _government_candidate_facts(
    ctx: "SimulationContext",
    rec,
    *,
    composite_rows: tuple,
    year: int,
) -> tuple[float, float, float, int, int, float, float]:
    """Per-year reusable office-candidate facts: leadership, military, cfs, age, male, duty, body."""
    y = int(year)
    pid = int(rec.person_id)
    cache = getattr(ctx, "_gov_candidate_fact_cache", None)
    if cache is None:
        cache = {}
        setattr(ctx, "_gov_candidate_fact_cache", cache)
    key = (y, pid)
    cached = cache.get(key)
    if cached is not None:
        _gov_profile_count(ctx, y, "candidate_fact_cache_hits")
        return cached
    _gov_profile_count(ctx, y, "candidate_fact_cache_misses")
    prof = simulation_timing.active_for_year(y)
    tpc = time.perf_counter
    if prof:
        t0 = tpc()
    li, mi = leadership_and_military_indexes(
        rec.person, composite_rows=composite_rows
    )
    cf = _cfs_safe(rec.person)
    age = y - int(rec.person.birthyear)
    male = 1 if (rec.person.gender or "").strip() == "Male" else 0
    if prof:
        simulation_timing.accumulate("government.candidate_facts.composites", tpc() - t0)
        t0 = tpc()
    duty = _childcare_duty_factor_safe(ctx, rec, y)
    if prof:
        simulation_timing.accumulate("government.candidate_facts.duty", tpc() - t0)
    body_power = body_power_01(rec.person)
    facts = (li, mi, cf, age, male, duty, body_power)
    cache[key] = facts
    return facts


def _government_scored_candidate_pool(
    ctx: "SimulationContext",
    records,
    *,
    title: TitleRow,
    composite_rows: tuple,
    year: int,
) -> tuple[tuple[float, int], ...]:
    """Score title-eligible candidates once per year/title/sample."""
    y = int(year)
    record_ids = tuple(int(rec.person_id) for rec in records if rec is not None)
    force_authority = float(getattr(title, "force_authority_01", 0.0) or 0.0)
    era_key = _government_job_era_key(ctx, y)
    magic_leveling = _government_magic_leveling(ctx)
    cache = getattr(ctx, "_gov_scored_candidate_cache", None)
    if cache is None:
        cache = {}
        setattr(ctx, "_gov_scored_candidate_cache", cache)
    key = (
        y,
        str(title.title_id),
        round(force_authority, 4),
        era_key,
        round(magic_leveling, 4),
        record_ids,
    )
    cached = cache.get(key)
    if cached is not None:
        _gov_profile_count(ctx, y, "scored_pool_cache_hits")
        return cached
    _gov_profile_count(ctx, y, "scored_pool_cache_misses")
    _gov_profile_count(ctx, y, "scored_pool_records", len(record_ids))
    rule = (title.selection_rule or "").lower()
    min_age = int(title.min_age)
    min_li = float(title.min_leadership_index)
    min_mi = float(title.min_military_quality_index)
    min_cf = float(title.min_career_fitness)
    male_weight = float(title.male_weight)
    scored: list[tuple[float, int]] = []
    prof = simulation_timing.active_for_year(y)
    tpc = time.perf_counter
    if prof:
        t0 = tpc()
    for rec in records:
        if rec is None or rec.person.deathyear is not None:
            continue
        age = y - int(rec.person.birthyear)
        if age < min_age:
            _gov_profile_count(ctx, y, "scored_pool_age_skips")
            continue
        cf = _cfs_safe(rec.person)
        if cf < min_cf:
            _gov_profile_count(ctx, y, "scored_pool_cfs_skips")
            continue
        li, mi, cf, age, male_code, duty, body_power = _government_candidate_facts(
            ctx, rec, composite_rows=composite_rows, year=y
        )
        if age < min_age or li < min_li or mi < min_mi or cf < min_cf:
            continue
        gender_weight = male_weight if male_code else (1.0 - male_weight)
        if "election" in rule or "merit" in rule:
            base = li * 0.55 + cf * 0.35 + mi * 0.10
        elif "appointment" in rule:
            base = cf * 0.55 + li * 0.35
        else:
            base = li
        duty_mult = max(0.0, 1.0 - GOV_CHILD_DUTY_MERIT_WEIGHT * duty)
        force_fit = authority_force_fit_from_body_power(
            body_power=body_power,
            force_authority_01=force_authority,
            era=era_key,
            magic_leveling_01=magic_leveling,
        )
        score = (
            base
            * gender_weight
            * duty_mult
            * force_fit.physical_demand_multiplier
            * _government_office_composite_multiplier(rec, title)
        )
        scored.append((score, int(rec.person_id)))
    if prof:
        simulation_timing.accumulate("government.scored_pool.loop", tpc() - t0)
    _gov_profile_count(ctx, y, "scored_pool_candidates", len(scored))
    cached = tuple(scored)
    cache[key] = cached
    return cached


def init_government_state(ctx: "SimulationContext") -> None:
    if not hasattr(ctx, "gov_polities") or ctx.gov_polities is None:
        ctx.gov_polities = {}
    if not hasattr(ctx, "gov_territory_rows") or ctx.gov_territory_rows is None:
        ctx.gov_territory_rows = []
    if not hasattr(ctx, "gov_office_seats") or ctx.gov_office_seats is None:
        ctx.gov_office_seats = {}
    if not hasattr(ctx, "gov_dynasties") or ctx.gov_dynasties is None:
        ctx.gov_dynasties = {}
    if not hasattr(ctx, "gov_alliances") or ctx.gov_alliances is None:
        ctx.gov_alliances = []
    if not hasattr(ctx, "gov_campaigns") or ctx.gov_campaigns is None:
        ctx.gov_campaigns = []
    ctx.next_gov_polity_id = int(getattr(ctx, "next_gov_polity_id", 1) or 1)
    ctx.next_gov_seat_id = int(getattr(ctx, "next_gov_seat_id", 1) or 1)
    ctx.next_gov_dynasty_id = int(getattr(ctx, "next_gov_dynasty_id", 1) or 1)
    ctx.next_gov_campaign_id = int(getattr(ctx, "next_gov_campaign_id", 1) or 1)
    ctx.next_gov_alliance_id = int(getattr(ctx, "next_gov_alliance_id", 1) or 1)


def vacate_seat(
    ctx: "SimulationContext",
    seat: OfficeSeatState,
    year: int,
    *,
    end_reason: str,
    conn: sqlite3.Connection | None = None,
) -> None:
    old = seat.holder_person_id
    if old is None:
        return
    w = ctx.world.strip()
    if conn is None:
        with _open_save(ctx.save_db_path) as own_conn:
            gov_ckpt.ensure_government_schema(own_conn)
            gov_ckpt.close_office_holding(
                own_conn,
                world=w,
                seat_id=seat.seat_id,
                holder_person_id=old,
                end_sim_year=year,
                end_reason=end_reason,
            )
            own_conn.commit()
    else:
        gov_ckpt.close_office_holding(
            conn,
            world=w,
            seat_id=seat.seat_id,
            holder_person_id=old,
            end_sim_year=year,
            end_reason=end_reason,
            ensure_schema=False,
        )
    ctx.gov_office_seats[seat.seat_id] = replace(
        seat,
        holder_person_id=None,
        vacant_since_sim_year=year,
        term_expires_sim_year=None,
    )


def assign_holder(
    ctx: "SimulationContext",
    seat: OfficeSeatState,
    person_id: int,
    year: int,
    *,
    display_job: str,
    term_expires: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    w = ctx.world.strip()
    if conn is None:
        with _open_save(ctx.save_db_path) as own_conn:
            gov_ckpt.ensure_government_schema(own_conn)
            gov_ckpt.append_office_holding(
                own_conn,
                world=w,
                seat_id=seat.seat_id,
                holder_person_id=person_id,
                start_sim_year=year,
            )
            own_conn.commit()
    else:
        gov_ckpt.append_office_holding(
            conn,
            world=w,
            seat_id=seat.seat_id,
            holder_person_id=person_id,
            start_sim_year=year,
            ensure_schema=False,
        )
    ctx.gov_office_seats[seat.seat_id] = replace(
        seat,
        holder_person_id=person_id,
        vacant_since_sim_year=None,
        term_expires_sim_year=term_expires,
    )
    rec = ctx.id_to_record.get(person_id)
    if rec is not None:
        try:
            from library.job_archetypes import JobArchetypeCatalog

            archetype = JobArchetypeCatalog.load(ctx.db_path).lookup(display_job)
            social_standing = min(
                1.0,
                max(
                    0.0,
                    0.55 * float(archetype.public_prestige_01)
                    + 0.45 * float(archetype.perceived_worth_01),
                ),
            )
        except Exception:
            archetype = None
            social_standing = 0.72
        class_band = getattr(archetype, "class_band", None) if archetype is not None else "upper"
        existing_standing = float(getattr(rec.person, "social_standing_01", 0.0) or 0.0)
        existing_impact = float(getattr(rec.person, "societal_impact_01", 0.0) or 0.0)
        existing_worth = float(getattr(rec.person, "perceived_worth_01", 0.0) or 0.0)
        rec.person = replace(
            rec.person,
            social_class_band=class_band or rec.person.social_class_band,
            social_standing_01=round(max(existing_standing, float(social_standing)), 4),
            societal_impact_01=round(
                max(existing_impact, float(getattr(archetype, "societal_impact_01", 0.72))),
                4,
            ),
            perceived_worth_01=round(
                max(existing_worth, float(getattr(archetype, "perceived_worth_01", 0.74))),
                4,
            ),
        )


def _region_display(ctx: "SimulationContext", region_id: str) -> str:
    from library.geography import get_region

    rid = (region_id or "").strip()
    if not rid:
        return region_id
    ovr = (getattr(ctx, "region_display_label_overrides", None) or {}).get(rid)
    if ovr and str(ovr).strip():
        return str(ovr).strip()
    try:
        thr = naming_threshold_for_world(ctx.world, ctx.db_path)
    except Exception:
        thr = 1
    if _mixed_alive_in_region(ctx, rid) < thr:
        return placeholder_region_label(rid)
    try:
        r = get_region(rid, world=ctx.world, db_path=ctx.db_path)
        return (r.region_name or region_id).strip() or region_id
    except Exception:
        return region_id


def _pick_head_candidate_in_region(
    ctx: "SimulationContext",
    *,
    region_id: str,
    head_title: TitleRow | None,
    composite_rows: tuple,
    year: int | None = None,
) -> int | None:
    """Pick the highest-leadership living resident of ``region_id`` for a head seat."""
    rid = (region_id or "").strip()
    if not rid:
        return None
    eff_year = int(year) if year is not None else int(ctx.current_year or 0)
    residents = ctx.decision_sample_people_in_region(
        rid,
        year=eff_year,
        stream=20_001,
    )
    if not residents:
        promoted = promote_passive_candidate_for_office(
            ctx,
            year=eff_year,
            region_id=rid,
            min_age=int(head_title.min_age) if head_title is not None else 16,
            reason="office_selection_head_region",
            source={"region_id": rid},
        )
        return promoted.person_id if promoted is not None else None
    already_holding = _holder_ids_currently_in_office(ctx)
    available = [r for r in residents if int(r.person_id) not in already_holding]
    if available:
        residents = available
    pref = float(head_title.male_weight) if head_title is not None else 0.5
    force_authority = float(getattr(head_title, "force_authority_01", 0.0) or 0.0)
    era_key = _government_job_era_key(ctx, eff_year)
    magic_leveling = _government_magic_leveling(ctx)

    def head_score(rec) -> tuple[float, float, int]:
        li, _mi, cf, _age, male, duty, body_power = _government_candidate_facts(
            ctx, rec, composite_rows=composite_rows, year=eff_year
        )
        boost = pref * male + (1.0 - pref) * (1 - male)
        duty_mult = max(0.0, 1.0 - GOV_CHILD_DUTY_HEAD_WEIGHT * duty)
        force_fit = authority_force_fit_from_body_power(
            body_power=body_power,
            force_authority_01=force_authority,
            era=era_key,
            magic_leveling_01=magic_leveling,
        )
        score = (
            li
            * boost
            * duty_mult
            * force_fit.physical_demand_multiplier
            * _government_office_composite_multiplier(rec, head_title)
        )
        return (score, cf, -rec.person_id)

    residents.sort(key=head_score, reverse=True)
    return residents[0].person_id


def _pick_head_candidate_in_settlement(
    ctx: "SimulationContext",
    *,
    settlement_id: str,
    head_title: TitleRow | None,
    composite_rows: tuple,
    year: int | None = None,
) -> int | None:
    """Pick the highest-leadership living resident of ``settlement_id`` for a head seat."""
    sid = (settlement_id or "").strip()
    if not sid:
        return None
    eff_year = int(year) if year is not None else int(ctx.current_year or 0)
    residents = ctx.decision_sample_people_in_settlement(
        sid,
        year=eff_year,
        stream=20_002,
    )
    if not residents:
        promoted = promote_passive_candidate_for_office(
            ctx,
            year=eff_year,
            settlement_id=sid,
            min_age=int(head_title.min_age) if head_title is not None else 16,
            reason="office_selection_head_settlement",
            source={"settlement_id": sid},
        )
        return promoted.person_id if promoted is not None else None
    already_holding = _holder_ids_currently_in_office(ctx)
    available = [r for r in residents if int(r.person_id) not in already_holding]
    if available:
        residents = available
    pref = float(head_title.male_weight) if head_title is not None else 0.5
    force_authority = float(getattr(head_title, "force_authority_01", 0.0) or 0.0)
    era_key = _government_job_era_key(ctx, eff_year)
    magic_leveling = _government_magic_leveling(ctx)

    def head_score(rec) -> tuple[float, float, int]:
        li, _mi, cf, _age, male, duty, body_power = _government_candidate_facts(
            ctx, rec, composite_rows=composite_rows, year=eff_year
        )
        boost = pref * male + (1.0 - pref) * (1 - male)
        duty_mult = max(0.0, 1.0 - GOV_CHILD_DUTY_HEAD_WEIGHT * duty)
        force_fit = authority_force_fit_from_body_power(
            body_power=body_power,
            force_authority_01=force_authority,
            era=era_key,
            magic_leveling_01=magic_leveling,
        )
        score = (
            li
            * boost
            * duty_mult
            * force_fit.physical_demand_multiplier
            * _government_office_composite_multiplier(rec, head_title)
        )
        return (score, cf, -rec.person_id)

    residents.sort(key=head_score, reverse=True)
    return residents[0].person_id


def _vassalize_existing_settlement_polities(
    ctx: "SimulationContext",
    year: int,
    *,
    region_id: str,
    suzerain_polity_id: int,
    catalog: GovernmentCatalog,
) -> None:
    """Attach independent settlement-grain polities in ``region_id`` under a new liege."""
    rid = (region_id or "").strip()
    if not rid:
        return
    for pol in polities_in_region(ctx, rid):
        if int(pol.polity_id) == int(suzerain_polity_id):
            continue
        if pol.parent_polity_id is not None:
            continue
        if polity_regions(ctx, pol.polity_id):
            continue
        sids = polity_settlement_territory_ids(ctx, pol.polity_id)
        if not sids:
            continue
        pti = catalog.polity_type_by_id(pol.polity_type_id)
        if pti is None or not pti.can_be_vassalized:
            continue
        ctx.gov_polities[pol.polity_id] = replace(
            pol, parent_polity_id=int(suzerain_polity_id)
        )


def _bootstrap_region_polity(
    ctx: "SimulationContext",
    year: int,
    *,
    region_id: str,
    catalog: GovernmentCatalog,
    era,
    ptype,
    composite_rows: tuple,
    rng: random.Random,
) -> None:
    rid = (region_id or "").strip()
    if not rid or polity_for_region(ctx, rid) is not None:
        return
    if _mixed_alive_in_region(ctx, rid) <= 0:
        return
    if (ptype.jurisdiction_grain or "region").strip().lower() != "region":
        return

    capital = ctx.ensure_active_settlement_for_region(rid)
    pid = ctx.next_gov_polity_id
    ctx.next_gov_polity_id += 1
    name = _polity_display_name(ptype.polity_type_id, _region_display(ctx, rid))
    pol = PolityState(
        polity_id=pid,
        polity_type_id=ptype.polity_type_id,
        parent_polity_id=None,
        name=name,
        capital_settlement_id=capital.settlement_id,
        founding_dynasty_id=None,
        founded_sim_year=year,
        status="active",
    )
    ctx.gov_polities[pid] = pol
    ctx.gov_territory_rows.append(
        TerritoryOpenRow(
            polity_id=pid,
            target_kind="region",
            target_id=rid,
            since_sim_year=year,
        )
    )

    titles = catalog.titles_for_polity_type(ptype.polity_type_id)
    for t in titles:
        role_lower = (t.role or "").strip().lower()
        if role_lower == "settlement_merit":
            continue
        for slot in range(int(t.max_holders)):
            scope = None
            if role_lower == "local_merit":
                scope = capital.settlement_id
            sid = ctx.next_gov_seat_id
            ctx.next_gov_seat_id += 1
            ctx.gov_office_seats[sid] = OfficeSeatState(
                seat_id=sid,
                polity_id=pid,
                title_id=t.title_id,
                slot_index=slot,
                scope_settlement_id=scope,
            )

    era_key = __import__(
        "library.simulation_careers", fromlist=["resolve_job_era"]
    ).resolve_job_era(ctx.get_historical_year(year))

    head_title = catalog.title_by_id(ptype.head_title_id)
    head_seat = next(
        (
            s
            for s in ctx.gov_office_seats.values()
            if s.polity_id == pid and head_title and s.title_id == head_title.title_id
        ),
        None,
    )
    if head_title is None or head_seat is None:
        head_seat = next((s for s in ctx.gov_office_seats.values() if s.polity_id == pid), None)
    if head_seat is None:
        return

    ruler = _pick_head_candidate_in_region(
        ctx,
        region_id=rid,
        head_title=head_title,
        composite_rows=composite_rows,
        year=year,
    )
    if ruler is None:
        return
    dyn_id = ctx.next_gov_dynasty_id
    ctx.next_gov_dynasty_id += 1
    rrec = ctx.id_to_record[ruler]
    house = (rrec.person.last_name or "House").strip() or "House"
    ctx.gov_dynasties[dyn_id] = DynastyState(
        dynasty_id=dyn_id,
        founder_person_id=ruler,
        house_name=house,
        founded_sim_year=year,
        line="agnatic",
    )
    ctx.gov_polities[pid] = replace(pol, founding_dynasty_id=dyn_id)
    disp = display_title_name(head_title, era_key) if head_title else head_seat.title_id
    assign_holder(ctx, head_seat, ruler, year, display_job=disp)

    for s in ctx.gov_office_seats.values():
        if s.polity_id != pid or s.seat_id == head_seat.seat_id or s.holder_person_id:
            continue
        t = catalog.title_by_id(s.title_id)
        if t is None or (t.role or "").strip().lower() == "head":
            continue
        _fill_merit_or_election(ctx, year, s, t, catalog, composite_rows, era_key, rng)

    _vassalize_existing_settlement_polities(
        ctx, year, region_id=rid, suzerain_polity_id=pid, catalog=catalog
    )


def _bootstrap_settlement_polity(
    ctx: "SimulationContext",
    year: int,
    *,
    settlement_id: str,
    region_id: str,
    catalog: GovernmentCatalog,
    era,
    ptype,
    composite_rows: tuple,
    rng: random.Random,
    population_scale: float,
) -> None:
    sid = (settlement_id or "").strip()
    rid = (region_id or "").strip()
    if not sid or not rid or polity_for_settlement(ctx, sid) is not None:
        return
    st = ctx.settlements_by_id.get(sid)
    if st is None or (st.status or "").strip().lower() != "active":
        return
    if _mixed_alive_in_settlement(ctx, sid) < effective_min_population_to_form(
        ptype, population_scale
    ):
        return
    if (ptype.jurisdiction_grain or "settlement").strip().lower() != "settlement":
        return

    pid = ctx.next_gov_polity_id
    ctx.next_gov_polity_id += 1
    name = placeholder_polity_name(ptype.polity_type_id, region_id=rid, settlement_id=sid)
    pol = PolityState(
        polity_id=pid,
        polity_type_id=ptype.polity_type_id,
        parent_polity_id=None,
        name=name,
        capital_settlement_id=sid,
        founding_dynasty_id=None,
        founded_sim_year=year,
        status="active",
        notes={"lazy_geographic_name": True},
    )
    ctx.gov_polities[pid] = pol
    ctx.gov_territory_rows.append(
        TerritoryOpenRow(
            polity_id=pid,
            target_kind="settlement",
            target_id=sid,
            since_sim_year=year,
        )
    )

    titles = catalog.titles_for_polity_type(ptype.polity_type_id)
    for t in titles:
        role_lower = (t.role or "").strip().lower()
        if role_lower == "settlement_merit":
            continue
        for slot in range(int(t.max_holders)):
            scope = None
            if role_lower == "local_merit":
                scope = sid
            seat_id = ctx.next_gov_seat_id
            ctx.next_gov_seat_id += 1
            ctx.gov_office_seats[seat_id] = OfficeSeatState(
                seat_id=seat_id,
                polity_id=pid,
                title_id=t.title_id,
                slot_index=slot,
                scope_settlement_id=scope,
            )

    era_key = __import__(
        "library.simulation_careers", fromlist=["resolve_job_era"]
    ).resolve_job_era(ctx.get_historical_year(year))

    head_title = catalog.title_by_id(ptype.head_title_id)
    head_seat = next(
        (
            s
            for s in ctx.gov_office_seats.values()
            if s.polity_id == pid and head_title and s.title_id == head_title.title_id
        ),
        None,
    )
    if head_title is None or head_seat is None:
        head_seat = next((s for s in ctx.gov_office_seats.values() if s.polity_id == pid), None)
    if head_seat is None:
        return

    ruler = _pick_head_candidate_in_settlement(
        ctx,
        settlement_id=sid,
        head_title=head_title,
        composite_rows=composite_rows,
        year=year,
    )
    if ruler is None:
        return
    dyn_id = ctx.next_gov_dynasty_id
    ctx.next_gov_dynasty_id += 1
    rrec = ctx.id_to_record[ruler]
    house = (rrec.person.last_name or "House").strip() or "House"
    ctx.gov_dynasties[dyn_id] = DynastyState(
        dynasty_id=dyn_id,
        founder_person_id=ruler,
        house_name=house,
        founded_sim_year=year,
        line="agnatic",
    )
    ctx.gov_polities[pid] = replace(pol, founding_dynasty_id=dyn_id)
    disp = display_title_name(head_title, era_key) if head_title else head_seat.title_id
    assign_holder(ctx, head_seat, ruler, year, display_job=disp)

    for s in ctx.gov_office_seats.values():
        if s.polity_id != pid or s.seat_id == head_seat.seat_id or s.holder_person_id:
            continue
        t = catalog.title_by_id(s.title_id)
        if t is None or (t.role or "").strip().lower() == "head":
            continue
        _fill_merit_or_election(ctx, year, s, t, catalog, composite_rows, era_key, rng)


def _bootstrap_polities_for_region(
    ctx: "SimulationContext",
    year: int,
    *,
    region_id: str,
    catalog: GovernmentCatalog,
    era,
    ptype,
    composite_rows: tuple,
    rng: random.Random,
    population_scale: float,
) -> None:
    rid = (region_id or "").strip()
    if not rid or _mixed_alive_in_region(ctx, rid) <= 0:
        return
    grain = (ptype.jurisdiction_grain or "region").strip().lower()
    if grain == "settlement":
        for sid in ctx.settlement_ids_by_region.get(rid, []):
            _bootstrap_settlement_polity(
                ctx,
                year,
                settlement_id=sid,
                region_id=rid,
                catalog=catalog,
                era=era,
                ptype=ptype,
                composite_rows=composite_rows,
                rng=rng,
                population_scale=population_scale,
            )
        return
    _bootstrap_region_polity(
        ctx,
        year,
        region_id=rid,
        catalog=catalog,
        era=era,
        ptype=ptype,
        composite_rows=composite_rows,
        rng=rng,
    )


def _holder_counts_currently_in_office(ctx: "SimulationContext") -> dict[int, int]:
    """Person ids that occupy one or more seats (merit, hereditary, or head)."""
    counts: dict[int, int] = {}
    for seat in ctx.gov_office_seats.values():
        if seat.holder_person_id is None:
            continue
        pid = int(seat.holder_person_id)
        counts[pid] = counts.get(pid, 0) + 1
    return counts


def _holder_ids_currently_in_office(ctx: "SimulationContext") -> set[int]:
    """Person ids that already occupy any seat (merit, hereditary, or head)."""
    return set(_holder_counts_currently_in_office(ctx))


def _fill_merit_or_election(
    ctx: "SimulationContext",
    year: int,
    seat: OfficeSeatState,
    title: TitleRow,
    catalog: GovernmentCatalog,
    composite_rows: tuple,
    era_key: str,
    rng: random.Random,
    conn: sqlite3.Connection | None = None,
    already_holding: set[int] | None = None,
    passive_office_index=None,
    allow_passive_promotion: bool = True,
) -> int | None:
    prof = simulation_timing.active_for_year(year)
    tpc = time.perf_counter
    if prof:
        t0 = tpc()
    if already_holding is None:
        already_holding = _holder_ids_currently_in_office(ctx)
    if prof:
        simulation_timing.accumulate("government.fill.holder_set", tpc() - t0)
        t0 = tpc()
    if seat.scope_settlement_id:
        records = ctx.decision_sample_people_in_settlement(
            seat.scope_settlement_id,
            year=year,
            stream=20_103 + int(seat.seat_id),
        )
    else:
        records = realm_resident_decision_sample(
            ctx,
            seat.polity_id,
            year=year,
            scope=f"seat:{int(seat.seat_id)}",
            stream=20_100,
        )
    if prof:
        simulation_timing.accumulate("government.fill.sample", tpc() - t0)
        t0 = tpc()
    if prof:
        score_start = t0
    scored_pool = _government_scored_candidate_pool(
        ctx,
        records,
        title=title,
        composite_rows=composite_rows,
        year=year,
    )
    if prof:
        simulation_timing.accumulate("government.fill.score_pool", tpc() - t0)
        t0 = tpc()
    top = nlargest(
        8,
        (
            (score, pid)
            for score, pid in scored_pool
            if int(pid) not in already_holding
        ),
    )
    if prof:
        simulation_timing.accumulate("government.fill.score_top", tpc() - t0)
        simulation_timing.accumulate("government.fill.score", tpc() - score_start)
        t0 = tpc()
    if not top:
        if not allow_passive_promotion:
            return None
        promoted = promote_passive_candidate_for_office(
            ctx,
            year=year,
            settlement_id=seat.scope_settlement_id,
            region_id=None
            if seat.scope_settlement_id
            else next(iter(polity_regions(ctx, seat.polity_id)), None),
            min_age=int(title.min_age),
            reason="office_selection",
            source={
                "seat_id": int(seat.seat_id),
                "title_id": title.title_id,
                "polity_id": int(seat.polity_id),
            },
            candidate_index=passive_office_index,
            save_conn=conn,
        )
        if promoted is None:
            if prof:
                simulation_timing.accumulate("government.fill.passive_promote", tpc() - t0)
            return None
        pick = int(promoted.person_id)
        disp = display_title_name(title, era_key)
        term = year + int(title.term_years) if title.term_years else None
        assign_holder(
            ctx,
            seat,
            pick,
            year,
            display_job=disp,
            term_expires=term,
            conn=conn,
        )
        if prof:
            simulation_timing.accumulate("government.fill.passive_promote", tpc() - t0)
        return pick
    pick = rng.choice([t[1] for t in top])
    disp = display_title_name(title, era_key)
    term = None
    if title.term_years:
        term = year + int(title.term_years)
    assign_holder(
        ctx,
        seat,
        pick,
        year,
        display_job=disp,
        term_expires=term,
        conn=conn,
    )
    if prof:
        simulation_timing.accumulate("government.fill.assign", tpc() - t0)
    return pick


_TIER_LABEL_BY_TYPE: dict[str, str] = {
    "county": "County",
    "duchy": "Duchy",
    "kingdom": "Kingdom",
    "tribe": "Tribe",
    "city_state": "City",
    "band": "Band",
    "republic": "Republic",
}


def _polity_display_name(ptype_id: str, region_label: str) -> str:
    label = _TIER_LABEL_BY_TYPE.get((ptype_id or "").strip().lower())
    if not label:
        label = (ptype_id or "realm").replace("_", " ").title()
    return f"{label} of {region_label}"


def _install_mixed_population_cache(ctx: "SimulationContext") -> None:
    census = ctx.alive_census_cache()
    try:
        passive_by_sid = ctx.passive_population_counts_by_settlement()
    except Exception:
        passive_by_sid = {}
    all_sids = set(census.count_by_settlement) | set(passive_by_sid)
    all_sids.update(getattr(ctx, "settlements_by_id", {}).keys())
    settlement_counts: dict[str, int] = {}
    region_counts: dict[str, int] = dict(census.count_by_region)
    for sid in all_sids:
        sid_s = str(sid or "").strip()
        if not sid_s:
            continue
        count = int(census.count_by_settlement.get(sid_s, 0)) + int(
            passive_by_sid.get(sid_s, 0)
        )
        settlement_counts[sid_s] = count
        st = getattr(ctx, "settlements_by_id", {}).get(sid_s)
        rid = (st.region_id if st is not None else sid_s.split(":", 1)[0]).strip()
        if rid:
            region_counts[rid] = region_counts.get(rid, 0) + int(
                passive_by_sid.get(sid_s, 0)
            )
    ctx._gov_mixed_settlement_counts = settlement_counts
    ctx._gov_mixed_region_counts = region_counts


def _mixed_alive_in_settlement(ctx: "SimulationContext", settlement_id: str) -> int:
    sid = (settlement_id or "").strip()
    if not sid:
        return 0
    cache = getattr(ctx, "_gov_mixed_settlement_counts", None)
    if isinstance(cache, dict) and sid in cache:
        return int(cache.get(sid, 0))
    mixed = getattr(ctx, "mixed_population_count_in_settlement", None)
    if callable(mixed):
        return int(mixed(sid))
    return int(ctx.count_alive_in_settlement(sid))


def _mixed_alive_in_region(ctx: "SimulationContext", region_id: str) -> int:
    rid = (region_id or "").strip()
    if not rid:
        return 0
    cache = getattr(ctx, "_gov_mixed_region_counts", None)
    if isinstance(cache, dict) and rid in cache:
        return int(cache.get(rid, 0))
    mixed = getattr(ctx, "mixed_population_count_in_region", None)
    if callable(mixed):
        return int(mixed(rid))
    return int(ctx.count_alive_in_region(rid))


def _maybe_promote_polity(
    ctx: "SimulationContext",
    year: int,
    *,
    catalog: GovernmentCatalog,
    era,
    composite_rows: tuple,
    era_key: str,
    population_scale: float = 1.0,
) -> None:
    """Upgrade existing polities (county→duchy→kingdom) when alive population crosses tiers.

    Keeps the founding dynasty and current head holder; closes the prior head-title seat
    (its time-bounded holdings stay archived in ``simulation_office_holdings``); creates
    a fresh head seat for the new tier and assigns the prior head holder; adds seats for
    any titles defined for the new tier that did not exist on the prior tier.
    """
    for pol in list(ctx.gov_polities.values()):
        if (pol.status or "").lower() != "active":
            continue
        cur_type = catalog.polity_type_by_id(pol.polity_type_id)
        if cur_type is None:
            continue
        regions = polity_regions(ctx, pol.polity_id)
        sids = polity_settlement_territory_ids(ctx, pol.polity_id)
        if not regions and not sids:
            continue
        # Vassal settlement-tier counties follow their liege; do not self-promote here.
        if not regions and sids and pol.parent_polity_id is not None:
            continue
        rid0 = (regions[0] if regions else "").strip()
        if not rid0 and sids:
            st0 = ctx.settlements_by_id.get(str(sids[0]).strip())
            rid0 = (st0.region_id or "").strip() if st0 is not None else ""
        if not rid0:
            continue
        if regions:
            pop = sum(_mixed_alive_in_region(ctx, r) for r in regions)
        else:
            pop = _mixed_alive_in_region(ctx, rid0)
        target = pick_polity_type_for_region_population(
            catalog, era, alive_in_region=pop, population_scale=population_scale
        )
        if target is None or target.polity_type_id == cur_type.polity_type_id:
            continue
        cur_min = int(cur_type.min_population_to_form or 1)
        tgt_min = int(target.min_population_to_form or 1)
        if tgt_min <= cur_min:
            continue

        # County with settlement territory → region-grain duchy/kingdom; sibling counties vassalize.
        if (
            (cur_type.polity_type_id or "").lower() == "county"
            and not regions
            and sids
            and pol.parent_polity_id is None
            and (target.jurisdiction_grain or "region").strip().lower() == "region"
            and (target.polity_type_id or "").lower() in ("duchy", "kingdom")
        ):
            ctx.gov_territory_rows = [
                t
                for t in ctx.gov_territory_rows
                if not (
                    t.polity_id == pol.polity_id
                    and (t.target_kind or "").strip().lower() == "settlement"
                )
            ]
            ctx.gov_territory_rows.append(
                TerritoryOpenRow(
                    polity_id=pol.polity_id,
                    target_kind="region",
                    target_id=rid0,
                    since_sim_year=year,
                )
            )
            for other in list(ctx.gov_polities.values()):
                if other.polity_id == pol.polity_id:
                    continue
                if (other.polity_type_id or "").lower() != "county":
                    continue
                if other.parent_polity_id is not None:
                    continue
                o_sids = polity_settlement_territory_ids(ctx, other.polity_id)
                if polity_regions(ctx, other.polity_id) or not o_sids:
                    continue
                ost = ctx.settlements_by_id.get(str(o_sids[0]).strip())
                if ost is None or (ost.region_id or "").strip() != rid0:
                    continue
                ctx.gov_polities[other.polity_id] = replace(
                    other, parent_polity_id=pol.polity_id
                )
            regions = polity_regions(ctx, pol.polity_id)

        old_head_title_id = (cur_type.head_title_id or "").strip()
        old_head_seat = next(
            (
                s
                for s in ctx.gov_office_seats.values()
                if s.polity_id == pol.polity_id and s.title_id == old_head_title_id
            ),
            None,
        )
        prior_holder = old_head_seat.holder_person_id if old_head_seat else None

        if old_head_seat is not None:
            if prior_holder is not None:
                vacate_seat(ctx, old_head_seat, year, end_reason="promotion")
            ctx.gov_office_seats[old_head_seat.seat_id] = replace(
                ctx.gov_office_seats[old_head_seat.seat_id],
                status="promoted",
            )

        capital_sid = pol.capital_settlement_id
        if not capital_sid and regions:
            capital_sid = ctx.ensure_active_settlement_for_region(regions[0]).settlement_id

        primary_region_label = (
            _region_display(ctx, regions[0]) if regions else pol.name
        )
        new_name = _polity_display_name(target.polity_type_id, primary_region_label)
        ctx.gov_polities[pol.polity_id] = replace(
            pol,
            polity_type_id=target.polity_type_id,
            name=new_name,
            capital_settlement_id=capital_sid,
        )

        existing_titles = {
            s.title_id
            for s in ctx.gov_office_seats.values()
            if s.polity_id == pol.polity_id
            and (s.status or "").strip().lower() != "promoted"
        }
        for t in catalog.titles_for_polity_type(target.polity_type_id):
            role_lower = (t.role or "").strip().lower()
            if role_lower == "settlement_merit":
                continue
            if t.title_id in existing_titles and role_lower != "head":
                continue
            for slot in range(int(t.max_holders)):
                scope = None
                if role_lower == "local_merit":
                    scope = capital_sid
                seat_id = ctx.next_gov_seat_id
                ctx.next_gov_seat_id += 1
                ctx.gov_office_seats[seat_id] = OfficeSeatState(
                    seat_id=seat_id,
                    polity_id=pol.polity_id,
                    title_id=t.title_id,
                    slot_index=slot,
                    scope_settlement_id=scope,
                )

        new_head_title = catalog.title_by_id(target.head_title_id)
        if new_head_title is not None and prior_holder is not None:
            new_head_seat = next(
                (
                    s
                    for s in ctx.gov_office_seats.values()
                    if s.polity_id == pol.polity_id
                    and s.title_id == new_head_title.title_id
                    and s.holder_person_id is None
                    and (s.status or "").strip().lower() != "promoted"
                ),
                None,
            )
            if new_head_seat is not None and ctx.is_alive(prior_holder):
                disp = display_title_name(new_head_title, era_key)
                assign_holder(ctx, new_head_seat, prior_holder, year, display_job=disp)

        ctx._record_simulation_event(
            year,
            "polity_promoted",
            {
                "polity_id": pol.polity_id,
                "from_polity_type_id": cur_type.polity_type_id,
                "to_polity_type_id": target.polity_type_id,
                "alive_population": pop,
                "prior_head_person_id": prior_holder,
            },
        )


def _maybe_split_vassal(
    ctx: "SimulationContext",
    year: int,
    *,
    catalog: GovernmentCatalog,
    composite_rows: tuple,
    rng: random.Random,
    population_scale: float = 1.0,
) -> None:
    for pol in list(ctx.gov_polities.values()):
        if (pol.status or "").lower() != "active" or pol.parent_polity_id is not None:
            continue
        ptype = catalog.polity_type_by_id(pol.polity_type_id)
        if ptype is None or not ptype.can_have_vassals:
            continue
        regions = polity_regions(ctx, pol.polity_id)
        if len(regions) < 2:
            continue
        pop = sum(_mixed_alive_in_region(ctx, r) for r in regions)
        split_threshold = effective_max_population_before_split(ptype, population_scale)
        if split_threshold <= 0 or pop <= split_threshold:
            continue
        child_type = catalog.polity_type_by_id("duchy")
        if child_type is None or (child_type.parent_polity_type_id or "") != "kingdom":
            continue
        if pol.polity_type_id != "kingdom":
            continue
        victim_region = max(regions, key=lambda r: _mixed_alive_in_region(ctx, r))
        if victim_region == regions[0]:
            victim_region = regions[-1]
        d_pid = ctx.next_gov_polity_id
        ctx.next_gov_polity_id += 1
        duchy = PolityState(
            polity_id=d_pid,
            polity_type_id="duchy",
            parent_polity_id=pol.polity_id,
            name=f"Duchy of {_region_display(ctx, victim_region)}",
            capital_settlement_id=ctx.ensure_active_settlement_for_region(
                victim_region
            ).settlement_id,
            founding_dynasty_id=pol.founding_dynasty_id,
            founded_sim_year=year,
            status="active",
        )
        ctx.gov_polities[d_pid] = duchy
        ctx.gov_territory_rows = [
            t
            for t in ctx.gov_territory_rows
            if not (
                t.polity_id == pol.polity_id
                and t.target_kind == "region"
                and t.target_id == victim_region
            )
        ]
        ctx.gov_territory_rows.append(
            TerritoryOpenRow(
                polity_id=d_pid,
                target_kind="region",
                target_id=victim_region,
                since_sim_year=year,
            )
        )
        w = ctx.world.strip()
        with _open_save(ctx.save_db_path) as conn:
            gov_ckpt.ensure_government_schema(conn)
            gov_ckpt.close_territory_row(
                conn,
                world=w,
                polity_id=pol.polity_id,
                target_kind="region",
                target_id=victim_region,
                until_sim_year=year,
            )
            gov_ckpt.insert_territory_open(
                conn,
                world=w,
                polity_id=d_pid,
                target_kind="region",
                target_id=victim_region,
                since_sim_year=year,
            )
            conn.commit()
        head_title = catalog.title_by_id(child_type.head_title_id)
        head_seat: OfficeSeatState | None = None
        for t in catalog.titles_for_polity_type("duchy"):
            sid = ctx.next_gov_seat_id
            ctx.next_gov_seat_id += 1
            seat = OfficeSeatState(
                seat_id=sid,
                polity_id=d_pid,
                title_id=t.title_id,
                slot_index=0,
            )
            ctx.gov_office_seats[sid] = seat
            if head_title is not None and t.title_id == head_title.title_id:
                head_seat = seat
        if head_seat is not None:
            duke_id = _pick_head_candidate_in_region(
                ctx,
                region_id=victim_region,
                head_title=head_title,
                composite_rows=composite_rows,
                year=year,
            )
            if duke_id is not None:
                era_key_for_split = __import__(
                    "library.simulation_careers", fromlist=["resolve_job_era"]
                ).resolve_job_era(ctx.get_historical_year(year))
                disp = (
                    display_title_name(head_title, era_key_for_split)
                    if head_title is not None
                    else head_seat.title_id
                )
                assign_holder(ctx, head_seat, duke_id, year, display_job=disp)
        ctx._record_simulation_event(
            year,
            "polity_split_vassal",
            {"parent_polity_id": pol.polity_id, "child_polity_id": d_pid, "region": victim_region},
        )
        aid = ctx.next_gov_alliance_id
        ctx.next_gov_alliance_id += 1
        ctx.gov_alliances.append(
            AllianceState(
                alliance_id=aid,
                polity_a_id=pol.polity_id,
                polity_b_id=d_pid,
                kind="vassal_oath",
                since_sim_year=year,
                payload={},
                loyalty_score=0.78,
            )
        )
        break


def _reap_dead_holders(ctx: "SimulationContext", year: int) -> None:
    prof = simulation_timing.active_for_year(year)
    tpc = time.perf_counter
    if prof:
        t0 = tpc()
    dead_holder_seats: list[OfficeSeatState] = []
    for seat in list(ctx.gov_office_seats.values()):
        if seat.holder_person_id is None:
            continue
        rec = ctx.id_to_record.get(seat.holder_person_id)
        if rec is None or rec.person.deathyear is None:
            continue
        if int(rec.person.deathyear) <= year:
            dead_holder_seats.append(seat)
    if prof:
        simulation_timing.accumulate("government.reap_dead_holders.scan", tpc() - t0)
    if not dead_holder_seats:
        return
    if prof:
        t0 = tpc()
    with _open_save(ctx.save_db_path) as conn:
        gov_ckpt.ensure_government_schema(conn)
        for seat in dead_holder_seats:
            current = ctx.gov_office_seats.get(seat.seat_id, seat)
            if current.holder_person_id is None:
                continue
            rec = ctx.id_to_record.get(current.holder_person_id)
            if rec is None or rec.person.deathyear is None or int(rec.person.deathyear) > year:
                continue
            vacate_seat(ctx, current, year, end_reason="death", conn=conn)
        conn.commit()
    if prof:
        simulation_timing.accumulate("government.reap_dead_holders.vacate", tpc() - t0)


def _succession_tick(
    ctx: "SimulationContext",
    year: int,
    *,
    catalog: GovernmentCatalog,
    composite_rows: tuple,
    era_key: str,
    rng: random.Random | None = None,
) -> None:
    """Fill seats whose holder just died.

    Hereditary (``primogeniture`` / ``salic`` / ``cognatic``) seats first roll
    ``title.merit_takeover_chance``. On a hit, the seat is filled by
    :func:`_fill_merit_or_election` (an "ambitious leader" displaces the heir). At the
    default CSV tuning this fires often for **counts** (mixed succession), rarely for
    **dukes**, and very rarely for **kings** (heavily hereditary). On a miss the
    primogeniture chain runs as before.
    """
    succ_rng = rng if rng is not None else random.Random(year * 7919 + 1)
    for seat in list(ctx.gov_office_seats.values()):
        if seat.holder_person_id is not None:
            continue
        t = catalog.title_by_id(seat.title_id)
        if t is None:
            continue
        rule = (t.selection_rule or "").lower()
        if "primogeniture" not in rule and "salic" not in rule and "cognatic" not in rule:
            continue
        with _open_save(ctx.save_db_path) as conn:
            gov_ckpt.ensure_government_schema(conn)
            cur = conn.execute(
                """
                SELECT holder_person_id FROM simulation_office_holdings
                WHERE seat_id = ?
                ORDER BY COALESCE(end_sim_year, start_sim_year) DESC, holding_id DESC
                LIMIT 1
                """,
                (seat.seat_id,),
            ).fetchone()
        prev = int(cur[0]) if cur and cur[0] is not None else None
        if prev is None:
            continue
        takeover_chance = float(getattr(t, "merit_takeover_chance", 0.0) or 0.0)
        if takeover_chance > 0.0 and succ_rng.random() < takeover_chance:
            holder_before = seat.holder_person_id
            _fill_merit_or_election(
                ctx, year, seat, t, catalog, composite_rows, era_key, succ_rng
            )
            new_holder = ctx.gov_office_seats[seat.seat_id].holder_person_id
            if new_holder is not None and new_holder != holder_before:
                ctx._record_simulation_event(
                    year,
                    "office_succession",
                    {
                        "seat_id": seat.seat_id,
                        "holder_person_id": new_holder,
                        "title_id": t.title_id,
                        "via": "merit_takeover",
                        "previous_holder_id": prev,
                    },
                )
                continue
        chain = primogeniture_candidates(ctx, previous_holder_id=prev, title=t, year=year)
        if not chain:
            continue
        nxt = chain[0]
        disp = display_title_name(t, era_key)
        assign_holder(ctx, seat, nxt, year, display_job=disp)
        ctx._record_simulation_event(
            year,
            "office_succession",
            {
                "seat_id": seat.seat_id,
                "holder_person_id": nxt,
                "title_id": t.title_id,
                "via": "hereditary",
                "previous_holder_id": prev,
            },
        )


def _term_expiry(
    ctx: "SimulationContext",
    year: int,
    catalog: GovernmentCatalog,
    composite_rows: tuple,
    *,
    passive_office_index=None,
) -> None:
    prof = simulation_timing.active_for_year(year)
    tpc = time.perf_counter
    if prof:
        t0 = tpc()
    era_key = __import__(
        "library.simulation_careers", fromlist=["resolve_job_era"]
    ).resolve_job_era(ctx.get_historical_year(year))
    expired = []
    for seat in list(ctx.gov_office_seats.values()):
        if seat.holder_person_id is None or seat.term_expires_sim_year is None:
            continue
        if year < int(seat.term_expires_sim_year):
            continue
        expired.append(seat)
    if prof:
        simulation_timing.accumulate("government.term_expiry.scan", tpc() - t0)
    if not expired:
        return
    expired_count = 0
    if prof:
        t0 = tpc()
    holder_counts = _holder_counts_currently_in_office(ctx)
    already_holding = set(holder_counts)
    if prof:
        simulation_timing.accumulate("government.term_expiry.holder_index", tpc() - t0)
    if prof:
        t0 = tpc()
    with _open_save(ctx.save_db_path) as conn:
        gov_ckpt.ensure_government_schema(conn)
        if prof:
            simulation_timing.accumulate("government.term_expiry.open", tpc() - t0)
        for seat in expired:
            if prof:
                t0 = tpc()
            current = ctx.gov_office_seats.get(seat.seat_id, seat)
            if current.holder_person_id is None:
                if prof:
                    simulation_timing.accumulate("government.term_expiry.check", tpc() - t0)
                continue
            if current.term_expires_sim_year is None or year < int(current.term_expires_sim_year):
                if prof:
                    simulation_timing.accumulate("government.term_expiry.check", tpc() - t0)
                continue
            seat = current
            expired_count += 1
            t = catalog.title_by_id(seat.title_id)
            if prof:
                simulation_timing.accumulate("government.term_expiry.check", tpc() - t0)
                t0 = tpc()
            old_holder = (
                int(seat.holder_person_id)
                if seat.holder_person_id is not None
                else None
            )
            vacate_seat(ctx, seat, year, end_reason="term_expiry", conn=conn)
            if old_holder is not None:
                remaining = int(holder_counts.get(old_holder, 0)) - 1
                if remaining > 0:
                    holder_counts[old_holder] = remaining
                else:
                    holder_counts.pop(old_holder, None)
                    already_holding.discard(old_holder)
            if prof:
                simulation_timing.accumulate("government.term_expiry.vacate", tpc() - t0)
            if t is not None:
                if prof:
                    t0 = tpc()
                rng = random.Random(year * 9973 + seat.seat_id)
                new_holder = _fill_merit_or_election(
                    ctx,
                    year,
                    seat,
                    t,
                    catalog,
                    composite_rows,
                    era_key,
                    rng,
                    conn=conn,
                    already_holding=already_holding,
                    passive_office_index=passive_office_index,
                    allow_passive_promotion=(
                        (t.role or "").strip().lower() != "settlement_merit"
                    ),
                )
                if new_holder is not None:
                    holder_counts[int(new_holder)] = (
                        int(holder_counts.get(int(new_holder), 0)) + 1
                    )
                    already_holding.add(int(new_holder))
                if prof:
                    simulation_timing.accumulate("government.term_expiry.refill", tpc() - t0)
        if prof:
            t0 = tpc()
        conn.commit()
        if prof:
            simulation_timing.accumulate("government.term_expiry.commit", tpc() - t0)
    simulation_timing.record_gauge(
        year, "government", "term_expired_seats", expired_count
    )


def _fill_vacancies(
    ctx: "SimulationContext",
    year: int,
    *,
    catalog: GovernmentCatalog,
    composite_rows: tuple,
    era_key: str,
    rng: random.Random,
    passive_office_index=None,
) -> None:
    vacancies: list[tuple[OfficeSeatState, TitleRow]] = []
    for seat in list(ctx.gov_office_seats.values()):
        if seat.holder_person_id is not None:
            continue
        t = catalog.title_by_id(seat.title_id)
        if t is None:
            continue
        rule = (t.selection_rule or "").lower()
        if "primogeniture" in rule or "salic" in rule:
            continue
        vacancies.append((seat, t))
    if not vacancies:
        return
    holder_counts = _holder_counts_currently_in_office(ctx)
    already_holding = set(holder_counts)
    with _open_save(ctx.save_db_path) as conn:
        gov_ckpt.ensure_government_schema(conn)
        for seat, t in vacancies:
            current = ctx.gov_office_seats.get(seat.seat_id, seat)
            if current.holder_person_id is not None:
                continue
            new_holder = _fill_merit_or_election(
                ctx,
                year,
                current,
                t,
                catalog,
                composite_rows,
                era_key,
                rng,
                conn=conn,
                already_holding=already_holding,
                passive_office_index=passive_office_index,
                allow_passive_promotion=(
                    (t.role or "").strip().lower() != "settlement_merit"
                ),
            )
            if new_holder is not None:
                holder_counts[int(new_holder)] = (
                    int(holder_counts.get(int(new_holder), 0)) + 1
                )
                already_holding.add(int(new_holder))
        conn.commit()


_VASSAL_EXTINCTION_GRACE_YEARS = 5


def _required_seats_for_settlement_title(
    *, alive: int, title: TitleRow, scale: float
) -> int:
    """How many seats a single settlement should have for a population-scaled title.

    Returns ``0`` when the settlement has fewer alive than the (scaled) first-holder
    threshold; otherwise ``1`` when ``pop_per_holder`` is unset, and
    ``floor((alive - first_threshold) / per_holder_threshold) + 1`` when set.
    """
    first = max(0, int(title.min_population_for_first_holder or 0))
    per = max(0, int(title.pop_per_holder or 0))
    eff_first = max(0, int(round(first * float(scale)))) if first > 0 else 0
    eff_per = max(0, int(round(per * float(scale)))) if per > 0 else 0
    a = max(0, int(alive))
    if first > 0 and a < max(1, eff_first):
        return 0
    if per <= 0:
        return 1
    extras = (a - max(1, eff_first)) // max(1, eff_per)
    return min(
        SETTLEMENT_MERIT_MAX_SEATS_PER_TITLE,
        max(1, 1 + max(0, int(extras))),
    )


def _ensure_settlement_offices(
    ctx: "SimulationContext",
    year: int,
    *,
    catalog: GovernmentCatalog,
    composite_rows: tuple,
    era_key: str,
    rng: random.Random,
    population_scale: float,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Size population-scaled per-settlement merit seats (``settlement_leader`` etc.).

    Adds new seats when a settlement crosses the next scaled threshold and fills them
    with :func:`_fill_merit_or_election`. Existing seats are never reduced; if a
    settlement shrinks, the surplus seat just stays around (its merit cycle will fail
    to find a candidate and it remains vacant). Universal titles
    (``polity_type_id='*'``) are applied to every active polity's settlements.
    """
    universal = [t for t in catalog.universal_titles() if (t.role or "").strip().lower() == "settlement_merit"]
    if not universal:
        return
    seats_index: dict[tuple[int, str, str], int] = {}
    for s in ctx.gov_office_seats.values():
        if (s.status or "").strip().lower() == "promoted":
            continue
        sid = (s.scope_settlement_id or "").strip()
        if not sid:
            continue
        key = (int(s.polity_id), str(s.title_id), sid)
        seats_index[key] = seats_index.get(key, 0) + 1

    for pol in list(ctx.gov_polities.values()):
        if (pol.status or "").lower() != "active":
            continue
        regions = polity_regions(ctx, pol.polity_id)
        settlements = polity_settlement_territory_ids(ctx, pol.polity_id)
        if regions:
            for rid in regions:
                sids = list(ctx.settlement_ids_by_region.get(rid, ()))
                for sid in sids:
                    _ensure_settlement_offices_for_sid(
                        ctx,
                        year,
                        pol=pol,
                        sid=sid,
                        catalog=catalog,
                        composite_rows=composite_rows,
                        era_key=era_key,
                        rng=rng,
                        population_scale=population_scale,
                        seats_index=seats_index,
                        conn=conn,
                    )
        elif settlements:
            for sid in settlements:
                _ensure_settlement_offices_for_sid(
                    ctx,
                    year,
                    pol=pol,
                    sid=sid,
                    catalog=catalog,
                    composite_rows=composite_rows,
                    era_key=era_key,
                    rng=rng,
                    population_scale=population_scale,
                    seats_index=seats_index,
                    conn=conn,
                )


def _ensure_settlement_offices_for_sid(
    ctx: "SimulationContext",
    year: int,
    *,
    pol,
    sid: str,
    catalog: GovernmentCatalog,
    composite_rows: tuple,
    era_key: str,
    rng: random.Random,
    population_scale: float,
    seats_index: dict[tuple[int, str, str], int],
    conn: sqlite3.Connection | None = None,
) -> None:
    state = ctx.settlements_by_id.get(sid)
    if state is None or (state.status or "").strip().lower() != "active":
        return
    alive = _mixed_alive_in_settlement(ctx, sid)
    universal = [
        t
        for t in catalog.universal_titles()
        if (t.role or "").strip().lower() == "settlement_merit"
    ]
    for t in universal:
        needed = _required_seats_for_settlement_title(
            alive=alive, title=t, scale=population_scale
        )
        if needed <= 0:
            continue
        have = seats_index.get((int(pol.polity_id), str(t.title_id), sid), 0)
        if needed <= have:
            continue
        for slot in range(have, needed):
            seat_id = ctx.next_gov_seat_id
            ctx.next_gov_seat_id += 1
            seat = OfficeSeatState(
                seat_id=seat_id,
                polity_id=pol.polity_id,
                title_id=t.title_id,
                slot_index=slot,
                scope_settlement_id=sid,
            )
            ctx.gov_office_seats[seat_id] = seat
            _fill_merit_or_election(
                ctx,
                year,
                seat,
                t,
                catalog,
                composite_rows,
                era_key,
                rng,
                conn=conn,
                allow_passive_promotion=False,
            )
        seats_index[(int(pol.polity_id), str(t.title_id), sid)] = needed


def _alive_in_polity_scope(ctx: "SimulationContext", pol: PolityState) -> int:
    regions = polity_regions(ctx, pol.polity_id)
    sids = polity_settlement_territory_ids(ctx, pol.polity_id)
    if regions:
        return sum(_mixed_alive_in_region(ctx, r) for r in regions)
    if sids:
        return sum(_mixed_alive_in_settlement(ctx, s) for s in sids)
    return 0


def _maybe_name_regions_and_polities(
    ctx: "SimulationContext",
    year: int,
    *,
    catalog: GovernmentCatalog,
    rng: random.Random,
) -> None:
    from library.geography import get_region

    thr = naming_threshold_for_world(ctx.world, ctx.db_path)
    if getattr(ctx, "region_display_label_overrides", None) is None:
        ctx.region_display_label_overrides = {}
    if getattr(ctx, "region_label_source", None) is None:
        ctx.region_label_source = {}
    if getattr(ctx, "region_city_rename_miss_streak", None) is None:
        ctx.region_city_rename_miss_streak = {}
    ovr = ctx.region_display_label_overrides
    src = ctx.region_label_source
    miss = ctx.region_city_rename_miss_streak

    region_ids = sorted(set(ctx.settlement_ids_by_region.keys()))
    for rid in region_ids:
        if _mixed_alive_in_region(ctx, rid) < thr:
            continue
        cur = str(ovr.get(rid, "") or "").strip()
        if cur:
            continue
        try:
            reg = get_region(rid, world=ctx.world, db_path=ctx.db_path)
        except Exception:
            continue
        seed = int(year) * 7919 + hash((ctx.world, rid, ctx.placename_rng_salt)) % 1_000_000
        label = region_geographic_label(reg, rng_seed=seed)
        ctx.region_display_label_overrides[rid] = label
        src[rid] = "geo"
        ctx._record_simulation_event(
            int(year),
            "region_named",
            {"region_id": rid, "region_display_label": label},
        )

    ratio = REGION_RENAME_DOMINANT_CITY_RATIO
    hy = REGION_RENAME_DOMINANT_CITY_HYSTERESIS_YEARS

    for rid in sorted(set(ctx.settlement_ids_by_region.keys())):
        if _mixed_alive_in_region(ctx, rid) < thr:
            continue
        sids = list(ctx.settlement_ids_by_region.get(rid, []))
        counts = [(s, _mixed_alive_in_settlement(ctx, s)) for s in sids]
        counts = [(s, n) for s, n in counts if n > 0]
        if not counts:
            continue
        counts.sort(key=lambda x: -x[1])
        top_sid, top_n = counts[0]
        second_n = counts[1][1] if len(counts) > 1 else 0
        region_n = max(1, _mixed_alive_in_region(ctx, rid))
        cond = top_n >= ratio * region_n and top_n >= 2.0 * second_n

        if cond:
            miss[rid] = 0
            st = ctx.settlements_by_id.get(top_sid)
            dname = (
                (st.display_name or st.settlement_id).strip()
                if st is not None
                else top_sid
            )
            culture = (
                (getattr(st, "name_culture_primary", None) or "").strip()
                if st is not None
                else ""
            )
            takeover_seed = (
                hash((ctx.world, rid, top_sid, "city_takeover")) & 0xFFFFFFFF
            )
            new_lab = region_label_after_dominant_city(
                dname, culture=culture or None, rng_seed=takeover_seed
            )
            old = str(ovr.get(rid, "") or "").strip()
            if old != new_lab:
                ctx.region_display_label_overrides[rid] = new_lab
                src[rid] = "city"
                ctx._record_simulation_event(
                    int(year),
                    "region_renamed_after_city",
                    {
                        "region_id": rid,
                        "settlement_id": top_sid,
                        "settlement_display_name": dname,
                        "region_display_label": new_lab,
                    },
                )
        else:
            if str(src.get(rid) or "") == "city":
                miss[rid] = int(miss.get(rid, 0)) + 1
                if miss[rid] >= hy:
                    try:
                        reg = get_region(rid, world=ctx.world, db_path=ctx.db_path)
                    except Exception:
                        miss[rid] = 0
                        continue
                    seed = int(year) * 11003 + hash((ctx.world, rid, "revert")) % 1_000_000
                    lab = region_geographic_label(reg, rng_seed=seed)
                    ctx.region_display_label_overrides[rid] = lab
                    src[rid] = "geo"
                    miss[rid] = 0
                    ctx._record_simulation_event(
                        int(year),
                        "region_renamed_after_city",
                        {
                            "region_id": rid,
                            "reason": "revert_to_geo",
                            "region_display_label": lab,
                        },
                    )
            else:
                miss[rid] = 0

    for pol in list(ctx.gov_polities.values()):
        if (pol.status or "").lower() != "active":
            continue
        pti = catalog.polity_type_by_id(pol.polity_type_id)
        if pti is None:
            continue
        alive_scope = _alive_in_polity_scope(ctx, pol)
        if alive_scope < thr:
            continue
        if not (
            is_placeholder_polity_name(pol.name, pol.polity_type_id)
            or bool((pol.notes or {}).get("lazy_geographic_name"))
        ):
            continue
        regions = polity_regions(ctx, pol.polity_id)
        sids = polity_settlement_territory_ids(ctx, pol.polity_id)
        rlab = ""
        if regions:
            r0 = regions[0]
            rlab = str(ovr.get(r0) or "").strip() or _region_display(ctx, r0)
        elif sids:
            st0 = ctx.settlements_by_id.get(str(sids[0]).strip())
            rid2 = (st0.region_id or "").strip() if st0 is not None else ""
            if rid2:
                rlab = str(ovr.get(rid2) or "").strip() or _region_display(ctx, rid2)
        anchor_name = None
        cap = (pol.capital_settlement_id or (sids[0] if sids else "")).strip()
        if cap:
            cst = ctx.settlements_by_id.get(cap)
            anchor_name = (
                (cst.display_name or cap).strip() if cst is not None else cap
            )
        new_name = polity_geographic_label(
            pol.polity_type_id,
            region_label=rlab or "lands",
            anchor_settlement_display_name=anchor_name,
            jurisdiction_grain=(pti.jurisdiction_grain or "region"),
        )
        new_notes = dict(pol.notes or {})
        new_notes.pop("lazy_geographic_name", None)
        ctx.gov_polities[pol.polity_id] = replace(pol, name=new_name, notes=new_notes)
        ctx._record_simulation_event(
            int(year),
            "polity_named",
            {"polity_id": pol.polity_id, "name": new_name},
        )


def _dissolve_landless_polities(ctx: "SimulationContext", year: int) -> None:
    """Mark active polities ``status='dissolved'`` once they own zero open territory.

    Vacates remaining seats on the same call so household care / economy do not
    keep crediting a phantom realm. Skips bootstrap-fresh polities (founded this
    tick) so a brand-new region does not get reaped before its territory row is
    visible to the next iteration.
    """
    y = int(year)
    for pol in list(ctx.gov_polities.values()):
        if (pol.status or "").lower() != "active":
            continue
        if y == int(pol.founded_sim_year):
            continue
        has_terr = any(t.polity_id == pol.polity_id for t in ctx.gov_territory_rows)
        if has_terr:
            continue
        for seat in list(ctx.gov_office_seats.values()):
            if seat.polity_id != pol.polity_id:
                continue
            if seat.holder_person_id is not None:
                vacate_seat(ctx, seat, y, end_reason="polity_dissolved")
        ctx.gov_polities[pol.polity_id] = replace(
            pol, status="dissolved", dissolved_sim_year=y
        )
        ctx._record_simulation_event(
            year, "polity_dissolved", {"polity_id": pol.polity_id, "reason": "no_territory"}
        )


def _suzerain_inherit_extinct_vassal(ctx: "SimulationContext", year: int) -> None:
    w = ctx.world.strip()
    y = int(year)
    for pol in list(ctx.gov_polities.values()):
        if (pol.status or "").lower() != "active" or pol.parent_polity_id is None:
            continue
        if y - int(pol.founded_sim_year) < _VASSAL_EXTINCTION_GRACE_YEARS:
            continue
        has_holder = any(
            s.polity_id == pol.polity_id and s.holder_person_id
            for s in ctx.gov_office_seats.values()
        )
        if has_holder:
            continue
        parent = ctx.gov_polities.get(pol.parent_polity_id or 0)
        if parent is None:
            continue
        terrs = [t for t in ctx.gov_territory_rows if t.polity_id == pol.polity_id]
        if not terrs:
            continue
        with _open_save(ctx.save_db_path) as conn:
            gov_ckpt.ensure_government_schema(conn)
            for t in terrs:
                gov_ckpt.close_territory_row(
                    conn,
                    world=w,
                    polity_id=pol.polity_id,
                    target_kind=t.target_kind,
                    target_id=t.target_id,
                    until_sim_year=y,
                )
                gov_ckpt.insert_territory_open(
                    conn,
                    world=w,
                    polity_id=parent.polity_id,
                    target_kind=t.target_kind,
                    target_id=t.target_id,
                    since_sim_year=y,
                )
            conn.commit()
        kept = [x for x in ctx.gov_territory_rows if x.polity_id != pol.polity_id]
        for t in terrs:
            kept.append(
                TerritoryOpenRow(
                    polity_id=parent.polity_id,
                    target_kind=t.target_kind,
                    target_id=t.target_id,
                    since_sim_year=y,
                )
            )
        ctx.gov_territory_rows = kept
        ctx.gov_polities[pol.polity_id] = replace(
            pol, status="absorbed", dissolved_sim_year=y
        )


def simulation_government_annual_tick(ctx: "SimulationContext", year: int) -> None:
    prof = simulation_timing.active_for_year(year)
    tpc = time.perf_counter
    if prof:
        t0 = tpc()
    init_government_state(ctx)
    try:
        catalog = load_government_catalog(ctx.db_path)
    except sqlite3.OperationalError:
        return
    y = int(year)
    hy = ctx.get_historical_year(y)
    era = resolve_government_era(catalog, world=ctx.world, historical_year=hy)
    if era is None:
        return
    if pick_primary_polity_type(catalog, era) is None:
        return
    composite_rows = government_candidate_composite_rows(
        load_genome_composite_rows(ctx.db_path)
    )
    era_key = __import__(
        "library.simulation_careers", fromlist=["resolve_job_era"]
    ).resolve_job_era(hy)
    rng = random.Random(y * 1_000_003 + hash(ctx.world) % 999_983 + int(ctx.placename_rng_salt))
    pop_scale = population_scale_for_world(ctx.world, db_path=ctx.db_path)
    ctx._gov_candidate_fact_cache = {}
    ctx._gov_scored_candidate_cache = {}
    ctx._gov_care_indexes = ctx.annual_care_indexes(y)
    if prof:
        ctx._gov_profile_counts = {}
    if prof:
        simulation_timing.accumulate("government.setup", tpc() - t0)
        simulation_timing.record_gauge(
            y, "government", "polities", len(getattr(ctx, "gov_polities", {}))
        )
        simulation_timing.record_gauge(
            y, "government", "office_seats", len(getattr(ctx, "gov_office_seats", {}))
        )
        t0 = tpc()

    _install_mixed_population_cache(ctx)
    cols = ctx.alive_person_columns(y)
    regions_with_people = {
        cols.region_id_by_code[int(code)]
        for code in set(cols.region_codes)
        if int(code) != 0 and int(code) in cols.region_id_by_code
    }
    regions_with_people.update(
        rid
        for rid, count in getattr(ctx, "_gov_mixed_region_counts", {}).items()
        if int(count) > 0
    )
    if prof:
        simulation_timing.accumulate("government.alive_region_scan", tpc() - t0)
        simulation_timing.record_gauge(
            y, "government", "regions_with_people", len(regions_with_people)
        )
        t0 = tpc()

    for rid in sorted(regions_with_people):
        n_alive = _mixed_alive_in_region(ctx, rid)
        ptype = pick_polity_type_for_region_population(
            catalog, era, alive_in_region=n_alive, population_scale=pop_scale
        )
        if ptype is None:
            continue
        _bootstrap_polities_for_region(
            ctx,
            y,
            region_id=rid,
            catalog=catalog,
            era=era,
            ptype=ptype,
            composite_rows=composite_rows,
            rng=rng,
            population_scale=pop_scale,
        )
    if prof:
        simulation_timing.accumulate("government.bootstrap_regions", tpc() - t0)
        t0 = tpc()

    _maybe_promote_polity(
        ctx,
        y,
        catalog=catalog,
        era=era,
        composite_rows=composite_rows,
        era_key=era_key,
        population_scale=pop_scale,
    )
    if prof:
        simulation_timing.accumulate("government.promote_polity", tpc() - t0)
        t0 = tpc()
    _maybe_split_vassal(
        ctx,
        y,
        catalog=catalog,
        composite_rows=composite_rows,
        rng=rng,
        population_scale=pop_scale,
    )
    if prof:
        simulation_timing.accumulate("government.split_vassal", tpc() - t0)
        t0 = tpc()

    with _open_save(ctx.save_db_path) as conn:
        gov_ckpt.ensure_government_schema(conn)
        _ensure_settlement_offices(
            ctx,
            y,
            catalog=catalog,
            composite_rows=composite_rows,
            era_key=era_key,
            rng=rng,
            population_scale=pop_scale,
            conn=conn,
        )
        conn.commit()
    if prof:
        simulation_timing.accumulate("government.ensure_settlement_offices", tpc() - t0)
        simulation_timing.record_gauge(
            y,
            "government",
            "office_seats_after_ensure",
            len(getattr(ctx, "gov_office_seats", {})),
        )
        t0 = tpc()
    _maybe_name_regions_and_polities(ctx, y, catalog=catalog, rng=rng)
    if prof:
        simulation_timing.accumulate("government.naming", tpc() - t0)
        t0 = tpc()
    _reap_dead_holders(ctx, y)
    if prof:
        simulation_timing.accumulate("government.reap_dead_holders", tpc() - t0)
        t0 = tpc()
    _succession_tick(
        ctx, y, catalog=catalog, composite_rows=composite_rows, era_key=era_key, rng=rng
    )
    if prof:
        simulation_timing.accumulate("government.succession", tpc() - t0)
        t0 = tpc()
    passive_office_index = build_passive_office_candidate_index(ctx)
    if prof:
        simulation_timing.accumulate("government.passive_office_index", tpc() - t0)
        t0 = tpc()
    _term_expiry(
        ctx,
        y,
        catalog,
        composite_rows,
        passive_office_index=passive_office_index,
    )
    # Term expiry records exclusive inner phases.
    if prof:
        t0 = tpc()
    _fill_vacancies(
        ctx,
        y,
        catalog=catalog,
        composite_rows=composite_rows,
        era_key=era_key,
        rng=rng,
        passive_office_index=passive_office_index,
    )
    if prof:
        simulation_timing.accumulate("government.fill_vacancies", tpc() - t0)
        simulation_timing.record_gauge(
            y,
            "government",
            "vacant_office_seats",
            sum(
                1
                for s in getattr(ctx, "gov_office_seats", {}).values()
                if s.holder_person_id is None
            ),
        )
        t0 = tpc()

    import library.simulation_warfare as war

    war.simulation_warfare_annual_tick(
        ctx, y, catalog=catalog, composite_rows=composite_rows, rng=rng
    )
    if prof:
        simulation_timing.accumulate("government.warfare", tpc() - t0)
        t0 = tpc()
    if prof:
        alliance_start = t0
    war.decay_alliance_loyalty(ctx, rng)
    if prof:
        simulation_timing.accumulate("government.alliances.decay", tpc() - t0)
        t0 = tpc()
    war.roll_marriage_alliances(ctx, y, rng, catalog=catalog)
    if prof:
        simulation_timing.accumulate("government.alliances.marriage", tpc() - t0)
        simulation_timing.accumulate("government.alliances", tpc() - alliance_start)
        t0 = tpc()
    _suzerain_inherit_extinct_vassal(ctx, y)
    _dissolve_landless_polities(ctx, y)
    if prof:
        simulation_timing.accumulate("government.cleanup", tpc() - t0)
        simulation_timing.record_gauge(
            y,
            "government",
            "candidate_fact_cache_size",
            len(getattr(ctx, "_gov_candidate_fact_cache", {})),
        )
        simulation_timing.record_gauge(
            y,
            "government",
            "scored_candidate_cache_size",
            len(getattr(ctx, "_gov_scored_candidate_cache", {})),
        )
        for metric, value in sorted(getattr(ctx, "_gov_profile_counts", {}).items()):
            simulation_timing.record_gauge(y, "government", metric, value)


def vacate_government_holders_not_in_ram(ctx: "SimulationContext") -> None:
    """After pruning people from RAM, clear office seats whose holder is gone."""
    init_government_state(ctx)
    if not ctx.gov_office_seats:
        return
    y = int(ctx.current_year or ctx.simulation_start_year)
    valid = set(ctx.id_to_record.keys())
    for seat in list(ctx.gov_office_seats.values()):
        hid = seat.holder_person_id
        if hid is not None and hid not in valid:
            vacate_seat(ctx, seat, y, end_reason="exile")


def person_holds_government_treasury_seat(ctx: "SimulationContext", person_id: int) -> bool:
    init_government_state(ctx)
    for s in ctx.gov_office_seats.values():
        if s.holder_person_id != person_id:
            continue
        t = None
        try:
            cat = load_government_catalog(ctx.db_path)
            t = cat.title_by_id(s.title_id)
        except Exception:
            pass
        if t is None:
            continue
        role = (t.role or "").strip().lower()
        if role in ("head", "court"):
            return True
    return False

