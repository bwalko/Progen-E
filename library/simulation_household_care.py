"""Yearly household / caregiver batch pass with fast-path gates.

Runs once per simulation year from :meth:`library.simulation_context.SimulationContext.record_year_summary`
**after** careers, migration, and social ticks so jobs, residence moves, and partnerships
are stable for the tick. Mind/body refresh runs earlier in the same summary (before careers).
See ``dev_rules/module_map.md``.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from library import simulation_timing
from library.mind_body import clamp_mind_body_value
from library.person import Person
from library.simulation_careers import _household_ids_for_job_move, _residence_settlement_id
from library.simulation_outlaws import is_outlaw_absent

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
_FAST_RNG_MASK = (1 << 64) - 1
_FAST_RNG_UNIT_53 = 1.0 / float(1 << 53)
_FAST_RNG_INCREMENT = 0x9E3779B97F4A7C15

CARE_CAPACITY_TRAIT_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("empathy", 0.45),
    ("patience", 0.35),
    ("nurturance", 0.5),
    ("temperance", 0.25),
    ("neurochemical", 0.2),
    ("physical", 0.15),
)

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
    childcare_duty_factor_by_adult: Mapping[int, float]
    largest_active_settlement_id: str | None


def _mix64(value: int) -> int:
    x = int(value) & _FAST_RNG_MASK
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & _FAST_RNG_MASK
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & _FAST_RNG_MASK
    return (x ^ (x >> 31)) & _FAST_RNG_MASK


class _FastTickRng:
    __slots__ = ("_state",)

    def __init__(self, seed: int) -> None:
        self._state = _mix64(seed)

    def random(self) -> float:
        self._state = _mix64(self._state + _FAST_RNG_INCREMENT)
        return float(self._state >> 11) * _FAST_RNG_UNIT_53


def _tick_rng(year: int, salt: int, stream: int) -> _FastTickRng:
    return _FastTickRng(int(year) * 1_000_003 + int(salt) * 19 + int(stream))


_DEFAULT_TICK_RNG = _tick_rng


def _unit_float_from_mixed(value: int) -> float:
    return float(int(value) >> 11) * _FAST_RNG_UNIT_53


def _shortfall_roll_pair(year: int, salt: int, stream: int) -> tuple[float, float]:
    if _tick_rng is not _DEFAULT_TICK_RNG:
        rng = _tick_rng(year, salt, stream)
        return rng.random(), rng.random()
    state = _mix64(int(year) * 1_000_003 + int(salt) * 19 + int(stream))
    first = _mix64(state + _FAST_RNG_INCREMENT)
    second = _mix64(first + _FAST_RNG_INCREMENT)
    return _unit_float_from_mixed(first), _unit_float_from_mixed(second)


def _residence_sid(rec: SimulationPersonRecord) -> str:
    return _residence_settlement_id(rec)


def _care_work_trait_value(person: Person, key: str) -> float:
    mb = person.mind_body or {}
    if mb:
        if key in mb:
            return clamp_mind_body_value(mb.get(key))
        for k, v in mb.items():
            if str(k) == key:
                return clamp_mind_body_value(v)

    genome = person.genome or {}
    if not genome:
        return 0.0
    if key in genome:
        return clamp_mind_body_value(genome.get(key))
    for k, v in genome.items():
        if str(k) == key:
            return clamp_mind_body_value(v)
    return 0.0


def build_year_indexes(ctx: SimulationContext, year: int) -> YearCareIndexes:
    prof = simulation_timing.active_for_year(year)
    tpc = time.perf_counter
    if prof:
        t0 = tpc()
    alive_ids: set[int] = set(ctx.current_people_ids)
    minor_ids: list[int] = []
    children_by_parent: dict[int, set[int]] = defaultdict(set)

    for rec in ctx.people:
        for p in (rec.father_id, rec.mother_id):
            if p is not None:
                children_by_parent[p].add(rec.person_id)
    if prof:
        simulation_timing.accumulate("household_care.index.children", tpc() - t0)
        t0 = tpc()

    by_settlement = ctx.current_people_by_settlement()
    for rec in ctx.iter_current_people(sorted_by_id=True):
        if is_outlaw_absent(rec.person):
            continue
        if rec.person_id in alive_ids and ctx._person_is_dependent_minor(rec, year):
            minor_ids.append(rec.person_id)
    minor_id_set = set(minor_ids)
    if prof:
        simulation_timing.accumulate("household_care.index.minors", tpc() - t0)
        t0 = tpc()

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
            if child_id not in minor_id_set:
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
            ids.add(int(child_id))
        return tuple(sorted(ids))

    for rec in ctx.iter_current_people(sorted_by_id=True):
        if is_outlaw_absent(rec.person):
            continue
        if int(rec.person_id) in minor_id_set:
            continue
        hids = indexed_household_ids(rec)
        household_ids_by_adult[int(rec.person_id)] = hids
        hkey = frozenset(hids)
        household_settlement_id.setdefault(hkey, _residence_sid(rec))
        n_minors = 0
        minors_for_household = minor_ids_by_household_mut.setdefault(hkey, set())
        for mid in hids:
            if int(mid) in minor_id_set:
                n_minors += 1
                minors_for_household.add(int(mid))
        dependent_minor_count_by_adult[int(rec.person_id)] = n_minors

    for mid in minor_ids:
        mrec = ctx.id_to_record.get(int(mid))
        if mrec is None:
            continue
        if is_outlaw_absent(mrec.person):
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

    for service_rec in ctx.iter_current_people(sorted_by_id=True):
        if is_outlaw_absent(service_rec.person):
            continue
        if int(service_rec.person_id) in minor_id_set:
            continue
        if (service_rec.person.job_market_type or "").strip().lower() != "domestic_service":
            continue
        employer_id = service_rec.person.employer_person_id
        if employer_id is None or int(employer_id) not in alive_ids:
            continue
        employer_rec = ctx.id_to_record.get(int(employer_id))
        if employer_rec is None:
            continue
        if _residence_sid(employer_rec) != _residence_sid(service_rec):
            continue
        employer_hids = household_ids_by_adult.get(int(employer_id))
        if not employer_hids:
            continue
        old_hkey = frozenset(int(x) for x in employer_hids)
        if int(service_rec.person_id) in old_hkey:
            continue
        new_hkey = frozenset(set(old_hkey) | {int(service_rec.person_id)})
        minors = set(minor_ids_by_household_mut.pop(old_hkey, set()))
        minor_ids_by_household_mut.setdefault(new_hkey, set()).update(minors)
        sid = household_settlement_id.pop(old_hkey, _residence_sid(employer_rec))
        household_settlement_id.setdefault(new_hkey, sid)
        new_hids = tuple(sorted(new_hkey))
        for adult_id in new_hkey:
            if int(adult_id) in minor_id_set:
                continue
            household_ids_by_adult[int(adult_id)] = new_hids
            dependent_minor_count_by_adult[int(adult_id)] = len(minors)
    if prof:
        simulation_timing.accumulate("household_care.index.households", tpc() - t0)
        t0 = tpc()

    minor_ids_by_household = {
        hkey: frozenset(ids) for hkey, ids in minor_ids_by_household_mut.items()
    }
    grandparent_extras_by_household = {
        hkey: _grandparent_supply_extras(ctx, year, hkey, minors, household_settlement_id.get(hkey, ""))
        for hkey, minors in minor_ids_by_household.items()
        if minors
    }
    if prof:
        simulation_timing.accumulate("household_care.index.grandparents", tpc() - t0)
        t0 = tpc()
    childcare_duty_factor_by_adult: dict[int, float] = {}
    for adult_id, hids in household_ids_by_adult.items():
        n = int(dependent_minor_count_by_adult.get(int(adult_id), 0))
        if n <= 0:
            childcare_duty_factor_by_adult[int(adult_id)] = 0.0
            continue
        raw = 1.0 - math.exp(-CHILD_DUTY_GROWTH * float(n))
        hkey = frozenset(hids)
        minors = minor_ids_by_household.get(hkey, frozenset())
        relief = 0.0
        if minors:
            relief = (
                float(len(grandparent_extras_by_household.get(hkey, frozenset())))
                * CHILD_DUTY_GRANDPARENT_RELIEF
            )
        duty = max(0.0, raw - relief)
        childcare_duty_factor_by_adult[int(adult_id)] = min(CHILD_DUTY_FACTOR_CAP, duty)
    if prof:
        simulation_timing.accumulate("household_care.index.duty", tpc() - t0)
        t0 = tpc()
    largest_active_settlement_id = _largest_active_settlement_id(ctx)
    if prof:
        simulation_timing.accumulate("household_care.index.largest", tpc() - t0)
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
        childcare_duty_factor_by_adult=childcare_duty_factor_by_adult,
        largest_active_settlement_id=largest_active_settlement_id,
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

    def band_bonus(key: str, weight: float) -> float:
        v = _care_work_trait_value(person, key)
        mag = abs(v)
        return weight * max(0.0, 1.0 - mag / 100.0)

    for key, weight in CARE_CAPACITY_TRAIT_WEIGHTS:
        score += band_bonus(key, weight)

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
    market_type = (rec.person.job_market_type or "").strip().lower()
    if market_type == "household_care":
        return base * (0.95 + 0.05 * min(1.0, base / 4.0))
    if market_type == "domestic_service":
        jk = job.lower()
        if any(token in jk for token in ("nanny", "child watcher", "care aide", "household manager")):
            return base * 0.88
        return base * 0.45
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
    if indexes is not None:
        cached = indexes.childcare_duty_factor_by_adult.get(int(rec.person_id))
        if cached is not None:
            return float(cached)
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


def _is_aunt_or_uncle_of_minor(
    ctx: SimulationContext,
    adult_id: int,
    minor_rec: SimulationPersonRecord,
) -> bool:
    for parent_id in (minor_rec.father_id, minor_rec.mother_id):
        if parent_id is None or int(parent_id) == int(adult_id):
            continue
        parent_rec = ctx.id_to_record.get(int(parent_id))
        if parent_rec is None:
            continue
        parent_parents = {
            int(pid)
            for pid in (parent_rec.father_id, parent_rec.mother_id)
            if pid is not None
        }
        if not parent_parents:
            continue
        adult_rec = ctx.id_to_record.get(int(adult_id))
        if adult_rec is None:
            return False
        adult_parents = {
            int(pid)
            for pid in (adult_rec.father_id, adult_rec.mother_id)
            if pid is not None
        }
        if parent_parents & adult_parents:
            return True
    return False


def childcare_kinship_bonus_01(
    ctx: SimulationContext,
    rec: SimulationPersonRecord,
    year: int,
    indexes: YearCareIndexes | None = None,
) -> float:
    """Kinship pull toward household childcare for parents, grandparents, aunts/uncles."""
    if ctx._person_is_dependent_minor(rec, year):
        return 0.0
    adult_id = int(rec.person_id)
    sid = _residence_sid(rec)
    if not sid:
        return 0.0
    if indexes is not None:
        minor_id_set = set(indexes.minor_ids)
        minor_ids = tuple(
            int(r.person_id)
            for r in indexes.by_settlement.get(sid, ())
            if int(r.person_id) in minor_id_set
        )
    else:
        minor_ids = tuple(
            int(r.person_id)
            for r in ctx.iter_current_people(sorted_by_id=True)
            if _residence_sid(r) == sid and ctx._person_is_dependent_minor(r, year)
        )
    best = 0.0
    for mid in minor_ids:
        minor_rec = ctx.id_to_record.get(int(mid))
        if minor_rec is None:
            continue
        parents = {
            int(pid)
            for pid in (minor_rec.father_id, minor_rec.mother_id)
            if pid is not None
        }
        if adult_id in parents:
            return 1.0
        if adult_id in _living_grandparent_ids_for_minor(ctx, minor_rec):
            best = max(best, 0.72)
        elif _is_aunt_or_uncle_of_minor(ctx, adult_id, minor_rec):
            best = max(best, 0.62)
    return min(1.0, best)


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
            if (
                gr is not None
                and not is_outlaw_absent(gr.person)
                and _residence_sid(gr) == household_sid
            ):
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
        if not is_outlaw_absent(rec.person) and ctx._person_is_dependent_minor(rec, year)
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
    indexes: YearCareIndexes | None = None,
) -> None:
    """One crisis outcome per household with supply strictly below dependent count."""
    salt = int(ctx.placename_rng_salt)
    supply_by_person_id: dict[int, float] = {}
    mortality_victims: set[int] = set()
    largest = indexes.largest_active_settlement_id if indexes is not None else None
    current_people_ids = ctx.current_people_ids
    id_to_record = ctx.id_to_record
    prof = simulation_timing.active_for_year(year)
    tpc = time.perf_counter
    if prof:
        t0 = tpc()

    def supply_for(person_id: int) -> float:
        pid = int(person_id)
        if pid in mortality_victims:
            return 0.0
        if pid in supply_by_person_id:
            return supply_by_person_id[pid]
        if pid not in current_people_ids:
            supply_by_person_id[pid] = 0.0
            return 0.0
        rec = id_to_record.get(pid)
        if rec is None:
            supply_by_person_id[pid] = 0.0
            return 0.0
        supply = effective_caregiver_supply(ctx, rec, year)
        supply_by_person_id[pid] = supply
        return supply

    households = sorted(
        household_to_minors.items(),
        key=lambda kv: min(kv[1]) if kv[1] else 0,
    )
    if prof:
        simulation_timing.accumulate("household_care.shortfalls.sort", tpc() - t0)
        t0 = tpc()
    checked_households = 0
    adult_supply_met = 0
    grandparent_supply_met = 0
    crisis_events = 0
    for hkey, minors in households:
        if not minors:
            continue
        checked_households += 1
        need = len(minors)
        need_float = float(need)
        supply = 0.0
        if prof:
            phase_t0 = tpc()
        for hid in hkey:
            if int(hid) in minors:
                continue
            supply += supply_for(hid)
            if supply >= need_float:
                break
        if prof:
            simulation_timing.accumulate(
                "household_care.shortfalls.household_supply", tpc() - phase_t0
            )
        if supply >= need_float:
            adult_supply_met += 1
            continue
        if prof:
            phase_t0 = tpc()
        if indexes is not None:
            extras = indexes.grandparent_extras_by_household.get(hkey, frozenset())
        else:
            hh_sid = _household_settlement_id(ctx, hkey)
            extras = _grandparent_supply_extras(ctx, year, hkey, minors, hh_sid)
        for gp in extras:
            supply += supply_for(gp) * CARE_GRANDPARENT_SUPPLY_SHARE
            if supply >= need_float:
                break
        if prof:
            simulation_timing.accumulate(
                "household_care.shortfalls.grandparent_supply", tpc() - phase_t0
            )
        if supply >= need_float:
            grandparent_supply_met += 1
            continue
        if prof:
            phase_t0 = tpc()
        shortfall = float(need) - float(supply)
        if shortfall <= 0.0:
            if prof:
                simulation_timing.accumulate(
                    "household_care.shortfalls.crisis", tpc() - phase_t0
                )
            continue
        crisis_roll, run_away_roll = _shortfall_roll_pair(
            year, salt + hash(hkey) % 10_000_007, RNG_STREAM_SHORTFALL
        )
        p = min(
            CARE_SHORTFALL_CRISIS_CAP,
            CARE_SHORTFALL_CRISIS_BASE * max(1.0, shortfall),
        )
        if crisis_roll >= p:
            if prof:
                simulation_timing.accumulate(
                    "household_care.shortfalls.crisis", tpc() - phase_t0
                )
            continue
        crisis_events += 1
        victim = min(minors)
        run_away = run_away_roll < CARE_SHORTFALL_RUN_WEIGHT
        if largest is None:
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
        if run_away and largest and victim not in mortality_victims:
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
                    mortality_victims.add(victim)
        elif run_away and not largest:
            mortality_victims.add(victim)
        else:
            mortality_victims.add(victim)
        if prof:
            simulation_timing.accumulate(
                "household_care.shortfalls.crisis", tpc() - phase_t0
            )
    if mortality_victims:
        ctx.mark_dead(mortality_victims, deathyear=year)
    if prof:
        simulation_timing.record_gauge(
            year, "household_care", "shortfall_households_checked", checked_households
        )
        simulation_timing.record_gauge(
            year, "household_care", "shortfall_adult_supply_met", adult_supply_met
        )
        simulation_timing.record_gauge(
            year,
            "household_care",
            "shortfall_grandparent_supply_met",
            grandparent_supply_met,
        )
        simulation_timing.record_gauge(
            year, "household_care", "shortfall_crisis_events", crisis_events
        )


def simulation_household_care_annual_tick(ctx: SimulationContext, year: int) -> None:
    """Yearly batch: partner residence fix, orphan gates + routing, childcare shortfall."""
    y = int(year)
    prof = simulation_timing.active_for_year(y)
    tpc = time.perf_counter
    if prof:
        t0 = tpc()
    _reconcile_partner_residence_mismatch(ctx, y)
    if prof:
        simulation_timing.accumulate("household_care.partner_reconcile", tpc() - t0)
        t0 = tpc()
    indexes = ctx.annual_care_indexes(y)
    if prof:
        simulation_timing.accumulate("household_care.get_indexes", tpc() - t0)
        t0 = tpc()
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
    if prof:
        simulation_timing.accumulate("household_care.orphan_gates", tpc() - t0)
        t0 = tpc()

    _route_local_orphans(ctx, y, uncovered, largest)
    if prof:
        simulation_timing.accumulate("household_care.route_orphans", tpc() - t0)
        t0 = tpc()

    hh_minors = _collect_household_keys_with_minors(ctx, y, indexes)
    if prof:
        simulation_timing.accumulate("household_care.collect_households", tpc() - t0)
        t0 = tpc()
    _process_childcare_shortfalls(ctx, y, hh_minors, indexes=indexes)
    if prof:
        simulation_timing.accumulate("household_care.shortfalls", tpc() - t0)
