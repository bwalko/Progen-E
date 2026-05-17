"""Multi-year campaigns: levies, battles, casualties, territory transfer, treasury drain."""

from __future__ import annotations

import random
from dataclasses import replace
from typing import TYPE_CHECKING

from library import government_checkpoint as gov_ckpt
from library.government_catalog import GovernmentCatalog, load_government_catalog
from library.leadership import military_quality_index
from library.polity import (
    CampaignState,
    polity_regions,
    realm_resident_decision_sample,
    realm_resident_ids,
)

if TYPE_CHECKING:
    from library.simulation_context import SimulationContext

MAX_CAMPAIGN_YEARS = 8
LEVY_FRACTION_BY_ERA = {
    "prehistoric": 0.06,
    "bronze_age": 0.08,
    "iron_age": 0.10,
    "medieval": 0.12,
    "modern": 0.05,
}
MERCENARY_COST_PER_STRENGTH = 0.08
VASSAL_LEVY_SHARE = 0.35
CONQUEST_NEIGHBOR_PROB = 0.04
USURP_ROLL_SCALE = 1.15


def _job_era_key(ctx: "SimulationContext", year: int) -> str:
    from library.simulation_careers import resolve_job_era

    return resolve_job_era(ctx.get_historical_year(year))


def _fighting_age_males(
    ctx: "SimulationContext", polity_id: int, year: int
) -> list[int]:
    from library.simulation_careers import _person_maturity_age, job_eligibility_age

    era = _job_era_key(ctx, year)
    pids: list[int] = []
    for pid in realm_resident_ids(ctx, polity_id):
        rec = ctx.id_to_record.get(pid)
        if rec is None or rec.person.deathyear is not None:
            continue
        if (rec.person.gender or "").strip() != "Male":
            continue
        m_age = _person_maturity_age(rec.person, ctx.db_path)
        min_age = max(16, int(job_eligibility_age(m_age, era)))
        age = year - int(rec.person.birthyear)
        if age < min_age:
            continue
        pids.append(pid)
    return sorted(pids)


def _officer_quality(
    ctx: "SimulationContext",
    polity_id: int,
    rows: tuple,
    catalog: GovernmentCatalog,
) -> float:
    q = 0.35
    for s in ctx.gov_office_seats.values():
        if s.polity_id != polity_id or s.holder_person_id is None:
            continue
        trow = catalog.title_by_id(s.title_id)
        if trow is None:
            continue
        if (trow.role or "").strip().lower() not in ("head", "court"):
            continue
        rec = ctx.id_to_record.get(s.holder_person_id)
        if rec is None:
            continue
        q = max(q, military_quality_index(rec.person, composite_rows=rows))
    return min(1.2, q)


def _vassal_polities(ctx: "SimulationContext", suzerain_id: int) -> list[int]:
    return [
        p.polity_id
        for p in ctx.gov_polities.values()
        if p.parent_polity_id == suzerain_id and (p.status or "").lower() == "active"
    ]


def _compute_force_strength(
    ctx: "SimulationContext",
    polity_id: int,
    year: int,
    *,
    composite_rows: tuple,
    treasury_spent: float,
    catalog: GovernmentCatalog,
) -> float:
    era_key = _job_era_key(ctx, year)
    levy_rate = LEVY_FRACTION_BY_ERA.get(era_key, 0.10)
    males = _fighting_age_males(ctx, polity_id, year)
    levy = max(1.0, len(males) * levy_rate * 10.0)
    officer = _officer_quality(ctx, polity_id, composite_rows, catalog)
    merc = max(0.0, treasury_spent / max(MERCENARY_COST_PER_STRENGTH, 1e-6))
    vassal_bonus = 0.0
    for vid in _vassal_polities(ctx, polity_id):
        vm = _fighting_age_males(ctx, vid, year)
        loyalty = 0.75
        for a in ctx.gov_alliances:
            if a.until_sim_year is not None:
                continue
            if a.kind == "vassal_oath" and {
                a.polity_a_id,
                a.polity_b_id,
            } == {polity_id, vid}:
                loyalty = max(0.2, min(1.0, a.loyalty_score))
                break
        vassal_bonus += len(vm) * levy_rate * VASSAL_LEVY_SHARE * loyalty * 3.0
    return max(1.0, (levy + merc) * (0.55 + officer * 0.9) + vassal_bonus)


def _random_strength(base: float, rng: random.Random) -> float:
    return base * (0.85 + 0.30 * rng.random())


def _drain_treasury_for_war(
    ctx: "SimulationContext", polity_id: int, amount: float
) -> float:
    regions = polity_regions(ctx, polity_id)
    if not regions:
        return 0.0
    per = float(amount) / len(regions)
    spent = 0.0
    for rid in regions:
        cur = float(ctx.region_treasury_balance.get(rid, 0.0))
        take = min(per, max(0.0, cur * 0.15))
        ctx.region_treasury_balance[rid] = cur - take
        spent += take
    return spent


def _persist_new_campaign(ctx: "SimulationContext", c: CampaignState) -> None:
    from library.world_save import _open_save

    with _open_save(ctx.save_db_path) as conn:
        gov_ckpt.ensure_government_schema(conn)
        gov_ckpt.insert_ongoing_campaign(
            conn,
            world=ctx.world.strip(),
            campaign={
                "campaign_id": c.campaign_id,
                "attacker_polity_id": c.attacker_polity_id,
                "defender_polity_id": c.defender_polity_id,
                "kind": c.kind,
                "objective": c.objective,
                "start_sim_year": c.start_sim_year,
                "outcome": c.outcome,
                "attacker_force_strength": c.attacker_force_strength,
                "defender_force_strength": c.defender_force_strength,
                "attacker_treasury_spent": c.attacker_treasury_spent,
                "defender_treasury_spent": c.defender_treasury_spent,
                "siege_target_settlement_id": c.siege_target_settlement_id,
                "siege_years": c.siege_years,
            },
        )
        conn.commit()


def roll_new_campaigns(
    ctx: "SimulationContext",
    year: int,
    *,
    catalog: GovernmentCatalog,
    composite_rows: tuple,
    rng: random.Random,
) -> None:
    """Start usurpation, conquest, vassal revolt, succession-war campaigns."""

    # Usurpation against holders of usurpable seats
    for seat in list(ctx.gov_office_seats.values()):
        if seat.holder_person_id is None:
            continue
        t = catalog.title_by_id(seat.title_id)
        if t is None or not t.can_be_usurped:
            continue
        holder = ctx.id_to_record.get(seat.holder_person_id)
        if holder is None:
            continue
        hq = military_quality_index(holder.person, composite_rows=composite_rows)
        rid = ctx._residence_region_id(holder)
        if not rid:
            continue
        st = ctx.settlements_by_id.get(
            (holder.person.current_settlement_id or holder.person.birthplace_settlement_id or "")
        )
        stability = float(st.stability) if st is not None else 0.5
        realm_total = max(1, len(realm_resident_ids(ctx, seat.polity_id)))
        claimants = realm_resident_decision_sample(
            ctx,
            seat.polity_id,
            year=year,
            scope=f"usurpation:seat:{int(seat.seat_id)}",
            stream=40_001,
        )
        for cand_id in sorted(int(rec.person_id) for rec in claimants):
            if cand_id == seat.holder_person_id:
                continue
            cr = ctx.id_to_record.get(cand_id)
            if cr is None:
                continue
            cq = military_quality_index(cr.person, composite_rows=composite_rows)
            if cq < 0.55:
                continue
            p = (
                float(t.usurp_base_chance)
                * USURP_ROLL_SCALE
                * cq
                / max(hq, 0.2)
                * (1.0 - stability)
                / realm_total
            )
            if rng.random() > p:
                continue
            if any(
                c.outcome == "ongoing"
                and c.objective.get("seat_id") == seat.seat_id
                for c in ctx.gov_campaigns
            ):
                continue
            cid = ctx.next_gov_campaign_id
            ctx.next_gov_campaign_id += 1
            ctx.gov_campaigns.append(
                CampaignState(
                    campaign_id=cid,
                    attacker_polity_id=seat.polity_id,
                    defender_polity_id=seat.polity_id,
                    kind="usurpation",
                    objective={"seat_id": seat.seat_id, "claimant_id": cand_id},
                    start_sim_year=year,
                    outcome="ongoing",
                )
            )
            _persist_new_campaign(ctx, ctx.gov_campaigns[-1])
            ctx._record_simulation_event(
                year,
                "campaign_started",
                {
                    "campaign_id": cid,
                    "kind": "usurpation",
                    "seat_id": seat.seat_id,
                    "claimant_id": cand_id,
                },
            )
            break

    # External conquest: sovereign picks weaker neighbor region polity
    sovereigns = [
        p
        for p in ctx.gov_polities.values()
        if p.parent_polity_id is None and (p.status or "").lower() == "active"
    ]
    for att in sovereigns:
        if rng.random() > CONQUEST_NEIGHBOR_PROB:
            continue
        my_regions = polity_regions(ctx, att.polity_id)
        if not my_regions:
            continue
        from library.geography import list_routes_from

        candidates: list[tuple[int, str]] = []
        for mr in my_regions:
            for route in list_routes_from(
                mr, world=ctx.world, db_path=ctx.db_path, simulation_year=year
            ):
                nrid = (route.to_region_id or "").strip()
                if not nrid or nrid in my_regions:
                    continue
                def_p = None
                for terr in ctx.gov_territory_rows:
                    if terr.target_kind == "region" and terr.target_id == nrid:
                        def_p = ctx.gov_polities.get(terr.polity_id)
                        break
                if def_p is None or def_p.polity_id == att.polity_id:
                    continue
                if def_p.parent_polity_id is not None:
                    continue
                candidates.append((def_p.polity_id, nrid))
        if not candidates:
            continue
        def_pid, nrid = min(candidates, key=lambda x: ctx.count_alive_in_region(x[1]))
        if any(
            c.outcome == "ongoing"
            and c.attacker_polity_id == att.polity_id
            and c.defender_polity_id == def_pid
            for c in ctx.gov_campaigns
        ):
            continue
        cid = ctx.next_gov_campaign_id
        ctx.next_gov_campaign_id += 1
        ctx.gov_campaigns.append(
            CampaignState(
                campaign_id=cid,
                attacker_polity_id=att.polity_id,
                defender_polity_id=def_pid,
                kind="conquest",
                objective={"target_region_id": nrid},
                start_sim_year=year,
                outcome="ongoing",
            )
        )
        _persist_new_campaign(ctx, ctx.gov_campaigns[-1])
        ctx._record_simulation_event(
            year,
            "campaign_started",
            {
                "campaign_id": cid,
                "kind": "conquest",
                "attacker_polity_id": att.polity_id,
                "defender_polity_id": def_pid,
                "target_region_id": nrid,
            },
        )

    # Vassal revolt when loyalty low
    for a in list(ctx.gov_alliances):
        if a.until_sim_year is not None or a.kind != "vassal_oath":
            continue
        if a.loyalty_score > 0.28:
            continue
        if rng.random() > 0.08:
            continue
        suzerain = a.polity_a_id
        vassal = a.polity_b_id
        if ctx.gov_polities.get(vassal) is None:
            continue
        if any(
            c.outcome == "ongoing"
            and c.kind == "vassal_revolt"
            and c.defender_polity_id == suzerain
            and c.attacker_polity_id == vassal
            for c in ctx.gov_campaigns
        ):
            continue
        cid = ctx.next_gov_campaign_id
        ctx.next_gov_campaign_id += 1
        ctx.gov_campaigns.append(
            CampaignState(
                campaign_id=cid,
                attacker_polity_id=vassal,
                defender_polity_id=suzerain,
                kind="vassal_revolt",
                objective={},
                start_sim_year=year,
                outcome="ongoing",
            )
        )
        _persist_new_campaign(ctx, ctx.gov_campaigns[-1])
        ctx._record_simulation_event(
            year,
            "campaign_started",
            {"campaign_id": cid, "kind": "vassal_revolt"},
        )


def advance_campaigns(
    ctx: "SimulationContext",
    year: int,
    *,
    catalog: GovernmentCatalog,
    composite_rows: tuple,
    rng: random.Random,
) -> None:
    from library.world_save import _open_save

    w = ctx.world.strip()
    ongoing_out: list[CampaignState] = []
    for camp in list(ctx.gov_campaigns):
        if (camp.outcome or "").strip().lower() != "ongoing":
            continue
        years_running = year - int(camp.start_sim_year) + 1
        spend_a = _drain_treasury_for_war(ctx, camp.attacker_polity_id, 0.45)
        spend_d = _drain_treasury_for_war(ctx, camp.defender_polity_id, 0.35)
        camp.attacker_treasury_spent += spend_a
        camp.defender_treasury_spent += spend_d
        fa = _compute_force_strength(
            ctx,
            camp.attacker_polity_id,
            year,
            composite_rows=composite_rows,
            treasury_spent=camp.attacker_treasury_spent,
            catalog=catalog,
        )
        fd = _compute_force_strength(
            ctx,
            camp.defender_polity_id,
            year,
            composite_rows=composite_rows,
            treasury_spent=camp.defender_treasury_spent,
            catalog=catalog,
        )
        fa = _random_strength(fa, rng)
        fd = _random_strength(fd, rng)
        loc_sid = None
        if camp.kind == "conquest":
            tr = camp.objective.get("target_region_id")
            if tr:
                act = ctx.active_settlements_in_region(str(tr))
                if act:
                    loc_sid = act[0].settlement_id
                    camp.siege_target_settlement_id = loc_sid
        if fa > fd * 1.08:
            battle_outcome = "attacker_field"
        elif fd > fa * 1.08:
            battle_outcome = "defender_field"
        else:
            battle_outcome = "siege_ongoing"
        cas_a = int(max(0, round(abs(fa - fd) * 0.04)))
        cas_d = int(max(0, round(abs(fd - fa) * 0.04)))
        if battle_outcome == "attacker_field":
            cas_d = max(cas_d, 1)
        elif battle_outcome == "defender_field":
            cas_a = max(cas_a, 1)
        _apply_casualties(ctx, year, camp.attacker_polity_id, cas_a, rng)
        _apply_casualties(ctx, year, camp.defender_polity_id, cas_d, rng)
        with _open_save(ctx.save_db_path) as conn:
            gov_ckpt.ensure_government_schema(conn)
            gov_ckpt.append_battle_row(
                conn,
                world=w,
                campaign_id=camp.campaign_id,
                sim_year=year,
                location_settlement_id=loc_sid,
                attacker_force_strength=fa,
                defender_force_strength=fd,
                attacker_casualties=cas_a,
                defender_casualties=cas_d,
                outcome=battle_outcome,
            )
            conn.commit()

        camp.attacker_force_strength = fa
        camp.defender_force_strength = fd
        if camp.kind == "conquest" and battle_outcome == "attacker_field":
            camp.siege_years = int(camp.siege_years) + 1
        elif camp.kind == "conquest":
            camp.siege_years = 0

        done = False
        if years_running >= MAX_CAMPAIGN_YEARS:
            camp.outcome = "stalemate"
            done = True
        elif fa < 2.0 and camp.attacker_treasury_spent > 5.0:
            camp.outcome = "attacker_collapsed"
            done = True
        elif camp.kind == "usurpation":
            seat_id = int(camp.objective.get("seat_id") or 0)
            claimant = int(camp.objective.get("claimant_id") or 0)
            seat = ctx.gov_office_seats.get(seat_id)
            if seat and battle_outcome == "attacker_field" and rng.random() < 0.35:
                _resolve_usurpation(ctx, year, seat, claimant, _job_era_key(ctx, year))
                camp.outcome = "attacker_won"
                done = True
            elif battle_outcome == "defender_field" and years_running >= 3:
                camp.outcome = "defender_won"
                done = True
        elif camp.kind == "conquest":
            tr = str(camp.objective.get("target_region_id") or "")
            if tr and int(camp.siege_years) >= 3 and battle_outcome == "attacker_field":
                _resolve_conquest_territory_transfer(
                    ctx, year, camp.attacker_polity_id, camp.defender_polity_id, tr
                )
                camp.outcome = "attacker_won"
                done = True
        elif camp.kind == "vassal_revolt":
            if battle_outcome == "attacker_field" and years_running >= 4:
                v = ctx.gov_polities.get(camp.attacker_polity_id)
                if v is not None:
                    ctx.gov_polities[camp.attacker_polity_id] = replace(
                        v, parent_polity_id=None
                    )
                camp.outcome = "attacker_won"
                done = True
            elif battle_outcome == "defender_field" and years_running >= 4:
                camp.outcome = "defender_won"
                done = True

        if done:
            with _open_save(ctx.save_db_path) as conn:
                gov_ckpt.ensure_government_schema(conn)
                gov_ckpt.update_campaign_row(
                    conn,
                    world=w,
                    campaign_id=camp.campaign_id,
                    fields={
                        "end_sim_year": year,
                        "outcome": camp.outcome,
                        "attacker_force_strength": fa,
                        "defender_force_strength": fd,
                        "attacker_treasury_spent": camp.attacker_treasury_spent,
                        "defender_treasury_spent": camp.defender_treasury_spent,
                        "siege_target_settlement_id": camp.siege_target_settlement_id,
                        "siege_years": camp.siege_years,
                    },
                )
                conn.commit()
            ctx._record_simulation_event(
                year,
                "campaign_ended",
                {
                    "campaign_id": camp.campaign_id,
                    "outcome": camp.outcome,
                },
            )
        else:
            ongoing_out.append(camp)

    ctx.gov_campaigns = ongoing_out


def _apply_casualties(
    ctx: "SimulationContext",
    year: int,
    polity_id: int,
    count: int,
    rng: random.Random,
) -> None:
    if count <= 0:
        return
    pool = _fighting_age_males(ctx, polity_id, year)
    if not pool:
        return
    rng.shuffle(pool)
    dead = set(pool[: min(count, len(pool))])
    if dead:
        ctx.mark_dead(dead, deathyear=year)


def _resolve_usurpation(
    ctx: "SimulationContext",
    year: int,
    seat: "OfficeSeatState",
    claimant_id: int,
    era_key: str,
) -> None:
    import library.simulation_government as sg
    from library.government_catalog import display_title_name, load_government_catalog

    catalog = load_government_catalog(ctx.db_path)
    old = seat.holder_person_id
    if old:
        sg.vacate_seat(ctx, seat, year, end_reason="usurped")
    t = catalog.title_by_id(seat.title_id)
    name = display_title_name(t, era_key) if t else seat.title_id
    sg.assign_holder(ctx, seat, claimant_id, year, display_job=name)


def _resolve_conquest_territory_transfer(
    ctx: "SimulationContext",
    year: int,
    attacker_pid: int,
    defender_pid: int,
    region_id: str,
) -> None:
    """Transfer a conquered region row, or a single settlement row if the defender is settlement-grain."""
    rid = (region_id or "").strip()
    if not rid:
        return
    has_region = any(
        t.polity_id == defender_pid
        and (t.target_kind or "").strip().lower() == "region"
        and t.target_id == rid
        for t in ctx.gov_territory_rows
    )
    if has_region:
        _transfer_region(ctx, year, attacker_pid, defender_pid, rid)
        return
    for t in list(ctx.gov_territory_rows):
        if t.polity_id != defender_pid:
            continue
        if (t.target_kind or "").strip().lower() != "settlement":
            continue
        sid = str(t.target_id or "").strip()
        st = ctx.settlements_by_id.get(sid)
        if st is not None and (st.region_id or "").strip() == rid:
            _transfer_settlement_territory(
                ctx, year, attacker_pid, defender_pid, sid
            )
            return


def _transfer_settlement_territory(
    ctx: "SimulationContext",
    year: int,
    attacker_pid: int,
    defender_pid: int,
    settlement_id: str,
) -> None:
    w = ctx.world.strip()
    from library.polity import TerritoryOpenRow

    sid = (settlement_id or "").strip()
    if not sid:
        return
    new_rows: list[TerritoryOpenRow] = []
    removed = False
    with __import__("library.world_save", fromlist=["_open_save"])._open_save(
        ctx.save_db_path
    ) as conn:
        gov_ckpt.ensure_government_schema(conn)
        gov_ckpt.close_territory_row(
            conn,
            world=w,
            polity_id=defender_pid,
            target_kind="settlement",
            target_id=sid,
            until_sim_year=year,
        )
        gov_ckpt.insert_territory_open(
            conn,
            world=w,
            polity_id=attacker_pid,
            target_kind="settlement",
            target_id=sid,
            since_sim_year=year,
        )
        conn.commit()
    for t in list(ctx.gov_territory_rows):
        if (
            t.polity_id == defender_pid
            and (t.target_kind or "").strip().lower() == "settlement"
            and t.target_id == sid
        ):
            removed = True
            continue
        new_rows.append(t)
    if removed:
        new_rows.append(
            TerritoryOpenRow(
                polity_id=attacker_pid,
                target_kind="settlement",
                target_id=sid,
                since_sim_year=year,
            )
        )
    ctx.gov_territory_rows = new_rows


def _transfer_region(
    ctx: "SimulationContext",
    year: int,
    attacker_pid: int,
    defender_pid: int,
    region_id: str,
) -> None:
    w = ctx.world.strip()
    from library.polity import TerritoryOpenRow

    new_rows: list[TerritoryOpenRow] = []
    removed = False
    with __import__("library.world_save", fromlist=["_open_save"])._open_save(
        ctx.save_db_path
    ) as conn:
        gov_ckpt.ensure_government_schema(conn)
        gov_ckpt.close_territory_row(
            conn,
            world=w,
            polity_id=defender_pid,
            target_kind="region",
            target_id=region_id,
            until_sim_year=year,
        )
        gov_ckpt.insert_territory_open(
            conn,
            world=w,
            polity_id=attacker_pid,
            target_kind="region",
            target_id=region_id,
            since_sim_year=year,
        )
        conn.commit()
    for t in list(ctx.gov_territory_rows):
        if (
            t.polity_id == defender_pid
            and t.target_kind == "region"
            and t.target_id == region_id
        ):
            removed = True
            continue
        new_rows.append(t)
    if removed:
        new_rows.append(
            TerritoryOpenRow(
                polity_id=attacker_pid,
                target_kind="region",
                target_id=region_id,
                since_sim_year=year,
            )
        )
    ctx.gov_territory_rows = new_rows


def simulation_warfare_annual_tick(
    ctx: "SimulationContext",
    year: int,
    *,
    catalog: GovernmentCatalog,
    composite_rows: tuple,
    rng: random.Random,
) -> None:
    advance_campaigns(ctx, year, catalog=catalog, composite_rows=composite_rows, rng=rng)
    roll_new_campaigns(ctx, year, catalog=catalog, composite_rows=composite_rows, rng=rng)


def decay_alliance_loyalty(ctx: "SimulationContext", rng: random.Random) -> None:
    out: list = []
    from library.polity import AllianceState

    for a in ctx.gov_alliances:
        if a.until_sim_year is not None:
            out.append(a)
            continue
        if a.kind == "vassal_oath":
            delta = 0.02 + rng.random() * 0.03
            new_loy = max(0.0, a.loyalty_score - delta)
            out.append(
                AllianceState(
                    alliance_id=a.alliance_id,
                    polity_a_id=a.polity_a_id,
                    polity_b_id=a.polity_b_id,
                    kind=a.kind,
                    since_sim_year=a.since_sim_year,
                    until_sim_year=a.until_sim_year,
                    payload=a.payload,
                    loyalty_score=new_loy,
                )
            )
        elif a.kind == "marriage" and a.payload.get("heir_bonus"):
            out.append(
                AllianceState(
                    alliance_id=a.alliance_id,
                    polity_a_id=a.polity_a_id,
                    polity_b_id=a.polity_b_id,
                    kind=a.kind,
                    since_sim_year=a.since_sim_year,
                    until_sim_year=a.until_sim_year,
                    payload=a.payload,
                    loyalty_score=min(1.0, a.loyalty_score + 0.01),
                )
            )
        else:
            out.append(a)
    ctx.gov_alliances = out


def roll_marriage_alliances(ctx: "SimulationContext", year: int, rng: random.Random) -> None:
    """Link allied polities when heirs of rulers marry (simplified)."""
    from library.polity import AllianceState

    heads: list[tuple[int, int]] = []
    for s in ctx.gov_office_seats.values():
        t = load_government_catalog(ctx.db_path).title_by_id(s.title_id)
        if t is None or (t.role or "").strip().lower() != "head":
            continue
        if s.holder_person_id is None:
            continue
        rec = ctx.id_to_record.get(s.holder_person_id)
        if rec is None or rec.person.partner_person_id is None:
            continue
        partner = ctx.id_to_record.get(rec.person.partner_person_id)
        if partner is None:
            continue
        for s2 in ctx.gov_office_seats.values():
            if s2.seat_id == s.seat_id or s2.holder_person_id != partner.person_id:
                continue
            t2 = load_government_catalog(ctx.db_path).title_by_id(s2.title_id)
            if t2 is None or (t2.role or "").strip().lower() != "head":
                continue
            if s2.polity_id == s.polity_id:
                continue
            heads.append((s.polity_id, s2.polity_id))
    for a_id, b_id in heads:
        if rng.random() > 0.06:
            continue
        if any(
            x.until_sim_year is None
            and {x.polity_a_id, x.polity_b_id} == {a_id, b_id}
            and x.kind == "marriage"
            for x in ctx.gov_alliances
        ):
            continue
        aid = ctx.next_gov_alliance_id
        ctx.next_gov_alliance_id += 1
        ctx.gov_alliances.append(
            AllianceState(
                alliance_id=aid,
                polity_a_id=min(a_id, b_id),
                polity_b_id=max(a_id, b_id),
                kind="marriage",
                since_sim_year=year,
                payload={"heir_bonus": True},
                loyalty_score=0.85,
            )
        )
        ctx._record_simulation_event(
            year,
            "dynastic_marriage_alliance",
            {"alliance_id": aid, "polity_a_id": a_id, "polity_b_id": b_id},
        )


def roll_contested_succession_war(
    ctx: "SimulationContext",
    year: int,
    *,
    claimants: list[int],
    polity_id: int,
    rng: random.Random,
) -> None:
    if len(claimants) < 2:
        return
    if rng.random() > 0.12:
        return
    a, b = claimants[0], claimants[1]
    cid = ctx.next_gov_campaign_id
    ctx.next_gov_campaign_id += 1
    ctx.gov_campaigns.append(
        CampaignState(
            campaign_id=cid,
            attacker_polity_id=polity_id,
            defender_polity_id=polity_id,
            kind="civil_war",
            objective={"claimant_a": a, "claimant_b": b},
            start_sim_year=year,
            outcome="ongoing",
        )
    )
    _persist_new_campaign(ctx, ctx.gov_campaigns[-1])
    ctx._record_simulation_event(
        year,
        "campaign_started",
        {"campaign_id": cid, "kind": "civil_war", "polity_id": polity_id},
    )
