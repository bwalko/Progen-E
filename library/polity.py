"""Runtime polity, office seat, dynasty, alliance, and campaign records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from library.government_catalog import TitleRow
    from library.simulation_context import SimulationContext


@dataclass
class PolityState:
    polity_id: int
    polity_type_id: str
    parent_polity_id: int | None
    name: str
    capital_settlement_id: str | None
    founding_dynasty_id: int | None
    founded_sim_year: int
    dissolved_sim_year: int | None = None
    status: str = "active"
    notes: dict[str, Any] = field(default_factory=dict)


@dataclass
class TerritoryOpenRow:
    polity_id: int
    target_kind: str  # region | settlement
    target_id: str
    since_sim_year: int


@dataclass
class OfficeSeatState:
    seat_id: int
    polity_id: int
    title_id: str
    slot_index: int = 0
    scope_settlement_id: str | None = None
    vacant_since_sim_year: int | None = None
    status: str = "active"
    holder_person_id: int | None = None
    term_expires_sim_year: int | None = None


@dataclass
class DynastyState:
    dynasty_id: int
    founder_person_id: int
    house_name: str
    founded_sim_year: int
    extinct_sim_year: int | None = None
    line: str = "agnatic"


@dataclass
class AllianceState:
    alliance_id: int
    polity_a_id: int
    polity_b_id: int
    kind: str
    since_sim_year: int
    until_sim_year: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    loyalty_score: float = 1.0  # runtime vassal loyalty; not persisted in v1 column


@dataclass
class CampaignState:
    campaign_id: int
    attacker_polity_id: int
    defender_polity_id: int
    kind: str
    objective: dict[str, Any] = field(default_factory=dict)
    start_sim_year: int = 0
    end_sim_year: int | None = None
    outcome: str = "ongoing"
    attacker_force_strength: float = 0.0
    defender_force_strength: float = 0.0
    attacker_treasury_spent: float = 0.0
    defender_treasury_spent: float = 0.0
    siege_target_settlement_id: str | None = None
    siege_years: int = 0


def polity_owns_region(ctx: SimulationContext, polity_id: int, region_id: str) -> bool:
    rid = (region_id or "").strip()
    for t in ctx.gov_territory_rows:
        if t.polity_id == polity_id and t.target_kind == "region" and t.target_id == rid:
            return True
    return False


def polity_regions(ctx: SimulationContext, polity_id: int) -> list[str]:
    return [
        t.target_id
        for t in ctx.gov_territory_rows
        if t.polity_id == polity_id and t.target_kind == "region"
    ]


def polity_settlement_territory_ids(ctx: SimulationContext, polity_id: int) -> list[str]:
    """Open settlement ids held by ``polity_id`` (``target_kind == settlement``)."""
    return [
        str(t.target_id or "").strip()
        for t in ctx.gov_territory_rows
        if t.polity_id == polity_id
        and (t.target_kind or "").strip().lower() == "settlement"
        and str(t.target_id or "").strip()
    ]


def polity_for_settlement(ctx: SimulationContext, settlement_id: str) -> PolityState | None:
    sid = (settlement_id or "").strip()
    if not sid:
        return None
    for t in ctx.gov_territory_rows:
        if (t.target_kind or "").strip().lower() == "settlement" and t.target_id == sid:
            return ctx.gov_polities.get(t.polity_id)
    return None


def polities_in_region(ctx: SimulationContext, region_id: str) -> list[PolityState]:
    """Active polities with any open territory tied to ``region_id`` (region rows or settlements in that region)."""
    rid = (region_id or "").strip()
    if not rid:
        return []
    seen: set[int] = set()
    out: list[PolityState] = []
    for t in ctx.gov_territory_rows:
        pid = int(t.polity_id)
        if pid in seen:
            continue
        pol = ctx.gov_polities.get(pid)
        if pol is None or (pol.status or "").strip().lower() != "active":
            continue
        kind = (t.target_kind or "").strip().lower()
        if kind == "region" and t.target_id == rid:
            seen.add(pid)
            out.append(pol)
        elif kind == "settlement":
            st = ctx.settlements_by_id.get(str(t.target_id or "").strip())
            if st is not None and (st.region_id or "").strip() == rid:
                seen.add(pid)
                out.append(pol)
    return out


def polity_for_region(ctx: SimulationContext, region_id: str) -> PolityState | None:
    """Return the active polity that holds an open **region**-grain row for ``region_id``.

    Settlement-grain counties in the region are ignored here so higher-tier region
    polities are unambiguous and county-only regions do not block bootstrapping a
    region-tier polity. Use :func:`polities_in_region` to enumerate all polities tied
    to a region (region rows plus settlements in that region).
    """
    rid = (region_id or "").strip()
    if not rid:
        return None
    for t in ctx.gov_territory_rows:
        if (t.target_kind or "").strip().lower() == "region" and t.target_id == rid:
            pol = ctx.gov_polities.get(t.polity_id)
            if pol is not None and (pol.status or "").strip().lower() == "active":
                return pol
    return None


def seat_by_id(ctx: SimulationContext, seat_id: int) -> OfficeSeatState | None:
    return ctx.gov_office_seats.get(seat_id)


def _children_ids(ctx: SimulationContext, parent_id: int) -> list[int]:
    out: list[int] = []
    for rec in ctx.people:
        if rec.father_id == parent_id or rec.mother_id == parent_id:
            out.append(rec.person_id)
    return sorted(out)


def _descendants_preorder(ctx: SimulationContext, root_id: int) -> list[int]:
    """Depth-first preorder: root first, then subtrees in sorted child order."""
    order: list[int] = []
    seen: set[int] = set()

    def walk(pid: int) -> None:
        if pid in seen:
            return
        seen.add(pid)
        order.append(pid)
        for cid in _children_ids(ctx, pid):
            walk(cid)

    walk(root_id)
    return order


def primogeniture_candidates(
    ctx: SimulationContext,
    *,
    previous_holder_id: int,
    title: TitleRow,
    year: int,
) -> list[int]:
    """Ordered list of eligible successor person_ids (best first)."""
    rule = (title.selection_rule or "").strip().lower()
    desc = _descendants_preorder(ctx, previous_holder_id)
    desc = [pid for pid in desc if pid != previous_holder_id]

    def alive_mature(pid: int) -> bool:
        rec = ctx.id_to_record.get(pid)
        if rec is None or rec.person.deathyear is not None:
            return False
        age = year - int(rec.person.birthyear)
        if age < int(title.min_age or 0):
            return False
        return True

    salic = "salic" in rule
    cognatic = "cognatic" in rule or "absolute" in rule
    prefer_male = "cognatic" in rule and "absolute" not in rule

    pool = [pid for pid in desc if alive_mature(pid)]
    if not pool:
        return []

    if salic:
        males = [
            pid
            for pid in pool
            if (ctx.id_to_record[pid].person.gender or "").strip() == "Male"
        ]
        if males:
            return _sort_birth_order(ctx, males)
        return _sort_birth_order(ctx, pool)

    if prefer_male:
        males = [
            pid
            for pid in pool
            if (ctx.id_to_record[pid].person.gender or "").strip() == "Male"
        ]
        females = [
            pid
            for pid in pool
            if (ctx.id_to_record[pid].person.gender or "").strip() == "Female"
        ]
        return _sort_birth_order(ctx, males) + _sort_birth_order(ctx, females)

    return _sort_birth_order(ctx, pool)


def _sort_birth_order(ctx: SimulationContext, ids: list[int]) -> list[int]:
    def key(pid: int) -> tuple[int, int]:
        rec = ctx.id_to_record.get(pid)
        if rec is None:
            return (10**9, pid)
        return (int(rec.person.birthyear), pid)

    return sorted(ids, key=key)


def realm_resident_ids(ctx: SimulationContext, polity_id: int) -> set[int]:
    regions = polity_regions(ctx, polity_id)
    settlements = polity_settlement_territory_ids(ctx, polity_id)
    out: set[int] = set()
    for rid in regions:
        for rec in ctx.iter_current_people(sorted_by_id=True):
            if (ctx._residence_region_id(rec) or "") == rid:
                out.add(rec.person_id)
    for sid in settlements:
        for rec in ctx.iter_current_people(sorted_by_id=True):
            if rec.person.deathyear is not None:
                continue
            cur = (
                rec.person.current_settlement_id
                or rec.person.birthplace_settlement_id
                or ""
            ).strip()
            if cur == sid:
                out.add(rec.person_id)
    return out


def in_dynasty_line(
    ctx: SimulationContext,
    person_id: int,
    founder_id: int,
    *,
    cognatic: bool = False,
) -> bool:
    """True if ``person_id`` is a descendant of ``founder_id`` (agnatic default)."""
    seen: set[int] = set()
    stack = [person_id]
    while stack:
        cur = stack.pop()
        if cur == founder_id:
            return True
        if cur in seen or cur <= 0:
            continue
        seen.add(cur)
        rec = ctx.id_to_record.get(cur)
        if rec is None:
            continue
        if rec.father_id:
            stack.append(rec.father_id)
        if cognatic and rec.mother_id:
            stack.append(rec.mother_id)
    return False
