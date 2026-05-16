"""Population-growth scenario: founder couples, births, pairing, mortality, yearly summaries."""

from __future__ import annotations

import json
import os
import random
import secrets
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

from library.generator import generate_person_random
from library.settlements import SettlementState
from library.reproduction import (
    annual_conception_probability,
    conception_rng,
    having_sex_birth_event,
)
from library.simulation_careers import resource_pressure_for_person
from library import simulation_timing
from library.simulation_context import SimulationContext, SimulationPersonRecord
from library.simulation_export import people_export_payload, settlements_geo_export_payload
from library.simulation_mortality import apply_annual_mortality

KIN_PAIR_PARENT_CHILD_PROB = 0.000001
KIN_PAIR_GRANDPARENT_GRANDCHILD_PROB = 0.000002
KIN_PAIR_FULL_SIBLING_PROB = 0.000005
KIN_PAIR_HALF_SIBLING_PROB = 0.00002
KIN_PAIR_RNG_STREAM = 612_047


def resolve_population_sim_seed() -> int:
    raw = os.environ.get("POPULATION_GROWTH_SIM_SEED")
    if raw is not None and str(raw).strip() != "":
        return int(raw)
    s = secrets.randbelow(2_147_483_647)
    os.environ["POPULATION_GROWTH_SIM_SEED"] = str(s)
    return s


def _is_mature(partner: SimulationPersonRecord, year: int) -> bool:
    if partner.person.deathyear is not None and int(partner.person.deathyear) <= int(year):
        return False
    age = year - int(partner.person.birthyear)
    min_fertility_age = partner.person.min_fertility_age
    if min_fertility_age is None:
        return True
    return age >= int(min_fertility_age)


def _eligible_for_birth(partner: SimulationPersonRecord, year: int) -> bool:
    if partner.person.deathyear is not None and int(partner.person.deathyear) <= int(year):
        return False
    age = year - int(partner.person.birthyear)
    if not _is_mature(partner, year):
        return False
    max_fertility_age = partner.person.max_fertility_age
    if max_fertility_age is not None and age > int(max_fertility_age):
        return False
    return True


def _parents_of(rec: SimulationPersonRecord) -> set[int]:
    return {int(pid) for pid in (rec.father_id, rec.mother_id) if pid is not None}


def _is_parent_child(a: SimulationPersonRecord, b: SimulationPersonRecord) -> bool:
    return int(a.person_id) in _parents_of(b) or int(b.person_id) in _parents_of(a)


def _is_grandparent_grandchild(
    ctx: SimulationContext, a: SimulationPersonRecord, b: SimulationPersonRecord
) -> bool:
    def grandparents_of(rec: SimulationPersonRecord) -> set[int]:
        out: set[int] = set()
        for parent_id in _parents_of(rec):
            parent = ctx.id_to_record.get(parent_id)
            if parent is not None:
                out.update(_parents_of(parent))
        return out

    return int(a.person_id) in grandparents_of(b) or int(b.person_id) in grandparents_of(a)


def _sibling_kind(a: SimulationPersonRecord, b: SimulationPersonRecord) -> str | None:
    shared = _parents_of(a).intersection(_parents_of(b))
    if len(shared) >= 2:
        return "full_sibling"
    if len(shared) == 1:
        return "half_sibling"
    return None


def _close_kin_pairing_exception_probability(
    ctx: SimulationContext, a: SimulationPersonRecord, b: SimulationPersonRecord
) -> tuple[str | None, float]:
    if _is_parent_child(a, b):
        return "parent_child", KIN_PAIR_PARENT_CHILD_PROB
    if _is_grandparent_grandchild(ctx, a, b):
        return "grandparent_grandchild", KIN_PAIR_GRANDPARENT_GRANDCHILD_PROB
    sibling = _sibling_kind(a, b)
    if sibling == "full_sibling":
        return sibling, KIN_PAIR_FULL_SIBLING_PROB
    if sibling == "half_sibling":
        return sibling, KIN_PAIR_HALF_SIBLING_PROB
    return None, 1.0


def _kin_pairing_rng(ctx: SimulationContext, year: int, a_id: int, b_id: int) -> random.Random:
    lo, hi = sorted((int(a_id), int(b_id)))
    return random.Random(
        int(year) * KIN_PAIR_RNG_STREAM
        + int(getattr(ctx, "placename_rng_salt", 0)) * 37
        + lo * 10_033
        + hi
    )


def _pairing_allowed_by_kinship(
    ctx: SimulationContext, year: int, a: SimulationPersonRecord, b: SimulationPersonRecord
) -> tuple[bool, str | None, float | None]:
    relation, probability = _close_kin_pairing_exception_probability(ctx, a, b)
    if relation is None:
        return True, None, None
    rng = _kin_pairing_rng(ctx, year, int(a.person_id), int(b.person_id))
    return rng.random() < probability, relation, probability


def _format_government_report_appendix(ctx: SimulationContext) -> list[str]:
    """Short polity / ruler / campaign summary from end-of-run RAM (optional report block)."""
    pols = getattr(ctx, "gov_polities", None) or {}
    seats = getattr(ctx, "gov_office_seats", None) or {}
    terr = getattr(ctx, "gov_territory_rows", None) or []
    camps = getattr(ctx, "gov_campaigns", None) or []
    lines: list[str] = [
        "",
        "Government (end-of-run RAM state)",
        "----------------------------",
        f"Polities loaded: {len(pols)}",
        f"Open territory rows: {len(terr)}",
        f"Office seats: {len(seats)}",
        f"Campaigns (all outcomes): {len(camps)}",
    ]
    active_pol = [
        p for p in pols.values() if (getattr(p, "status", "") or "").strip().lower() == "active"
    ]
    lines.append(f"Active polities: {len(active_pol)}")
    for pol in sorted(active_pol, key=lambda p: int(p.polity_id)):
        lines.append(
            " | ".join(
                (
                    f"polity_id={pol.polity_id}",
                    f"name={pol.name}",
                    f"type={pol.polity_type_id}",
                    f"parent={pol.parent_polity_id}",
                    f"founded={pol.founded_sim_year}",
                )
            )
        )
    held = [s for s in seats.values() if s.holder_person_id is not None]
    lines.append(f"Seats with holder: {len(held)}")
    for s in sorted(held, key=lambda x: int(x.seat_id)):
        rec = ctx.id_to_record.get(int(s.holder_person_id or 0))
        nm = rec.person.full_name if rec is not None else "?"
        lines.append(
            " | ".join(
                (
                    f"seat_id={s.seat_id}",
                    f"polity_id={s.polity_id}",
                    f"title_id={s.title_id}",
                    f"holder_id={s.holder_person_id}",
                    f"holder_name={nm}",
                )
            )
        )
    ongoing = [
        c for c in camps if (getattr(c, "outcome", "") or "").strip().lower() == "ongoing"
    ]
    lines.append(f"Campaigns ongoing: {len(ongoing)}")
    for c in sorted(ongoing, key=lambda x: int(x.campaign_id)):
        lines.append(
            " | ".join(
                (
                    f"campaign_id={c.campaign_id}",
                    f"kind={c.kind}",
                    f"attacker={c.attacker_polity_id}",
                    f"defender={c.defender_polity_id}",
                    f"started={c.start_sim_year}",
                )
            )
        )
    return lines


def _geo_summary(local_geography_json: str | None) -> str:
    if not local_geography_json:
        return ""
    try:
        d = json.loads(local_geography_json)
        nf = len(d.get("features") or [])
        ns = len(d.get("settlements") or [])
        ne = len(d.get("edges") or [])
        return f" abstract_geo[features={nf} pins={ns} edges={ne}]"
    except json.JSONDecodeError:
        return " abstract_geo[invalid_json]"


def build_population_growth_report(
    people: list[SimulationPersonRecord],
    couples: list[tuple[int, int]],
    settlements_by_id: dict[str, SettlementState],
    *,
    random_seed: int,
    start_year: int,
    duration_years: int,
    ctx: SimulationContext | None = None,
) -> str:
    couples_by_member: dict[int, int] = {}
    for a_id, b_id in couples:
        couples_by_member[a_id] = b_id
        couples_by_member[b_id] = a_id

    def cur_residence_region(rec: SimulationPersonRecord) -> str:
        p = rec.person
        sid = (p.current_settlement_id or p.birthplace_settlement_id or "").strip()
        if not sid:
            return ""
        st = settlements_by_id.get(sid)
        if st is not None:
            return (st.region_id or "").strip()
        if ":" in sid:
            return sid.split(":")[0].strip()
        return ""

    lines: list[str] = []
    lines.append("Population Growth Simulation Report")
    lines.append(f"Random seed: {random_seed}")
    lines.append(f"Start year: {start_year}")
    lines.append(f"Duration: {duration_years} years")
    lines.append(f"Total people: {len(people)}")
    lines.append(f"Total couples: {len(couples)}")
    if people:
        end_year = start_year + duration_years - 1
        alive = [
            rec
            for rec in people
            if rec.person.deathyear is None or int(rec.person.deathyear) > end_year
        ]
        dead = [
            rec
            for rec in people
            if rec.person.deathyear is not None and int(rec.person.deathyear) <= end_year
        ]
        lines.append(f"Alive at end year: {len(alive)}")
        lines.append(f"Dead by end year: {len(dead)}")
        if alive:
            alive_cross = sum(
                1
                for rec in alive
                if (cur_residence_region(rec) or "").strip()
                != (rec.person.birthplace_region_id or "").strip()
            )
            lines.append(
                f"Alive with residence region != birth region (incl. migration): {alive_cross}"
            )
            alive_avg = sum(end_year - int(rec.person.birthyear) for rec in alive) / len(alive)
            lines.append(f"Average age alive at end year: {alive_avg:.2f}")
        if dead:
            dead_avg = (
                sum(int(rec.person.deathyear or 0) - int(rec.person.birthyear) for rec in dead)
                / len(dead)
            )
            lines.append(f"Average age at death: {dead_avg:.2f}")
    lines.append("")
    lines.append("People")
    lines.append("------")

    for rec in people:
        p = rec.person
        partner_id = couples_by_member.get(rec.person_id)
        cur_reg = cur_residence_region(rec)
        lines.append(
            " | ".join(
                (
                    f"id={rec.person_id}",
                    f"name={p.full_name}",
                    f"gender={p.gender}",
                    f"species={p.species}",
                    f"ethnic={p.ethnic}",
                    f"birthyear={p.birthyear}",
                    f"deathyear={p.deathyear}",
                    f"birthplace={p.birthplace}",
                    f"birth_region={p.birthplace_region_id}",
                    f"birth_settlement={p.birthplace_settlement_id}",
                    f"current_region={cur_reg}",
                    f"current_settlement={p.current_settlement_id}",
                    f"founder={rec.is_founder}",
                    f"father_id={rec.father_id}",
                    f"mother_id={rec.mother_id}",
                    f"partner_id={partner_id}",
                )
            )
        )
    lines.append("")
    lines.append("Places (generated settlement names + abstract local geography)")
    lines.append("--------------------------------------------------------------")
    for sid in sorted(settlements_by_id.keys()):
        st = settlements_by_id[sid]
        rid = st.region_id
        dn = st.display_name or ""
        et = st.etymology or ""
        geo = _geo_summary(st.local_geography_json)
        lines.append(
            " | ".join(
                (
                    f"settlement_id={sid}",
                    f"region_id={rid}",
                    f"region_display_name={st.region_display_name}",
                    f"display_name={dn}",
                    f"level={st.level}",
                    f"resident_count={st.resident_count}",
                    f"household_cap={st.household_cap}",
                    f"name_category_primary={st.name_category_primary}",
                    f"name_category_secondary={st.name_category_secondary}",
                    f"name_culture_primary={st.name_culture_primary}",
                    f"name_culture_secondary={st.name_culture_secondary}",
                    f"etymology={et}",
                )
            )
            + geo
        )
    if ctx is not None:
        lines.extend(_format_government_report_appendix(ctx))
    return "\n".join(lines) + "\n"


def _pair_from_records(
    ctx: SimulationContext,
    records: list[SimulationPersonRecord],
    year: int,
    paired_ids: set[int],
) -> None:
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
    remaining_females = list(eligible_females)
    for male in eligible_males:
        chosen: tuple[SimulationPersonRecord, str | None, float | None] | None = None
        for female in remaining_females:
            allowed, relation, probability = _pairing_allowed_by_kinship(
                ctx, year, male, female
            )
            if allowed:
                chosen = (female, relation, probability)
                break
        if chosen is None:
            continue
        female, relation, probability = chosen
        a_id = male.person_id
        b_id = female.person_id
        ctx.add_couple(a_id, b_id)
        if relation is not None:
            ctx._pending_simulation_events[-1][2].update(
                {
                    "kinship_exception": relation,
                    "kinship_exception_probability": probability,
                }
            )
        paired_ids.add(a_id)
        paired_ids.add(b_id)
        remaining_females.remove(female)


def pair_people_by_settlement_then_region(
    ctx: SimulationContext,
    year: int,
    by_settlement: dict[str, list[SimulationPersonRecord]],
) -> None:
    """Pair local residents first, then use same-region fallback without world-global lists.

    New pairs are opposite-sex only (one mature male, one mature female per pair).
    Same-sex official couples are formed in ``library.simulation_social`` instead.
    """
    paired_ids = {pid for pair in ctx.couples for pid in pair}
    for sid in sorted(by_settlement.keys()):
        _pair_from_records(ctx, by_settlement[sid], year, paired_ids)

    by_region: dict[str, list[SimulationPersonRecord]] = {}
    for sid in sorted(by_settlement.keys()):
        records = by_settlement[sid]
        rid = ""
        st = ctx.settlements_by_id.get(sid)
        if st is not None:
            rid = (st.region_id or "").strip()
        if not rid and records:
            rid = (ctx._residence_region_id(records[0]) or "").strip()
        if not rid:
            continue
        for rec in records:
            if rec.person_id not in paired_ids:
                by_region.setdefault(rid, []).append(rec)

    for rid in sorted(by_region.keys()):
        _pair_from_records(ctx, by_region[rid], year, paired_ids)


def births_by_settlement(
    ctx: SimulationContext,
    year: int,
    *,
    sim_seed: int,
    by_settlement: dict[str, list[SimulationPersonRecord]],
) -> int:
    """Run birth attempts by settlement id, then mother person id.

    Genetic births require a **female** mother with a **male** ``partner_person_id`` or
    ``paramour_person_id`` who passes the same fertility gate. Same-sex marriages
    (``add_couple``) do **not** supply a male genetic parent between spouses; a female
    spouse alone does not enable conception without a separate male partner/paramour.
    """
    births_count = 0
    rng = random.Random(year * 1_000_003 + sim_seed)
    for sid in sorted(by_settlement.keys()):
        mothers_this_year = [
            r
            for r in by_settlement[sid]
            if (r.person.gender or "").strip() == "Female"
        ]
        for rec in mothers_this_year:
            if not _eligible_for_birth(rec, year):
                continue
            if rec.person.last_birth_event_year == year:
                continue
            candidates: list[int] = []
            pid = rec.person.partner_person_id
            mid = rec.person.paramour_person_id
            if pid is not None and ctx.is_alive(pid):
                pr = ctx.id_to_record.get(pid)
                if pr is not None and _eligible_for_birth(pr, year):
                    candidates.append(pid)
            if mid is not None and ctx.is_alive(mid):
                mr = ctx.id_to_record.get(mid)
                if mr is not None and _eligible_for_birth(mr, year):
                    candidates.append(mid)
            if not candidates:
                continue
            father_id = rng.choice(candidates)
            father = ctx.id_to_record[father_id]
            pressure = resource_pressure_for_person(ctx, rec)
            p_try = annual_conception_probability(
                rec.person, father.person, pressure=pressure
            )
            crng = conception_rng(year, sim_seed, rec.person_id, father_id)
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
                births_count += 1
    return births_count


def run_population_growth_simulation(
    ctx: SimulationContext,
    *,
    sim_seed: int,
    start_year: int,
    duration_years: int,
    starting_couples: int,
) -> None:
    """Drive the canonical population-growth yearly loop until ``finalize_run`` (context exit)."""
    random.seed(sim_seed)
    np.random.seed(int(sim_seed) % (2**32))
    simulation_timing.configure_profile_window(
        start_year=start_year, duration_years=duration_years
    )

    for _ in range(starting_couples):
        male = generate_person_random(
            gender="Male",
            age=18,
            simulation_year=start_year,
            simulation_context=ctx,
        )
        female = generate_person_random(
            gender="Female",
            age=18,
            simulation_year=start_year,
            simulation_context=ctx,
        )
        male_rec = ctx.add_person(person=male, is_founder=True)
        female_rec = ctx.add_person(person=female, is_founder=True)
        ctx.add_couple(male_rec.person_id, female_rec.person_id)

    end_exclusive = start_year + duration_years
    for year in range(start_year, end_exclusive):
        ctx.current_year = year
        births_count = 0
        prof = simulation_timing.active_for_year(year)
        tpc = time.perf_counter

        if prof:
            t0 = tpc()
        people_by_settlement = ctx.current_people_by_settlement()
        pair_people_by_settlement_then_region(ctx, year, people_by_settlement)
        if prof:
            simulation_timing.accumulate("runner.pairing", tpc() - t0)

        if prof:
            t0 = tpc()
        births_count = births_by_settlement(
            ctx, year, sim_seed=sim_seed, by_settlement=people_by_settlement
        )
        if prof:
            simulation_timing.accumulate("runner.births", tpc() - t0)

        if prof:
            t0 = tpc()
        mortality_rates = apply_annual_mortality(ctx, year)
        if prof:
            simulation_timing.accumulate("runner.mortality", tpc() - t0)

        ctx.record_year_summary(
            year=year,
            births_count=births_count,
            deaths_count=int(mortality_rates["deaths_count"]),
            mortality_rates=mortality_rates,
        )

    simulation_timing.print_report_if_configured()


def write_population_growth_report_files(
    ctx: SimulationContext,
    *,
    sim_seed: int,
    start_year: int,
    duration_years: int,
    output_path: Path,
    people_json_path: Path,
    places_geo_path: Path,
) -> None:
    output_path.write_text(
        build_population_growth_report(
            ctx.people,
            ctx.couples,
            ctx.settlements_by_id,
            random_seed=sim_seed,
            start_year=start_year,
            duration_years=duration_years,
            ctx=ctx,
        ),
        encoding="utf-8",
    )
    end_exclusive = start_year + duration_years
    people_json_path.write_text(
        json.dumps(
            people_export_payload(
                ctx.people,
                random_seed=sim_seed,
                simulation_start_year=start_year,
                simulation_end_year_exclusive=end_exclusive,
            ),
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    places_geo_path.write_text(
        json.dumps(
            settlements_geo_export_payload(ctx.settlements_by_id),
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
