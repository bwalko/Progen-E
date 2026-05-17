"""Yearly household / caregiver batch pass with fast-path gates.

Runs once per simulation year from :meth:`library.simulation_context.SimulationContext.record_year_summary`
**after** careers, migration, and social ticks so jobs, residence moves, and partnerships
are stable for the tick. Mind/body refresh runs earlier in the same summary (before careers).
See ``dev_rules/module_map.md``.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from library.mind_body import work_trait_values
from library.person import Person
from library.simulation_careers import _household_ids_for_job_move, _residence_settlement_id

if TYPE_CHECKING:
    from library.simulation_context import SimulationContext, SimulationPersonRecord

# Genealogy / graph safety: max record lookups per child in extended-family gate.
CARE_EXTENDED_FAMILY_MAX_VISITS = 48

# Employed adults retain only a fraction of baseline caregiver capacity.
CARE_CAPACITY_EMPLOYED_MULTIPLIER = 0.25

# Co-resident grandparents (same settlement, not in implicit job household) contribute
# this fraction of their baseline effective supply toward childcare math.
CARE_GRANDPARENT_SUPPLY_SHARE = 0.45

# Past max fertility (or fallback age), baseline caregiver score gets a small bump
# so elders read as more available for grandchildren in the same settlement.
ELDER_POST_FERTILITY_CAPACITY_BONUS = 0.38
ELDER_AGE_FALLBACK_NO_MAX_FERTILITY = 55

# Household childcare shortfall → at most one crisis outcome per household per year.
CARE_SHORTFALL_CRISIS_BASE = 0.12
CARE_SHORTFALL_CRISIS_CAP = 0.45
CARE_SHORTFALL_RUN_WEIGHT = 0.55

RNG_STREAM_SHORTFALL = 71_012

# --- Caregiver-duty penalty knobs (used by careers + government) -------------
# An adult sharing an implicit household with N dependent minors is treated as
# carrying childcare duty. ``childcare_duty_factor`` returns a smooth value in
# ``[0, CHILD_DUTY_FACTOR_CAP]`` consumed by:
#   * :mod:`library.simulation_careers` — multiplies rehire chance, slightly
#     boosts job-loss chance.
#   * :mod:`library.simulation_government` — down-weights merit / head-pick
#     scores so heavy caregivers are less likely to win merit offices.
# ``CHILD_DUTY_GROWTH`` controls how fast duty saturates with each extra minor
# (1 - exp(-growth * n)); ``CHILD_DUTY_GRANDPARENT_RELIEF`` is the per-co-resident
# grandparent reduction (clamped so duty stays non-negative).
CHILD_DUTY_GROWTH = 0.55
CHILD_DUTY_FACTOR_CAP = 0.85
CHILD_DUTY_GRANDPARENT_RELIEF = 0.18


@dataclass(frozen=True)
class YearCareIndexes:
    alive_ids: frozenset[int]
    by_settlement: dict[str, tuple[SimulationPersonRecord, ...]]
    minor_ids: tuple[int, ...]
    children_by_parent: Mapping[int, frozenset[int]]
    household_ids_by_adult: Mapping[int, tuple[int, ...]]
    dependent_minor_count_by_adult: Mapping[int, int]
    minor_ids_by_household: Mapping[frozenset[int], frozenset[int]]
    household_settlement_id: Mapping[frozenset[int], str]
    grandparent_extras_by_household: Mapping[frozenset[int], frozenset[int]]
    largest_active_settlement_id: str | None


def _tick_rng(year: int, salt: int, stream: int) -> random.Random:
    return random.Random(int(year) * 1_000_003 + int(salt) * 19 + int(stream))


def _residence_sid(rec: SimulationPersonRecord) -> str:
    return _residence_settlement_id(rec)


def build_year_indexes(ctx: SimulationContext, year: int) -> YearCareIndexes:
    alive_ids: set[int] = set(ctx.current_people_ids)
    minor_ids: list[int] = []
    children_by_parent: dict[int, set[int]] = defaultdict(set)

    for rec in ctx.people:
        for p in (rec.father_id, rec.mother_id):
            if p is not None:
                children_by_parent[p].add(rec.person_id)

    by_settlement = ctx.current_people_by_settlement()
    for rec in ctx.iter_current_people(sorted_by_id=True):
        if rec.person_id in alive_ids and ctx._person_is_dependent_minor(rec, year):
            minor_ids.append(rec.person_id)

    frozen_children = {k: frozenset(v) for k, v in children_by_parent.items()}
    frozen_by_settlement = {k: tuple(v) for k, v in by_settlement.items()}
    household_ids_by_adult: dict[int, tuple[int, ...]] = {}
    dependent_minor_count_by_adult: dict[int, int] = {}
    minor_ids_by_household_mut: dict[frozenset[int], set[int]] = {}
    household_settlement_id: dict[frozenset[int], str] = {}

    def indexed_household_ids(rec: SimulationPersonRecord) -> tuple[int, ...]:
        worker_id = int(rec.person_id)
        origin_sid = _residence_sid(rec)
        if not origin_sid:
            return (worker_id,)
        ids: set[int] = {worker_id}
        partner_id = rec.person.partner_person_id
        partner_rec = ctx.id_to_record.get(partner_id) if partner_id is not None else None
        if (
            partner_id is not None
            and partner_id in alive_ids
            and partner_rec is not None
            and _residence_sid(partner_rec) == origin_sid
        ):
            ids.add(int(partner_id))

        parent_set = {worker_id}
        if partner_id is not None:
            parent_set.add(int(partner_id))
        candidate_child_ids: set[int] = set(children_by_parent.get(worker_id, ()))
        if partner_id is not None:
            candidate_child_ids.update(children_by_parent.get(int(partner_id), ()))
        for child_id in candidate_child_ids:
            if child_id in parent_set:
                continue
            child = ctx.id_to_record.get(child_id)
            if child is None or child_id not in alive_ids:
                continue
            if _residence_sid(child) != origin_sid:
                continue
            child_parents = {x for x in (child.father_id, child.mother_id) if x is not None}
            if not child_parents:
                continue
            if not child_parents.issubset(parent_set):
                continue
            if ctx._person_is_dependent_minor(child, year):
                ids.add(int(child_id))
        return tuple(sorted(ids))

    for rec in ctx.iter_current_people(sorted_by_id=True):
        if ctx._person_is_dependent_minor(rec, year):
            continue
        hids = indexed_household_ids(rec)
        household_ids_by_adult[int(rec.person_id)] = hids
        hkey = frozenset(hids)
        household_settlement_id.setdefault(hkey, _household_settlement_id(ctx, hkey))
        n_minors = 0
        minors_for_household = minor_ids_by_household_mut.setdefault(hkey, set())
        for mid in hids:
            mrec = ctx.id_to_record.get(mid)
            if mrec is None:
                continue
            if ctx._person_is_dependent_minor(mrec, year):
                n_minors += 1
                minors_for_household.add(int(mid))
        dependent_minor_count_by_adult[int(rec.person_id)] = n_minors

    for mid in minor_ids:
        mrec = ctx.id_to_record.get(int(mid))
        if mrec is None:
            continue
        msid = _residence_sid(mrec)
        hkey: frozenset[int] | None = None
        for pid in (mrec.father_id, mrec.mother_id):
            if pid is None or pid not in alive_ids:
                continue
            prec = ctx.id_to_record.get(pid)
            if prec is None or _residence_sid(prec) != msid:
                continue
            parent_hids = household_ids_by_adult.get(int(pid))
            if parent_hids is not None:
                hkey = frozenset(parent_hids)
                break
        if hkey is None:
            hkey = frozenset({int(mid)})
            household_settlement_id.setdefault(hkey, msid)
        minor_ids_by_household_mut.setdefault(hkey, set()).add(int(mid))

    minor_ids_by_household = {
        hkey: frozenset(ids) for hkey, ids in minor_ids_by_household_mut.items()
    }
    grandparent_extras_by_household = {
        hkey: _grandparent_supply_extras(ctx, year, hkey, minors, household_settlement_id.get(hkey, ""))
        for hkey, minors in minor_ids_by_household.items()
        if minors
    }
    return YearCareIndexes(
        alive_ids=frozenset(alive_ids),
        by_settlement=frozen_by_settlement,
        minor_ids=tuple(minor_ids),
        children_by_parent=frozen_children,
        household_ids_by_adult=household_ids_by_adult,
        dependent_minor_count_by_adult=dependent_minor_count_by_adult,
        minor_ids_by_household=minor_ids_by_household,
        household_settlement_id=household_settlement_id,
        grandparent_extras_by_household=grandparent_extras_by_household,
        largest_active_settlement_id=_largest_active_settlement_id(ctx),
    )


def caregiver_capacity(person: Person, year: int) -> float:
    """Ideal ~4.0 = can cover four dependent children; clamped to [0, 4]."""
    score = 0.0
    g = (person.gender or "").strip().lower()
    if g == "female":
        score += 0.55
    elif g == "male":
        score += 0.25

    gm = (person.gender_mind or "").strip().lower()
    if gm == "feminine":
        score += 0.55
    elif gm == "masculine":
        score += 0.12

    traits = work_trait_values(person)

    def band_bonus(key: str, weight: float) -> float:
        v = float(traits.get(key, 0.0))
        mag = abs(v)
        return weight * max(0.0, 1.0 - mag / 100.0)

    score += band_bonus("empathy", 0.45)
    score += band_bonus("patience", 0.35)
    score += band_bonus("nurturance", 0.5)
    score += band_bonus("temperance", 0.25)
    score += band_bonus("neurochemical", 0.2)
    score += band_bonus("physical", 0.15)

    age = int(year) - int(person.birthyear)
    mx = person.max_fertility_age
    if mx is not None and age > int(mx):
        score += ELDER_POST_FERTILITY_CAPACITY_BONUS
    elif mx is None and age >= ELDER_AGE_FALLBACK_NO_MAX_FERTILITY:
        score += ELDER_POST_FERTILITY_CAPACITY_BONUS * 0.65

    return max(0.0, min(4.0, score))


def gate_a_co_resident_parent(
    ctx: SimulationContext,
    child_rec: SimulationPersonRecord,
    child_sid: str,
) -> bool:
    """True if any biological parent is alive and shares the child's settlement."""
    if not child_sid:
        return False
    for pid in (child_rec.father_id, child_rec.mother_id):
        if pid is None or pid not in ctx.current_people_ids:
            continue
        pr = ctx.id_to_record.get(pid)
        if pr is not None and _residence_sid(pr) == child_sid:
            return True
    return False


def gate_b_extended_family_in_settlement(
    ctx: SimulationContext,
    child_rec: SimulationPersonRecord,
    child_sid: str,
    indexes: YearCareIndexes,
) -> bool:
    """Grandparent or aunt/uncle (parent's sibling) alive in same settlement as child."""
    if not child_sid:
        return False
    visits = 0
    cid = int(child_rec.person_id)
    alive = indexes.alive_ids
    cbp = indexes.children_by_parent

    parents = [x for x in (child_rec.father_id, child_rec.mother_id) if x is not None]

    # Grandparents of child (traverse through parent records even if parent dead).
    for p in parents:
        if visits >= CARE_EXTENDED_FAMILY_MAX_VISITS:
            return False
        visits += 1
        pr = ctx.id_to_record.get(p)
        if pr is None:
            continue
        for gp in (pr.father_id, pr.mother_id):
            if gp is None:
                continue
            if visits >= CARE_EXTENDED_FAMILY_MAX_VISITS:
                return False
            visits += 1
            if gp == cid:
                continue
            gpr = ctx.id_to_record.get(gp)
            if (
                gpr is not None
                and gp in alive
                and _residence_sid(gpr) == child_sid
            ):
                return True

    # Aunts / uncles: other children of each grandparent of each parent.
    for p in parents:
        pr = ctx.id_to_record.get(p)
        if pr is None:
            continue
        for gp in (pr.father_id, pr.mother_id):
            if gp is None:
                continue
            for sib in cbp.get(gp, ()):
                if sib == p or sib == cid:
                    continue
                if visits >= CARE_EXTENDED_FAMILY_MAX_VISITS:
                    return False
                visits += 1
                srec = ctx.id_to_record.get(sib)
                if (
                    srec is not None
                    and sib in alive
                    and _residence_sid(srec) == child_sid
                ):
                    return True
    return False


def effective_caregiver_supply(ctx: SimulationContext, rec: SimulationPersonRecord, year: int) -> float:
    """Care capacity for an adult; employed people contribute a reduced fraction."""
    if ctx._person_is_dependent_minor(rec, year):
        return 0.0
    base = caregiver_capacity(rec.person, year)
    job = (rec.person.job or "").strip()
    if job:
        base *= CARE_CAPACITY_EMPLOYED_MULTIPLIER
    return base


def dependent_minors_in_implicit_household(
    ctx: SimulationContext,
    rec: SimulationPersonRecord,
    year: int,
    indexes: YearCareIndexes | None = None,
) -> int:
    """Number of dependent minors sharing ``rec``'s implicit job-move household.

    Uses the same household primitive as :func:`_implicit_household_frozenset` so
    the duty signal stays consistent with the existing childcare math.
    """
    if ctx._person_is_dependent_minor(rec, year):
        return 0
    if indexes is not None:
        return int(indexes.dependent_minor_count_by_adult.get(int(rec.person_id), 0))
    hkey = _implicit_household_frozenset(ctx, rec, year)
    n = 0
    for mid in hkey:
        mrec = ctx.id_to_record.get(mid)
        if mrec is None:
            continue
        if ctx._person_is_dependent_minor(mrec, year):
            n += 1
    return n


def childcare_duty_factor(
    ctx: SimulationContext,
    rec: SimulationPersonRecord,
    year: int,
    indexes: YearCareIndexes | None = None,
) -> float:
    """Caregiver burden in ``[0, CHILD_DUTY_FACTOR_CAP]``.

    Smoothly grows with the count of dependent minors in the adult's implicit
    household (``1 - exp(-growth * n)``), then receives ``CHILD_DUTY_GRANDPARENT_RELIEF``
    per co-resident grandparent of those minors who is **not** themselves in the
    household — mirroring the supply boost in :func:`_grandparent_supply_extras`.
    Returns ``0.0`` for anyone who is themselves a dependent minor.
    """
    if ctx._person_is_dependent_minor(rec, year):
        return 0.0
    n = dependent_minors_in_implicit_household(ctx, rec, year, indexes=indexes)
    if n <= 0:
        return 0.0
    raw = 1.0 - math.exp(-CHILD_DUTY_GROWTH * float(n))

    hkey = (
        frozenset(indexes.household_ids_by_adult.get(int(rec.person_id), (int(rec.person_id),)))
        if indexes is not None
        else _implicit_household_frozenset(ctx, rec, year)
    )
    minors = (
        indexes.minor_ids_by_household.get(hkey, frozenset())
        if indexes is not None
        else frozenset(
            mid
            for mid in hkey
            if (mr := ctx.id_to_record.get(mid)) is not None
            and ctx._person_is_dependent_minor(mr, year)
        )
    )
    relief = 0.0
    if minors:
        extras = (
            indexes.grandparent_extras_by_household.get(hkey, frozenset())
            if indexes is not None
            else _grandparent_supply_extras(
                ctx, year, hkey, minors, _household_settlement_id(ctx, hkey)
            )
        )
        relief = float(len(extras)) * CHILD_DUTY_GRANDPARENT_RELIEF
    duty = max(0.0, raw - relief)
    return min(CHILD_DUTY_FACTOR_CAP, duty)


def _largest_active_settlement_id(ctx: SimulationContext) -> str | None:
    best: tuple[int, str] | None = None
    for sid, st in ctx.settlements_by_id.items():
        if (st.status or "").strip().lower() != "active":
            continue
        n = ctx.count_alive_in_settlement(sid)
        cand = (n, sid)
        if best is None or cand[0] > best[0] or (cand[0] == best[0] and cand[1] > best[1]):
            best = cand
    return best[1] if best else None


def _implicit_household_frozenset(
    ctx: SimulationContext,
    head_rec: SimulationPersonRecord,
    year: int,
    indexes: YearCareIndexes | None = None,
) -> frozenset[int]:
    if indexes is not None:
        return frozenset(
            indexes.household_ids_by_adult.get(int(head_rec.person_id), (int(head_rec.person_id),))
        )
    return frozenset(_household_ids_for_job_move(ctx, head_rec, year))


def _household_key_for_minor(
    ctx: SimulationContext,
    child_rec: SimulationPersonRecord,
    year: int,
    indexes: YearCareIndexes | None = None,
) -> frozenset[int]:
    """Household containing the minor; prefers a co-resident parent's implicit home."""
    child_sid = _residence_sid(child_rec)
    for pid in (child_rec.father_id, child_rec.mother_id):
        if pid is None or pid not in ctx.current_people_ids:
            continue
        pr = ctx.id_to_record[pid]
        if _residence_sid(pr) == child_sid:
            return _implicit_household_frozenset(ctx, pr, year, indexes=indexes)
    return frozenset({int(child_rec.person_id)})


def _living_grandparent_ids_for_minor(
    ctx: SimulationContext, minor_rec: SimulationPersonRecord
) -> frozenset[int]:
    """Biological grandparents (four slots), including via records of possibly dead parents."""
    out: set[int] = set()
    for pid in (minor_rec.father_id, minor_rec.mother_id):
        if pid is None:
            continue
        pr = ctx.id_to_record.get(pid)
        if pr is None:
            continue
        for gp in (pr.father_id, pr.mother_id):
            if gp is not None:
                out.add(int(gp))
    return frozenset(out)


def _household_settlement_id(ctx: SimulationContext, hkey: frozenset[int]) -> str:
    for hid in sorted(hkey):
        if hid not in ctx.current_people_ids:
            continue
        hr = ctx.id_to_record.get(hid)
        if hr is None:
            continue
        sid = _residence_sid(hr)
        if sid:
            return sid
    return ""


def _grandparent_supply_extras(
    ctx: SimulationContext,
    year: int,
    hkey: frozenset[int],
    minors: frozenset[int],
    household_sid: str,
) -> frozenset[int]:
    """Alive grandparents of minors in ``hkey``, co-resident in ``household_sid``, not in ``hkey``."""
    if not household_sid:
        return frozenset()
    extras: set[int] = set()
    for mid in minors:
        mrec = ctx.id_to_record.get(mid)
        if mrec is None:
            continue
        for gp in _living_grandparent_ids_for_minor(ctx, mrec):
            if gp in hkey:
                continue
            if gp not in ctx.current_people_ids:
                continue
            gr = ctx.id_to_record.get(gp)
            if gr is not None and _residence_sid(gr) == household_sid:
                extras.add(gp)
    return frozenset(extras)


def _collect_household_keys_with_minors(
    ctx: SimulationContext, year: int, indexes: YearCareIndexes | None = None
) -> dict[frozenset[int], frozenset[int]]:
    """Map implicit household member set -> dependent minor ids in that set."""
    if indexes is not None:
        return dict(indexes.minor_ids_by_household)
    out: dict[frozenset[int], set[int]] = {}
    minor_ids = indexes.minor_ids if indexes is not None else tuple(
        int(rec.person_id)
        for rec in ctx.iter_current_people(sorted_by_id=True)
        if ctx._person_is_dependent_minor(rec, year)
    )
    for mid in minor_ids:
        crec = ctx.id_to_record.get(int(mid))
        if crec is None or int(mid) not in ctx.current_people_ids:
            continue
        key = _household_key_for_minor(ctx, crec, year, indexes=indexes)
        out.setdefault(key, set()).add(int(mid))
    return {k: frozenset(v) for k, v in out.items()}


def _reconcile_partner_residence_mismatch(ctx: SimulationContext, year: int) -> None:
    """Partners with different settlements: lower ``person_id`` moves to the partner's site."""
    seen_pairs: set[tuple[int, int]] = set()
    for a0, b0 in list(ctx.couples):
        if a0 not in ctx.current_people_ids or b0 not in ctx.current_people_ids:
            continue
        lo, hi = (int(a0), int(b0)) if int(a0) < int(b0) else (int(b0), int(a0))
        if (lo, hi) in seen_pairs:
            continue
        seen_pairs.add((lo, hi))
        ra = ctx.id_to_record[lo]
        rb = ctx.id_to_record[hi]
        sa = _residence_sid(ra)
        sb = _residence_sid(rb)
        if not sa or not sb or sa == sb:
            continue
        try:
            ctx.queue_person_move_to_settlement(
                lo,
                sb,
                move_reason="partner_residence_reconciled",
                requested_year=year,
                apply_year=year + 1,
                source_event="partner_residence_reconciled",
                group_id=f"partner_residence:{lo}:{hi}:{year}",
            )
        except (ValueError, LookupError):
            continue
        ctx._record_simulation_event(
            year,
            "partner_residence_reconciled",
            {
                "year": year,
                "moved_person_id": lo,
                "target_settlement_id": sb,
            },
        )


def _route_local_orphans(
    ctx: SimulationContext,
    year: int,
    uncovered_minor_ids: list[int],
    largest_sid: str | None,
) -> None:
    if not largest_sid or not uncovered_minor_ids:
        return
    for mid in sorted(uncovered_minor_ids):
        crec = ctx.id_to_record.get(mid)
        if crec is None or mid not in ctx.current_people_ids:
            continue
        sid = _residence_sid(crec)
        if sid == largest_sid:
            continue
        try:
            ctx.queue_person_move_to_settlement(
                mid,
                largest_sid,
                move_reason="orphan_seeking_care_congregation",
                requested_year=year,
                apply_year=year + 1,
                source_event="orphan_routed_to_largest_settlement",
                group_id=f"orphan_route:{mid}:{year}",
            )
        except (ValueError, LookupError):
            continue
        ctx._record_simulation_event(
            year,
            "orphan_routed_to_largest_settlement",
            {
                "year": year,
                "person_id": mid,
                "from_settlement_id": sid,
                "to_settlement_id": largest_sid,
            },
        )


def _process_childcare_shortfalls(
    ctx: SimulationContext,
    year: int,
    household_to_minors: Mapping[frozenset[int], frozenset[int]],
) -> None:
    """One crisis outcome per household with supply strictly below dependent count."""
    salt = int(ctx.placename_rng_salt)
    for hkey, minors in sorted(
        household_to_minors.items(),
        key=lambda kv: min(kv[1]) if kv[1] else 0,
    ):
        if not minors:
            continue
        need = len(minors)
        supply = 0.0
        for hid in hkey:
            if hid not in ctx.current_people_ids:
                continue
            hr = ctx.id_to_record.get(hid)
            if hr is None:
                continue
            supply += effective_caregiver_supply(ctx, hr, year)
        hh_sid = _household_settlement_id(ctx, hkey)
        for gp in _grandparent_supply_extras(ctx, year, hkey, minors, hh_sid):
            gr = ctx.id_to_record.get(gp)
            if gr is None:
                continue
            supply += (
                effective_caregiver_supply(ctx, gr, year) * CARE_GRANDPARENT_SUPPLY_SHARE
            )
        shortfall = float(need) - float(supply)
        if shortfall <= 0.0:
            continue
        rng = _tick_rng(year, salt + hash(hkey) % 10_000_007, RNG_STREAM_SHORTFALL)
        p = min(
            CARE_SHORTFALL_CRISIS_CAP,
            CARE_SHORTFALL_CRISIS_BASE * max(1.0, shortfall),
        )
        if rng.random() >= p:
            continue
        victim = min(minors)
        run_away = rng.random() < CARE_SHORTFALL_RUN_WEIGHT
        largest = _largest_active_settlement_id(ctx)
        ctx._record_simulation_event(
            year,
            "household_childcare_shortfall",
            {
                "year": year,
                "household_member_ids": sorted(hkey),
                "dependent_minor_ids": sorted(minors),
                "need": need,
                "supply": supply,
                "shortfall": shortfall,
                "outcome": "run_away" if run_away else "mortality",
                "victim_person_id": victim,
            },
        )
        if run_away and largest:
            vrec = ctx.id_to_record.get(victim)
            vsid = _residence_sid(vrec) if vrec else ""
            if vsid and vsid != largest:
                try:
                    ctx.queue_person_move_to_settlement(
                        victim,
                        largest,
                        move_reason="childcare_shortfall_run_away",
                        requested_year=year,
                        apply_year=year + 1,
                        source_event="household_childcare_shortfall",
                        group_id=f"childcare_shortfall:{victim}:{year}",
                    )
                except (ValueError, LookupError):
                    ctx.mark_dead({victim}, deathyear=year)
        elif run_away and not largest:
            ctx.mark_dead({victim}, deathyear=year)
        else:
            ctx.mark_dead({victim}, deathyear=year)


def simulation_household_care_annual_tick(ctx: SimulationContext, year: int) -> None:
    """Yearly batch: partner residence fix, orphan gates + routing, childcare shortfall."""
    y = int(year)
    _reconcile_partner_residence_mismatch(ctx, y)
    indexes = ctx.annual_care_indexes(y)
    largest = indexes.largest_active_settlement_id

    uncovered: list[int] = []
    for mid in indexes.minor_ids:
        crec = ctx.id_to_record.get(mid)
        if crec is None or mid not in ctx.current_people_ids:
            continue
        child_sid = _residence_sid(crec)
        if gate_a_co_resident_parent(ctx, crec, child_sid):
            continue
        if gate_b_extended_family_in_settlement(ctx, crec, child_sid, indexes):
            continue
        uncovered.append(mid)

    _route_local_orphans(ctx, y, uncovered, largest)

    hh_minors = _collect_household_keys_with_minors(ctx, y, indexes)
    _process_childcare_shortfalls(ctx, y, hh_minors)
