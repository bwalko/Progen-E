"""Annual social dynamics: paramour formation, geography-based dissolution, same-sex couples."""

from __future__ import annotations

import random
from collections import deque
from typing import TYPE_CHECKING

from library.geography import _get_route_edges_by_origin
from library.mind_body import attractiveness_01 as attractiveness_score
from library.mind_body import work_trait_values
from library.person import Person
from library.population_growth_runner import _is_mature
from library.reproduction import pair_prosperity_01
from library.simulation_careers import resource_pressure_for_person

if TYPE_CHECKING:
    from library.simulation_context import SimulationContext

# Separation between two alive persons' residence settlements (0 = same site).
PARAMOUR_MAX_SEPARATION_TO_SURVIVE = 2
PARAMOUR_FORMATION_TRIAL_PROB = 0.04
PARAMOUR_END_BASE_PROB = 0.025
PARAMOUR_END_MAX_PROB = 0.55
PARAMOUR_END_RNG_STREAM = 709_367
PARAMOUR_PROMOTION_MAX_PROB = 0.42
PARAMOUR_PROMOTION_RNG_STREAM = 811_531
# With ``min_fertility_age`` set, paramour age floor is ``min(PARAMOUR_MIN_SIM_AGE, mf)``; if unset,
# only ``PARAMOUR_MIN_SIM_AGE`` applies.
PARAMOUR_MIN_SIM_AGE = 18

# Official partners: stable by default, but stacked stressors can end the partnership.
PARTNER_BREAKUP_BASE_PROB = 0.0015
PARTNER_BREAKUP_MAX_PROB = 0.35
PARTNER_BREAKUP_RNG_STREAM = 507_331

# Same-sex official couples: romantic score only, prosperity-scaled, extra social/biological friction.
SAME_SEX_SOCIAL_FRICTION = 0.18
SAME_SEX_MAX_TRIALS_PER_SETTLEMENT = 48
SAME_SEX_RNG_STREAM = 904_007


def _settlement_region_id(ctx: SimulationContext, settlement_id: str) -> str | None:
    sid = (settlement_id or "").strip()
    if not sid:
        return None
    st = ctx.settlements_by_id.get(sid)
    return (st.region_id or "").strip() or None if st is not None else None


def region_hops(world: str, db_path, region_a: str, region_b: str) -> int:
    """Shortest hop count between regions on the route graph (0 = same)."""
    ra = (region_a or "").strip()
    rb = (region_b or "").strip()
    if not ra or not rb:
        return 10**6
    if ra == rb:
        return 0
    by_origin = _get_route_edges_by_origin(world.strip(), db_path)
    dist: dict[str, int] = {ra: 0}
    dq: deque[str] = deque([ra])
    while dq:
        u = dq.popleft()
        d_u = dist[u]
        for edge in by_origin.get(u, ()):
            v = (edge.to_region_id or "").strip()
            if not v:
                continue
            if v == rb:
                return d_u + 1
            if v not in dist:
                dist[v] = d_u + 1
                dq.append(v)
    return 10**6


def settlement_separation(ctx: SimulationContext, person_a_id: int, person_b_id: int) -> int:
    """0 same settlement; 1 same region different site; else 1 + inter-region hops."""
    ra = ctx.id_to_record.get(person_a_id)
    rb = ctx.id_to_record.get(person_b_id)
    if ra is None or rb is None:
        return 10**6
    sa = (ra.person.current_settlement_id or ra.person.birthplace_settlement_id or "").strip()
    sb = (rb.person.current_settlement_id or rb.person.birthplace_settlement_id or "").strip()
    if not sa or not sb:
        return 10**6
    if sa == sb:
        return 0
    reg_a = _settlement_region_id(ctx, sa)
    reg_b = _settlement_region_id(ctx, sb)
    if not reg_a or not reg_b:
        return 10**6
    if reg_a == reg_b:
        return 1
    hops = region_hops(ctx.world, ctx.db_path, reg_a, reg_b)
    if hops >= 10**5:
        return 10**6
    return 1 + hops


def _paramour_minimum_age_years(rec) -> int:
    """Minimum age for paramour ties: ``min(PARAMOUR_MIN_SIM_AGE, min_fertility_age)`` when set."""
    mf = rec.person.min_fertility_age
    if mf is None:
        return PARAMOUR_MIN_SIM_AGE
    return min(PARAMOUR_MIN_SIM_AGE, int(mf))


def paramour_individual_eligible(rec, year: int) -> bool:
    """Alive through ``year`` and at or past the paramour age floor for that year."""
    if rec.person.deathyear is not None and int(rec.person.deathyear) <= int(year):
        return False
    age = int(year) - int(rec.person.birthyear)
    return age >= _paramour_minimum_age_years(rec)


def paramour_pair_eligible(ra, rb, year: int) -> bool:
    """Both adults for paramour purposes and not parent/child or full siblings."""
    if not paramour_individual_eligible(ra, year) or not paramour_individual_eligible(rb, year):
        return False
    return not _close_kin_blocked(ra, rb)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _trait(person: Person, key: str) -> float:
    return float(work_trait_values(person).get(key, 0.0))


def _deviation_01(person: Person, key: str) -> float:
    return _clamp01(abs(_trait(person, key)) / 100.0)


def _positive_trait_01(person: Person, key: str) -> float:
    return _clamp01((100.0 + _trait(person, key)) / 200.0)


def _negative_trait_01(person: Person, key: str) -> float:
    return _clamp01((100.0 - _trait(person, key)) / 200.0)


def _sexual_nature(person: Person) -> str:
    return (person.sexual_nature or "heterosexual").strip().lower()


def _same_gender(a: Person, b: Person) -> bool:
    return (a.gender or "").strip().lower() == (b.gender or "").strip().lower()


def _has_opposite_sex_partner(ctx: SimulationContext, person: Person) -> bool:
    pid = person.partner_person_id
    if pid is None:
        return False
    rec = ctx.id_to_record.get(int(pid))
    if rec is None:
        return False
    return not _same_gender(person, rec.person)


def _paramour_impulse_01(person: Person) -> float:
    """Interest in paramour formation: libido raises it; loyalty near ideal suppresses it."""
    mating = _positive_trait_01(person, "mating drive")
    loyalty_deviation = _deviation_01(person, "loyalty")
    disloyal_pull = _negative_trait_01(person, "loyalty")
    # Near-ideal loyalty is the main brake. Explicit disloyalty adds extra risk;
    # sycophantic excess is less protective than true commitment.
    loyalty_risk = 0.15 + 0.55 * loyalty_deviation + 0.30 * disloyal_pull
    return _clamp01(mating * loyalty_risk)


def _paramour_orientation_multiplier(
    ctx: SimulationContext | None, a: Person, b: Person
) -> float:
    """Orientation weighting for paramour formation without making bisexuality exotic."""
    same = _same_gender(a, b)
    na = _sexual_nature(a)
    nb = _sexual_nature(b)
    if same:
        if "heterosexual" in (na, nb):
            return 0.06
        multiplier = 1.0
        if na == "homosexual":
            multiplier *= 2.0
        if nb == "homosexual":
            multiplier *= 2.0
        if ctx is not None and na == "homosexual" and _has_opposite_sex_partner(ctx, a):
            multiplier *= 1.65
        if ctx is not None and nb == "homosexual" and _has_opposite_sex_partner(ctx, b):
            multiplier *= 1.65
        return min(7.0, multiplier)
    if na == "homosexual" or nb == "homosexual":
        return 0.08
    return 1.0


def _paramour_pair_probability(
    a: Person, b: Person, ctx: SimulationContext | None = None
) -> float:
    impulse = (_paramour_impulse_01(a) + _paramour_impulse_01(b)) / 2.0
    return min(
        0.28,
        PARAMOUR_FORMATION_TRIAL_PROB
        * impulse
        * _paramour_orientation_multiplier(ctx, a, b),
    )


def _paramour_bond_score_01(ctx: SimulationContext, ra, rb, year: int) -> float:
    pa, pb = ra.person, rb.person
    romantic = _romantic_infatuation_score(pa, pb, int(year))
    prosperity = pair_prosperity_01(
        pa,
        pb,
        pressure_a=resource_pressure_for_person(ctx, ra),
        pressure_b=resource_pressure_for_person(ctx, rb),
    )
    stability = 1.0 - (
        _deviation_01(pa, "neurochemical") + _deviation_01(pb, "neurochemical")
    ) / 2.0
    patience = (
        _positive_trait_01(pa, "patience") + _positive_trait_01(pb, "patience")
    ) / 2.0
    return _clamp01(0.45 * romantic + 0.25 * prosperity + 0.20 * stability + 0.10 * patience)


def _has_outside_paramour(person: Person, partner_id: int) -> bool:
    pid = person.paramour_person_id
    return pid is not None and int(pid) != int(partner_id)


def _person_breakup_stress_01(ctx: SimulationContext, rec, partner_id: int) -> tuple[float, list[str]]:
    p = rec.person
    stress = 0.0
    reasons: list[str] = []

    neuro = _deviation_01(p, "neurochemical")
    if neuro >= 0.65:
        stress += 0.11 + 0.15 * neuro
        reasons.append("mental_instability")

    if _has_outside_paramour(p, partner_id):
        stress += 0.24
        reasons.append("paramour")

    loyalty = _trait(p, "loyalty")
    if loyalty <= -55.0:
        stress += 0.18
        reasons.append("disloyalty")
    elif abs(loyalty) >= 75.0:
        stress += 0.06
        reasons.append("extreme_loyalty_expression")

    for key, reason in (
        ("empathy", "low_empathy"),
        ("honesty", "dishonesty"),
        ("patience", "impatience"),
    ):
        if _trait(p, key) <= -65.0:
            stress += 0.07
            reasons.append(reason)

    if _trait(p, "assertiveness") >= 70.0:
        stress += 0.05
        reasons.append("domineering_assertiveness")
    if _trait(p, "temperance") <= -70.0:
        stress += 0.05
        reasons.append("indulgence")

    pressure = resource_pressure_for_person(ctx, rec)
    if pressure >= 1.0:
        stress += min(0.18, 0.06 * float(pressure))
        reasons.append("resource_pressure")

    hp = p.household_prosperity
    if hp is not None and float(hp) < 0.15:
        stress += 0.12
        reasons.append("household_hardship")

    return _clamp01(stress), reasons


def _partner_breakup_probability(ctx: SimulationContext, ra, rb) -> tuple[float, list[str]]:
    sa, reasons_a = _person_breakup_stress_01(ctx, ra, int(rb.person_id))
    sb, reasons_b = _person_breakup_stress_01(ctx, rb, int(ra.person_id))
    stress = sa + sb
    if stress <= 0.0:
        return PARTNER_BREAKUP_BASE_PROB, []
    p = PARTNER_BREAKUP_BASE_PROB + 0.03 * stress + 0.08 * max(0.0, stress - 0.45)
    reasons = sorted(set([*reasons_a, *reasons_b]))
    return min(PARTNER_BREAKUP_MAX_PROB, p), reasons


def _partner_breakup_rng(year: int, salt: int, person_a_id: int, person_b_id: int) -> random.Random:
    lo, hi = sorted((int(person_a_id), int(person_b_id)))
    return random.Random(int(year) * PARTNER_BREAKUP_RNG_STREAM + int(salt) * 31 + lo * 10_009 + hi)


def maybe_dissolve_partner_couples(ctx: SimulationContext, year: int) -> None:
    """Rare annual breakup roll for official partners under stacked stress."""
    salt = int(ctx.placename_rng_salt)
    for a_id, b_id in list(ctx.couples):
        ra = ctx.id_to_record.get(a_id)
        rb = ctx.id_to_record.get(b_id)
        if ra is None or rb is None:
            continue
        if a_id not in ctx.current_people_ids or b_id not in ctx.current_people_ids:
            continue
        if ra.person.partner_person_id != b_id or rb.person.partner_person_id != a_id:
            continue
        p, reasons = _partner_breakup_probability(ctx, ra, rb)
        rng = _partner_breakup_rng(int(year), salt, a_id, b_id)
        if rng.random() >= p:
            continue
        ctx.dissolve_couple(a_id, b_id)
        ctx._pending_simulation_events[-1][2].update(
            {
                "breakup_probability": round(p, 5),
                "breakup_reasons": reasons,
            }
        )


def dissolve_invalid_paramours(ctx: SimulationContext, year: int) -> None:
    """End paramour ties that violate age floor or close-kin rules (repairs legacy state)."""
    y = int(year)
    for a_id, b_id in list(ctx.paramours):
        ra = ctx.id_to_record.get(a_id)
        rb = ctx.id_to_record.get(b_id)
        if ra is None or rb is None:
            continue
        if not paramour_pair_eligible(ra, rb, y):
            ctx.end_paramour_relationship(a_id, b_id)
            ctx._pending_simulation_events[-1][2].update(
                {
                    "end_reason": "invalid_age_or_kinship",
                    "end_reasons": ["invalid_age_or_kinship"],
                }
            )


def dissolve_distant_paramours(ctx: SimulationContext) -> None:
    pairs = list(ctx.paramours)
    for a_id, b_id in pairs:
        if settlement_separation(ctx, a_id, b_id) > PARAMOUR_MAX_SEPARATION_TO_SURVIVE:
            ctx.end_paramour_relationship(a_id, b_id)
            ctx._pending_simulation_events[-1][2].update(
                {
                    "end_reason": "distance",
                    "end_reasons": ["distance"],
                }
            )


def _paramour_end_probability(ctx: SimulationContext, ra, rb, year: int) -> tuple[float, list[str]]:
    pa, pb = ra.person, rb.person
    bond = _paramour_bond_score_01(ctx, ra, rb, int(year))
    p = PARAMOUR_END_BASE_PROB + 0.12 * max(0.0, 0.45 - bond)
    reasons: list[str] = []

    if bond < 0.35:
        reasons.append("weak_bond")
    if _positive_trait_01(pa, "mating drive") < 0.25 or _positive_trait_01(pb, "mating drive") < 0.25:
        p += 0.07
        reasons.append("waning_desire")
    loyalty = (_deviation_01(pa, "loyalty") + _deviation_01(pb, "loyalty")) / 2.0
    if loyalty < 0.22:
        p += 0.08
        reasons.append("loyalty_or_guilt")
    instability = (_deviation_01(pa, "neurochemical") + _deviation_01(pb, "neurochemical")) / 2.0
    if instability > 0.70:
        p += 0.08
        reasons.append("emotional_volatility")
    pressure = max(resource_pressure_for_person(ctx, ra), resource_pressure_for_person(ctx, rb))
    if pressure >= 1.0:
        p += min(0.12, 0.04 * float(pressure))
        reasons.append("hardship")
    if not reasons:
        reasons.append("relationship_cooled")
    return min(PARAMOUR_END_MAX_PROB, p), sorted(set(reasons))


def _paramour_end_rng(year: int, salt: int, person_a_id: int, person_b_id: int) -> random.Random:
    lo, hi = sorted((int(person_a_id), int(person_b_id)))
    return random.Random(int(year) * PARAMOUR_END_RNG_STREAM + int(salt) * 17 + lo * 20_011 + hi)


def maybe_end_paramour_relationships(ctx: SimulationContext, year: int) -> None:
    salt = int(ctx.placename_rng_salt)
    for a_id, b_id in list(ctx.paramours):
        ra = ctx.id_to_record.get(a_id)
        rb = ctx.id_to_record.get(b_id)
        if ra is None or rb is None:
            continue
        if a_id not in ctx.current_people_ids or b_id not in ctx.current_people_ids:
            continue
        p, reasons = _paramour_end_probability(ctx, ra, rb, int(year))
        rng = _paramour_end_rng(int(year), salt, a_id, b_id)
        if rng.random() >= p:
            continue
        ctx.end_paramour_relationship(a_id, b_id)
        ctx._pending_simulation_events[-1][2].update(
            {
                "end_reason": reasons[0],
                "end_reasons": reasons,
                "end_probability": round(p, 5),
            }
        )


def _paramour_promotion_probability(ctx: SimulationContext, ra, rb, year: int) -> tuple[float, list[str]]:
    pa, pb = ra.person, rb.person
    bond = _paramour_bond_score_01(ctx, ra, rb, int(year))
    p = 0.02 + 0.22 * max(0.0, bond - 0.50)
    reasons: list[str] = []
    if bond >= 0.65:
        p += 0.08
        reasons.append("strong_bond")
    if pa.partner_person_id is None and pb.partner_person_id is None:
        p += 0.07
        reasons.append("both_partnerless")
    else:
        p += 0.03
        reasons.append("leaves_partner_for_paramour")
    if _same_gender(pa, pb):
        if _sexual_nature(pa) == "homosexual" or _sexual_nature(pb) == "homosexual":
            p += 0.06
            reasons.append("same_sex_romantic_fit")
        else:
            p *= 0.65
    if _deviation_01(pa, "loyalty") < 0.18 and pa.partner_person_id not in (None, int(rb.person_id)):
        p *= 0.45
        reasons.append("existing_partner_loyalty")
    if _deviation_01(pb, "loyalty") < 0.18 and pb.partner_person_id not in (None, int(ra.person_id)):
        p *= 0.45
        reasons.append("existing_partner_loyalty")
    return min(PARAMOUR_PROMOTION_MAX_PROB, max(0.0, p)), sorted(set(reasons))


def _paramour_promotion_rng(year: int, salt: int, person_a_id: int, person_b_id: int) -> random.Random:
    lo, hi = sorted((int(person_a_id), int(person_b_id)))
    return random.Random(int(year) * PARAMOUR_PROMOTION_RNG_STREAM + int(salt) * 19 + lo * 30_011 + hi)


def maybe_promote_paramours_to_partners(ctx: SimulationContext, year: int) -> None:
    salt = int(ctx.placename_rng_salt)
    for a_id, b_id in list(ctx.paramours):
        ra = ctx.id_to_record.get(a_id)
        rb = ctx.id_to_record.get(b_id)
        if ra is None or rb is None:
            continue
        if a_id not in ctx.current_people_ids or b_id not in ctx.current_people_ids:
            continue
        if not paramour_pair_eligible(ra, rb, int(year)):
            continue
        p, reasons = _paramour_promotion_probability(ctx, ra, rb, int(year))
        rng = _paramour_promotion_rng(int(year), salt, a_id, b_id)
        if rng.random() >= p:
            continue
        former_partners: list[int] = []
        for pid, rec, other_id in ((a_id, ra, b_id), (b_id, rb, a_id)):
            partner_id = rec.person.partner_person_id
            if partner_id is not None and int(partner_id) != int(other_id):
                former_partners.append(int(partner_id))
                ctx.dissolve_couple(pid, int(partner_id))
                ctx._pending_simulation_events[-1][2].update(
                    {
                        "breakup_reasons": ["left_for_paramour"],
                        "new_partner_person_id": int(other_id),
                    }
                )
        ctx.end_paramour_relationship(a_id, b_id)
        ctx._pending_simulation_events[-1][2].update(
            {
                "end_reason": "became_partners",
                "end_reasons": ["became_partners"],
            }
        )
        ctx.add_couple(a_id, b_id)
        payload = ctx._pending_simulation_events[-1][2]
        payload.update(
            {
                "partnership_motive": "paramour_became_partner",
                "promotion_probability": round(p, 5),
                "promotion_reasons": reasons,
                "former_partner_ids": former_partners,
            }
        )
        if _same_gender(ra.person, rb.person):
            ctx._record_simulation_event(
                int(year),
                "same_sex_couple_formed",
                {
                    "year": int(year),
                    "person_a_id": a_id,
                    "person_b_id": b_id,
                    "partnership_motive": "paramour_became_partner",
                    "promotion_probability": round(p, 5),
                    "promotion_reasons": reasons,
                },
            )


def _maybe_form_paramours_one_settlement(
    ctx: SimulationContext, year: int, rng: random.Random, residents: list[int]
) -> None:
    """Low-rate formation among non-spouse pairs in one settlement."""
    n = len(residents)
    for i in range(n):
        for j in range(i + 1, n):
            ia, ib = residents[i], residents[j]
            ra = ctx.id_to_record.get(ia)
            rb = ctx.id_to_record.get(ib)
            if ra is None or rb is None:
                continue
            if ia not in ctx.current_people_ids or ib not in ctx.current_people_ids:
                continue
            pa, pb = ra.person, rb.person
            if pa.paramour_person_id is not None or pb.paramour_person_id is not None:
                continue
            if pa.partner_person_id == ib or pb.partner_person_id == ia:
                continue
            if not paramour_pair_eligible(ra, rb, int(year)):
                continue
            if rng.random() > _paramour_pair_probability(pa, pb, ctx):
                continue
            try:
                ctx.add_paramour_relationship(ia, ib)
                ctx._pending_simulation_events[-1][2].update(
                    {
                        "formation_probability": round(
                            _paramour_pair_probability(pa, pb, ctx), 5
                        ),
                        "same_gender": _same_gender(pa, pb),
                        "orientation_multiplier": round(
                            _paramour_orientation_multiplier(ctx, pa, pb), 4
                        ),
                    }
                )
            except (LookupError, ValueError):
                pass


def maybe_form_paramours(ctx: SimulationContext, year: int, rng: random.Random) -> None:
    by_sid = ctx.current_people_by_settlement()
    for sid in sorted(by_sid.keys()):
        ids = [rec.person_id for rec in by_sid[sid]]
        _maybe_form_paramours_one_settlement(ctx, year, rng, ids)


def _paired_person_ids(ctx: SimulationContext) -> set[int]:
    out: set[int] = set()
    for a, b in ctx.couples:
        out.add(int(a))
        out.add(int(b))
    return out


def _eligible_same_sex_couple_candidate(
    ctx: SimulationContext,
    rec,
    year: int,
    paired_ids: set[int],
) -> bool:
    if rec.is_founder:
        return False
    if int(rec.person_id) in paired_ids:
        return False
    if rec.person.partner_person_id is not None or rec.person.paramour_person_id is not None:
        return False
    return _is_mature(rec, int(year))


def _are_full_siblings(ra, rb) -> bool:
    fa, fb = ra.father_id, rb.father_id
    ma, mb = ra.mother_id, rb.mother_id
    if fa is not None and fa == fb:
        return True
    if ma is not None and ma == mb:
        return True
    return False


def _close_kin_blocked(ra, rb) -> bool:
    """Block parent-child and full-sibling official same-sex pairs."""
    a, b = int(ra.person_id), int(rb.person_id)
    if ra.father_id == b or ra.mother_id == b or rb.father_id == a or rb.mother_id == a:
        return True
    return _are_full_siblings(ra, rb)


def _romantic_infatuation_score(pa: Person, pb: Person, year: int) -> float:
    """Pair attractiveness from stored 0..1 scores (elderly penalty on rating, not symmetry)."""

    def one(p: Person) -> float:
        if p.attractiveness_01 is not None:
            return max(0.0, min(1.0, float(p.attractiveness_01)))
        return attractiveness_score(p, int(year))

    return (one(pa) + one(pb)) / 2.0


def _same_sex_acceptance_probability(*, romantic_01: float, prosperity_01: float) -> float:
    base = romantic_01 * (0.12 + 0.88 * prosperity_01)
    return min(0.88, base * SAME_SEX_SOCIAL_FRICTION)


def _same_sex_pair_rng(year: int, salt: int, sid: str, ia: int, ib: int) -> random.Random:
    sk = sum((i + 1) * ord(c) for i, c in enumerate(sid[:48]))
    lo, hi = (ia, ib) if ia < ib else (ib, ia)
    return random.Random(
        int(year) * SAME_SEX_RNG_STREAM + int(salt) * 13 + sk + lo * 10_007 + hi
    )


def _sid_hash_for_rng(sid: str) -> int:
    return sum((i + 1) * ord(c) for i, c in enumerate(sid[:64])) % (2**31)


def _maybe_form_same_sex_couples_one_gender(
    ctx: SimulationContext,
    year: int,
    sid: str,
    eligible_ids: list[int],
    paired_ids: set[int],
) -> None:
    pool = [pid for pid in eligible_ids if pid not in paired_ids]
    if len(pool) < 2:
        return
    salt = int(ctx.placename_rng_salt)
    pick = random.Random(int(year) * 404_011 + salt + len(pool) + _sid_hash_for_rng(sid))

    trials = 0
    while trials < SAME_SEX_MAX_TRIALS_PER_SETTLEMENT and len(pool) >= 2:
        trials += 1
        ia, ib = pick.sample(pool, 2)
        if ia in paired_ids or ib in paired_ids:
            continue
        ra = ctx.id_to_record.get(ia)
        rb = ctx.id_to_record.get(ib)
        if ra is None or rb is None:
            continue
        if ia not in ctx.current_people_ids or ib not in ctx.current_people_ids:
            continue
        if _close_kin_blocked(ra, rb):
            continue
        pa, pb = ra.person, rb.person
        romantic = _romantic_infatuation_score(pa, pb, year)
        pa_p = resource_pressure_for_person(ctx, ra)
        pb_p = resource_pressure_for_person(ctx, rb)
        prosperity = pair_prosperity_01(pa, pb, pressure_a=pa_p, pressure_b=pb_p)
        p_acc = _same_sex_acceptance_probability(
            romantic_01=romantic, prosperity_01=prosperity
        )
        prng = _same_sex_pair_rng(year, salt, sid, ia, ib)
        if prng.random() >= p_acc:
            continue
        try:
            ctx.add_couple(ia, ib)
        except LookupError:
            continue
        paired_ids.add(ia)
        paired_ids.add(ib)
        ctx._record_simulation_event(
            int(year),
            "same_sex_couple_formed",
            {
                "year": int(year),
                "person_a_id": ia,
                "person_b_id": ib,
                "settlement_id": sid,
                "romantic_score": round(romantic, 4),
                "prosperity_01": round(prosperity, 4),
                "acceptance_probability": round(p_acc, 4),
                "partnership_motive": "same_sex_romantic",
                "social_friction_factor": SAME_SEX_SOCIAL_FRICTION,
            },
        )
        pool = [p for p in pool if p not in (ia, ib)]


def maybe_form_same_sex_couples(ctx: SimulationContext, year: int) -> None:
    """Form female-female and male-male couples from romantic compatibility and prosperity."""
    paired_ids = _paired_person_ids(ctx)
    by_sid = ctx.current_people_by_settlement()
    y = int(year)
    for sid in sorted(by_sid.keys()):
        females: list[int] = []
        males: list[int] = []
        for rec in by_sid[sid]:
            g = (rec.person.gender or "").strip().lower()
            if g == "female" and _eligible_same_sex_couple_candidate(ctx, rec, y, paired_ids):
                females.append(int(rec.person_id))
            elif g == "male" and _eligible_same_sex_couple_candidate(ctx, rec, y, paired_ids):
                males.append(int(rec.person_id))
        _maybe_form_same_sex_couples_one_gender(ctx, y, sid, females, paired_ids)
        _maybe_form_same_sex_couples_one_gender(ctx, y, sid, males, paired_ids)


def simulation_social_annual_tick(ctx: SimulationContext, year: int) -> None:
    """Partner breakups, paramour dynamics, and same-sex official couples."""
    rng = random.Random(
        int(year) * 400_009 + int(ctx.placename_rng_salt) + 1777
    )
    dissolve_invalid_paramours(ctx, year)
    dissolve_distant_paramours(ctx)
    maybe_promote_paramours_to_partners(ctx, year)
    maybe_end_paramour_relationships(ctx, year)
    maybe_dissolve_partner_couples(ctx, year)
    maybe_form_paramours(ctx, year, rng)
    maybe_form_same_sex_couples(ctx, year)
