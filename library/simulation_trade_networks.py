"""Maritime mercantile settlement networks and commercial outpost founding."""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from library.geography import Route, get_region, list_routes_from

if TYPE_CHECKING:
    from library.geography import Region
    from library.simulation_context import SimulationContext, SimulationPersonRecord


PORT_NETWORK_OUTPOST_THRESHOLD = 0.62
PORT_NETWORK_SUCCESSOR_THRESHOLD = 0.65
PORT_NETWORK_OUTPOST_AUTONOMY_THRESHOLD = 0.75
PORT_NETWORK_MOTHER_DECLINE_THRESHOLD = 0.45
PORT_NETWORK_MIN_MOTHER_RESIDENTS = 25
PORT_NETWORK_OUTPOST_COOLDOWN_YEARS = 25
PORT_NETWORK_WORLD_YEAR_CAP = 2
PORT_NETWORK_OUTPOST_MATURE_YEARS = 50
PORTABLE_KNOWLEDGE_DOMAINS = frozenset(
    {
        "navigation",
        "shipbuilding",
        "writing",
        "accounting",
        "trade_law",
        "craft",
        "art",
    }
)

_COASTAL_REGION_TOKENS = frozenset(
    {
        "coast",
        "coastal",
        "port",
        "harbor",
        "harbour",
        "delta",
        "fishery",
        "fishing",
        "trade",
        "bay",
        "littoral",
        "maritime",
        "estuary",
    }
)
_TRADE_JOB_TOKENS = frozenset(
    {"merchant", "trader", "market", "broker", "caravan", "vendor", "factor"}
)
_TRANSPORT_JOB_TOKENS = frozenset(
    {"sail", "ship", "dock", "ferry", "fish", "boat", "navigator", "pilot", "porter"}
)
_ADMIN_KNOWLEDGE_JOB_TOKENS = frozenset(
    {"scribe", "clerk", "account", "judge", "law", "admin", "steward", "scholar"}
)
_CRAFT_JOB_TOKENS = frozenset(
    {"artisan", "craft", "smith", "potter", "carpenter", "weaver", "dyer", "mason"}
)


@dataclass(frozen=True)
class PortNetworkScore:
    settlement_id: str
    region_id: str
    score: float
    drivers: dict[str, float]


def _stable_seed(*parts: object) -> int:
    text = "|".join(str(part) for part in parts)
    h = 14_695_981_039_346_656_037
    for ch in text:
        h ^= ord(ch)
        h = (h * 1_099_511_628_211) & 0xFFFFFFFFFFFFFFFF
    return h


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _region_text(region: "Region") -> str:
    return " ".join(
        str(getattr(region, attr, "") or "").lower()
        for attr in ("region_id", "region_name", "biome", "terrain", "keywords")
    )


def _region_has_coastal_trade_flavor(region: "Region") -> bool:
    text = _region_text(region)
    return any(token in text for token in _COASTAL_REGION_TOKENS)


def _sea_routes(ctx: "SimulationContext", region_id: str, year: int) -> list[Route]:
    try:
        return [
            route
            for route in list_routes_from(
                region_id,
                world=ctx.world,
                db_path=ctx.db_path,
                simulation_year=int(year),
            )
            if route.route_type.strip().lower() == "sea"
        ]
    except LookupError:
        return []


def _job_text(rec: "SimulationPersonRecord") -> str:
    return str(rec.person.job or rec.person.last_job or "").strip().lower()


def _job_signal(job: str) -> float:
    signal = 0.0
    if any(token in job for token in _TRADE_JOB_TOKENS):
        signal += 1.0
    if any(token in job for token in _TRANSPORT_JOB_TOKENS):
        signal += 0.85
    if any(token in job for token in _ADMIN_KNOWLEDGE_JOB_TOKENS):
        signal += 0.70
    if any(token in job for token in _CRAFT_JOB_TOKENS):
        signal += 0.55
    return min(1.0, signal)


def _adult_records(ctx: "SimulationContext", settlement_id: str, year: int) -> list["SimulationPersonRecord"]:
    records = ctx.current_people_by_settlement().get(settlement_id, ())
    out: list["SimulationPersonRecord"] = []
    for rec in records:
        try:
            if int(year) - int(rec.person.birthyear) >= 16:
                out.append(rec)
        except (TypeError, ValueError):
            continue
    return out


def _settlement_job_density(ctx: "SimulationContext", settlement_id: str, year: int) -> float:
    adults = _adult_records(ctx, settlement_id, year)
    if not adults:
        return 0.0
    weighted = sum(_job_signal(_job_text(rec)) for rec in adults)
    return _clamp01(weighted / max(1, len(adults)))


def _domain_scores(ctx: "SimulationContext", region_id: str) -> dict[str, float]:
    path = Path(getattr(ctx, "save_db_path", "") or "")
    if not path.is_file():
        return {}
    try:
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            exists = conn.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type='view' AND name='simulation_domain_states_readable'
                UNION ALL
                SELECT 1
                FROM sqlite_master
                WHERE type='table' AND name='simulation_domain_states_readable'
                LIMIT 1
                """
            ).fetchone()
            if exists is None:
                return {}
            rows = conn.execute(
                """
                SELECT domain, domain_score
                FROM simulation_domain_states_readable
                WHERE region_id = ?
                """,
                (region_id,),
            ).fetchall()
    except sqlite3.Error:
        return {}
    out: dict[str, float] = {}
    for row in rows:
        domain = str(row["domain"] or "").strip()
        if not domain:
            continue
        try:
            out[domain] = float(row["domain_score"] or 0.0)
        except (TypeError, ValueError):
            continue
    return out


def score_port_network(
    ctx: "SimulationContext", settlement_id: str, year: int
) -> PortNetworkScore:
    sid = (settlement_id or "").strip()
    st = ctx.settlements_by_id.get(sid)
    if st is None:
        return PortNetworkScore(sid, "", 0.0, {})
    rid = (st.region_id or "").strip()
    if (st.status or "").strip().lower() != "active":
        return PortNetworkScore(sid, rid, 0.0, {})
    try:
        region = get_region(rid, world=ctx.world, db_path=ctx.db_path)
    except LookupError:
        return PortNetworkScore(sid, rid, 0.0, {})

    coastal = 1.0 if _region_has_coastal_trade_flavor(region) else 0.0
    route_count = 0.0
    sea_friction_signal = 0.0
    for route in _sea_routes(ctx, rid, year):
        route_count += 1.0
        sea_friction_signal += 1.0 / max(1.0, float(route.friction))
    route_score = _clamp01(sea_friction_signal * 10.0)
    geography_score = _clamp01(0.72 * coastal + 0.28 * min(1.0, route_count / 2.0))

    job_density = _settlement_job_density(ctx, sid, year)
    market_signal = _clamp01(
        float(getattr(st, "market_pull", 0.0) or 0.0) * 0.55
        + min(1.0, float(getattr(st, "prosperity_pool", 1.0) or 0.0) / 2.0) * 0.45
    )
    economy_score = _clamp01(job_density * 0.72 + market_signal * 0.28)

    domain_scores = _domain_scores(ctx, rid)
    knowledge_total = sum(
        max(0.0, float(domain_scores.get(domain, 0.0)))
        for domain in PORTABLE_KNOWLEDGE_DOMAINS
    )
    domain_knowledge_score = _clamp01(knowledge_total / 0.45)
    try:
        from library.simulation_innovation import portable_innovation_score_for_region

        portable_innovation_score = portable_innovation_score_for_region(ctx, rid)
    except Exception:
        portable_innovation_score = 0.0
    knowledge_score = max(domain_knowledge_score, _clamp01(portable_innovation_score))

    score = round(
        0.35 * geography_score
        + 0.25 * route_score
        + 0.20 * economy_score
        + 0.20 * knowledge_score,
        5,
    )
    drivers = {
        "geography": round(geography_score, 5),
        "sea_route_centrality": round(route_score, 5),
        "job_market": round(economy_score, 5),
        "portable_knowledge": round(knowledge_score, 5),
        "portable_innovations": round(portable_innovation_score, 5),
        "job_density": round(job_density, 5),
        "market_signal": round(market_signal, 5),
        "sea_route_count": round(route_count, 5),
    }
    return PortNetworkScore(sid, rid, score, drivers)


def _network_id(st: object) -> str:
    sid = str(getattr(st, "settlement_id", "") or "").strip()
    return str(getattr(st, "trade_network_id", "") or "").strip() or sid


def _outpost_recently_founded(
    ctx: "SimulationContext", mother_settlement_id: str, year: int
) -> bool:
    mother = (mother_settlement_id or "").strip()
    for st in ctx.settlements_by_id.values():
        if (st.mother_settlement_id or "").strip() != mother:
            continue
        if (st.founding_reason or "").strip().lower() != "commercial_outpost":
            continue
        founded = st.founded_sim_year
        if founded is not None and int(year) - int(founded) < PORT_NETWORK_OUTPOST_COOLDOWN_YEARS:
            return True
    return False


def _network_has_active_settlement_in_region(
    ctx: "SimulationContext", region_id: str, trade_network_id: str
) -> bool:
    rid = (region_id or "").strip()
    network = (trade_network_id or "").strip()
    for st in ctx.active_settlements_in_region(rid):
        if _network_id(st) == network:
            return True
    return False


def _destination_headroom(ctx: "SimulationContext", region_id: str) -> int:
    try:
        return int(ctx.effective_regional_population_cap(region_id)) - int(
            ctx.mixed_population_count_in_region(region_id)
        )
    except LookupError:
        return 0


def _destination_candidates(
    ctx: "SimulationContext",
    mother: PortNetworkScore,
    trade_network_id: str,
    year: int,
) -> list[tuple[float, Route]]:
    out: list[tuple[float, Route]] = []
    for route in _sea_routes(ctx, mother.region_id, year):
        try:
            region = get_region(route.to_region_id, world=ctx.world, db_path=ctx.db_path)
        except LookupError:
            continue
        if not _region_has_coastal_trade_flavor(region):
            continue
        if _destination_headroom(ctx, region.region_id) < 4:
            continue
        if _network_has_active_settlement_in_region(ctx, region.region_id, trade_network_id):
            continue
        headroom_score = _clamp01(_destination_headroom(ctx, region.region_id) / 250.0)
        route_score = _clamp01(10.0 / max(1.0, float(route.friction)))
        score = round(0.55 + route_score * 0.25 + headroom_score * 0.20, 5)
        out.append((score, route))
    out.sort(key=lambda item: (-item[0], item[1].to_region_id, item[1].friction))
    return out


def _founder_preference(rec: "SimulationPersonRecord", year: int) -> float:
    try:
        age = int(year) - int(rec.person.birthyear)
    except (TypeError, ValueError):
        age = 0
    if age < 18:
        return 0.0
    job = _job_text(rec)
    signal = _job_signal(job)
    if signal <= 0.0:
        signal = 0.15
    prosperity = rec.person.job_prosperity_01
    if prosperity is None:
        prosperity = 0.35
    return round(signal + float(prosperity) * 0.25, 5)


def _select_founder_households(
    ctx: "SimulationContext", settlement_id: str, year: int, rng: random.Random
) -> list[int]:
    scored: list[tuple[float, int]] = []
    for rec in _adult_records(ctx, settlement_id, year):
        pref = _founder_preference(rec, year)
        if pref <= 0.0:
            continue
        scored.append((pref + rng.random() * 0.00001, int(rec.person_id)))
    scored.sort(key=lambda item: (-item[0], item[1]))
    target = min(len(scored), rng.randint(2, 4))
    selected: list[int] = []
    covered: set[int] = set()
    for _pref, pid in scored:
        if len(selected) >= target:
            break
        household = ctx.settlement_household_member_ids_for_move(pid, int(year))
        if covered.intersection(household):
            continue
        selected.append(pid)
        covered.update(household)
    return selected


def _make_commercial_outpost(
    ctx: "SimulationContext",
    mother_score: PortNetworkScore,
    route: Route,
    founders: list[int],
    trade_network_id: str,
    year: int,
) -> bool:
    active_before = ctx.active_settlements_in_region(route.to_region_id)
    before_ids = set(ctx.settlements_by_id)
    if active_before:
        new_st = ctx.create_additional_active_settlement(route.to_region_id)
        if new_st.settlement_id in before_ids:
            return False
    else:
        new_st = ctx.ensure_active_settlement_for_region(route.to_region_id)
    outpost = replace(
        new_st,
        founding_reason="commercial_outpost",
        mother_settlement_id=mother_score.settlement_id,
        trade_network_id=trade_network_id,
        autonomy_level="dependent",
        founded_sim_year=int(year),
        status="active",
    )
    ctx.settlements_by_id[outpost.settlement_id] = outpost
    ctx.rebuild_settlement_region_index()

    moved: list[int] = []
    group_id = f"commercial_outpost:{mother_score.settlement_id}:{outpost.settlement_id}:{int(year)}"
    for founder_id in founders:
        moved.extend(
            ctx.queue_household_move_to_settlement(
                int(founder_id),
                outpost.settlement_id,
                move_reason="commercial_outpost_founder",
                requested_year=int(year),
                apply_year=int(year) + 1,
                source_event="settlement_commercial_outpost_founded",
                group_id=group_id,
            )
        )
    moved = sorted(dict.fromkeys(moved))
    ctx._record_simulation_event(
        int(year),
        "settlement_commercial_outpost_founded",
        {
            "year": int(year),
            "event_type": "settlement_commercial_outpost_founded",
            "settlement_id": outpost.settlement_id,
            "region_id": outpost.region_id,
            "mother_settlement_id": mother_score.settlement_id,
            "mother_region_id": mother_score.region_id,
            "destination_settlement_id": outpost.settlement_id,
            "destination_region_id": outpost.region_id,
            "route_type": route.route_type,
            "route_friction": round(float(route.friction), 5),
            "port_network_score": mother_score.score,
            "port_network_drivers": mother_score.drivers,
            "founder_person_ids": moved,
            "founder_household_roots": [int(pid) for pid in founders],
            "trade_network_id": trade_network_id,
            "autonomy_level": "dependent",
            "founding_reason": "commercial_outpost",
        },
    )
    return True


def _autonomize_mature_outposts(
    ctx: "SimulationContext",
    scores: dict[str, PortNetworkScore],
    year: int,
) -> None:
    for sid, st in list(ctx.settlements_by_id.items()):
        if (st.status or "").strip().lower() != "active":
            continue
        if (st.autonomy_level or "").strip().lower() != "dependent":
            continue
        if (st.founding_reason or "").strip().lower() != "commercial_outpost":
            continue
        founded = st.founded_sim_year
        if founded is None or int(year) - int(founded) < PORT_NETWORK_OUTPOST_MATURE_YEARS:
            continue
        mother_id = (st.mother_settlement_id or "").strip()
        if not mother_id:
            continue
        outpost_score = scores.get(sid) or score_port_network(ctx, sid, year)
        mother_score = scores.get(mother_id) or score_port_network(ctx, mother_id, year)
        if (
            outpost_score.score < PORT_NETWORK_OUTPOST_AUTONOMY_THRESHOLD
            or mother_score.score >= PORT_NETWORK_MOTHER_DECLINE_THRESHOLD
        ):
            continue
        ctx.settlements_by_id[sid] = replace(st, autonomy_level="autonomous")
        ctx._record_simulation_event(
            int(year),
            "settlement_outpost_autonomized",
            {
                "year": int(year),
                "event_type": "settlement_outpost_autonomized",
                "settlement_id": sid,
                "region_id": st.region_id,
                "mother_settlement_id": mother_id,
                "trade_network_id": _network_id(st),
                "outpost_score": outpost_score.score,
                "mother_score": mother_score.score,
                "autonomy_level": "autonomous",
            },
        )


def _recenter_declined_networks(
    ctx: "SimulationContext",
    scores: dict[str, PortNetworkScore],
    year: int,
) -> None:
    grouped: dict[str, list[str]] = {}
    for sid, st in ctx.settlements_by_id.items():
        grouped.setdefault(_network_id(st), []).append(sid)
    for network_id, member_ids in grouped.items():
        root = ctx.settlements_by_id.get(network_id)
        root_active = (
            root is not None
            and (root.status or "").strip().lower() == "active"
            and ctx.count_alive_in_settlement(network_id) >= 5
        )
        if root_active:
            continue
        candidates: list[PortNetworkScore] = []
        for sid in member_ids:
            st = ctx.settlements_by_id.get(sid)
            if st is None or (st.status or "").strip().lower() != "active":
                continue
            score = scores.get(sid) or score_port_network(ctx, sid, year)
            if score.score >= PORT_NETWORK_SUCCESSOR_THRESHOLD:
                candidates.append(score)
        if not candidates:
            continue
        successor = sorted(candidates, key=lambda s: (-s.score, s.settlement_id))[0]
        st = ctx.settlements_by_id[successor.settlement_id]
        ctx.settlements_by_id[successor.settlement_id] = replace(
            st, autonomy_level="successor"
        )
        ctx._record_simulation_event(
            int(year),
            "trade_network_recentered",
            {
                "year": int(year),
                "event_type": "trade_network_recentered",
                "settlement_id": successor.settlement_id,
                "region_id": successor.region_id,
                "previous_network_root_id": network_id,
                "trade_network_id": network_id,
                "successor_score": successor.score,
                "autonomy_level": "successor",
            },
        )


def simulation_trade_networks_annual_tick(ctx: "SimulationContext", year: int) -> None:
    """Found and maintain generic maritime mercantile settlement networks."""
    y = int(year)
    ctx.sync_settlement_resident_counts()
    rng = random.Random(
        _stable_seed(getattr(ctx, "world", "default"), getattr(ctx, "placename_rng_salt", 0), y, "trade_networks")
    )
    scores = {
        sid: score_port_network(ctx, sid, y)
        for sid, st in ctx.settlements_by_id.items()
        if (st.status or "").strip().lower() == "active"
    }
    _autonomize_mature_outposts(ctx, scores, y)
    _recenter_declined_networks(ctx, scores, y)

    founded_this_year = 0
    mothers = sorted(scores.values(), key=lambda s: (-s.score, s.settlement_id))
    for mother_score in mothers:
        if founded_this_year >= PORT_NETWORK_WORLD_YEAR_CAP:
            break
        if mother_score.score < PORT_NETWORK_OUTPOST_THRESHOLD:
            continue
        mother_st = ctx.settlements_by_id.get(mother_score.settlement_id)
        if mother_st is None or (mother_st.status or "").strip().lower() != "active":
            continue
        if ctx.count_alive_in_settlement(mother_score.settlement_id) < PORT_NETWORK_MIN_MOTHER_RESIDENTS:
            continue
        if _outpost_recently_founded(ctx, mother_score.settlement_id, y):
            continue
        trade_network_id = _network_id(mother_st)
        destinations = _destination_candidates(ctx, mother_score, trade_network_id, y)
        if not destinations:
            continue
        founders = _select_founder_households(ctx, mother_score.settlement_id, y, rng)
        if len(founders) < 2:
            continue
        _dest_score, route = destinations[0]
        if _make_commercial_outpost(
            ctx,
            mother_score,
            route,
            founders,
            trade_network_id,
            y,
        ):
            founded_this_year += 1
