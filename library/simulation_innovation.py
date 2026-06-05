"""Annual innovation seeding, discovery, diffusion, and era-state driver."""

from __future__ import annotations

import math
import random
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from library.innovation_catalog import (
    Innovation,
    InnovationCatalog,
    InnovationCategoryRule,
)
from library.world_save import ensure_checkpoint_schema

if TYPE_CHECKING:
    from library.simulation_context import SimulationContext, SimulationPersonRecord

STARTUP_SEED_META_PREFIX = "innovation_startup_seeded"
INNOVATION_RNG_STREAM = 2_000_113
INNOVATION_SAMPLE_STREAM = 2_000_179
INNOVATION_SETTLEMENT_SAMPLE_CAP = 240
INNOVATION_PROPENSITY_THRESHOLD = 0.20
INNOVATION_MAX_DISCOVERIES_PER_YEAR = 2
STARTER_PREVALENCE_MIN = 0.22
ADOPTED_SCORE_MIN = 0.35
PORTABLE_INNOVATION_SCORE_WEIGHT = 0.035
PORTABLE_INNOVATION_DOMAINS = frozenset(
    {
        "navigation",
        "shipbuilding",
        "writing",
        "accounting",
        "trade_law",
        "craft",
        "art",
        "transport",
    }
)


def simulation_innovation_annual_tick(ctx: "SimulationContext", year: int) -> dict[str, int]:
    catalog = InnovationCatalog.load(ctx.db_path)
    if not catalog.active_innovations():
        return {"seeded": 0, "diffused": 0, "era_updates": 0, "discoveries": 0}
    y = int(year)
    conn = sqlite3.connect(ctx.save_db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_checkpoint_schema(conn)
        seeded = seed_starting_innovations_for_save(conn, ctx, catalog, y)
        diffused = diffuse_innovations_for_save(conn, ctx, catalog, y)
        era_updates = update_innovation_era_state_for_save(conn, catalog, y)
        conn.commit()
    finally:
        conn.close()
    discoveries = discover_innovations_for_year(ctx, y, catalog)
    setattr(ctx, "_innovation_wealth_bonus_cache", None)
    setattr(ctx, "_portable_innovation_score_cache", None)
    return {
        "seeded": seeded,
        "diffused": diffused,
        "era_updates": era_updates,
        "discoveries": discoveries,
    }


def innovation_candidate_allowed(
    candidate: Innovation,
    *,
    known_ids: set[str],
    category_frontiers: dict[str, tuple[int, int]],
    effective_era_rank: int,
    historical_year: int,
    rule: InnovationCategoryRule,
    catalog: InnovationCatalog,
) -> bool:
    if candidate.innovation_id in known_ids:
        return False
    if any(prereq not in known_ids for prereq in candidate.prerequisite_ids):
        return False
    candidate_era_rank = catalog.era_rank(candidate.era_id)
    historical_era = catalog.era_for_year(historical_year)
    historical_era_rank = catalog.era_rank(
        historical_era.era_id if historical_era is not None else None
    )
    allowed_era_rank = max(effective_era_rank, historical_era_rank) + 1
    if candidate_era_rank > allowed_era_rank:
        return False
    frontier_rank, frontier_year = category_frontiers.get(candidate.category, (0, historical_year))
    if frontier_rank <= 0:
        return candidate.rank <= max(1, int(rule.max_rank_jump))
    if candidate.rank > frontier_rank + max(1, int(rule.max_rank_jump)):
        return False
    gap = max(0, int(candidate.history_year) - int(frontier_year))
    if gap > 0 and math.log1p(gap) > float(rule.max_log_gap):
        return False
    return True


def seed_starting_innovations_for_save(
    conn: sqlite3.Connection,
    ctx: "SimulationContext",
    catalog: InnovationCatalog,
    year: int,
) -> int:
    history_start = ctx.get_historical_year(ctx.simulation_start_year)
    meta_key = f"{STARTUP_SEED_META_PREFIX}:{ctx.simulation_start_year}:{history_start}"
    row = conn.execute(
        "SELECT meta_value FROM simulation_meta WHERE meta_key = ?",
        (meta_key,),
    ).fetchone()
    if row is not None:
        return 0
    active_settlements = _active_settlements(ctx)
    if not active_settlements:
        return 0
    eligible = [
        item
        for item in catalog.active_innovations()
        if item.history_year <= history_start
        and item.starter_prevalence >= STARTER_PREVALENCE_MIN
    ]
    if not eligible:
        return 0
    count = 0
    for st in active_settlements:
        sid = str(st.settlement_id)
        rid = str(st.region_id)
        settlement_key = _lookup_or_insert_settlement_key(conn, sid, rid)
        region_key = _lookup_or_insert_region_key(conn, rid)
        if settlement_key is None or region_key is None:
            continue
        polity_id = _polity_id_for_place(ctx, sid, rid)
        for item in eligible:
            score = round(max(0.15, min(1.0, item.starter_prevalence)), 5)
            count += _upsert_known_innovation(
                conn,
                item,
                scope_kind="settlement",
                scope_key=_scope_key("settlement", settlement_key),
                adoption_score=score,
                year=year,
                source_kind="startup_seed",
                polity_id=polity_id,
                region_key=region_key,
                settlement_key=settlement_key,
            )
            count += _upsert_known_innovation(
                conn,
                item,
                scope_kind="region",
                scope_key=_scope_key("region", region_key),
                adoption_score=round(score * 0.92, 5),
                year=year,
                source_kind="startup_seed",
                polity_id=polity_id,
                region_key=region_key,
                settlement_key=settlement_key,
            )
            if polity_id is not None:
                count += _upsert_known_innovation(
                    conn,
                    item,
                    scope_kind="polity",
                    scope_key=_scope_key("polity", polity_id),
                    adoption_score=round(score * 0.85, 5),
                    year=year,
                    source_kind="startup_seed",
                    polity_id=polity_id,
                    region_key=region_key,
                    settlement_key=settlement_key,
                )
    conn.execute(
        """
        INSERT OR REPLACE INTO simulation_meta (meta_key, meta_value)
        VALUES (?, ?)
        """,
        (meta_key, str(year)),
    )
    return count


def diffuse_innovations_for_save(
    conn: sqlite3.Connection,
    ctx: "SimulationContext",
    catalog: InnovationCatalog,
    year: int,
) -> int:
    promoted = _promote_place_knowledge_to_polities(conn, ctx, catalog, year)
    polity_spread = _spread_polity_knowledge(conn, ctx, catalog, year)
    route_spread = _spread_route_knowledge(conn, ctx, catalog, year)
    return promoted + polity_spread + route_spread


def update_innovation_era_state_for_save(
    conn: sqlite3.Connection, catalog: InnovationCatalog, year: int
) -> int:
    rows = conn.execute(
        """
        SELECT scope_kind, scope_key, era_id, polity_id, region_key, settlement_key
        FROM simulation_innovation_knowledge
        WHERE adoption_score >= ? AND status IN ('known', 'adopted')
        """,
        (ADOPTED_SCORE_MIN,),
    ).fetchall()
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for row in rows:
        key = (str(row["scope_kind"]), str(row["scope_key"]))
        item = grouped.setdefault(
            key,
            {
                "counts": {},
                "adopted": 0,
                "polity_id": row["polity_id"],
                "region_key": row["region_key"],
                "settlement_key": row["settlement_key"],
            },
        )
        counts = item["counts"]
        assert isinstance(counts, dict)
        era_id = str(row["era_id"] or "unknown")
        counts[era_id] = int(counts.get(era_id, 0)) + 1
        item["adopted"] = int(item["adopted"]) + 1
        for col in ("polity_id", "region_key", "settlement_key"):
            if item[col] is None and row[col] is not None:
                item[col] = row[col]
    updates = 0
    ts = _utc_now(conn)
    for (scope_kind, scope_key), item in grouped.items():
        counts = item["counts"]
        assert isinstance(counts, dict)
        era_rank = 0
        era_id = "paleolithic"
        for era in catalog.eras:
            adopted = int(counts.get(era.era_id, 0))
            if adopted >= int(era.advancement_threshold):
                era_rank = int(era.sort_order)
                era_id = era.era_id
        next_era = catalog.era_by_rank(era_rank + 1)
        next_count = int(counts.get(next_era.era_id, 0)) if next_era is not None else 0
        conn.execute(
            """
            INSERT INTO simulation_innovation_era_state (
                scope_kind, scope_key, era_id, era_rank, adopted_count,
                next_era_adopted_count, latest_year, polity_id, region_key,
                settlement_key, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope_kind, scope_key) DO UPDATE SET
                era_id = excluded.era_id,
                era_rank = excluded.era_rank,
                adopted_count = excluded.adopted_count,
                next_era_adopted_count = excluded.next_era_adopted_count,
                latest_year = excluded.latest_year,
                polity_id = COALESCE(excluded.polity_id, simulation_innovation_era_state.polity_id),
                region_key = COALESCE(excluded.region_key, simulation_innovation_era_state.region_key),
                settlement_key = COALESCE(
                    excluded.settlement_key,
                    simulation_innovation_era_state.settlement_key
                ),
                updated_at = excluded.updated_at
            """,
            (
                scope_kind,
                scope_key,
                era_id,
                era_rank,
                int(item["adopted"]),
                next_count,
                int(year),
                item["polity_id"],
                item["region_key"],
                item["settlement_key"],
                ts,
                ts,
            ),
        )
        updates += 1
    return updates


def discover_innovations_for_year(
    ctx: "SimulationContext", year: int, catalog: InnovationCatalog
) -> int:
    y = int(year)
    rng = random.Random(
        y * INNOVATION_RNG_STREAM + int(getattr(ctx, "placename_rng_salt", 0)) + 2243
    )
    discoveries = 0
    historical_year = ctx.get_historical_year(y)
    for settlement_id, residents in sorted(ctx.current_people_by_settlement().items()):
        if discoveries >= INNOVATION_MAX_DISCOVERIES_PER_YEAR:
            break
        if not residents:
            continue
        if discover_innovation_for_settlement(
            ctx,
            y,
            str(settlement_id),
            list(residents),
            catalog,
            rng=rng,
            historical_year=historical_year,
        ):
            discoveries += 1
    return discoveries


def discover_innovation_for_settlement(
    ctx: "SimulationContext",
    year: int,
    settlement_id: str,
    residents: list["SimulationPersonRecord"],
    catalog: InnovationCatalog,
    *,
    rng: random.Random,
    historical_year: int | None = None,
    force: bool = False,
) -> bool:
    from library.simulation_incidents import knowledge_culture_propensity

    y = int(year)
    hist_year = (
        int(historical_year) if historical_year is not None else ctx.get_historical_year(y)
    )
    sampled = ctx.decision_sample_records(
        residents,
        year=y,
        scope=f"settlement:{settlement_id}:innovation",
        stream=INNOVATION_SAMPLE_STREAM,
        cap=INNOVATION_SETTLEMENT_SAMPLE_CAP,
    )
    adults = [rec for rec in sampled if _adult_alive(rec, y)]
    if not adults:
        return False
    propensities = {int(rec.person_id): knowledge_culture_propensity(rec) for rec in adults}
    max_propensity = max(propensities.values(), default=0.0)
    if not force and max_propensity < INNOVATION_PROPENSITY_THRESHOLD:
        return False
    st = ctx.settlements_by_id.get(settlement_id)
    region_id = str(getattr(st, "region_id", "") or _region_id_for_residents(ctx, adults))
    polity_id = _polity_id_for_place(ctx, settlement_id, region_id)
    state = _local_innovation_state(ctx, catalog, settlement_id, region_id, polity_id)
    effective_era_rank = max(
        state["effective_era_rank"],
        catalog.era_rank(
            catalog.era_for_year(hist_year).era_id
            if catalog.era_for_year(hist_year) is not None
            else None
        ),
    )
    max_ahead_years = min(250, max(25, 40 + effective_era_rank * 25))
    candidates: list[Innovation] = []
    for item in catalog.candidate_innovations(hist_year, max_ahead_years=max_ahead_years):
        rule = catalog.category_rule(item.category)
        if innovation_candidate_allowed(
            item,
            known_ids=state["known_ids"],
            category_frontiers=state["frontiers"],
            effective_era_rank=effective_era_rank,
            historical_year=hist_year,
            rule=rule,
            catalog=catalog,
        ):
            candidates.append(item)
    if not candidates:
        return False
    creator = _choose_creator(adults, propensities, rng)
    if creator is None:
        return False
    candidate = _choose_innovation_candidate(candidates, catalog, hist_year, rng)
    rule = catalog.category_rule(candidate.category)
    chance = min(
        0.55,
        (0.10 + max_propensity * 0.36)
        * max(0.05, rule.base_discovery_chance)
        * (0.55 + candidate.spreadability * 0.45)
        * (1.05 - min(0.8, candidate.complexity * 0.45)),
    )
    if not force and rng.random() >= chance:
        return False
    patron = _choose_patron(adults, creator, rng)
    witness_ids = [
        int(rec.person_id)
        for rec in adults
        if int(rec.person_id)
        not in {int(creator.person_id), int(getattr(patron, "person_id", -1))}
    ][:2]
    novelty = round(
        min(
            0.32,
            0.08
            + max(0.0, propensities[int(creator.person_id)] - INNOVATION_PROPENSITY_THRESHOLD)
            * 0.18
            + max(0.0, 1.0 - candidate.complexity) * 0.05,
        ),
        5,
    )
    payload = {
        "year": y,
        "event_type": "knowledge_culture",
        "incident_kind": "innovation",
        "innovation_id": candidate.innovation_id,
        "innovation_category": candidate.category,
        "innovation_era_id": candidate.era_id,
        "innovation_analogue_name": candidate.analogue_name,
        "source_innovation_title": candidate.source_title,
        "knowledge_domain": candidate.domain,
        "historical_year": hist_year,
        "motive": "experimentation",
        "creator_person_id": int(creator.person_id),
        "patron_person_id": int(patron.person_id) if patron is not None else None,
        "witness_person_ids": witness_ids,
        "settlement_id": settlement_id,
        "region_id": region_id,
        "polity_id": polity_id,
        "actor_knowledge_culture_propensity": round(
            propensities[int(creator.person_id)], 5
        ),
        "historical_importance": round(0.36 + novelty * 1.25, 5),
        "novelty_value": novelty,
        "consequences": {
            "knowledge_state": {
                "domain": candidate.domain,
                "state_delta": round(max(0.01, novelty * 0.45), 5),
                "state_key": f"{region_id}:{candidate.domain}",
            },
            "innovation_adoption": {
                "innovation_id": candidate.innovation_id,
                "analogue_name": candidate.analogue_name,
                "category": candidate.category,
                "domain": candidate.domain,
                "era_id": candidate.era_id,
                "history_year": candidate.history_year,
                "adoption_score": round(max(ADOPTED_SCORE_MIN, novelty * 2.8), 5),
                "polity_id": polity_id,
            },
        },
        "genome_signals": _genome_signal_payload(
            creator,
            (
                "curiosity",
                "creativity",
                "intellect",
                "focus",
                "perception",
                "discipline",
                "civics",
                "adaptability",
            ),
        ),
    }
    ctx._record_simulation_event(y, "knowledge_culture", payload)
    return True


def innovation_wealth_bonus_for_settlement(
    ctx: "SimulationContext", settlement_id: str
) -> float:
    cache = getattr(ctx, "_innovation_wealth_bonus_cache", None)
    if cache is None:
        cache = _build_innovation_wealth_bonus_cache(ctx)
        setattr(ctx, "_innovation_wealth_bonus_cache", cache)
    return float(cache.get(str(settlement_id or "").strip(), 0.0))


def portable_innovation_score_for_region(ctx: "SimulationContext", region_id: str) -> float:
    cache = getattr(ctx, "_portable_innovation_score_cache", None)
    if cache is None:
        cache = _build_portable_innovation_score_cache(ctx)
        setattr(ctx, "_portable_innovation_score_cache", cache)
    return float(cache.get(str(region_id or "").strip(), 0.0))


def _active_settlements(ctx: "SimulationContext") -> list[object]:
    out = []
    for st in ctx.settlements_by_id.values():
        status = str(getattr(st, "status", "active") or "active").strip().lower()
        if status in {"", "active"}:
            out.append(st)
    return sorted(out, key=lambda st: str(getattr(st, "settlement_id", "")))


def _lookup_or_insert_region_key(conn: sqlite3.Connection, region_id: str) -> int | None:
    rid = str(region_id or "").strip()
    if not rid:
        return None
    conn.execute(
        "INSERT OR IGNORE INTO simulation_region_lookup (region_id) VALUES (?)",
        (rid,),
    )
    row = conn.execute(
        "SELECT region_key FROM simulation_region_lookup WHERE region_id = ?",
        (rid,),
    ).fetchone()
    return int(row["region_key"]) if row is not None else None


def _lookup_or_insert_settlement_key(
    conn: sqlite3.Connection, settlement_id: str, region_id: str
) -> int | None:
    sid = str(settlement_id or "").strip()
    if not sid:
        return None
    row = conn.execute(
        "SELECT settlement_key FROM simulation_settlement_lookup WHERE settlement_id = ?",
        (sid,),
    ).fetchone()
    if row is not None:
        return int(row["settlement_key"])
    region_key = _lookup_or_insert_region_key(conn, region_id)
    if region_key is None:
        return None
    conn.execute(
        """
        INSERT OR IGNORE INTO simulation_settlement_lookup (settlement_id, region_key)
        VALUES (?, ?)
        """,
        (sid, region_key),
    )
    row = conn.execute(
        "SELECT settlement_key FROM simulation_settlement_lookup WHERE settlement_id = ?",
        (sid,),
    ).fetchone()
    return int(row["settlement_key"]) if row is not None else None


def _scope_key(scope_kind: str, key: int) -> str:
    return f"{scope_kind}:{int(key)}"


def _utc_now(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()
    return str(row[0])


def _upsert_known_innovation(
    conn: sqlite3.Connection,
    item: Innovation,
    *,
    scope_kind: str,
    scope_key: str,
    adoption_score: float,
    year: int,
    source_kind: str,
    polity_id: int | None = None,
    region_key: int | None = None,
    settlement_key: int | None = None,
) -> int:
    existing = conn.execute(
        """
        SELECT adoption_score FROM simulation_innovation_knowledge
        WHERE innovation_id = ? AND scope_kind = ? AND scope_key = ?
        """,
        (item.innovation_id, scope_kind, scope_key),
    ).fetchone()
    before = float(existing["adoption_score"]) if existing is not None else -1.0
    score = round(max(0.0, min(1.0, float(adoption_score))), 5)
    if existing is not None and before >= score:
        return 0
    ts = _utc_now(conn)
    conn.execute(
        """
        INSERT INTO simulation_innovation_knowledge (
            innovation_id, innovation_name, category, domain, era_id,
            scope_kind, scope_key, status, adoption_score,
            first_known_year, latest_known_year, source_kind,
            polity_id, region_key, settlement_key, details_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(innovation_id, scope_kind, scope_key) DO UPDATE SET
            status = excluded.status,
            adoption_score = max(
                simulation_innovation_knowledge.adoption_score,
                excluded.adoption_score
            ),
            latest_known_year = excluded.latest_known_year,
            source_kind = excluded.source_kind,
            polity_id = COALESCE(excluded.polity_id, simulation_innovation_knowledge.polity_id),
            region_key = COALESCE(excluded.region_key, simulation_innovation_knowledge.region_key),
            settlement_key = COALESCE(
                excluded.settlement_key,
                simulation_innovation_knowledge.settlement_key
            ),
            details_json = excluded.details_json,
            updated_at = excluded.updated_at
        """,
        (
            item.innovation_id,
            item.analogue_name,
            item.category,
            item.domain,
            item.era_id,
            scope_kind,
            scope_key,
            "adopted" if score >= ADOPTED_SCORE_MIN else "known",
            score,
            int(year),
            int(year),
            source_kind,
            int(polity_id) if polity_id is not None else None,
            int(region_key) if region_key is not None else None,
            int(settlement_key) if settlement_key is not None else None,
            '{"history_year":%d}' % int(item.history_year),
            ts,
            ts,
        ),
    )
    return 1


def _polity_id_for_place(
    ctx: "SimulationContext", settlement_id: str, region_id: str
) -> int | None:
    try:
        from library.polity import polity_for_region, polity_for_settlement

        pol = polity_for_settlement(ctx, settlement_id)
        if pol is None:
            pol = polity_for_region(ctx, region_id)
        return int(pol.polity_id) if pol is not None else None
    except Exception:
        return None


def _promote_place_knowledge_to_polities(
    conn: sqlite3.Connection,
    ctx: "SimulationContext",
    catalog: InnovationCatalog,
    year: int,
) -> int:
    rows = conn.execute(
        """
        SELECT innovation_id, adoption_score, region_key, settlement_key
        FROM simulation_innovation_knowledge
        WHERE scope_kind IN ('settlement', 'region') AND adoption_score >= ?
        """,
        (ADOPTED_SCORE_MIN,),
    ).fetchall()
    count = 0
    for row in rows:
        item = catalog.innovation_by_id(str(row["innovation_id"] or ""))
        if item is None:
            continue
        settlement_id = _settlement_id_from_key(conn, row["settlement_key"])
        region_id = _region_id_from_key(conn, row["region_key"])
        polity_id = _polity_id_for_place(ctx, settlement_id or "", region_id or "")
        if polity_id is None:
            continue
        rule = catalog.category_rule(item.category)
        count += _upsert_known_innovation(
            conn,
            item,
            scope_kind="polity",
            scope_key=_scope_key("polity", polity_id),
            adoption_score=float(row["adoption_score"]) * 0.85 * rule.polity_spread_multiplier,
            year=year,
            source_kind="polity_integration",
            polity_id=polity_id,
            region_key=row["region_key"],
            settlement_key=row["settlement_key"],
        )
    return count


def _spread_polity_knowledge(
    conn: sqlite3.Connection,
    ctx: "SimulationContext",
    catalog: InnovationCatalog,
    year: int,
) -> int:
    try:
        from library.polity import polity_regions, polity_settlement_territory_ids
    except Exception:
        return 0
    rows = conn.execute(
        """
        SELECT innovation_id, adoption_score, polity_id
        FROM simulation_innovation_knowledge
        WHERE scope_kind = 'polity' AND adoption_score >= ? AND polity_id IS NOT NULL
        """,
        (ADOPTED_SCORE_MIN,),
    ).fetchall()
    count = 0
    for row in rows:
        item = catalog.innovation_by_id(str(row["innovation_id"] or ""))
        if item is None:
            continue
        polity_id = int(row["polity_id"])
        rule = catalog.category_rule(item.category)
        spread_score = (
            float(row["adoption_score"])
            * item.spreadability
            * rule.spread_multiplier
            * rule.polity_spread_multiplier
            * 0.35
        )
        for rid in polity_regions(ctx, polity_id):
            region_key = _lookup_or_insert_region_key(conn, rid)
            if region_key is None:
                continue
            count += _upsert_known_innovation(
                conn,
                item,
                scope_kind="region",
                scope_key=_scope_key("region", region_key),
                adoption_score=spread_score,
                year=year,
                source_kind="same_polity_diffusion",
                polity_id=polity_id,
                region_key=region_key,
            )
        for sid in polity_settlement_territory_ids(ctx, polity_id):
            st = ctx.settlements_by_id.get(sid)
            rid = str(getattr(st, "region_id", "") or "")
            settlement_key = _lookup_or_insert_settlement_key(conn, sid, rid)
            region_key = _lookup_or_insert_region_key(conn, rid)
            if settlement_key is None:
                continue
            count += _upsert_known_innovation(
                conn,
                item,
                scope_kind="settlement",
                scope_key=_scope_key("settlement", settlement_key),
                adoption_score=spread_score * 1.05,
                year=year,
                source_kind="same_polity_diffusion",
                polity_id=polity_id,
                region_key=region_key,
                settlement_key=settlement_key,
            )
    return count


def _spread_route_knowledge(
    conn: sqlite3.Connection,
    ctx: "SimulationContext",
    catalog: InnovationCatalog,
    year: int,
) -> int:
    try:
        from library.geography import list_routes_from
    except Exception:
        return 0
    rows = conn.execute(
        """
        SELECT innovation_id, adoption_score, region_key
        FROM simulation_innovation_knowledge
        WHERE scope_kind = 'region' AND adoption_score >= ? AND region_key IS NOT NULL
        """,
        (ADOPTED_SCORE_MIN,),
    ).fetchall()
    count = 0
    for row in rows:
        item = catalog.innovation_by_id(str(row["innovation_id"] or ""))
        if item is None:
            continue
        source_region_id = _region_id_from_key(conn, row["region_key"])
        if not source_region_id:
            continue
        rule = catalog.category_rule(item.category)
        try:
            routes = list_routes_from(
                source_region_id,
                world=ctx.world,
                db_path=ctx.db_path,
                simulation_year=int(year),
            )
        except Exception:
            continue
        for route in routes:
            target_region_key = _lookup_or_insert_region_key(conn, route.to_region_id)
            if target_region_key is None:
                continue
            friction = max(0.1, float(route.friction or 0.1))
            spread_score = (
                float(row["adoption_score"])
                * item.spreadability
                * rule.spread_multiplier
                * 0.24
                / (1.0 + friction)
            )
            if spread_score < 0.035:
                continue
            count += _upsert_known_innovation(
                conn,
                item,
                scope_kind="region",
                scope_key=_scope_key("region", target_region_key),
                adoption_score=spread_score,
                year=year,
                source_kind="route_diffusion",
                region_key=target_region_key,
            )
    return count


def _settlement_id_from_key(conn: sqlite3.Connection, settlement_key: object) -> str | None:
    if settlement_key is None:
        return None
    row = conn.execute(
        "SELECT settlement_id FROM simulation_settlement_lookup WHERE settlement_key = ?",
        (int(settlement_key),),
    ).fetchone()
    return str(row["settlement_id"]) if row is not None else None


def _region_id_from_key(conn: sqlite3.Connection, region_key: object) -> str | None:
    if region_key is None:
        return None
    row = conn.execute(
        "SELECT region_id FROM simulation_region_lookup WHERE region_key = ?",
        (int(region_key),),
    ).fetchone()
    return str(row["region_id"]) if row is not None else None


def _local_innovation_state(
    ctx: "SimulationContext",
    catalog: InnovationCatalog,
    settlement_id: str,
    region_id: str,
    polity_id: int | None,
) -> dict[str, object]:
    known_ids: set[str] = set()
    frontiers: dict[str, tuple[int, int]] = {}
    effective_era_rank = 0
    if not Path(ctx.save_db_path).exists():
        return {
            "known_ids": known_ids,
            "frontiers": frontiers,
            "effective_era_rank": effective_era_rank,
        }
    conn = sqlite3.connect(ctx.save_db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT innovation_id, category, scope_kind, scope_key, adoption_score
            FROM simulation_innovation_knowledge
            WHERE adoption_score > 0.0
            """
        ).fetchall()
        wanted_scopes = _wanted_scopes(conn, settlement_id, region_id, polity_id)
        for row in rows:
            scope = (str(row["scope_kind"]), str(row["scope_key"]))
            if scope not in wanted_scopes:
                continue
            iid = str(row["innovation_id"] or "")
            item = catalog.innovation_by_id(iid)
            if item is None:
                continue
            known_ids.add(iid)
            old_rank, old_year = frontiers.get(item.category, (0, -9_999_999))
            if item.rank > old_rank or (
                item.rank == old_rank and item.history_year > old_year
            ):
                frontiers[item.category] = (item.rank, item.history_year)
        for row in conn.execute(
            """
            SELECT scope_kind, scope_key, era_rank
            FROM simulation_innovation_era_state
            """
        ).fetchall():
            if (str(row["scope_kind"]), str(row["scope_key"])) in wanted_scopes:
                effective_era_rank = max(effective_era_rank, int(row["era_rank"] or 0))
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return {
        "known_ids": known_ids,
        "frontiers": frontiers,
        "effective_era_rank": effective_era_rank,
    }


def _wanted_scopes(
    conn: sqlite3.Connection,
    settlement_id: str,
    region_id: str,
    polity_id: int | None,
) -> set[tuple[str, str]]:
    scopes: set[tuple[str, str]] = set()
    row = conn.execute(
        "SELECT settlement_key FROM simulation_settlement_lookup WHERE settlement_id = ?",
        (settlement_id,),
    ).fetchone()
    if row is not None:
        scopes.add(("settlement", _scope_key("settlement", int(row["settlement_key"]))))
    row = conn.execute(
        "SELECT region_key FROM simulation_region_lookup WHERE region_id = ?",
        (region_id,),
    ).fetchone()
    if row is not None:
        scopes.add(("region", _scope_key("region", int(row["region_key"]))))
    if polity_id is not None:
        scopes.add(("polity", _scope_key("polity", int(polity_id))))
    return scopes


def _choose_creator(
    adults: list["SimulationPersonRecord"],
    propensities: dict[int, float],
    rng: random.Random,
) -> "SimulationPersonRecord | None":
    candidates = [
        rec
        for rec in adults
        if propensities.get(int(rec.person_id), 0.0) >= INNOVATION_PROPENSITY_THRESHOLD
    ]
    if not candidates:
        candidates = list(adults)
    weights = [
        max(0.001, propensities.get(int(rec.person_id), 0.0) ** 2)
        for rec in candidates
    ]
    total = sum(weights)
    if total <= 0.0:
        return None
    return rng.choices(candidates, weights=weights, k=1)[0]


def _choose_patron(
    adults: list["SimulationPersonRecord"],
    creator: "SimulationPersonRecord",
    rng: random.Random,
) -> "SimulationPersonRecord | None":
    pool = [rec for rec in adults if int(rec.person_id) != int(creator.person_id)]
    if not pool:
        return None
    weights = [
        0.25
        + float(getattr(rec.person, "job_prosperity_01", 0.35) or 0.35) * 0.8
        + _trait_positive(rec, "civics") * 0.2
        for rec in pool
    ]
    return rng.choices(pool, weights=weights, k=1)[0]


def _choose_innovation_candidate(
    candidates: list[Innovation],
    catalog: InnovationCatalog,
    historical_year: int,
    rng: random.Random,
) -> Innovation:
    weights: list[float] = []
    for item in candidates:
        rule = catalog.category_rule(item.category)
        ahead = max(0, item.history_year - int(historical_year))
        weights.append(
            max(
                0.001,
                rule.base_discovery_chance
                * (0.35 + item.spreadability)
                * (1.1 - min(0.95, item.complexity * 0.5))
                / (1.0 + math.log1p(ahead) * 0.25),
            )
        )
    return rng.choices(candidates, weights=weights, k=1)[0]


def _adult_alive(rec: "SimulationPersonRecord", year: int) -> bool:
    if rec.person.deathyear is not None and int(rec.person.deathyear) <= int(year):
        return False
    return int(year) - int(rec.person.birthyear) >= 16


def _region_id_for_residents(
    ctx: "SimulationContext", adults: list["SimulationPersonRecord"]
) -> str:
    for rec in adults:
        sid = str(
            rec.person.current_settlement_id or rec.person.birthplace_settlement_id or ""
        ).strip()
        st = ctx.settlements_by_id.get(sid)
        if st is not None and str(st.region_id or "").strip():
            return str(st.region_id).strip()
        if rec.person.birthplace_region_id:
            return str(rec.person.birthplace_region_id)
    return ""


def _trait_positive(rec: "SimulationPersonRecord", trait: str) -> float:
    try:
        value = float((rec.person.genome or {}).get(trait, 0.0))
    except (TypeError, ValueError):
        value = 0.0
    return max(0.0, min(1.0, (value - 35.0) / 65.0))


def _genome_signal_payload(
    rec: "SimulationPersonRecord", traits: tuple[str, ...]
) -> dict[str, float]:
    out: dict[str, float] = {}
    genome = rec.person.genome or {}
    for trait in traits:
        try:
            out[trait] = round(float(genome.get(trait, 0.0)), 5)
        except (TypeError, ValueError):
            out[trait] = 0.0
    return out


def _build_innovation_wealth_bonus_cache(ctx: "SimulationContext") -> dict[str, float]:
    catalog = InnovationCatalog.load(ctx.db_path)
    bonuses = {sid: 0.0 for sid in ctx.settlements_by_id}
    if not Path(ctx.save_db_path).exists():
        return bonuses
    conn = sqlite3.connect(ctx.save_db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT innovation_id, adoption_score, region_key, settlement_key
            FROM simulation_innovation_knowledge
            WHERE adoption_score >= 0.15
            """
        ).fetchall()
        settlement_by_key = _settlement_key_map(conn)
        region_by_key = _region_key_map(conn)
    except sqlite3.Error:
        conn.close()
        return bonuses
    finally:
        try:
            conn.close()
        except Exception:
            pass
    for row in rows:
        item = catalog.innovation_by_id(str(row["innovation_id"] or ""))
        if item is None:
            continue
        rule = catalog.category_rule(item.category)
        value = float(row["adoption_score"] or 0.0) * rule.wealth_weight
        settlement_id = settlement_by_key.get(row["settlement_key"])
        if settlement_id:
            bonuses[settlement_id] = bonuses.get(settlement_id, 0.0) + value
            continue
        region_id = region_by_key.get(row["region_key"])
        if region_id:
            for sid, st in ctx.settlements_by_id.items():
                if str(st.region_id) == region_id:
                    bonuses[sid] = bonuses.get(sid, 0.0) + value * 0.55
    return {sid: round(min(0.12, value), 5) for sid, value in bonuses.items()}


def _build_portable_innovation_score_cache(ctx: "SimulationContext") -> dict[str, float]:
    scores: dict[str, float] = {}
    if not Path(ctx.save_db_path).exists():
        return scores
    conn = sqlite3.connect(ctx.save_db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT domain, adoption_score, region_key
            FROM simulation_innovation_knowledge
            WHERE scope_kind = 'region' AND adoption_score >= 0.15
            """
        ).fetchall()
        region_by_key = _region_key_map(conn)
    except sqlite3.Error:
        conn.close()
        return scores
    finally:
        try:
            conn.close()
        except Exception:
            pass
    for row in rows:
        domain = str(row["domain"] or "").strip()
        if domain not in PORTABLE_INNOVATION_DOMAINS:
            continue
        rid = region_by_key.get(row["region_key"])
        if not rid:
            continue
        scores[rid] = scores.get(rid, 0.0) + float(row["adoption_score"] or 0.0) * PORTABLE_INNOVATION_SCORE_WEIGHT
    return {rid: round(min(1.0, value), 5) for rid, value in scores.items()}


def _settlement_key_map(conn: sqlite3.Connection) -> dict[int, str]:
    return {
        int(row["settlement_key"]): str(row["settlement_id"])
        for row in conn.execute(
            "SELECT settlement_key, settlement_id FROM simulation_settlement_lookup"
        ).fetchall()
    }


def _region_key_map(conn: sqlite3.Connection) -> dict[int, str]:
    return {
        int(row["region_key"]): str(row["region_id"])
        for row in conn.execute(
            "SELECT region_key, region_id FROM simulation_region_lookup"
        ).fetchall()
    }
