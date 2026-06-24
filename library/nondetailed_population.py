"""SQLite-backed city-directory population for non-detailed people."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
import math
import random
import sqlite3
import time
from pathlib import Path
from typing import Iterable

from library.geography import list_routes_from
from library import simulation_timing
from library.settlement_affordances import (
    cached_settlement_affordance_profile,
    growth_invariant_cap,
    new_settlement_backfill_cap,
)
from library.settlements import settlement_attraction_score


NONDETAILED_JOB_FAMILIES: tuple[str, ...] = (
    "food",
    "military",
    "craft",
    "trade",
    "care",
    "admin",
    "religious",
    "criminal",
    "dependent",
    "other",
)

_ADULT_JOB_FAMILIES: tuple[str, ...] = (
    "food",
    "military",
    "craft",
    "trade",
    "care",
    "admin",
    "religious",
    "criminal",
    "other",
)

_PERSON_ID_TABLES: tuple[str, ...] = (
    "simulation_people",
    "simulation_people_light",
    "simulation_people_nondetailed",
)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(float(lo), min(float(hi), float(value)))


@dataclass(frozen=True)
class NondetailedPersonSeed:
    birthyear: int
    gender: str
    region_id: str | None = None
    settlement_id: str | None = None
    species: str | None = None
    culture: str | None = None
    job_family: str | None = None
    is_partnered: bool = False
    partner_person_id: int | None = None
    father_id: int | None = None
    mother_id: int | None = None
    child_count: int = 0
    name_key: str | None = None


@dataclass(frozen=True)
class NondetailedTickResult:
    deaths: int = 0
    job_updates: int = 0
    newly_partnered: int = 0
    births: int = 0
    alive_after: int = 0
    total_after: int = 0
    elapsed_seconds: float = 0.0


@dataclass(frozen=True)
class NondetailedMigrationResult:
    moved: int = 0
    source_settlements: int = 0
    elapsed_seconds: float = 0.0


@dataclass(frozen=True)
class NondetailedEconomyResult:
    affected_settlements: int = 0
    total_population_seen: int = 0
    elapsed_seconds: float = 0.0


def normalize_nondetailed_job_family(value: object) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "farm": "food",
        "farming": "food",
        "farmer": "food",
        "herding": "food",
        "herder": "food",
        "labor": "food",
        "defense": "military",
        "soldier": "military",
        "war": "military",
        "artisan": "craft",
        "service": "care",
        "household_care": "care",
        "domestic_service": "care",
        "government": "admin",
        "office": "admin",
        "religion": "religious",
        "vice": "criminal",
        "child": "dependent",
    }
    raw = aliases.get(raw, raw)
    return raw if raw in NONDETAILED_JOB_FAMILIES else "other"


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def max_global_person_id(conn: sqlite3.Connection) -> int:
    """Return the highest person id used by detailed, passive, or non-detailed stores."""
    max_id = 0
    for table in _PERSON_ID_TABLES:
        if not _table_exists(conn, table):
            continue
        row = conn.execute(f"SELECT MAX(person_id) AS m FROM {table}").fetchone()
        value = row["m"] if isinstance(row, sqlite3.Row) else row[0]
        if value is not None:
            max_id = max(max_id, int(value))
    return max_id


def next_global_person_id(
    conn: sqlite3.Connection, *, minimum: int | None = None
) -> int:
    """Return a person id above every detailed, passive, and non-detailed row."""
    max_id = max_global_person_id(conn)
    if minimum is not None:
        max_id = max(max_id, int(minimum) - 1)
    return max_id + 1


def _person_id_exists_anywhere(conn: sqlite3.Connection, person_id: int) -> bool:
    pid = int(person_id)
    for table in _PERSON_ID_TABLES:
        if not _table_exists(conn, table):
            continue
        row = conn.execute(
            f"SELECT 1 FROM {table} WHERE person_id = ? LIMIT 1",
            (pid,),
        ).fetchone()
        if row is not None:
            return True
    return False


def _colliding_nondetailed_ids(
    conn: sqlite3.Connection, person_ids: Iterable[int] | None = None
) -> list[int]:
    if not _table_exists(conn, "simulation_people_nondetailed"):
        return []
    collision_checks: list[str] = []
    if _table_exists(conn, "simulation_people"):
        collision_checks.append(
            "EXISTS (SELECT 1 FROM simulation_people d WHERE d.person_id = p.person_id)"
        )
    if _table_exists(conn, "simulation_people_light"):
        collision_checks.append(
            "EXISTS (SELECT 1 FROM simulation_people_light l WHERE l.person_id = p.person_id)"
        )
    if not collision_checks:
        return []
    params: list[object] = []
    id_filter = ""
    clean_ids = [int(pid) for pid in (person_ids or ()) if int(pid) > 0]
    if clean_ids:
        id_filter = f" AND p.person_id IN ({', '.join('?' for _ in clean_ids)})"
        params.extend(clean_ids)
    rows = conn.execute(
        f"""
        SELECT p.person_id
        FROM simulation_people_nondetailed p
        WHERE ({" OR ".join(collision_checks)})
          {id_filter}
        ORDER BY p.person_id
        """,
        params,
    ).fetchall()
    return [int(row["person_id"] if isinstance(row, sqlite3.Row) else row[0]) for row in rows]


def repair_nondetailed_person_id_collisions(
    conn: sqlite3.Connection,
    *,
    person_ids: Iterable[int] | None = None,
    start_person_id: int | None = None,
    reserved_person_ids: Iterable[int] | None = None,
) -> dict[int, int]:
    """Rekey non-detailed rows that reuse detailed/passive person ids."""
    collisions = set(_colliding_nondetailed_ids(conn, person_ids))
    reserved = {int(pid) for pid in (reserved_person_ids or ()) if int(pid) > 0}
    if reserved and _table_exists(conn, "simulation_people_nondetailed"):
        ids_to_check = reserved
        if person_ids is not None:
            requested = {int(pid) for pid in person_ids if int(pid) > 0}
            ids_to_check = reserved.intersection(requested)
        if ids_to_check:
            rows = conn.execute(
                f"""
                SELECT person_id
                FROM simulation_people_nondetailed
                WHERE person_id IN ({", ".join("?" for _ in ids_to_check)})
                """,
                tuple(sorted(ids_to_check)),
            ).fetchall()
            collisions.update(
                int(row["person_id"] if isinstance(row, sqlite3.Row) else row[0])
                for row in rows
            )
    if not collisions:
        return {}
    next_id = next_global_person_id(conn, minimum=start_person_id)
    repaired: dict[int, int] = {}
    for old_id in sorted(collisions):
        while _person_id_exists_anywhere(conn, next_id):
            next_id += 1
        new_id = next_id
        next_id += 1
        conn.execute(
            """
            UPDATE simulation_people_nondetailed
            SET person_id = ?
            WHERE person_id = ?
            """,
            (new_id, old_id),
        )
        for ref_col in ("partner_person_id", "father_id", "mother_id"):
            conn.execute(
                f"""
                UPDATE simulation_people_nondetailed
                SET {ref_col} = ?
                WHERE {ref_col} = ?
                """,
                (new_id, old_id),
            )
        repaired[int(old_id)] = int(new_id)
    return repaired


def _region_key(conn: sqlite3.Connection, region_id: object) -> int | None:
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
    return int(row["region_key"] if isinstance(row, sqlite3.Row) else row[0])


def _settlement_key(
    conn: sqlite3.Connection, settlement_id: object, region_id: object | None = None
) -> int | None:
    sid = str(settlement_id or "").strip()
    if not sid:
        return None
    row = conn.execute(
        "SELECT settlement_key FROM simulation_settlement_lookup WHERE settlement_id = ?",
        (sid,),
    ).fetchone()
    if row is not None:
        return int(row["settlement_key"] if isinstance(row, sqlite3.Row) else row[0])
    rid = str(region_id or "").strip()
    if not rid and ":" in sid:
        rid = sid.split(":", 1)[0].strip()
    rkey = _region_key(conn, rid)
    if rkey is None:
        return None
    conn.execute(
        """
        INSERT OR IGNORE INTO simulation_settlement_lookup (settlement_id, region_key)
        VALUES (?, ?)
        """,
        (sid, rkey),
    )
    row = conn.execute(
        "SELECT settlement_key FROM simulation_settlement_lookup WHERE settlement_id = ?",
        (sid,),
    ).fetchone()
    return int(row["settlement_key"] if isinstance(row, sqlite3.Row) else row[0])


def add_nondetailed_person(
    conn: sqlite3.Connection,
    seed: NondetailedPersonSeed,
    *,
    person_id: int | None = None,
) -> int:
    """Insert one non-detailed directory row and return its person id."""
    assigned_person_id = (
        next_global_person_id(conn) if person_id is None else int(person_id)
    )
    region_key = _region_key(conn, seed.region_id)
    settlement_key = _settlement_key(conn, seed.settlement_id, seed.region_id)
    job_family = normalize_nondetailed_job_family(seed.job_family or "other")
    cols = (
        "birthyear",
        "deathyear",
        "is_alive",
        "gender",
        "species_key",
        "culture_key",
        "birthplace_region_key",
        "birthplace_settlement_key",
        "current_settlement_key",
        "job_family",
        "is_partnered",
        "partner_person_id",
        "father_id",
        "mother_id",
        "child_count",
        "name_key",
    )
    values: tuple[object, ...] = (
        int(seed.birthyear),
        None,
        1,
        str(seed.gender or ""),
        seed.species,
        seed.culture,
        region_key,
        settlement_key,
        settlement_key,
        job_family,
        1 if seed.is_partnered else 0,
        seed.partner_person_id,
        seed.father_id,
        seed.mother_id,
        int(seed.child_count),
        seed.name_key,
    )
    sql = f"""
        INSERT INTO simulation_people_nondetailed (person_id, {", ".join(cols)})
        VALUES (?, {", ".join("?" for _ in cols)})
    """
    conn.execute(sql, (assigned_person_id, *values))
    return assigned_person_id


def add_nondetailed_births_from_place_counts(
    conn: sqlite3.Connection,
    births_by_place: dict[tuple[str, str, str, str], int],
    *,
    year: int,
    start_person_id: int | None = None,
) -> int:
    """Insert detailed-overflow newborns into the SQLite city-directory table."""
    positive = [
        (key, int(count))
        for key, count in sorted((births_by_place or {}).items())
        if int(count) > 0
    ]
    if not positive:
        return 0
    next_id = next_global_person_id(conn, minimum=start_person_id)
    rows: list[tuple[object, ...]] = []
    for (rid, sid, species, culture), count in positive:
        region_key = _region_key(conn, rid)
        settlement_key = _settlement_key(conn, sid, rid)
        if settlement_key is None:
            continue
        for _i in range(count):
            pid = next_id
            next_id += 1
            rows.append(
                (
                    pid,
                    int(year),
                    "Female" if (pid & 1) else "Male",
                    (species or None),
                    (culture or None),
                    region_key,
                    settlement_key,
                    settlement_key,
                )
            )
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO simulation_people_nondetailed (
            person_id, birthyear, deathyear, is_alive, gender, species_key, culture_key,
            birthplace_region_key, birthplace_settlement_key, current_settlement_key,
            job_family, is_partnered, partner_person_id, father_id, mother_id,
            child_count, name_key
        )
        VALUES (?, ?, NULL, 1, ?, ?, ?, ?, ?, ?, 'dependent', 0, NULL, NULL, NULL, 0, NULL)
        """,
        rows,
    )
    return len(rows)


def nondetailed_alive_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM simulation_people_nondetailed WHERE is_alive = 1"
    ).fetchone()
    return int(row["c"] if isinstance(row, sqlite3.Row) else row[0])


def nondetailed_total_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS c FROM simulation_people_nondetailed").fetchone()
    return int(row["c"] if isinstance(row, sqlite3.Row) else row[0])


def nondetailed_counts_by_settlement(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT sl.settlement_id, COUNT(*) AS c
        FROM simulation_people_nondetailed p
        JOIN simulation_settlement_lookup sl
          ON sl.settlement_key = p.current_settlement_key
        WHERE p.is_alive = 1
        GROUP BY sl.settlement_id
        """
    ).fetchall()
    return {str(row["settlement_id"]): int(row["c"]) for row in rows}


def nondetailed_counts_by_region(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT rl.region_id, COUNT(*) AS c
        FROM simulation_people_nondetailed p
        LEFT JOIN simulation_settlement_lookup sl
          ON sl.settlement_key = p.current_settlement_key
        LEFT JOIN simulation_region_lookup rl
          ON rl.region_key = COALESCE(sl.region_key, p.birthplace_region_key)
        WHERE p.is_alive = 1
        GROUP BY rl.region_id
        """
    ).fetchall()
    return {str(row["region_id"]): int(row["c"]) for row in rows if row["region_id"]}


def nondetailed_job_counts_by_settlement(
    conn: sqlite3.Connection,
    settlement_ids: Iterable[str] | None = None,
) -> dict[str, dict[str, int]]:
    params: tuple[object, ...] = ()
    where = ""
    ids = [str(sid).strip() for sid in (settlement_ids or ()) if str(sid).strip()]
    if ids:
        where = f"AND sl.settlement_id IN ({', '.join('?' for _ in ids)})"
        params = tuple(ids)
    rows = conn.execute(
        f"""
        SELECT sl.settlement_id, COALESCE(NULLIF(p.job_family, ''), 'other') AS job_family,
               COUNT(*) AS c
        FROM simulation_people_nondetailed p
        JOIN simulation_settlement_lookup sl
          ON sl.settlement_key = p.current_settlement_key
        WHERE p.is_alive = 1
          {where}
        GROUP BY sl.settlement_id, job_family
        """,
        params,
    ).fetchall()
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        sid = str(row["settlement_id"])
        fam = normalize_nondetailed_job_family(row["job_family"])
        out.setdefault(sid, {})[fam] = int(row["c"])
    return out


def apply_nondetailed_job_family_economy_effects(
    conn: sqlite3.Connection,
    ctx: object,
    *,
    year: int,
) -> NondetailedEconomyResult:
    """Apply aggregate job-family pressure from city-directory rows to settlements."""
    started = time.perf_counter()
    job_counts = nondetailed_job_counts_by_settlement(conn)
    affected = 0
    total_seen = 0
    for sid, counts in sorted(job_counts.items()):
        st = getattr(ctx, "settlements_by_id", {}).get(sid)
        if st is None or str(getattr(st, "status", "") or "").strip().lower() != "active":
            continue
        total = sum(max(0, int(v)) for v in counts.values())
        if total <= 0:
            continue
        total_seen += total
        food = counts.get("food", 0) / total
        military = counts.get("military", 0) / total
        craft = counts.get("craft", 0) / total
        trade = counts.get("trade", 0) / total
        care = counts.get("care", 0) / total
        admin = counts.get("admin", 0) / total
        religious = counts.get("religious", 0) / total
        criminal = counts.get("criminal", 0) / total

        food_gap = max(0.0, 0.30 - food)
        food_surplus = max(0.0, food - 0.38)
        military_burden = max(0.0, military - 0.18)
        productive_surplus = craft * 0.025 + trade * 0.03
        civic_support = care * 0.018 + admin * 0.016 + religious * 0.010
        disorder = criminal * 0.030

        old_food = float(getattr(st, "food_pressure", 0.0) or 0.0)
        old_stability = float(getattr(st, "stability", 0.0) or 0.0)
        old_market = float(getattr(st, "market_pull", 0.0) or 0.0)
        old_pool = float(getattr(st, "prosperity_pool", 1.0) or 1.0)

        food_pressure = _clamp(
            old_food + food_gap * 0.13 + military_burden * 0.05 - food_surplus * 0.08,
            0.0,
            2.0,
        )
        prosperity_pool = _clamp(
            old_pool + productive_surplus + trade * 0.010 - food_gap * 0.030 - military_burden * 0.015,
            0.05,
            2.5,
        )
        market_pull = _clamp(old_market + trade * 0.020 + craft * 0.010 + admin * 0.006, 0.0, 1.0)
        stability = _clamp(
            old_stability + civic_support + military * 0.006 - disorder - food_gap * 0.025,
            0.0,
            1.0,
        )
        if (
            abs(food_pressure - old_food) < 0.0005
            and abs(prosperity_pool - old_pool) < 0.0005
            and abs(market_pull - old_market) < 0.0005
            and abs(stability - old_stability) < 0.0005
        ):
            continue
        ctx.settlements_by_id[sid] = replace(
            st,
            food_pressure=food_pressure,
            prosperity_pool=prosperity_pool,
            market_pull=market_pull,
            stability=stability,
        )
        affected += 1
        if hasattr(ctx, "_record_simulation_event"):
            ctx._record_simulation_event(
                int(year),
                "nondetailed_job_family_economy_effect",
                {
                    "settlement_id": sid,
                    "region_id": getattr(st, "region_id", None),
                    "population": total,
                    "food_share": round(food, 4),
                    "military_share": round(military, 4),
                    "craft_share": round(craft, 4),
                    "trade_share": round(trade, 4),
                    "care_share": round(care, 4),
                    "admin_share": round(admin, 4),
                    "food_pressure_delta": round(food_pressure - old_food, 5),
                    "prosperity_pool_delta": round(prosperity_pool - old_pool, 5),
                    "market_pull_delta": round(market_pull - old_market, 5),
                    "stability_delta": round(stability - old_stability, 5),
                },
            )
    return NondetailedEconomyResult(
        affected_settlements=affected,
        total_population_seen=total_seen,
        elapsed_seconds=time.perf_counter() - started,
    )


def run_nondetailed_sql_migration(
    conn: sqlite3.Connection,
    ctx: object,
    *,
    year: int,
    max_migrants_per_settlement: int = 1_000,
) -> NondetailedMigrationResult:
    """Move bounded sets of non-detailed adults toward existing attractive settlements."""
    started = time.perf_counter()
    counts = nondetailed_counts_by_settlement(conn)
    if not counts:
        return NondetailedMigrationResult()
    region_counts = nondetailed_counts_by_region(conn)
    detailed_by_region: dict[str, int] = {}
    try:
        census = ctx.alive_census_cache()
        detailed_by_region = {
            str(rid): int(count) for rid, count in getattr(census, "count_by_region", {}).items()
        }
    except Exception:
        detailed_by_region = {}
    passive_by_region: dict[str, int] = {}
    try:
        current_year = int(getattr(ctx, "current_year", year) or year)
        for rec in getattr(ctx, "passive_people", {}).values():
            p = rec.person
            if p.deathyear is not None and int(p.deathyear) <= current_year:
                continue
            rid_p = str(getattr(p, "birthplace_region_id", "") or "").strip()
            if not rid_p:
                sid_p = str(
                    getattr(p, "current_settlement_id", None)
                    or getattr(p, "birthplace_settlement_id", "")
                    or ""
                ).strip()
                if ":" in sid_p:
                    rid_p = sid_p.split(":", 1)[0].strip()
            if rid_p:
                passive_by_region[rid_p] = passive_by_region.get(rid_p, 0) + 1
        latest_year = None
        latest = getattr(ctx, "latest_passive_cohort_year", None)
        if callable(latest):
            latest_year = latest()
        else:
            latest_year = max(
                (int(c.sim_year) for c in getattr(ctx, "passive_cohorts", ())),
                default=None,
            )
        for cohort in getattr(ctx, "passive_cohorts", ()):
            if latest_year is None or int(cohort.sim_year) != int(latest_year):
                continue
            rid_c = str(cohort.region_id or "").strip()
            if rid_c:
                passive_by_region[rid_c] = passive_by_region.get(rid_c, 0) + max(
                    0, int(cohort.population_count)
                )
    except Exception:
        passive_by_region = {}
    mixed_region_counts = {
        rid: int(region_counts.get(rid, 0))
        + int(detailed_by_region.get(rid, 0))
        + int(passive_by_region.get(rid, 0))
        for rid in set(region_counts) | set(detailed_by_region) | set(passive_by_region)
    }
    moved_total = 0
    source_count = 0
    rng = random.Random(int(year) * 800_011 + int(getattr(ctx, "placename_rng_salt", 0)) + 93_337)
    for source_sid, pop in sorted(counts.items()):
        st = getattr(ctx, "settlements_by_id", {}).get(source_sid)
        if st is None or str(getattr(st, "status", "") or "").strip().lower() != "active":
            continue
        if int(pop) <= 0:
            continue
        rid = str(getattr(st, "region_id", "") or "").strip()
        try:
            cap = max(1, int(ctx.effective_regional_population_cap(rid)))
        except Exception:
            cap = max(1, int(pop))
        region_pop = int(mixed_region_counts.get(rid, 0))
        regional_pressure = region_pop / cap
        food_pressure = float(getattr(st, "food_pressure", 0.0) or 0.0)
        prosperity = float(getattr(st, "prosperity_pool", 1.0) or 1.0)
        source_pressure = max(
            0.0,
            regional_pressure - 0.88,
            food_pressure - 0.72,
            0.65 - prosperity,
        )
        if source_pressure <= 0.0:
            continue
        dest = _pick_nondetailed_destination(ctx, st, year=int(year), rng=rng)
        if dest is None or dest.settlement_id == source_sid:
            continue
        migrants = min(
            int(max_migrants_per_settlement),
            max(1, int(math.ceil(int(pop) * min(0.035, source_pressure * 0.025)))),
        )
        conn.execute("DROP TABLE IF EXISTS temp_nondetailed_migrants")
        conn.execute(
            """
            CREATE TEMP TABLE temp_nondetailed_migrants AS
            SELECT person_id
            FROM simulation_people_nondetailed
            WHERE is_alive = 1
              AND current_settlement_key = (
                SELECT settlement_key
                FROM simulation_settlement_lookup
                WHERE settlement_id = ?
              )
              AND (? - birthyear) >= 14
            ORDER BY ((person_id * 1103515245 + ?) % 2147483647), person_id
            LIMIT ?
            """,
            (source_sid, int(year), int(year), int(migrants)),
        )
        conn.execute(
            """
            UPDATE simulation_people_nondetailed
            SET current_settlement_key = (
                SELECT settlement_key
                FROM simulation_settlement_lookup
                WHERE settlement_id = ?
            )
            WHERE person_id IN (SELECT person_id FROM temp_nondetailed_migrants)
            """,
            (dest.settlement_id,),
        )
        moved = int(conn.execute("SELECT changes()").fetchone()[0])
        if moved <= 0:
            continue
        moved_total += moved
        source_count += 1
        if hasattr(ctx, "_record_simulation_event"):
            event_payload = {
                "from_settlement_id": source_sid,
                "to_settlement_id": dest.settlement_id,
                "from_region_id": rid,
                "to_region_id": getattr(dest, "region_id", None),
                "migrant_count": moved,
                "source_pressure": round(source_pressure, 4),
                "source_food_pressure": round(food_pressure, 4),
                "source_prosperity_pool": round(prosperity, 4),
                "destination_market_pull": round(float(getattr(dest, "market_pull", 0.0) or 0.0), 4),
                "destination_prosperity_pool": round(float(getattr(dest, "prosperity_pool", 1.0) or 1.0), 4),
            }
            try:
                profile = cached_settlement_affordance_profile(ctx, dest, year=year)
                event_payload.update(
                    {
                        "destination_affordance_role": profile.selected_role,
                        "destination_affordance_pull": round(profile.migration_pull, 4),
                        "destination_large_population_enablers": list(
                            profile.large_population_enablers
                        ),
                    }
                )
            except Exception:
                pass
            ctx._record_simulation_event(
                int(year),
                "nondetailed_settlement_migration",
                event_payload,
            )
    return NondetailedMigrationResult(
        moved=moved_total,
        source_settlements=source_count,
        elapsed_seconds=time.perf_counter() - started,
    )


def _pick_nondetailed_destination(ctx: object, source_st: object, *, year: int, rng: random.Random):
    origin_rid = str(getattr(source_st, "region_id", "") or "").strip()
    allowed_regions = {origin_rid}
    try:
        for route in list_routes_from(
            origin_rid,
            world=getattr(ctx, "world", "default"),
            db_path=getattr(ctx, "db_path"),
            simulation_year=int(year),
        ):
            dst = str(getattr(route, "to_region_id", "") or "").strip()
            if dst:
                allowed_regions.add(dst)
    except Exception:
        pass
    candidates = [
        st
        for st in getattr(ctx, "settlements_by_id", {}).values()
        if str(getattr(st, "region_id", "") or "").strip() in allowed_regions
        and str(getattr(st, "status", "") or "").strip().lower() == "active"
    ]
    mixed_counts_by_settlement = None
    mixed_counts_fn = getattr(ctx, "mixed_population_counts_by_settlement", None)
    if callable(mixed_counts_fn):
        try:
            mixed_counts_by_settlement = {
                str(sid): int(count)
                for sid, count in mixed_counts_fn().items()
            }
        except Exception:
            mixed_counts_by_settlement = None
    mixed_count_fn = getattr(ctx, "mixed_population_count_in_settlement", None)
    scored: list[tuple[float, object]] = []
    for st in candidates:
        sid = str(getattr(st, "settlement_id", "") or "").strip()
        if not sid:
            continue
        score = settlement_attraction_score(st, ctx=ctx, year=year)
        try:
            profile = cached_settlement_affordance_profile(ctx, st, year=year)
            residents = (
                int(mixed_counts_by_settlement.get(sid, 0))
                if mixed_counts_by_settlement is not None
                else int(mixed_count_fn(sid))
                if callable(mixed_count_fn)
                else max(0, int(getattr(st, "resident_count", 0) or 0))
            )
            region_cap = max(1, int(ctx.effective_regional_population_cap(profile.region_id)))
            soft_site_cap = max(
                24,
                int(round(region_cap * 0.18 * profile.population_ceiling_multiplier)),
            )
            headroom = _clamp(1.15 - residents / soft_site_cap, 0.18, 1.35)
            score *= headroom * (0.82 + profile.migration_pull * 0.38)
        except Exception:
            pass
        if sid == str(getattr(source_st, "settlement_id", "") or ""):
            score *= 0.62
        if score > 0.0:
            scored.append((score, st))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], str(getattr(item[1], "settlement_id", ""))))
    top = scored[: min(5, len(scored))]
    return rng.choices([st for _, st in top], weights=[score for score, _ in top], k=1)[0]


def seed_nondetailed_from_active_settlements(
    conn: sqlite3.Connection,
    ctx: object,
    *,
    year: int,
    population_scale: float,
    start_person_id: int | None = None,
) -> int:
    """Seed or top up city-directory rows from currently active settlements."""
    scale = max(0.0, float(population_scale))
    if scale <= 0.0:
        return 0
    repaired = repair_nondetailed_person_id_collisions(
        conn,
        start_person_id=(
            int(start_person_id)
            if start_person_id is not None
            else int(getattr(ctx, "next_person_id", 1))
        ),
    )
    settlements = [
        st
        for st in getattr(ctx, "settlements_by_id", {}).values()
        if str(getattr(st, "status", "") or "").strip().lower() == "active"
    ]
    if not settlements:
        return 0
    by_region: dict[str, list[object]] = {}
    for st in settlements:
        by_region.setdefault(str(getattr(st, "region_id", "") or ""), []).append(st)
    next_id = next_global_person_id(
        conn,
        minimum=(
            int(start_person_id)
            if start_person_id is not None
            else int(getattr(ctx, "next_person_id", 1))
        ),
    )
    current_by_settlement = nondetailed_counts_by_settlement(conn)
    rows: list[tuple[object, ...]] = []
    chunk = 50_000
    inserted = 0

    def flush() -> None:
        nonlocal rows
        if not rows:
            return
        conn.executemany(
            """
            INSERT INTO simulation_people_nondetailed (
                person_id, birthyear, deathyear, is_alive, gender, species_key, culture_key,
                birthplace_region_key, birthplace_settlement_key, current_settlement_key,
                job_family, is_partnered, partner_person_id, father_id, mother_id,
                child_count, name_key
            )
            VALUES (?, ?, NULL, 1, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, 0, NULL)
            """,
            rows,
        )
        rows = []

    for rid, region_settlements in sorted(by_region.items()):
        if not rid:
            continue
        try:
            target = int(round(ctx.effective_regional_population_cap(rid) * scale))
        except Exception:
            target = 0
        if target <= 0:
            continue
        weights: list[tuple[object, float, object | None]] = []
        for st in region_settlements:
            sid = str(getattr(st, "settlement_id", "") or "").strip()
            if not sid:
                continue
            profile = None
            try:
                profile = cached_settlement_affordance_profile(ctx, st, year=year)
            except Exception:
                profile = None
            weights.append(
                (
                    st,
                    max(0.01, settlement_attraction_score(st, ctx=ctx, year=year)),
                    profile,
                )
            )
        if not weights:
            continue
        total_weight = sum(weight for _, weight, _ in weights)
        for st, weight, profile in weights:
            target_count = max(0, int(round(target * weight / total_weight)))
            sid = str(getattr(st, "settlement_id", "") or "").strip()
            existing_alive = int(current_by_settlement.get(sid, 0))
            if profile is not None:
                target_count = growth_invariant_cap(profile, target_count)
                detailed_alive = 0
                try:
                    detailed_alive = int(ctx.count_alive_in_settlement(sid))
                except Exception:
                    detailed_alive = max(0, int(getattr(st, "resident_count", 0) or 0))
                cap = new_settlement_backfill_cap(
                    profile,
                    st,
                    year=year,
                    detailed_alive=detailed_alive,
                )
                if cap is not None:
                    target_count = min(target_count, max(0, cap - detailed_alive))
            count = max(0, target_count - existing_alive)
            if count <= 0:
                continue
            region_key = _region_key(conn, getattr(st, "region_id", None))
            settlement_key = _settlement_key(
                conn, sid, getattr(st, "region_id", None)
            )
            species = "Human"
            culture = str(getattr(st, "name_culture_primary", "") or "") or None
            for i in range(count):
                pid = next_id
                next_id += 1
                age = (pid * 37 + int(year)) % 91
                birthyear = int(year) - age
                gender = "Female" if (pid & 1) else "Male"
                job = "dependent" if age < 14 else _ADULT_JOB_FAMILIES[(pid * 13) % len(_ADULT_JOB_FAMILIES)]
                partnered = 0
                rows.append(
                    (
                        pid,
                        birthyear,
                        gender,
                        species,
                        culture,
                        region_key,
                        settlement_key,
                        settlement_key,
                        job,
                        partnered,
                    )
                )
                inserted += 1
                if len(rows) >= chunk:
                    flush()
    flush()
    if inserted or repaired:
        setattr(
            ctx,
            "next_person_id",
            max(
                int(getattr(ctx, "next_person_id", 1)),
                next_global_person_id(conn, minimum=next_id),
            ),
        )
    return inserted


def run_nondetailed_sql_annual_tick(
    conn: sqlite3.Connection,
    *,
    year: int,
    max_new_partnerships: int = 25_000,
    start_person_id: int | None = None,
) -> NondetailedTickResult:
    """Apply one annual set-based directory tick inside an existing transaction."""
    started = time.perf_counter()
    y = int(year)
    prof = simulation_timing.active_for_year(y)
    t0 = time.perf_counter()
    repair_nondetailed_person_id_collisions(conn)

    conn.execute(
        """
        UPDATE simulation_people_nondetailed
        SET is_alive = 0,
            deathyear = ?,
            partner_person_id = NULL,
            is_partnered = 0
        WHERE is_alive = 1
          AND (
            (? - birthyear >= 117 AND ((person_id * 1103515245 + ?) % 10000) < 9200)
            OR (? - birthyear BETWEEN 112 AND 116 AND ((person_id * 1103515245 + ?) % 10000) < 7800)
            OR (? - birthyear BETWEEN 105 AND 111 AND ((person_id * 1103515245 + ?) % 10000) < 5200)
            OR (? - birthyear BETWEEN 100 AND 104 AND ((person_id * 1103515245 + ?) % 10000) < 2400)
            OR (? - birthyear BETWEEN 70 AND 99 AND ((person_id * 1103515245 + ?) % 100) < 12)
            OR (? - birthyear BETWEEN 45 AND 69 AND ((person_id * 1103515245 + ?) % 1000) < 18)
            OR (? - birthyear BETWEEN 14 AND 44 AND ((person_id * 1103515245 + ?) % 1000) < 5)
            OR (? - birthyear < 14 AND ((person_id * 1103515245 + ?) % 1000) < 10)
          )
        """,
        (
            y,
            y,
            y,
            y,
            y + 1,
            y,
            y + 2,
            y,
            y + 3,
            y,
            y + 4,
            y,
            y + 5,
            y,
            y + 6,
            y,
            y + 7,
        ),
    )
    deaths = int(conn.execute("SELECT changes()").fetchone()[0])
    if prof:
        simulation_timing.accumulate("nondetailed_sql.deaths", time.perf_counter() - t0)
        t0 = time.perf_counter()

    conn.execute(
        """
        UPDATE simulation_people_nondetailed
        SET is_partnered = 0,
            partner_person_id = NULL
        WHERE is_alive = 1
          AND is_partnered = 1
          AND (
            partner_person_id IS NULL
            OR NOT EXISTS (
                SELECT 1
                FROM simulation_people_nondetailed partner
                WHERE partner.person_id = simulation_people_nondetailed.partner_person_id
                  AND partner.is_alive = 1
            )
          )
        """
    )

    conn.execute(
        """
        UPDATE simulation_people_nondetailed
        SET job_family = CASE
            WHEN (? - birthyear) < 14 THEN 'dependent'
            WHEN ((person_id + COALESCE(current_settlement_key, 0) * 17 + ?) % 100) < 42 THEN 'food'
            WHEN ((person_id + COALESCE(current_settlement_key, 0) * 17 + ?) % 100) < 55 THEN 'military'
            WHEN ((person_id + COALESCE(current_settlement_key, 0) * 17 + ?) % 100) < 70 THEN 'craft'
            WHEN ((person_id + COALESCE(current_settlement_key, 0) * 17 + ?) % 100) < 82 THEN 'trade'
            WHEN ((person_id + COALESCE(current_settlement_key, 0) * 17 + ?) % 100) < 91 THEN 'care'
            WHEN ((person_id + COALESCE(current_settlement_key, 0) * 17 + ?) % 100) < 95 THEN 'admin'
            WHEN ((person_id + COALESCE(current_settlement_key, 0) * 17 + ?) % 100) < 98 THEN 'religious'
            ELSE 'other'
        END
        WHERE is_alive = 1
        """,
        (y, y, y, y, y, y, y, y),
    )
    job_updates = int(conn.execute("SELECT changes()").fetchone()[0])
    if prof:
        simulation_timing.accumulate("nondetailed_sql.jobs", time.perf_counter() - t0)
        t0 = time.perf_counter()

    conn.execute("DROP TABLE IF EXISTS temp_nondetailed_new_partner_pairs")
    conn.execute(
        """
        CREATE TEMP TABLE temp_nondetailed_new_partner_pairs AS
        WITH eligible AS (
            SELECT
                person_id,
                current_settlement_key,
                CASE
                    WHEN lower(COALESCE(gender, '')) LIKE 'm%' THEN 'm'
                    WHEN lower(COALESCE(gender, '')) LIKE 'f%' THEN 'f'
                    ELSE ''
                END AS sex,
                ((person_id * 1664525 + ?) % 2147483647) AS sort_key
            FROM simulation_people_nondetailed
            WHERE is_alive = 1
              AND is_partnered = 0
              AND partner_person_id IS NULL
              AND current_settlement_key IS NOT NULL
              AND (? - birthyear) BETWEEN 18 AND 45
        ),
        ranked AS (
            SELECT
                person_id,
                current_settlement_key,
                sex,
                ROW_NUMBER() OVER (
                    PARTITION BY current_settlement_key, sex
                    ORDER BY sort_key, person_id
                ) AS rn,
                COUNT(*) OVER (
                    PARTITION BY current_settlement_key, sex
                ) AS sex_count
            FROM eligible
            WHERE sex IN ('m', 'f')
        ),
        men AS (
            SELECT person_id, current_settlement_key, rn
            FROM ranked
            WHERE sex = 'm'
              AND rn <= ((sex_count + 1) / 2)
        ),
        women AS (
            SELECT person_id, current_settlement_key, rn
            FROM ranked
            WHERE sex = 'f'
              AND rn <= ((sex_count + 1) / 2)
        )
        SELECT
            men.person_id AS male_id,
            women.person_id AS female_id
        FROM men
        JOIN women
          ON women.current_settlement_key = men.current_settlement_key
         AND women.rn = men.rn
        ORDER BY men.current_settlement_key, men.rn
        LIMIT ?
        """,
        (y, y, int(max_new_partnerships)),
    )
    conn.execute(
        """
        UPDATE simulation_people_nondetailed
        SET is_partnered = 1,
            partner_person_id = (
                SELECT female_id
                FROM temp_nondetailed_new_partner_pairs pairs
                WHERE pairs.male_id = simulation_people_nondetailed.person_id
            )
        WHERE person_id IN (SELECT male_id FROM temp_nondetailed_new_partner_pairs)
        """
    )
    newly_partnered = int(conn.execute("SELECT changes()").fetchone()[0])
    conn.execute(
        """
        UPDATE simulation_people_nondetailed
        SET is_partnered = 1,
            partner_person_id = (
                SELECT male_id
                FROM temp_nondetailed_new_partner_pairs pairs
                WHERE pairs.female_id = simulation_people_nondetailed.person_id
            )
        WHERE person_id IN (SELECT female_id FROM temp_nondetailed_new_partner_pairs)
        """
    )
    newly_partnered += int(conn.execute("SELECT changes()").fetchone()[0])
    if prof:
        simulation_timing.accumulate("nondetailed_sql.partnerships", time.perf_counter() - t0)
        t0 = time.perf_counter()

    next_person_id = next_global_person_id(conn, minimum=start_person_id)
    conn.execute("DROP TABLE IF EXISTS temp_nondetailed_birth_mothers")
    conn.execute(
        """
        CREATE TEMP TABLE temp_nondetailed_birth_mothers AS
        SELECT person_id AS mother_id,
               partner_person_id AS father_id,
               birthplace_region_key,
               birthplace_settlement_key,
               current_settlement_key,
               species_key,
               culture_key,
               ROW_NUMBER() OVER (ORDER BY current_settlement_key, person_id) AS rn
        FROM simulation_people_nondetailed
        WHERE is_alive = 1
          AND lower(gender) LIKE 'f%'
          AND is_partnered = 1
          AND (? - birthyear) BETWEEN 18 AND 38
          AND ((person_id * 22695477 + ?) % 100) < 16
        """,
        (y, y),
    )
    births_row = conn.execute("SELECT COUNT(*) AS c FROM temp_nondetailed_birth_mothers").fetchone()
    births = int(births_row["c"] if isinstance(births_row, sqlite3.Row) else births_row[0])
    if births:
        conn.execute(
            """
            INSERT INTO simulation_people_nondetailed (
                person_id, birthyear, deathyear, is_alive, gender, species_key, culture_key,
                birthplace_region_key, birthplace_settlement_key, current_settlement_key,
                job_family, is_partnered, partner_person_id, father_id, mother_id,
                child_count, name_key
            )
            SELECT ? + rn - 1,
                   ?,
                   NULL,
                   1,
                   CASE WHEN ((mother_id + rn) % 2) = 0 THEN 'Male' ELSE 'Female' END,
                   species_key,
                   culture_key,
                   birthplace_region_key,
                   current_settlement_key,
                   current_settlement_key,
                   'dependent',
                   0,
                   NULL,
                   father_id,
                   mother_id,
                   0,
                   NULL
            FROM temp_nondetailed_birth_mothers
            """,
            (next_person_id, y),
        )
        conn.execute(
            """
            UPDATE simulation_people_nondetailed
            SET child_count = child_count + 1
            WHERE person_id IN (SELECT mother_id FROM temp_nondetailed_birth_mothers)
            """
        )
        conn.execute(
            """
            UPDATE simulation_people_nondetailed
            SET child_count = child_count + 1
            WHERE person_id IN (
                SELECT father_id
                FROM temp_nondetailed_birth_mothers
                WHERE father_id IS NOT NULL
            )
            """
        )
    if prof:
        simulation_timing.accumulate("nondetailed_sql.births", time.perf_counter() - t0)

    alive_after = nondetailed_alive_count(conn)
    total_after = nondetailed_total_count(conn)
    return NondetailedTickResult(
        deaths=deaths,
        job_updates=job_updates,
        newly_partnered=newly_partnered,
        births=births,
        alive_after=alive_after,
        total_after=total_after,
        elapsed_seconds=time.perf_counter() - started,
    )


def run_nondetailed_sql_annual_tick_for_save(
    save_db_path: str | Path,
    *,
    year: int,
    max_new_partnerships: int = 25_000,
    start_person_id: int | None = None,
) -> NondetailedTickResult:
    from library.world_save import ensure_checkpoint_schema

    with sqlite3.connect(save_db_path) as conn:
        conn.row_factory = sqlite3.Row
        ensure_checkpoint_schema(conn)
        result = run_nondetailed_sql_annual_tick(
            conn,
            year=int(year),
            max_new_partnerships=int(max_new_partnerships),
            start_person_id=start_person_id,
        )
        conn.commit()
        return result
