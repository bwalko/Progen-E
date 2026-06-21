"""Generic city-state political dynamics layered on existing polity systems."""

from __future__ import annotations

import json
import random
import sqlite3
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from library.polity import (
    AllianceState,
    PolityState,
    polity_settlement_territory_ids,
)

if TYPE_CHECKING:
    from library.simulation_context import SimulationContext


CITY_STATE_NOTE_KEY = "city_state"
CITY_STATE_MIN_POPULATION = 5
CITY_STATE_LEAGUE_MIN_MEMBERS = 2
CITY_STATE_HEGEMONY_RATIO = 1.35
CITY_STATE_PUBLIC_WORKS_COOLDOWN_YEARS = 18
CITY_STATE_CRISIS_COOLDOWN_YEARS = 12
CITY_STATE_DISPUTE_COOLDOWN_YEARS = 20
CITY_STATE_LEAGUE_COOLDOWN_YEARS = 30
CITY_STATE_CALIBRATION_EVENT_PREFIX = "city_state_"

CITY_STATE_PATTERN_BUCKETS: dict[str, str] = {
    "city_state_urban_consolidation": "city_founding_or_consolidation",
    "city_state_public_works": "civic_public_works_or_institution",
    "city_state_resource_dispute": "rivalry_or_resource_dispute",
    "city_state_league_formed": "league_or_hegemony",
    "city_state_hegemony_declared": "league_or_hegemony",
    "city_state_colony_status_changed": "maritime_colony_lifecycle",
    "city_state_autonomy_changed": "empire_pressure_or_autonomy",
    "city_state_civic_crisis": "civic_crisis_or_reform",
    "city_state_civic_reform": "civic_crisis_or_reform",
}


def _stable_seed(*parts: object) -> int:
    text = "|".join(str(part) for part in parts)
    h = 14_695_981_039_346_656_037
    for ch in text:
        h ^= ord(ch)
        h = (h * 1_099_511_628_211) & 0xFFFFFFFFFFFFFFFF
    return h


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _note(polity: PolityState) -> dict[str, Any]:
    raw = polity.notes.get(CITY_STATE_NOTE_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def _with_note(polity: PolityState, note: dict[str, Any]) -> PolityState:
    notes = dict(polity.notes or {})
    notes[CITY_STATE_NOTE_KEY] = dict(note)
    return replace(polity, notes=notes)


def _update_note(ctx: "SimulationContext", polity: PolityState, **updates: Any) -> PolityState:
    note = _note(polity)
    note.update(updates)
    updated = _with_note(polity, note)
    ctx.gov_polities[int(polity.polity_id)] = updated
    return updated


def _cooldown_ready(note: dict[str, Any], key: str, year: int, cooldown: int) -> bool:
    last = note.get(key)
    try:
        return int(year) - int(last) >= int(cooldown)
    except (TypeError, ValueError):
        return True


def _city_state_polities(ctx: "SimulationContext") -> list[PolityState]:
    out: list[PolityState] = []
    for pol in ctx.gov_polities.values():
        if (pol.status or "").strip().lower() != "active":
            continue
        if (pol.polity_type_id or "").strip().lower() != "city_state":
            continue
        if not polity_settlement_territory_ids(ctx, pol.polity_id):
            continue
        out.append(pol)
    return sorted(out, key=lambda p: int(p.polity_id))


def _primary_settlement_id(ctx: "SimulationContext", polity: PolityState) -> str:
    if polity.capital_settlement_id:
        return str(polity.capital_settlement_id).strip()
    sids = polity_settlement_territory_ids(ctx, polity.polity_id)
    return sids[0] if sids else ""


def _primary_region_id(ctx: "SimulationContext", polity: PolityState) -> str:
    sid = _primary_settlement_id(ctx, polity)
    st = ctx.settlements_by_id.get(sid)
    return str(getattr(st, "region_id", "") or "").strip()


def _settlement_score(ctx: "SimulationContext", settlement_id: str) -> float:
    st = ctx.settlements_by_id.get((settlement_id or "").strip())
    if st is None:
        return 0.0
    pop = ctx.mixed_population_count_in_settlement(st.settlement_id)
    pop_score = min(1.0, pop / 80.0)
    prosperity = _clamp01(float(getattr(st, "prosperity_pool", 1.0) or 0.0) / 2.0)
    stability = _clamp01(float(getattr(st, "stability", 0.5) or 0.0))
    market = _clamp01(float(getattr(st, "market_pull", 0.0) or 0.0))
    colony_bonus = 0.08 if (st.founding_reason or "").strip() == "commercial_outpost" else 0.0
    successor_bonus = 0.12 if (st.autonomy_level or "").strip() == "successor" else 0.0
    return round(
        0.40 * pop_score
        + 0.22 * prosperity
        + 0.18 * stability
        + 0.20 * market
        + colony_bonus
        + successor_bonus,
        5,
    )


def _head_holder_id(ctx: "SimulationContext", polity_id: int) -> int | None:
    for seat in ctx.gov_office_seats.values():
        if int(seat.polity_id) != int(polity_id):
            continue
        if seat.holder_person_id is None:
            continue
        title = (seat.title_id or "").strip().lower()
        if title in {"king_of_city", "president", "chief", "king"}:
            return int(seat.holder_person_id)
    for seat in ctx.gov_office_seats.values():
        if int(seat.polity_id) == int(polity_id) and seat.holder_person_id is not None:
            return int(seat.holder_person_id)
    return None


def _alliance_pair(a_id: int, b_id: int) -> tuple[int, int]:
    return (min(int(a_id), int(b_id)), max(int(a_id), int(b_id)))


def _active_city_league_pairs(ctx: "SimulationContext") -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    for alliance in ctx.gov_alliances:
        if alliance.until_sim_year is not None:
            continue
        if (alliance.kind or "").strip() != "city_state_league":
            continue
        pairs.add(_alliance_pair(alliance.polity_a_id, alliance.polity_b_id))
    return pairs


def _record_city_event(
    ctx: "SimulationContext",
    year: int,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    marked = dict(payload)
    marked.setdefault("year", int(year))
    marked.setdefault("event_type", event_type)
    marked.setdefault("city_state_pattern", event_type.removeprefix("city_state_"))
    ctx._record_simulation_event(int(year), event_type, marked)


def _autonomy_from_state(
    ctx: "SimulationContext", polity: PolityState, settlement_id: str
) -> str:
    st = ctx.settlements_by_id.get(settlement_id)
    if polity.parent_polity_id is not None:
        parent = ctx.gov_polities.get(int(polity.parent_polity_id))
        if parent is not None and (parent.polity_type_id or "").strip().lower() != "city_state":
            return "autonomous_under_empire"
        return "vassal"
    if st is not None and (st.founding_reason or "").strip() == "commercial_outpost":
        level = (st.autonomy_level or "").strip().lower()
        if level == "dependent":
            return "tributary"
        if level == "successor":
            return "hegemon"
    note = _note(polity)
    prior = str(note.get("autonomy_state") or "").strip()
    if prior in {"league_member", "hegemon"}:
        return prior
    return "independent"


def _sync_autonomy_state(ctx: "SimulationContext", polity: PolityState, year: int) -> PolityState:
    sid = _primary_settlement_id(ctx, polity)
    rid = _primary_region_id(ctx, polity)
    if not sid:
        return polity
    note = _note(polity)
    new_state = _autonomy_from_state(ctx, polity, sid)
    old_state = str(note.get("autonomy_state") or "").strip()
    if not old_state:
        _record_city_event(
            ctx,
            year,
            "city_state_urban_consolidation",
            {
                "polity_id": int(polity.polity_id),
                "settlement_id": sid,
                "region_id": rid,
                "autonomy_state": new_state,
                "mixed_population": ctx.mixed_population_count_in_settlement(sid),
                "reason": "city_state_first_recorded",
            },
        )
    elif old_state != new_state:
        _record_city_event(
            ctx,
            year,
            "city_state_autonomy_changed",
            {
                "polity_id": int(polity.polity_id),
                "parent_polity_id": polity.parent_polity_id,
                "settlement_id": sid,
                "region_id": rid,
                "from_autonomy_state": old_state,
                "autonomy_state": new_state,
                "reason": "parent_or_colony_status_changed",
            },
        )
    return _update_note(
        ctx,
        polity,
        autonomy_state=new_state,
        first_city_state_year=note.get("first_city_state_year", int(year)),
        last_autonomy_check_year=int(year),
    )


def _public_work_project(ctx: "SimulationContext", settlement_id: str, rng: random.Random) -> str:
    st = ctx.settlements_by_id.get(settlement_id)
    market = float(getattr(st, "market_pull", 0.0) or 0.0) if st is not None else 0.0
    pressure = float(getattr(st, "food_pressure", 0.0) or 0.0) if st is not None else 0.0
    stability = float(getattr(st, "stability", 0.5) or 0.5) if st is not None else 0.5
    if pressure > 0.9:
        return "storehouse"
    if market > 0.55:
        return "harbor"
    if stability < 0.45:
        return "walls"
    return ("council_house", "temple_precinct", "civic_archive")[rng.randrange(3)]


def _maybe_public_works(ctx: "SimulationContext", polity: PolityState, year: int, rng: random.Random) -> PolityState:
    sid = _primary_settlement_id(ctx, polity)
    rid = _primary_region_id(ctx, polity)
    if not sid:
        return polity
    pop = ctx.mixed_population_count_in_settlement(sid)
    if pop < CITY_STATE_MIN_POPULATION:
        return polity
    note = _note(polity)
    if not _cooldown_ready(note, "last_public_works_year", year, CITY_STATE_PUBLIC_WORKS_COOLDOWN_YEARS):
        return polity
    score = _settlement_score(ctx, sid)
    if score < 0.42:
        return polity
    project = _public_work_project(ctx, sid, rng)
    holder = _head_holder_id(ctx, polity.polity_id)
    consequences: dict[str, Any] = {
        "institutions": [
            {
                "institution_key": f"city_state:{int(polity.polity_id)}:{project}",
                "institution_type": project,
                "focus_domain": "civic_order",
                "settlement_id": sid,
                "region_id": rid,
                "founded_year": int(year),
                "latest_year": int(year),
                "founder_person_id": holder,
                "strength_delta": 0.03 + min(0.05, score * 0.04),
                "influence_delta": 0.025 + min(0.05, score * 0.035),
            }
        ]
    }
    if holder is not None:
        consequences["reputation_marks"] = [
            {
                "mark_key": f"city_public_works:{holder}:{int(year)}:{project}",
                "person_id": holder,
                "reputation_axis": "leadership",
                "direction": "positive",
                "mark_strength": 0.08,
                "mark_year": int(year),
                "settlement_id": sid,
                "region_id": rid,
                "source_role": "city_state_public_works",
                "project": project,
            }
        ]
    _record_city_event(
        ctx,
        year,
        "city_state_public_works",
        {
            "polity_id": int(polity.polity_id),
            "settlement_id": sid,
            "region_id": rid,
            "civic_project": project,
            "city_state_score": score,
            "leader_person_id": holder,
            "mixed_population": pop,
            "consequences": consequences,
        },
    )
    return _update_note(ctx, polity, last_public_works_year=int(year), last_public_works_project=project)


def _maybe_civic_crisis_or_reform(
    ctx: "SimulationContext", polity: PolityState, year: int
) -> PolityState:
    sid = _primary_settlement_id(ctx, polity)
    rid = _primary_region_id(ctx, polity)
    st = ctx.settlements_by_id.get(sid)
    if st is None:
        return polity
    note = _note(polity)
    stability = _clamp01(float(getattr(st, "stability", 0.5) or 0.0))
    pressure = float(getattr(st, "food_pressure", 0.0) or 0.0)
    head = _head_holder_id(ctx, polity.polity_id)
    if (
        (stability < 0.34 or pressure > 1.15)
        and _cooldown_ready(note, "last_civic_crisis_year", year, CITY_STATE_CRISIS_COOLDOWN_YEARS)
    ):
        reason = "food_pressure" if pressure > 1.15 else "elite_faction_deadlock"
        consequences = {
            "faction_memory": [
                {
                    "memory_key": f"city_crisis:{int(polity.polity_id)}:{int(year)}",
                    "memory_type": "civic_grievance",
                    "faction_a_key": f"polity:{int(polity.polity_id)}:citizens",
                    "faction_b_key": f"polity:{int(polity.polity_id)}:elite",
                    "polarity": "negative",
                    "strength": 0.42,
                    "start_year": int(year),
                    "expected_duration_years": 16,
                    "settlement_id": sid,
                    "region_id": rid,
                    "source_role": "city_state_civic_crisis",
                    "reason": reason,
                }
            ]
        }
        if head is not None:
            consequences["legal_fallout"] = [
                {
                    "fallout_key": f"city_crisis_legitimacy:{head}:{int(year)}",
                    "fallout_type": "civic_legitimacy_dispute",
                    "principal_person_id": head,
                    "severity": 0.36,
                    "start_year": int(year),
                    "expected_duration_years": 6,
                    "settlement_id": sid,
                    "region_id": rid,
                    "source_role": "city_state_civic_crisis",
                }
            ]
        _record_city_event(
            ctx,
            year,
            "city_state_civic_crisis",
            {
                "polity_id": int(polity.polity_id),
                "settlement_id": sid,
                "region_id": rid,
                "leader_person_id": head,
                "crisis_reason": reason,
                "stability": stability,
                "food_pressure": round(pressure, 5),
                "consequences": consequences,
            },
        )
        return _update_note(
            ctx,
            polity,
            last_civic_crisis_year=int(year),
            unresolved_civic_crisis_year=int(year),
            unresolved_civic_crisis_reason=reason,
        )
    unresolved = note.get("unresolved_civic_crisis_year")
    if unresolved is not None and stability >= 0.56 and pressure <= 0.95:
        _record_city_event(
            ctx,
            year,
            "city_state_civic_reform",
            {
                "polity_id": int(polity.polity_id),
                "settlement_id": sid,
                "region_id": rid,
                "leader_person_id": head,
                "reform_kind": "civic_compromise",
                "resolved_crisis_year": unresolved,
                "stability": stability,
                "food_pressure": round(pressure, 5),
                "consequences": {
                    "faction_memory": [
                        {
                            "memory_key": f"city_reform:{int(polity.polity_id)}:{int(year)}",
                            "memory_type": "civic_compromise",
                            "faction_a_key": f"polity:{int(polity.polity_id)}:citizens",
                            "faction_b_key": f"polity:{int(polity.polity_id)}:elite",
                            "polarity": "positive",
                            "strength": 0.32,
                            "start_year": int(year),
                            "expected_duration_years": 10,
                            "settlement_id": sid,
                            "region_id": rid,
                            "source_role": "city_state_civic_reform",
                        }
                    ]
                },
            },
        )
        note.pop("unresolved_civic_crisis_year", None)
        note.pop("unresolved_civic_crisis_reason", None)
        note["last_civic_reform_year"] = int(year)
        return _with_note_and_store(ctx, polity, note)
    return polity


def _with_note_and_store(
    ctx: "SimulationContext", polity: PolityState, note: dict[str, Any]
) -> PolityState:
    updated = _with_note(polity, note)
    ctx.gov_polities[int(polity.polity_id)] = updated
    return updated


def _city_states_by_region(ctx: "SimulationContext", polities: list[PolityState]) -> dict[str, list[PolityState]]:
    grouped: dict[str, list[PolityState]] = {}
    for pol in polities:
        rid = _primary_region_id(ctx, pol)
        if rid:
            grouped.setdefault(rid, []).append(pol)
    return grouped


def _maybe_form_leagues(ctx: "SimulationContext", polities: list[PolityState], year: int) -> None:
    existing = _active_city_league_pairs(ctx)
    for rid, members in sorted(_city_states_by_region(ctx, polities).items()):
        active = [
            pol
            for pol in members
            if ctx.mixed_population_count_in_settlement(_primary_settlement_id(ctx, pol))
            >= CITY_STATE_MIN_POPULATION
        ]
        if len(active) < CITY_STATE_LEAGUE_MIN_MEMBERS:
            continue
        active.sort(
            key=lambda p: (
                -_settlement_score(ctx, _primary_settlement_id(ctx, p)),
                int(p.polity_id),
            )
        )
        league_id = f"city_league:{rid}:{active[0].polity_id}"
        pair_added = False
        for left, right in zip(active, active[1:]):
            pair = _alliance_pair(left.polity_id, right.polity_id)
            if pair in existing:
                continue
            left_note = _note(left)
            right_note = _note(right)
            if not (
                _cooldown_ready(left_note, "last_league_year", year, CITY_STATE_LEAGUE_COOLDOWN_YEARS)
                and _cooldown_ready(right_note, "last_league_year", year, CITY_STATE_LEAGUE_COOLDOWN_YEARS)
            ):
                continue
            aid = ctx.next_gov_alliance_id
            ctx.next_gov_alliance_id += 1
            ctx.gov_alliances.append(
                AllianceState(
                    alliance_id=aid,
                    polity_a_id=pair[0],
                    polity_b_id=pair[1],
                    kind="city_state_league",
                    since_sim_year=int(year),
                    payload={
                        "league_id": league_id,
                        "region_id": rid,
                        "status": "defensive_league",
                        "member_polity_ids": [int(p.polity_id) for p in active],
                    },
                    loyalty_score=0.78,
                )
            )
            existing.add(pair)
            pair_added = True
        if not pair_added:
            continue
        member_ids = [int(p.polity_id) for p in active]
        for pol in active:
            _update_note(ctx, pol, autonomy_state="league_member", last_league_year=int(year), league_id=league_id)
        _record_city_event(
            ctx,
            year,
            "city_state_league_formed",
            {
                "polity_id": member_ids[0],
                "polity_a_id": member_ids[0],
                "polity_b_id": member_ids[1] if len(member_ids) > 1 else None,
                "member_polity_ids": member_ids,
                "league_id": league_id,
                "settlement_id": _primary_settlement_id(ctx, active[0]),
                "region_id": rid,
                "reason": "shared_city_state_security",
                "league_status": "defensive_league",
            },
        )


def _maybe_hegemony(ctx: "SimulationContext", polities: list[PolityState], year: int) -> None:
    league_members: dict[str, set[int]] = {}
    for alliance in ctx.gov_alliances:
        if alliance.until_sim_year is not None or alliance.kind != "city_state_league":
            continue
        lid = str(alliance.payload.get("league_id") or "").strip()
        if not lid:
            continue
        members = league_members.setdefault(lid, set())
        members.add(int(alliance.polity_a_id))
        members.add(int(alliance.polity_b_id))
        for raw in alliance.payload.get("member_polity_ids") or []:
            try:
                members.add(int(raw))
            except (TypeError, ValueError):
                continue
    for league_id, member_ids in sorted(league_members.items()):
        members = [
            ctx.gov_polities[pid]
            for pid in sorted(member_ids)
            if pid in ctx.gov_polities
            and (ctx.gov_polities[pid].status or "").strip().lower() == "active"
        ]
        if len(members) < 2:
            continue
        scored = [
            (pol, _settlement_score(ctx, _primary_settlement_id(ctx, pol)))
            for pol in members
        ]
        scored.sort(key=lambda item: (-item[1], int(item[0].polity_id)))
        hegemon, hegemon_score = scored[0]
        runner_up = scored[1][1]
        note = _note(hegemon)
        if note.get("hegemony_league_id") == league_id:
            continue
        if runner_up <= 0 or hegemon_score < runner_up * CITY_STATE_HEGEMONY_RATIO:
            continue
        rid = _primary_region_id(ctx, hegemon)
        sid = _primary_settlement_id(ctx, hegemon)
        for alliance in ctx.gov_alliances:
            if alliance.kind != "city_state_league":
                continue
            if str(alliance.payload.get("league_id") or "") != league_id:
                continue
            payload = dict(alliance.payload)
            payload["status"] = "hegemon_led"
            payload["hegemon_polity_id"] = int(hegemon.polity_id)
            alliance.payload = payload
        _update_note(
            ctx,
            hegemon,
            autonomy_state="hegemon",
            hegemony_league_id=league_id,
            last_hegemony_year=int(year),
        )
        for member in members[1:]:
            _update_note(ctx, member, autonomy_state="league_member", hegemon_polity_id=int(hegemon.polity_id))
        _record_city_event(
            ctx,
            year,
            "city_state_hegemony_declared",
            {
                "polity_id": int(hegemon.polity_id),
                "hegemon_polity_id": int(hegemon.polity_id),
                "member_polity_ids": [int(p.polity_id) for p in members],
                "league_id": league_id,
                "settlement_id": sid,
                "region_id": rid,
                "hegemon_score": hegemon_score,
                "runner_up_score": runner_up,
                "league_status": "hegemon_led",
                "consequences": {
                    "faction_memory": [
                        {
                            "memory_key": f"city_hegemony:{league_id}:{int(year)}",
                            "memory_type": "hegemonic_pressure",
                            "faction_a_key": f"polity:{int(hegemon.polity_id)}",
                            "faction_b_key": f"league:{league_id}",
                            "polarity": "negative",
                            "strength": 0.30,
                            "start_year": int(year),
                            "expected_duration_years": 22,
                            "settlement_id": sid,
                            "region_id": rid,
                            "source_role": "city_state_hegemony_declared",
                        }
                    ]
                },
            },
        )


def _maybe_resource_disputes(
    ctx: "SimulationContext", polities: list[PolityState], year: int, rng: random.Random
) -> None:
    for rid, members in sorted(_city_states_by_region(ctx, polities).items()):
        if len(members) < 2:
            continue
        members = sorted(members, key=lambda p: int(p.polity_id))
        left, right = members[0], members[1]
        left_note = _note(left)
        right_note = _note(right)
        if not (
            _cooldown_ready(left_note, "last_resource_dispute_year", year, CITY_STATE_DISPUTE_COOLDOWN_YEARS)
            and _cooldown_ready(right_note, "last_resource_dispute_year", year, CITY_STATE_DISPUTE_COOLDOWN_YEARS)
        ):
            continue
        sid = _primary_settlement_id(ctx, left)
        st = ctx.settlements_by_id.get(sid)
        pressure = float(getattr(st, "food_pressure", 0.0) or 0.0) if st is not None else 0.0
        rivalry_pressure = max(pressure, 0.35 + 0.4 * rng.random())
        if rivalry_pressure < 0.48:
            continue
        dispute_kind = "water_rights" if pressure > 0.85 else "trade_route_rivalry"
        _update_note(ctx, left, last_resource_dispute_year=int(year))
        _update_note(ctx, right, last_resource_dispute_year=int(year))
        _record_city_event(
            ctx,
            year,
            "city_state_resource_dispute",
            {
                "polity_id": int(left.polity_id),
                "polity_a_id": int(left.polity_id),
                "polity_b_id": int(right.polity_id),
                "settlement_id": sid,
                "region_id": rid,
                "dispute_kind": dispute_kind,
                "pressure": round(rivalry_pressure, 5),
                "consequences": {
                    "faction_memory": [
                        {
                            "memory_key": f"city_dispute:{int(left.polity_id)}:{int(right.polity_id)}:{int(year)}",
                            "memory_type": "inter_city_rivalry",
                            "faction_a_key": f"polity:{int(left.polity_id)}",
                            "faction_b_key": f"polity:{int(right.polity_id)}",
                            "polarity": "negative",
                            "strength": 0.34,
                            "start_year": int(year),
                            "expected_duration_years": 18,
                            "settlement_id": sid,
                            "region_id": rid,
                            "source_role": "city_state_resource_dispute",
                        }
                    ]
                },
            },
        )
        return


def _record_colony_status(ctx: "SimulationContext", polity: PolityState, year: int) -> PolityState:
    sid = _primary_settlement_id(ctx, polity)
    rid = _primary_region_id(ctx, polity)
    st = ctx.settlements_by_id.get(sid)
    if st is None or (st.founding_reason or "").strip() != "commercial_outpost":
        return polity
    note = _note(polity)
    level = (st.autonomy_level or "").strip().lower() or "autonomous"
    previous = str(note.get("colony_autonomy_level") or "").strip()
    if previous == level:
        return polity
    mother_sid = st.mother_settlement_id
    mother_polity = None
    if mother_sid:
        for candidate in ctx.gov_polities.values():
            if mother_sid in polity_settlement_territory_ids(ctx, candidate.polity_id):
                mother_polity = candidate
                break
    _record_city_event(
        ctx,
        year,
        "city_state_colony_status_changed",
        {
            "polity_id": int(polity.polity_id),
            "mother_polity_id": int(mother_polity.polity_id) if mother_polity else None,
            "settlement_id": sid,
            "region_id": rid,
            "mother_settlement_id": mother_sid,
            "trade_network_id": st.trade_network_id,
            "from_colony_autonomy_level": previous or None,
            "colony_autonomy_level": level,
            "autonomy_state": _autonomy_from_state(ctx, polity, sid),
            "founding_reason": st.founding_reason,
        },
    )
    return _update_note(ctx, polity, colony_autonomy_level=level, mother_settlement_id=mother_sid)


def simulation_city_states_annual_tick(ctx: "SimulationContext", year: int) -> None:
    """Run lightweight city-state political events after government bootstraps."""
    y = int(year)
    rng = random.Random(
        _stable_seed(
            getattr(ctx, "world", "default"),
            getattr(ctx, "placename_rng_salt", 0),
            y,
            "city_states",
        )
    )
    ctx.sync_settlement_resident_counts()
    polities = _city_state_polities(ctx)
    synced: list[PolityState] = []
    for pol in polities:
        current = ctx.gov_polities.get(int(pol.polity_id), pol)
        current = _sync_autonomy_state(ctx, current, y)
        current = _record_colony_status(ctx, current, y)
        current = _maybe_public_works(ctx, current, y, rng)
        current = _maybe_civic_crisis_or_reform(ctx, current, y)
        synced.append(current)
    _maybe_form_leagues(ctx, synced, y)
    _maybe_hegemony(ctx, list(ctx.gov_polities.values()), y)
    _maybe_resource_disputes(ctx, _city_state_polities(ctx), y, rng)


def summarize_city_state_patterns(conn: sqlite3.Connection) -> dict[str, int]:
    """Return compact counts for city-state v1 calibration/reporting."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT event_type, payload_json
        FROM simulation_events
        WHERE event_type LIKE 'city_state_%'
           OR event_type IN (
                'settlement_commercial_outpost_founded',
                'settlement_outpost_autonomized',
                'trade_network_recentered'
           )
        """
    ).fetchall()
    counts: Counter[str] = Counter()
    counts["total_city_state_pattern_events"] = 0
    for row in rows:
        event_type = str(row["event_type"] or "").strip()
        bucket = CITY_STATE_PATTERN_BUCKETS.get(event_type)
        if bucket is None and event_type.startswith("settlement_"):
            bucket = "maritime_colony_lifecycle"
        elif bucket is None and event_type == "trade_network_recentered":
            bucket = "maritime_colony_lifecycle"
        if bucket is None:
            bucket = "other_city_state_event"
        counts[bucket] += 1
        counts["total_city_state_pattern_events"] += 1
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            state = str(
                payload.get("autonomy_state")
                or payload.get("colony_autonomy_level")
                or ""
            ).strip()
            if state:
                counts[f"autonomy_state:{state}"] += 1
    for bucket in sorted(set(CITY_STATE_PATTERN_BUCKETS.values())):
        counts.setdefault(bucket, 0)
    return dict(sorted(counts.items()))


def summarize_city_state_patterns_for_save(save_db_path: Path | str) -> dict[str, int]:
    with sqlite3.connect(save_db_path) as conn:
        return summarize_city_state_patterns(conn)
