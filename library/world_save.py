"""Checkpoint simulation state (people, settlements, couples) into ``save.sqlite``.

Coexists with ``world_state`` in the same file. Uses JSON for ``Person`` payloads.
"""

from __future__ import annotations

import csv
import json
import math
import sqlite3
import time
from collections import defaultdict
from contextlib import closing, contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from library import simulation_timing
from library.mind_body import mind_body_from_genome
from library.passive_population import PassiveCohort, PassivePerson, PassivePersonRecord
from library.person import Person
from library.geography import get_region, list_routes_from
from library.settlements import (
    PRIMARY_SETTLEMENT_SUFFIX,
    SettlementState,
    make_settlement_id,
    primary_settlement_id,
)

if TYPE_CHECKING:
    from library.simulation_context import SimulationContext

# Fallback if a minimal ``SimulationContext`` shell omits the field.
_DEFAULT_WORKING_SET_DEAD_RETENTION = 20

SAVE_SCHEMA_VERSION = 24
SAVE_SCHEMA_VERSION_META_KEY = "save_schema_version"
EVENT_PEOPLE_BACKFILLED_META_KEY = "simulation_event_people_backfilled"
EVENT_RECORDS_BACKFILLED_META_KEY = "simulation_event_records_backfilled"
EVENT_PUBLIC_STAGE_RECORDS_BACKFILLED_META_KEY = (
    "simulation_event_public_stage_records_backfilled"
)
DOMAIN_STATES_BACKFILLED_META_KEY = "simulation_domain_states_backfilled_event_id"
OBLIGATIONS_BACKFILLED_META_KEY = "simulation_obligations_backfilled_event_id"
REPUTATION_MARKS_BACKFILLED_META_KEY = "simulation_reputation_marks_backfilled_event_id"
LEGAL_FALLOUT_BACKFILLED_META_KEY = "simulation_legal_fallout_backfilled_event_id"
FACTION_MEMORY_BACKFILLED_META_KEY = "simulation_faction_memory_backfilled_event_id"
INSTITUTIONS_BACKFILLED_META_KEY = "simulation_institutions_backfilled_event_id"
INNOVATIONS_BACKFILLED_META_KEY = "simulation_innovations_backfilled_event_id"
LEGAL_FALLOUT_SCANDAL_KINDS: frozenset[str] = frozenset(
    {"heir_legitimacy_rumor", "inheritance_scandal"}
)

_SAVE_REBUILD_SOURCE_SCHEMA = "source_db"

_CREATE_SAVE_METADATA = """
    CREATE TABLE IF NOT EXISTS save_metadata (
        meta_key TEXT PRIMARY KEY,
        meta_value TEXT NOT NULL
    );
"""

_CREATE_WORLD_STATE = """
    CREATE TABLE IF NOT EXISTS world_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        start_year INTEGER NOT NULL,
        current_year INTEGER NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
"""

_SAVE_REBUILD_TABLES = (
    "world_state",
    "simulation_meta",
    "simulation_region_lookup",
    "simulation_settlement_lookup",
    "simulation_people",
    "simulation_people_nondetailed",
    "simulation_person_archive_scores",
    "simulation_person_archive_score_reasons",
    "simulation_people_light",
    "simulation_cohorts",
    "simulation_promotion_log",
    "simulation_household_service_contracts",
    "simulation_patronage_ties",
    "simulation_outlaw_cases",
    "simulation_outlaw_refuges",
    "simulation_outlaw_custodies",
    "simulation_serial_predation_candidates",
    "simulation_couples",
    "simulation_paramours",
    "simulation_events",
    "simulation_event_people",
    "simulation_event_moves",
    "simulation_event_records",
    "simulation_domain_states",
    "simulation_domain_diffusion",
    "simulation_obligations",
    "simulation_reputation_marks",
    "simulation_legal_fallout",
    "simulation_legal_adjudications",
    "simulation_faction_memory",
    "simulation_institutions",
    "simulation_innovation_discoveries",
    "simulation_innovation_knowledge",
    "simulation_innovation_era_state",
    "simulation_regions",
    "simulation_settlements",
    "simulation_polities",
    "simulation_polity_territory",
    "simulation_office_seats",
    "simulation_office_holdings",
    "simulation_dynasties",
    "simulation_alliances",
    "simulation_campaigns",
    "simulation_battles",
)

_PERSON_CHECKPOINT_COLUMNS: tuple[str, ...] = (
    "first_name",
    "last_name",
    "gender",
    "ethnic",
    "species",
    "birthyear",
    "deathyear",
    "birthplace",
    "birthplace_region_key",
    "birthplace_settlement_key",
    "current_settlement_key",
    "partner_person_id",
    "paramour_person_id",
    "last_birth_event_year",
    "job",
    "job_assigned_year",
    "job_era",
    "job_tier",
    "job_market_type",
    "housing_status",
    "household_role",
    "host_person_id",
    "employer_person_id",
    "social_class_band",
    "social_standing_01",
    "societal_impact_01",
    "perceived_worth_01",
    "status_tendency",
    "leader_quality",
    "leader_tendency",
    "outlaw_status",
    "outlaw_case_key",
    "outlaw_refuge_id",
    "outlaw_since_year",
    "last_free_settlement_id",
    "outlaw_custody_id",
    "outlaw_custody_status",
    "outlaw_custody_start_year",
    "outlaw_custody_expected_release_year",
    "outlaw_custody_release_year",
    "outlaw_custody_site_settlement_id",
    "employment_status",
    "job_lost_year",
    "unemployment_started_year",
    "last_job",
    "career_fitness_score",
    "job_prosperity_01",
    "household_prosperity",
    "household_purseholder_person_id",
    "birth_litter_size",
    "life_stage",
    "maturity_height_cm",
    "maturity_weight_kg",
    "skin_tone",
    "hair",
    "eyes",
    "min_fertility_age",
    "max_fertility_age",
    "attractiveness_01",
    "sexual_nature",
    "gender_mind",
    "father_name",
    "mother_name",
)

_ADDITIVE_PERSON_CHECKPOINT_COLUMNS: dict[str, str] = {
    "job_market_type": "TEXT",
    "housing_status": "TEXT",
    "household_role": "TEXT",
    "host_person_id": "INTEGER",
    "employer_person_id": "INTEGER",
    "social_class_band": "TEXT",
    "social_standing_01": "REAL",
    "societal_impact_01": "REAL",
    "perceived_worth_01": "REAL",
    "outlaw_status": "TEXT",
    "outlaw_case_key": "TEXT",
    "outlaw_refuge_id": "TEXT",
    "outlaw_since_year": "INTEGER",
    "last_free_settlement_id": "TEXT",
    "outlaw_custody_id": "TEXT",
    "outlaw_custody_status": "TEXT",
    "outlaw_custody_start_year": "INTEGER",
    "outlaw_custody_expected_release_year": "INTEGER",
    "outlaw_custody_release_year": "INTEGER",
    "outlaw_custody_site_settlement_id": "TEXT",
}

_PERSON_EXTENSION_KEYS: tuple[str, ...] = (
    "genome_composite_names",
    "genome_composite_scores",
    "genome_trait_phrases",
    "paramour_person_ids",
    "birth_relationship_type",
    "born_out_of_wedlock",
    "legitimacy_status",
)


def _profile_t0(year: int | None = None) -> float | None:
    if not simulation_timing.enabled():
        return None
    if year is not None and not simulation_timing.active_for_year(year):
        return None
    return time.perf_counter()


def _profile_accumulate(phase: str, t0: float | None) -> float | None:
    if t0 is None:
        return None
    now = time.perf_counter()
    simulation_timing.accumulate(phase, now - t0)
    return now


def person_belongs_in_working_ram(
    person: Person,
    *,
    reference_year: int,
    retention_years: int,
) -> bool:
    """Alive and recent-dead stay in RAM; older dead remain only in ``save.sqlite``."""
    if person.deathyear is None:
        return True
    return int(reference_year) - int(person.deathyear) <= int(retention_years)


def prune_ancient_dead_from_ram(ctx: "SimulationContext") -> None:
    """Remove long-dead persons from RAM after their state was upserted to SQLite.

    Does **not** delete ``simulation_people`` rows.
    """
    ref = ctx.current_year
    if ref is None:
        ref = int(ctx.simulation_start_year)
    ret = int(
        getattr(ctx, "working_set_dead_retention_years", _DEFAULT_WORKING_SET_DEAD_RETENTION)
    )
    keep_ids: set[int] = set()
    for rec in ctx.people:
        if person_belongs_in_working_ram(
            rec.person, reference_year=int(ref), retention_years=ret
        ):
            keep_ids.add(rec.person_id)
    if len(keep_ids) == len(ctx.people):
        return
    new_people = [rec for rec in ctx.people if rec.person_id in keep_ids]
    ctx.people = new_people
    ctx.id_to_record = {r.person_id: r for r in new_people}
    if hasattr(ctx, "invalidate_alive_census_cache"):
        ctx.invalidate_alive_census_cache()
    ctx.couples = [
        (a, b) for (a, b) in ctx.couples if a in keep_ids and b in keep_ids
    ]
    ctx.paramours = [
        (a, b) for (a, b) in ctx.paramours if a in keep_ids and b in keep_ids
    ]
    if hasattr(ctx, "sync_all_paramour_fields"):
        ctx.sync_all_paramour_fields()
    if hasattr(ctx, "surname_conventions_by_pair"):
        ctx.surname_conventions_by_pair = {
            pair: convention
            for pair, convention in ctx.surname_conventions_by_pair.items()
            if pair[0] in keep_ids and pair[1] in keep_ids
        }
    for rec in new_people:
        if rec.person_id not in ctx.current_people_ids:
            continue
        p = rec.person
        np = p
        if p.partner_person_id is not None and p.partner_person_id not in keep_ids:
            np = replace(np, partner_person_id=None)
        if p.paramour_person_id is not None and p.paramour_person_id not in keep_ids:
            ids = tuple(pid for pid in getattr(p, "paramour_person_ids", ()) if pid in keep_ids)
            np = replace(
                np,
                paramour_person_id=(ids[0] if ids else None),
                paramour_person_ids=ids,
            )
        if np is not p:
            rec.person = np
    from library.simulation_government import vacate_government_holders_not_in_ram

    vacate_government_holders_not_in_ram(ctx)


@contextmanager
def _open_save(path: Path | str):
    """Open save.sqlite; always close the connection (important on Windows)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _quote_identifier(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _ensure_save_metadata_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_CREATE_SAVE_METADATA)


def save_schema_version(conn: sqlite3.Connection) -> int:
    """Return the schema version stamped on a ``save.sqlite`` connection.

    Older saves predate explicit versioning and report ``0`` until
    :func:`ensure_checkpoint_schema` stamps them as the current v1 layout.
    """
    has_metadata = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='save_metadata'
        """
    ).fetchone()
    row = None
    if has_metadata is not None:
        row = conn.execute(
            """
            SELECT meta_value FROM save_metadata
            WHERE meta_key = ?
            """,
            (SAVE_SCHEMA_VERSION_META_KEY,),
        ).fetchone()
    if row is not None and row[0] is not None:
        try:
            return int(str(row[0]).strip())
        except (TypeError, ValueError):
            return 0
    pragma_row = conn.execute("PRAGMA user_version").fetchone()
    if pragma_row is not None and pragma_row[0] is not None:
        try:
            return int(pragma_row[0])
        except (TypeError, ValueError):
            return 0
    return 0


def _stamp_save_schema_version(
    conn: sqlite3.Connection, version: int = SAVE_SCHEMA_VERSION
) -> None:
    _ensure_save_metadata_schema(conn)
    v = int(version)
    conn.execute(
        """
        INSERT OR REPLACE INTO save_metadata (meta_key, meta_value)
        VALUES (?, ?)
        """,
        (SAVE_SCHEMA_VERSION_META_KEY, str(v)),
    )
    conn.execute(f"PRAGMA user_version = {v}")


def _ensure_supported_save_schema(conn: sqlite3.Connection) -> None:
    version = save_schema_version(conn)
    if version > SAVE_SCHEMA_VERSION:
        raise RuntimeError(
            f"save.sqlite schema version {version} is newer than supported "
            f"version {SAVE_SCHEMA_VERSION}"
        )
    if version not in (
        0,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
        18,
        19,
        20,
        21,
        22,
        23,
        SAVE_SCHEMA_VERSION,
    ):
        raise RuntimeError(
            f"save.sqlite schema version {version} needs a migration before "
            f"this code can open it"
        )
    if version == 0 and _save_looks_like_legacy_multiworld(conn):
        raise RuntimeError(
            "save.sqlite uses the legacy multi-world schema. Delete the save or "
            "rebuild it before opening with the compact single-world schema."
        )
    _stamp_save_schema_version(conn, SAVE_SCHEMA_VERSION)


def _table_exists(conn: sqlite3.Connection, table: str, *, schema: str = "main") -> bool:
    schema_sql = _quote_identifier(schema)
    row = conn.execute(
        f"""
        SELECT name FROM {schema_sql}.sqlite_master
        WHERE type='table' AND name=?
        """,
        (table,),
    ).fetchone()
    return row is not None


def _table_columns(
    conn: sqlite3.Connection, table: str, *, schema: str = "main"
) -> list[str]:
    schema_sql = _quote_identifier(schema)
    table_sql = _quote_identifier(table)
    return [
        str(row[1])
        for row in conn.execute(f"PRAGMA {schema_sql}.table_info({table_sql})")
    ]


def _save_looks_like_legacy_multiworld(conn: sqlite3.Connection) -> bool:
    for table in (
        "world_state",
        "simulation_meta",
        "simulation_people",
        "simulation_events",
        "simulation_regions",
        "simulation_settlements",
    ):
        if _table_exists(conn, table) and "world" in _table_columns(conn, table):
            return True
    return False


def _ensure_place_lookup_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS simulation_region_lookup (
            region_key INTEGER PRIMARY KEY AUTOINCREMENT,
            region_id TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS simulation_settlement_lookup (
            settlement_key INTEGER PRIMARY KEY AUTOINCREMENT,
            settlement_id TEXT NOT NULL UNIQUE,
            region_key INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_simulation_settlement_lookup_region
        ON simulation_settlement_lookup (region_key);
        """
    )


def _lookup_or_insert_region_key(conn: sqlite3.Connection, region_id: object) -> int | None:
    rid = str(region_id or "").strip()
    if not rid:
        return None
    conn.execute(
        """
        INSERT OR IGNORE INTO simulation_region_lookup (region_id)
        VALUES (?)
        """,
        (rid,),
    )
    row = conn.execute(
        """
        SELECT region_key FROM simulation_region_lookup
        WHERE region_id = ?
        """,
        (rid,),
    ).fetchone()
    return int(row["region_key"] if isinstance(row, sqlite3.Row) else row[0])


def _lookup_or_insert_settlement_key(
    conn: sqlite3.Connection, settlement_id: object, region_id: object | None = None
) -> int | None:
    sid = str(settlement_id or "").strip()
    if not sid:
        return None
    row = conn.execute(
        """
        SELECT settlement_key FROM simulation_settlement_lookup
        WHERE settlement_id = ?
        """,
        (sid,),
    ).fetchone()
    if row is not None:
        return int(row["settlement_key"] if isinstance(row, sqlite3.Row) else row[0])
    rid = str(region_id or "").strip()
    if not rid and ":" in sid:
        rid = sid.split(":", 1)[0].strip()
    rkey = _lookup_or_insert_region_key(conn, rid)
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
        """
        SELECT settlement_key FROM simulation_settlement_lookup
        WHERE settlement_id = ?
        """,
        (sid,),
    ).fetchone()
    return int(row["settlement_key"] if isinstance(row, sqlite3.Row) else row[0])


class _EventPlaceKeyCache:
    """Flush-local cache for normalized event place lookup keys."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.region_keys: dict[str, int] = {}
        self.settlement_keys: dict[str, int] = {}
        for row in conn.execute(
            "SELECT region_id, region_key FROM simulation_region_lookup"
        ):
            raw_region_id = row["region_id"] if isinstance(row, sqlite3.Row) else row[0]
            rid = str(raw_region_id).strip()
            if rid:
                self.region_keys[rid] = int(
                    row["region_key"] if isinstance(row, sqlite3.Row) else row[1]
                )
        for row in conn.execute(
            "SELECT settlement_id, settlement_key FROM simulation_settlement_lookup"
        ):
            raw_settlement_id = (
                row["settlement_id"] if isinstance(row, sqlite3.Row) else row[0]
            )
            sid = str(raw_settlement_id).strip()
            if sid:
                self.settlement_keys[sid] = int(
                    row["settlement_key"] if isinstance(row, sqlite3.Row) else row[1]
                )

    def region_key(self, region_id: object) -> int | None:
        rid = str(region_id or "").strip()
        if not rid:
            return None
        cached = self.region_keys.get(rid)
        if cached is not None:
            return cached
        key = _lookup_or_insert_region_key(self.conn, rid)
        if key is not None:
            self.region_keys[rid] = key
        return key

    def settlement_key(
        self, settlement_id: object, region_id: object | None = None
    ) -> int | None:
        sid = str(settlement_id or "").strip()
        if not sid:
            return None
        cached = self.settlement_keys.get(sid)
        if cached is not None:
            return cached
        rid = str(region_id or "").strip()
        if not rid and ":" in sid:
            rid = sid.split(":", 1)[0].strip()
        rkey = self.region_key(rid)
        if rkey is None:
            return None
        self.conn.execute(
            """
            INSERT OR IGNORE INTO simulation_settlement_lookup (settlement_id, region_key)
            VALUES (?, ?)
            """,
            (sid, rkey),
        )
        row = self.conn.execute(
            """
            SELECT settlement_key FROM simulation_settlement_lookup
            WHERE settlement_id = ?
            """,
            (sid,),
        ).fetchone()
        if row is None:
            return None
        key = int(row["settlement_key"] if isinstance(row, sqlite3.Row) else row[0])
        self.settlement_keys[sid] = key
        return key


def _ensure_simulation_people_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS simulation_people (
            person_id INTEGER PRIMARY KEY,
            is_founder INTEGER NOT NULL,
            father_id INTEGER,
            mother_id INTEGER,
            is_alive INTEGER NOT NULL,
            first_name TEXT NOT NULL DEFAULT '',
            last_name TEXT NOT NULL DEFAULT '',
            gender TEXT NOT NULL DEFAULT '',
            ethnic TEXT NOT NULL DEFAULT '',
            species TEXT NOT NULL DEFAULT '',
            birthyear INTEGER NOT NULL DEFAULT 0,
            deathyear INTEGER,
            birthplace TEXT NOT NULL DEFAULT 'Placeholder',
            birthplace_region_key INTEGER,
            birthplace_settlement_key INTEGER,
            current_settlement_key INTEGER,
            partner_person_id INTEGER,
            paramour_person_id INTEGER,
            last_birth_event_year INTEGER,
            job TEXT,
            job_assigned_year INTEGER,
            job_era TEXT,
            job_tier TEXT,
            job_market_type TEXT,
            housing_status TEXT,
            household_role TEXT,
            host_person_id INTEGER,
            employer_person_id INTEGER,
            social_class_band TEXT,
            social_standing_01 REAL,
            societal_impact_01 REAL,
            perceived_worth_01 REAL,
            status_tendency TEXT,
            leader_quality TEXT,
            leader_tendency TEXT,
            outlaw_status TEXT,
            outlaw_case_key TEXT,
            outlaw_refuge_id TEXT,
            outlaw_since_year INTEGER,
            last_free_settlement_id TEXT,
            outlaw_custody_id TEXT,
            outlaw_custody_status TEXT,
            outlaw_custody_start_year INTEGER,
            outlaw_custody_expected_release_year INTEGER,
            outlaw_custody_release_year INTEGER,
            outlaw_custody_site_settlement_id TEXT,
            employment_status TEXT,
            job_lost_year INTEGER,
            unemployment_started_year INTEGER,
            last_job TEXT,
            career_fitness_score REAL,
            job_prosperity_01 REAL,
            household_prosperity REAL,
            household_purseholder_person_id INTEGER,
            birth_litter_size INTEGER NOT NULL DEFAULT 1,
            life_stage TEXT,
            maturity_height_cm REAL,
            maturity_weight_kg REAL,
            skin_tone TEXT,
            hair TEXT,
            eyes TEXT,
            min_fertility_age INTEGER,
            max_fertility_age INTEGER,
            attractiveness_01 REAL,
            sexual_nature TEXT,
            gender_mind TEXT,
            father_name TEXT,
            mother_name TEXT,
            person_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_simulation_people_alive
        ON simulation_people (is_alive);
        CREATE INDEX IF NOT EXISTS idx_simulation_people_deathyear
        ON simulation_people (deathyear);
        CREATE INDEX IF NOT EXISTS idx_simulation_people_birthyear
        ON simulation_people (birthyear);
        CREATE INDEX IF NOT EXISTS idx_simulation_people_current_settlement
        ON simulation_people (current_settlement_key);
        """
    )
    cols = set(_table_columns(conn, "simulation_people"))
    for column, definition in _ADDITIVE_PERSON_CHECKPOINT_COLUMNS.items():
        if column not in cols:
            conn.execute(
                f"ALTER TABLE simulation_people ADD COLUMN {_quote_identifier(column)} {definition}"
            )
            cols.add(column)
    missing = [c for c in _PERSON_CHECKPOINT_COLUMNS if c not in cols]
    if missing:
        if {
            "birthplace_region_id",
            "birthplace_settlement_id",
            "current_settlement_id",
        } & cols:
            raise RuntimeError(
                "simulation_people uses a pre-v5 text-place schema. Delete or "
                "rebuild save.sqlite before opening it with the surrogate "
                "place-key schema."
            )
        raise RuntimeError(
            "simulation_people uses a pre-v3 schema. Delete or rebuild save.sqlite "
            "before opening it with the compact people checkpoint schema."
        )


def _ensure_household_service_contracts_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS simulation_household_service_contracts (
            contract_id INTEGER PRIMARY KEY AUTOINCREMENT,
            worker_person_id INTEGER NOT NULL,
            employer_person_id INTEGER,
            service_kind TEXT NOT NULL DEFAULT '',
            board_included INTEGER NOT NULL DEFAULT 0,
            cash_wage_01 REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            start_year INTEGER,
            end_year INTEGER,
            updated_year INTEGER,
            UNIQUE(worker_person_id, employer_person_id, service_kind, start_year)
        );
        CREATE INDEX IF NOT EXISTS idx_sim_household_service_worker
        ON simulation_household_service_contracts (worker_person_id);
        CREATE INDEX IF NOT EXISTS idx_sim_household_service_employer
        ON simulation_household_service_contracts (employer_person_id);
        CREATE INDEX IF NOT EXISTS idx_sim_household_service_status
        ON simulation_household_service_contracts (status);
        """
    )


def _sync_household_service_contracts(
    conn: sqlite3.Connection, ctx: "SimulationContext"
) -> None:
    year = int(ctx.current_year if ctx.current_year is not None else ctx.simulation_start_year)
    conn.execute(
        """
        UPDATE simulation_household_service_contracts
        SET status = 'ended',
            end_year = COALESCE(end_year, ?),
            updated_year = ?
        WHERE status = 'active'
        """,
        (year, year),
    )
    try:
        from library.job_archetypes import JobArchetypeCatalog

        archetypes = JobArchetypeCatalog.load(ctx.db_path)
    except Exception:
        archetypes = None

    for rec in ctx.iter_current_people(sorted_by_id=True):
        p = rec.person
        if (p.job_market_type or "").strip().lower() != "domestic_service":
            continue
        employer_id = p.employer_person_id
        if employer_id is None:
            continue
        job = (p.job or "").strip()
        if not job:
            continue
        archetype = archetypes.lookup(job) if archetypes is not None else None
        service_kind = (
            getattr(archetype, "domestic_service_kind", None)
            or (p.household_role or "").strip()
            or job.lower().replace(" ", "_")
        )
        board = bool(
            (p.housing_status or "").strip().lower() == "employer_household"
            or float(getattr(archetype, "board_compensation_01", 0.0) or 0.0) > 0.0
        )
        cash = p.job_prosperity_01
        if cash is None:
            cash = getattr(archetype, "personal_prosperity_01", 0.12) if archetype else 0.12
        start_year = int(p.job_assigned_year if p.job_assigned_year is not None else year)
        conn.execute(
            """
            INSERT INTO simulation_household_service_contracts (
                worker_person_id, employer_person_id, service_kind,
                board_included, cash_wage_01, status, start_year, end_year, updated_year
            )
            VALUES (?, ?, ?, ?, ?, 'active', ?, NULL, ?)
            ON CONFLICT(worker_person_id, employer_person_id, service_kind, start_year)
            DO UPDATE SET
                board_included = excluded.board_included,
                cash_wage_01 = excluded.cash_wage_01,
                status = 'active',
                end_year = NULL,
                updated_year = excluded.updated_year
            """,
            (
                int(rec.person_id),
                int(employer_id),
                str(service_kind),
                1 if board else 0,
                round(float(cash or 0.0), 5),
                start_year,
                year,
            ),
        )


def _ensure_patronage_ties_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS simulation_patronage_ties (
            tie_id INTEGER PRIMARY KEY AUTOINCREMENT,
            patron_person_id INTEGER NOT NULL,
            client_person_id INTEGER NOT NULL,
            tie_kind TEXT NOT NULL DEFAULT 'patronage',
            strength_01 REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            start_year INTEGER,
            end_year INTEGER,
            settlement_id TEXT,
            polity_id INTEGER,
            updated_year INTEGER,
            UNIQUE(patron_person_id, client_person_id, tie_kind, start_year)
        );
        CREATE INDEX IF NOT EXISTS idx_sim_patronage_patron
        ON simulation_patronage_ties (patron_person_id);
        CREATE INDEX IF NOT EXISTS idx_sim_patronage_client
        ON simulation_patronage_ties (client_person_id);
        CREATE INDEX IF NOT EXISTS idx_sim_patronage_status
        ON simulation_patronage_ties (status);
        CREATE INDEX IF NOT EXISTS idx_sim_patronage_settlement
        ON simulation_patronage_ties (settlement_id);
        """
    )


def _sync_patronage_ties(conn: sqlite3.Connection, ctx: "SimulationContext") -> None:
    year = int(ctx.current_year if ctx.current_year is not None else ctx.simulation_start_year)
    ties = getattr(ctx, "patronage_ties", {}) or {}
    for tie in ties.values():
        patron_id = int(getattr(tie, "patron_person_id", 0) or 0)
        client_id = int(getattr(tie, "client_person_id", 0) or 0)
        if patron_id <= 0 or client_id <= 0 or patron_id == client_id:
            continue
        tie_kind = str(getattr(tie, "tie_kind", "patronage") or "patronage").strip() or "patronage"
        start_year = getattr(tie, "start_year", None)
        if start_year is None:
            start_year = year
        conn.execute(
            """
            INSERT INTO simulation_patronage_ties (
                patron_person_id, client_person_id, tie_kind, strength_01,
                status, start_year, end_year, settlement_id, polity_id, updated_year
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(patron_person_id, client_person_id, tie_kind, start_year)
            DO UPDATE SET
                strength_01 = excluded.strength_01,
                status = excluded.status,
                end_year = excluded.end_year,
                settlement_id = excluded.settlement_id,
                polity_id = excluded.polity_id,
                updated_year = excluded.updated_year
            """,
            (
                patron_id,
                client_id,
                tie_kind,
                round(max(0.0, min(1.0, float(getattr(tie, "strength_01", 0.0) or 0.0))), 5),
                str(getattr(tie, "status", "active") or "active").strip() or "active",
                int(start_year),
                (
                    int(getattr(tie, "end_year"))
                    if getattr(tie, "end_year", None) is not None
                    else None
                ),
                (
                    str(getattr(tie, "settlement_id"))
                    if getattr(tie, "settlement_id", None)
                    else None
                ),
                (
                    int(getattr(tie, "polity_id"))
                    if getattr(tie, "polity_id", None) is not None
                    else None
                ),
                int(getattr(tie, "updated_year", None) or year),
            ),
        )


def _ensure_outlaw_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS simulation_outlaw_cases (
            case_key TEXT PRIMARY KEY,
            accused_person_id INTEGER NOT NULL,
            offense_type TEXT NOT NULL DEFAULT '',
            offense_kind TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            source_event_id INTEGER,
            source_event_key TEXT NOT NULL DEFAULT '',
            victim_person_id INTEGER,
            target_person_id INTEGER,
            severity_01 REAL NOT NULL DEFAULT 0,
            knownness_01 REAL NOT NULL DEFAULT 0,
            pursuit_pressure_01 REAL NOT NULL DEFAULT 0,
            buyoff_power_01 REAL NOT NULL DEFAULT 0,
            start_year INTEGER,
            last_seen_year INTEGER,
            expected_forget_year INTEGER,
            resolved_year INTEGER,
            resolution TEXT,
            region_key INTEGER,
            settlement_key INTEGER,
            refuge_id TEXT,
            custody_id TEXT,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_sim_outlaw_cases_accused
        ON simulation_outlaw_cases (accused_person_id);
        CREATE INDEX IF NOT EXISTS idx_sim_outlaw_cases_status
        ON simulation_outlaw_cases (status);
        CREATE INDEX IF NOT EXISTS idx_sim_outlaw_cases_refuge
        ON simulation_outlaw_cases (refuge_id);
        CREATE INDEX IF NOT EXISTS idx_sim_outlaw_cases_custody
        ON simulation_outlaw_cases (custody_id);
        CREATE INDEX IF NOT EXISTS idx_sim_outlaw_cases_place
        ON simulation_outlaw_cases (region_key, settlement_key);

        CREATE TABLE IF NOT EXISTS simulation_outlaw_refuges (
            refuge_id TEXT PRIMARY KEY,
            display_name TEXT,
            region_key INTEGER,
            near_settlement_key INTEGER,
            status TEXT NOT NULL DEFAULT 'active',
            founded_year INTEGER,
            discovered_year INTEGER,
            abandoned_year INTEGER,
            band_size INTEGER NOT NULL DEFAULT 1,
            concealment_01 REAL NOT NULL DEFAULT 0,
            support_01 REAL NOT NULL DEFAULT 0,
            last_activity_year INTEGER,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_sim_outlaw_refuges_region
        ON simulation_outlaw_refuges (region_key);
        CREATE INDEX IF NOT EXISTS idx_sim_outlaw_refuges_status
        ON simulation_outlaw_refuges (status);

        CREATE TABLE IF NOT EXISTS simulation_outlaw_custodies (
            custody_id TEXT PRIMARY KEY,
            case_key TEXT NOT NULL,
            person_id INTEGER NOT NULL,
            custody_type TEXT NOT NULL DEFAULT 'imprisonment',
            status TEXT NOT NULL DEFAULT 'active',
            site_settlement_key INTEGER,
            region_key INTEGER,
            start_year INTEGER,
            expected_release_year INTEGER,
            release_year INTEGER,
            severity_01 REAL NOT NULL DEFAULT 0,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_sim_outlaw_custodies_case
        ON simulation_outlaw_custodies (case_key);
        CREATE INDEX IF NOT EXISTS idx_sim_outlaw_custodies_person
        ON simulation_outlaw_custodies (person_id);
        CREATE INDEX IF NOT EXISTS idx_sim_outlaw_custodies_status
        ON simulation_outlaw_custodies (status);
        CREATE INDEX IF NOT EXISTS idx_sim_outlaw_custodies_place
        ON simulation_outlaw_custodies (region_key, site_settlement_key);
        """
    )
    case_cols = set(_table_columns(conn, "simulation_outlaw_cases"))
    if "custody_id" not in case_cols:
        conn.execute("ALTER TABLE simulation_outlaw_cases ADD COLUMN custody_id TEXT")
    cols = set(_table_columns(conn, "simulation_outlaw_refuges"))
    if "display_name" not in cols:
        conn.execute("ALTER TABLE simulation_outlaw_refuges ADD COLUMN display_name TEXT")


def _ensure_serial_predation_candidate_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS simulation_serial_predation_candidates (
            person_id INTEGER PRIMARY KEY,
            risk_lane TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'dormant',
            risk_score REAL NOT NULL DEFAULT 0,
            harm_drive REAL NOT NULL DEFAULT 0,
            inhibition REAL NOT NULL DEFAULT 0,
            control REAL NOT NULL DEFAULT 0,
            exposure_noise REAL NOT NULL DEFAULT 0,
            organized_serial_risk REAL NOT NULL DEFAULT 0,
            disorganized_serial_risk REAL NOT NULL DEFAULT 0,
            next_check_year INTEGER,
            last_checked_year INTEGER,
            last_serious_crime_year INTEGER,
            hidden_linked_kill_count INTEGER NOT NULL DEFAULT 0,
            suspected_linked_kill_count INTEGER NOT NULL DEFAULT 0,
            public_suspicion_score REAL NOT NULL DEFAULT 0,
            pattern_recognized INTEGER NOT NULL DEFAULT 0,
            offender_identity_confidence REAL NOT NULL DEFAULT 0,
            rejection_reasons_json TEXT NOT NULL DEFAULT '[]',
            details_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_sim_serial_candidates_due
        ON simulation_serial_predation_candidates (status, next_check_year);
        CREATE INDEX IF NOT EXISTS idx_sim_serial_candidates_risk
        ON simulation_serial_predation_candidates (risk_lane, risk_score DESC);
        CREATE INDEX IF NOT EXISTS idx_sim_serial_candidates_pattern
        ON simulation_serial_predation_candidates (
            pattern_recognized, public_suspicion_score DESC
        );
        """
    )


def _serial_candidate_int(row: dict[str, object], key: str) -> int | None:
    value = row.get(key)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _serial_candidate_float(row: dict[str, object], key: str) -> float:
    try:
        value = float(row.get(key, 0.0) or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    return max(0.0, min(1.0, value))


def _sync_serial_predation_candidates(
    conn: sqlite3.Connection, ctx: "SimulationContext"
) -> None:
    _ensure_serial_predation_candidate_table(conn)
    candidates = getattr(ctx, "serial_predation_candidates", {}) or {}
    now = datetime.now(timezone.utc).isoformat()
    for person_id, raw in candidates.items():
        if not isinstance(raw, dict):
            continue
        row = {str(k): v for k, v in raw.items()}
        pid = _serial_candidate_int(row, "person_id")
        if pid is None:
            try:
                pid = int(person_id)
            except (TypeError, ValueError):
                continue
        rejection_reasons = row.get("rejection_reasons")
        if not isinstance(rejection_reasons, (list, tuple)):
            rejection_reasons = []
        details = row.get("details")
        if not isinstance(details, dict):
            details = {}
        conn.execute(
            """
            INSERT INTO simulation_serial_predation_candidates (
                person_id, risk_lane, status, risk_score, harm_drive, inhibition,
                control, exposure_noise, organized_serial_risk,
                disorganized_serial_risk, next_check_year, last_checked_year,
                last_serious_crime_year, hidden_linked_kill_count,
                suspected_linked_kill_count, public_suspicion_score,
                pattern_recognized, offender_identity_confidence,
                rejection_reasons_json, details_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(person_id)
            DO UPDATE SET
                risk_lane = excluded.risk_lane,
                status = excluded.status,
                risk_score = excluded.risk_score,
                harm_drive = excluded.harm_drive,
                inhibition = excluded.inhibition,
                control = excluded.control,
                exposure_noise = excluded.exposure_noise,
                organized_serial_risk = excluded.organized_serial_risk,
                disorganized_serial_risk = excluded.disorganized_serial_risk,
                next_check_year = excluded.next_check_year,
                last_checked_year = excluded.last_checked_year,
                last_serious_crime_year = excluded.last_serious_crime_year,
                hidden_linked_kill_count = excluded.hidden_linked_kill_count,
                suspected_linked_kill_count = excluded.suspected_linked_kill_count,
                public_suspicion_score = excluded.public_suspicion_score,
                pattern_recognized = excluded.pattern_recognized,
                offender_identity_confidence = excluded.offender_identity_confidence,
                rejection_reasons_json = excluded.rejection_reasons_json,
                details_json = excluded.details_json,
                updated_at = excluded.updated_at
            """,
            (
                pid,
                str(row.get("risk_lane") or "").strip(),
                str(row.get("status") or "dormant").strip() or "dormant",
                _serial_candidate_float(row, "risk_score"),
                _serial_candidate_float(row, "harm_drive"),
                _serial_candidate_float(row, "inhibition"),
                _serial_candidate_float(row, "control"),
                _serial_candidate_float(row, "exposure_noise"),
                _serial_candidate_float(row, "organized_serial_risk"),
                _serial_candidate_float(row, "disorganized_serial_risk"),
                _serial_candidate_int(row, "next_check_year"),
                _serial_candidate_int(row, "last_checked_year"),
                _serial_candidate_int(row, "last_serious_crime_year"),
                max(0, int(row.get("hidden_linked_kill_count") or 0)),
                max(0, int(row.get("suspected_linked_kill_count") or 0)),
                _serial_candidate_float(row, "public_suspicion_score"),
                1 if bool(row.get("pattern_recognized")) else 0,
                _serial_candidate_float(row, "offender_identity_confidence"),
                json.dumps([str(v) for v in rejection_reasons], sort_keys=True),
                json.dumps(details, sort_keys=True),
                now,
            ),
        )


def _sync_outlaw_state(conn: sqlite3.Connection, ctx: "SimulationContext") -> None:
    cases = getattr(ctx, "outlaw_cases", {}) or {}
    refuges = getattr(ctx, "outlaw_refuges", {}) or {}
    custodies = getattr(ctx, "outlaw_custodies", {}) or {}
    now = datetime.now(timezone.utc).isoformat()
    for refuge in refuges.values():
        refuge_id = str(getattr(refuge, "refuge_id", "") or "").strip()
        if not refuge_id:
            continue
        region_id = str(getattr(refuge, "region_id", "") or "").strip()
        near_sid = str(getattr(refuge, "near_settlement_id", "") or "").strip()
        conn.execute(
            """
            INSERT INTO simulation_outlaw_refuges (
                refuge_id, display_name, region_key, near_settlement_key, status, founded_year,
                discovered_year, abandoned_year, band_size, concealment_01,
                support_01, last_activity_year, details_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(refuge_id)
            DO UPDATE SET
                display_name = excluded.display_name,
                region_key = excluded.region_key,
                near_settlement_key = excluded.near_settlement_key,
                status = excluded.status,
                founded_year = excluded.founded_year,
                discovered_year = excluded.discovered_year,
                abandoned_year = excluded.abandoned_year,
                band_size = excluded.band_size,
                concealment_01 = excluded.concealment_01,
                support_01 = excluded.support_01,
                last_activity_year = excluded.last_activity_year,
                details_json = excluded.details_json,
                updated_at = excluded.updated_at
            """,
            (
                refuge_id,
                str(getattr(refuge, "display_name", "") or "").strip() or None,
                _lookup_or_insert_region_key(conn, region_id),
                _lookup_or_insert_settlement_key(conn, near_sid, region_id),
                str(getattr(refuge, "status", "active") or "active").strip() or "active",
                (
                    int(getattr(refuge, "founded_year"))
                    if getattr(refuge, "founded_year", None) is not None
                    else None
                ),
                (
                    int(getattr(refuge, "discovered_year"))
                    if getattr(refuge, "discovered_year", None) is not None
                    else None
                ),
                (
                    int(getattr(refuge, "abandoned_year"))
                    if getattr(refuge, "abandoned_year", None) is not None
                    else None
                ),
                max(0, int(getattr(refuge, "band_size", 0) or 0)),
                round(max(0.0, min(1.0, float(getattr(refuge, "concealment_01", 0.0) or 0.0))), 5),
                round(max(0.0, min(1.0, float(getattr(refuge, "support_01", 0.0) or 0.0))), 5),
                (
                    int(getattr(refuge, "last_activity_year"))
                    if getattr(refuge, "last_activity_year", None) is not None
                    else None
                ),
                json.dumps(getattr(refuge, "details", {}) or {}, sort_keys=True),
                now,
                now,
            ),
        )
    for custody in custodies.values():
        custody_id = str(getattr(custody, "custody_id", "") or "").strip()
        case_key = str(getattr(custody, "case_key", "") or "").strip()
        if not custody_id or not case_key:
            continue
        region_id = str(getattr(custody, "region_id", "") or "").strip()
        site_sid = str(getattr(custody, "site_settlement_id", "") or "").strip()
        conn.execute(
            """
            INSERT INTO simulation_outlaw_custodies (
                custody_id, case_key, person_id, custody_type, status,
                site_settlement_key, region_key, start_year, expected_release_year,
                release_year, severity_01, details_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(custody_id)
            DO UPDATE SET
                case_key = excluded.case_key,
                person_id = excluded.person_id,
                custody_type = excluded.custody_type,
                status = excluded.status,
                site_settlement_key = excluded.site_settlement_key,
                region_key = excluded.region_key,
                start_year = excluded.start_year,
                expected_release_year = excluded.expected_release_year,
                release_year = excluded.release_year,
                severity_01 = excluded.severity_01,
                details_json = excluded.details_json,
                updated_at = excluded.updated_at
            """,
            (
                custody_id,
                case_key,
                int(getattr(custody, "person_id", 0) or 0),
                str(getattr(custody, "custody_type", "imprisonment") or "imprisonment").strip()
                or "imprisonment",
                str(getattr(custody, "status", "active") or "active").strip() or "active",
                _lookup_or_insert_settlement_key(conn, site_sid, region_id),
                _lookup_or_insert_region_key(conn, region_id),
                (
                    int(getattr(custody, "start_year"))
                    if getattr(custody, "start_year", None) is not None
                    else None
                ),
                (
                    int(getattr(custody, "expected_release_year"))
                    if getattr(custody, "expected_release_year", None) is not None
                    else None
                ),
                (
                    int(getattr(custody, "release_year"))
                    if getattr(custody, "release_year", None) is not None
                    else None
                ),
                round(max(0.0, min(1.0, float(getattr(custody, "severity_01", 0.0) or 0.0))), 5),
                json.dumps(getattr(custody, "details", {}) or {}, sort_keys=True),
                now,
                now,
            ),
        )
    for case in cases.values():
        case_key = str(getattr(case, "case_key", "") or "").strip()
        if not case_key:
            continue
        region_id = str(getattr(case, "region_id", "") or "").strip()
        settlement_id = str(getattr(case, "settlement_id", "") or "").strip()
        conn.execute(
            """
            INSERT INTO simulation_outlaw_cases (
                case_key, accused_person_id, offense_type, offense_kind, status,
                source_event_id, source_event_key, victim_person_id, target_person_id,
                severity_01, knownness_01, pursuit_pressure_01, buyoff_power_01,
                start_year, last_seen_year, expected_forget_year, resolved_year,
                resolution, region_key, settlement_key, refuge_id, custody_id, details_json,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(case_key)
            DO UPDATE SET
                accused_person_id = excluded.accused_person_id,
                offense_type = excluded.offense_type,
                offense_kind = excluded.offense_kind,
                status = excluded.status,
                source_event_id = excluded.source_event_id,
                source_event_key = excluded.source_event_key,
                victim_person_id = excluded.victim_person_id,
                target_person_id = excluded.target_person_id,
                severity_01 = excluded.severity_01,
                knownness_01 = excluded.knownness_01,
                pursuit_pressure_01 = excluded.pursuit_pressure_01,
                buyoff_power_01 = excluded.buyoff_power_01,
                start_year = excluded.start_year,
                last_seen_year = excluded.last_seen_year,
                expected_forget_year = excluded.expected_forget_year,
                resolved_year = excluded.resolved_year,
                resolution = excluded.resolution,
                region_key = excluded.region_key,
                settlement_key = excluded.settlement_key,
                refuge_id = excluded.refuge_id,
                custody_id = excluded.custody_id,
                details_json = excluded.details_json,
                updated_at = excluded.updated_at
            """,
            (
                case_key,
                int(getattr(case, "accused_person_id", 0) or 0),
                str(getattr(case, "offense_type", "") or "").strip(),
                str(getattr(case, "offense_kind", "") or "").strip(),
                str(getattr(case, "status", "active") or "active").strip() or "active",
                (
                    int(getattr(case, "source_event_id"))
                    if getattr(case, "source_event_id", None) is not None
                    else None
                ),
                str(getattr(case, "source_event_key", "") or "").strip(),
                (
                    int(getattr(case, "victim_person_id"))
                    if getattr(case, "victim_person_id", None) is not None
                    else None
                ),
                (
                    int(getattr(case, "target_person_id"))
                    if getattr(case, "target_person_id", None) is not None
                    else None
                ),
                round(max(0.0, min(1.0, float(getattr(case, "severity_01", 0.0) or 0.0))), 5),
                round(max(0.0, min(1.0, float(getattr(case, "knownness_01", 0.0) or 0.0))), 5),
                round(max(0.0, min(1.0, float(getattr(case, "pursuit_pressure_01", 0.0) or 0.0))), 5),
                round(max(0.0, min(1.0, float(getattr(case, "buyoff_power_01", 0.0) or 0.0))), 5),
                (
                    int(getattr(case, "start_year"))
                    if getattr(case, "start_year", None) is not None
                    else None
                ),
                (
                    int(getattr(case, "last_seen_year"))
                    if getattr(case, "last_seen_year", None) is not None
                    else None
                ),
                (
                    int(getattr(case, "expected_forget_year"))
                    if getattr(case, "expected_forget_year", None) is not None
                    else None
                ),
                (
                    int(getattr(case, "resolved_year"))
                    if getattr(case, "resolved_year", None) is not None
                    else None
                ),
                (
                    str(getattr(case, "resolution"))
                    if getattr(case, "resolution", None) is not None
                    else None
                ),
                _lookup_or_insert_region_key(conn, region_id),
                _lookup_or_insert_settlement_key(conn, settlement_id, region_id),
                (
                    str(getattr(case, "refuge_id"))
                    if getattr(case, "refuge_id", None)
                    else None
                ),
                (
                    str(getattr(case, "custody_id"))
                    if getattr(case, "custody_id", None)
                    else None
                ),
                json.dumps(getattr(case, "details", {}) or {}, sort_keys=True),
                now,
                now,
            ),
        )


def _ensure_hybrid_population_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS simulation_people_light (
            person_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            birthyear INTEGER NOT NULL,
            deathyear INTEGER,
            is_alive INTEGER NOT NULL,
            gender TEXT NOT NULL DEFAULT '',
            species TEXT,
            ethnic TEXT,
            birthplace_region_key INTEGER,
            birthplace_settlement_key INTEGER,
            current_settlement_key INTEGER,
            job_family TEXT,
            partner_person_id INTEGER,
            partner_name TEXT,
            partner_birthyear INTEGER,
            partner_deathyear INTEGER,
            partnership_start_year INTEGER,
            partnership_end_year INTEGER,
            father_id INTEGER,
            mother_id INTEGER,
            child_count INTEGER NOT NULL DEFAULT 0,
            child_person_ids_json TEXT NOT NULL DEFAULT '[]',
            child_birthyears_json TEXT NOT NULL DEFAULT '[]',
            status_bucket TEXT,
            prosperity_bucket TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_simulation_people_light_alive
        ON simulation_people_light (is_alive);
        CREATE INDEX IF NOT EXISTS idx_simulation_people_light_birthyear
        ON simulation_people_light (birthyear);
        CREATE INDEX IF NOT EXISTS idx_simulation_people_light_current_settlement
        ON simulation_people_light (current_settlement_key);

        CREATE TABLE IF NOT EXISTS simulation_cohorts (
            cohort_id INTEGER PRIMARY KEY AUTOINCREMENT,
            sim_year INTEGER NOT NULL,
            region_key INTEGER,
            settlement_key INTEGER,
            age_band TEXT NOT NULL DEFAULT '',
            gender TEXT NOT NULL DEFAULT '',
            species TEXT NOT NULL DEFAULT '',
            culture TEXT NOT NULL DEFAULT '',
            job_family TEXT NOT NULL DEFAULT '',
            status_bucket TEXT NOT NULL DEFAULT '',
            population_count INTEGER NOT NULL DEFAULT 0,
            birth_count INTEGER NOT NULL DEFAULT 0,
            death_count INTEGER NOT NULL DEFAULT 0,
            UNIQUE (
                sim_year, region_key, settlement_key, age_band, gender,
                species, culture, job_family, status_bucket
            )
        );
        CREATE INDEX IF NOT EXISTS idx_simulation_cohorts_place_year
        ON simulation_cohorts (sim_year, region_key, settlement_key);

        CREATE TABLE IF NOT EXISTS simulation_promotion_log (
            promotion_id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER NOT NULL,
            sim_year INTEGER,
            reason TEXT NOT NULL,
            source_event_id INTEGER,
            synthesized_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_simulation_promotion_log_person
        ON simulation_promotion_log (person_id, sim_year);

        CREATE TABLE IF NOT EXISTS simulation_people_nondetailed (
            person_id INTEGER PRIMARY KEY,
            birthyear INTEGER NOT NULL,
            deathyear INTEGER,
            is_alive INTEGER NOT NULL DEFAULT 1,
            gender TEXT NOT NULL DEFAULT '',
            species_key TEXT,
            culture_key TEXT,
            birthplace_region_key INTEGER,
            birthplace_settlement_key INTEGER,
            current_settlement_key INTEGER,
            job_family TEXT NOT NULL DEFAULT 'other',
            is_partnered INTEGER NOT NULL DEFAULT 0,
            partner_person_id INTEGER,
            father_id INTEGER,
            mother_id INTEGER,
            child_count INTEGER NOT NULL DEFAULT 0,
            name_key TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_simulation_people_nondetailed_alive_age
        ON simulation_people_nondetailed (is_alive, birthyear);
        CREATE INDEX IF NOT EXISTS idx_simulation_people_nondetailed_place
        ON simulation_people_nondetailed (is_alive, current_settlement_key);
        CREATE INDEX IF NOT EXISTS idx_simulation_people_nondetailed_job
        ON simulation_people_nondetailed (is_alive, current_settlement_key, job_family);
        CREATE INDEX IF NOT EXISTS idx_simulation_people_nondetailed_partner
        ON simulation_people_nondetailed (
            is_alive, current_settlement_key, gender, is_partnered, partner_person_id, birthyear
        );
        """
    )
    cols = set(_table_columns(conn, "simulation_people_light"))
    if "species" not in cols:
        conn.execute("ALTER TABLE simulation_people_light ADD COLUMN species TEXT")
    if "ethnic" not in cols:
        conn.execute("ALTER TABLE simulation_people_light ADD COLUMN ethnic TEXT")
    for col, spec in (
        ("partner_name", "TEXT"),
        ("partner_birthyear", "INTEGER"),
        ("partner_deathyear", "INTEGER"),
        ("partnership_start_year", "INTEGER"),
        ("partnership_end_year", "INTEGER"),
        ("child_person_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("child_birthyears_json", "TEXT NOT NULL DEFAULT '[]'"),
    ):
        if col not in cols:
            conn.execute(f"ALTER TABLE simulation_people_light ADD COLUMN {col} {spec}")
    nd_cols = set(_table_columns(conn, "simulation_people_nondetailed"))
    for col, spec in (
        ("is_partnered", "INTEGER NOT NULL DEFAULT 0"),
        ("name_key", "TEXT"),
    ):
        if col not in nd_cols:
            conn.execute(f"ALTER TABLE simulation_people_nondetailed ADD COLUMN {col} {spec}")


def _event_origin_from_payload(payload: dict) -> str:
    raw = str(payload.get("event_origin") or "generated").strip().lower()
    return raw if raw in _EVENT_ORIGINS else "generated"


_EVENT_PERSON_SCALAR_ROLES: dict[str, str] = {
    "person_id": "subject",
    "person_a_id": "person_a",
    "person_b_id": "person_b",
    "child_id": "child",
    "related_child_id": "child",
    "father_id": "father",
    "mother_id": "mother",
    "killer_person_id": "killer",
    "perpetrator_person_id": "perpetrator",
    "accused_person_id": "accused",
    "betrayed_partner_person_id": "betrayed_partner",
    "paramour_person_id": "paramour",
    "benefactor_person_id": "benefactor",
    "beneficiary_person_id": "beneficiary",
    "creator_person_id": "creator",
    "patron_person_id": "patron",
    "target_person_id": "target",
    "victim_person_id": "victim",
    "purseholder_person_id": "purseholder",
    "moved_person_id": "moved",
    "holder_person_id": "holder",
    "previous_holder_id": "previous_holder",
    "prior_head_person_id": "prior_head",
    "claimant_id": "claimant",
}

_EVENT_PERSON_LIST_ROLES: dict[str, str] = {
    "household_member_ids": "household_member",
    "dependent_minor_ids": "dependent_minor",
    "moved_person_ids": "moved",
    "child_ids": "child",
    "related_child_ids": "child",
    "witness_person_ids": "witness",
    "suspect_person_ids": "suspect",
    "betrayed_partner_person_ids": "betrayed_partner",
}

_EVENT_SETTLEMENT_KEYS: tuple[str, ...] = (
    "settlement_id",
    "to_settlement_id",
    "from_settlement_id",
    "current_settlement_id",
    "birthplace_settlement_id",
    "near_settlement_id",
    "last_free_settlement_id",
)

_EVENT_REGION_KEYS: tuple[str, ...] = (
    "region_id",
    "to_region_id",
    "from_region_id",
    "current_region_id",
    "birthplace_region_id",
)

_EVENT_PAYLOAD_META_KEYS: tuple[str, ...] = (
    "event_origin",
)

_EVENT_MOVE_DETAIL_KEYS: frozenset[str] = frozenset(
    {
        "year",
        "person_id",
        "moved_person_id",
        "moved_person_ids",
        "from_settlement_id",
        "to_settlement_id",
        "from_region_id",
        "to_region_id",
        "cross_region",
        "move_reason",
        "requested_year",
        "planned_apply_year",
        "source_event",
        "group_id",
    }
)

_EVENT_ORIGINS: frozenset[str] = frozenset({"generated", "inferred", "backfilled"})


def _coerce_event_person_id(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _coerce_event_person_id_list(value: object) -> list[int]:
    raw = value
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                raw = text
        if isinstance(raw, str):
            raw = [part.strip() for part in raw.split(";")]
    if not isinstance(raw, (list, tuple, set)):
        return []
    out: list[int] = []
    seen: set[int] = set()
    for item in raw:
        pid = _coerce_event_person_id(item)
        if pid is None or pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
    return out


def _event_person_links_from_payload(
    payload: dict,
    *,
    event_type: object = "",
) -> list[tuple[int, str]]:
    """Extract person timeline links from common event payload fields."""
    event_type_s = str(event_type or payload.get("event_type") or "").strip()
    context_only_keys: set[str] = set()
    if event_type_s == "job_assigned":
        context_only_keys.update({"employer_person_id", "host_person_id"})
    links: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()

    def add(pid_value: object, role: str) -> None:
        pid = _coerce_event_person_id(pid_value)
        clean_role = str(role or "related").strip() or "related"
        if pid is None:
            return
        key = (pid, clean_role)
        if key in seen:
            return
        seen.add(key)
        links.append(key)

    for key, role in _EVENT_PERSON_SCALAR_ROLES.items():
        if key in payload:
            add(payload.get(key), role)

    for key, role in _EVENT_PERSON_LIST_ROLES.items():
        if key in payload:
            for pid in _coerce_event_person_id_list(payload.get(key)):
                add(pid, role)

    for key, value in payload.items():
        if key in _EVENT_PERSON_SCALAR_ROLES or key in _EVENT_PERSON_LIST_ROLES:
            continue
        if key in context_only_keys:
            continue
        if key.endswith("_person_id"):
            add(value, key[: -len("_person_id")] or "related")
        elif key.endswith("_person_ids"):
            role = key[: -len("_person_ids")] or "related"
            for pid in _coerce_event_person_id_list(value):
                add(pid, role)

    return links


def _first_payload_text(payload: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        val = payload.get(key)
        if val is None:
            continue
        text = str(val).strip()
        if text:
            return text
    return None


def _event_common_columns(
    payload: dict,
    *,
    event_type: object = "",
) -> tuple[int | None, int | None, str | None, str | None]:
    return _event_common_columns_from_links(
        payload,
        _event_person_links_from_payload(payload, event_type=event_type),
    )


def _event_common_columns_from_links(
    payload: dict,
    links: list[tuple[int, str]],
) -> tuple[int | None, int | None, str | None, str | None]:
    person_ids: list[int] = []
    seen: set[int] = set()
    for pid, _role in links:
        if pid in seen:
            continue
        seen.add(pid)
        person_ids.append(pid)
    primary = person_ids[0] if person_ids else None
    secondary = person_ids[1] if len(person_ids) > 1 else None
    return (
        primary,
        secondary,
        _first_payload_text(payload, _EVENT_SETTLEMENT_KEYS),
        _first_payload_text(payload, _EVENT_REGION_KEYS),
    )


def _coerce_event_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y"}


def _coerce_event_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_event_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _event_move_person_ids(payload: dict) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()

    def add(value: object) -> None:
        pid = _coerce_event_person_id(value)
        if pid is None or pid in seen:
            return
        seen.add(pid)
        ids.append(pid)

    add(payload.get("person_id"))
    add(payload.get("moved_person_id"))
    for pid in _coerce_event_person_id_list(payload.get("moved_person_ids")):
        add(pid)
    return ids


def _event_optional_text(payload: dict, key: str) -> str | None:
    value = payload.get(key)
    text = str(value or "").strip()
    return text or None


def _insert_simulation_event_move_rows(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    event_type: str,
    payload: dict,
    place_cache: _EventPlaceKeyCache | None = None,
) -> None:
    if str(event_type or "").strip() != "settlement_moved":
        return
    moved_ids = _event_move_person_ids(payload)
    if not moved_ids:
        return
    from_region_id = _event_optional_text(payload, "from_region_id")
    to_region_id = _event_optional_text(payload, "to_region_id")
    from_settlement_id = _event_optional_text(payload, "from_settlement_id")
    to_settlement_id = _event_optional_text(payload, "to_settlement_id")
    if place_cache is not None:
        from_settlement_key = place_cache.settlement_key(
            from_settlement_id,
            from_region_id,
        )
        to_settlement_key = place_cache.settlement_key(to_settlement_id, to_region_id)
        from_region_key = place_cache.region_key(from_region_id)
        to_region_key = place_cache.region_key(to_region_id)
    else:
        from_settlement_key = _lookup_or_insert_settlement_key(
            conn,
            from_settlement_id,
            from_region_id,
        )
        to_settlement_key = _lookup_or_insert_settlement_key(
            conn,
            to_settlement_id,
            to_region_id,
        )
        from_region_key = _lookup_or_insert_region_key(conn, from_region_id)
        to_region_key = _lookup_or_insert_region_key(conn, to_region_id)
    move_reason = _event_optional_text(payload, "move_reason")
    requested_year = _coerce_event_int(payload.get("requested_year"))
    planned_apply_year = _coerce_event_int(payload.get("planned_apply_year"))
    source_event = _event_optional_text(payload, "source_event")
    group_id = _event_optional_text(payload, "group_id")
    cross_region = 1 if _coerce_event_bool(payload.get("cross_region")) else 0
    for moved_person_id in moved_ids:
        conn.execute(
            """
            INSERT OR IGNORE INTO simulation_event_moves (
                event_id, moved_person_id, from_settlement_key, to_settlement_key,
                from_region_key, to_region_key, cross_region, move_reason,
                requested_year, planned_apply_year, source_event, group_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(event_id),
                int(moved_person_id),
                from_settlement_key,
                to_settlement_key,
                from_region_key,
                to_region_key,
                cross_region,
                move_reason,
                requested_year,
                planned_apply_year,
                source_event,
                group_id,
            ),
        )


def _ensure_simulation_domain_state_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS simulation_domain_states (
            region_key INTEGER NOT NULL,
            domain TEXT NOT NULL,
            domain_score REAL NOT NULL DEFAULT 0.0,
            breakthrough_count INTEGER NOT NULL DEFAULT 0,
            first_event_year INTEGER,
            latest_event_year INTEGER,
            first_event_id INTEGER,
            latest_event_id INTEGER,
            latest_incident_kind TEXT,
            latest_creator_person_id INTEGER,
            latest_settlement_key INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (region_key, domain)
        );
        CREATE INDEX IF NOT EXISTS idx_simulation_domain_states_domain
        ON simulation_domain_states (domain, region_key);
        CREATE INDEX IF NOT EXISTS idx_simulation_domain_states_latest_event
        ON simulation_domain_states (latest_event_id);
        """
    )
    _backfill_simulation_domain_states(conn)


def _domain_states_processed_event_id(conn: sqlite3.Connection) -> int:
    _ensure_save_metadata_schema(conn)
    row = conn.execute(
        """
        SELECT meta_value FROM save_metadata
        WHERE meta_key = ?
        """,
        (DOMAIN_STATES_BACKFILLED_META_KEY,),
    ).fetchone()
    if row is None or row[0] is None:
        return 0
    try:
        return max(0, int(str(row[0]).strip()))
    except (TypeError, ValueError):
        return 0


def _set_domain_states_processed_event_id(
    conn: sqlite3.Connection, event_id: int | None
) -> None:
    _ensure_save_metadata_schema(conn)
    processed = max(0, int(event_id or 0))
    conn.execute(
        """
        INSERT OR REPLACE INTO save_metadata (meta_key, meta_value)
        VALUES (?, ?)
        """,
        (DOMAIN_STATES_BACKFILLED_META_KEY, str(processed)),
    )


def _knowledge_state_delta_from_payload(payload: dict) -> tuple[str, float] | None:
    consequences = payload.get("consequences")
    if not isinstance(consequences, dict):
        consequences = {}
    knowledge_state = consequences.get("knowledge_state")
    if not isinstance(knowledge_state, dict):
        knowledge_state = {}
    domain = str(
        knowledge_state.get("domain") or payload.get("knowledge_domain") or ""
    ).strip()
    if not domain:
        return None
    delta = _coerce_event_float(knowledge_state.get("state_delta"))
    if delta is None:
        novelty = _coerce_event_float(payload.get("novelty_value"))
        delta = max(0.01, float(novelty or 0.0) * 0.35)
    delta = round(max(0.0, float(delta)), 5)
    if delta <= 0.0:
        return None
    return domain, delta


def _knowledge_state_diffusion_from_payload(
    payload: dict,
) -> list[tuple[str, str | None, str, float]]:
    consequences = payload.get("consequences")
    if not isinstance(consequences, dict):
        return []
    raw = consequences.get("knowledge_state_diffusion")
    if not isinstance(raw, list):
        return []
    out: list[tuple[str, str | None, str, float]] = []
    fallback_domain = str(payload.get("knowledge_domain") or "").strip()
    for item in raw:
        if not isinstance(item, dict):
            continue
        region_id = str(item.get("region_id") or "").strip()
        if not region_id:
            continue
        domain = str(item.get("domain") or fallback_domain).strip()
        if not domain:
            continue
        delta = _coerce_event_float(item.get("state_delta"))
        if delta is None:
            continue
        delta = round(max(0.0, float(delta)), 5)
        if delta <= 0.0:
            continue
        settlement_id = str(item.get("settlement_id") or "").strip() or None
        out.append((region_id, settlement_id, domain, delta))
    return out


def _upsert_simulation_domain_state_from_event(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    sim_year: int | None,
    event_type: str,
    payload: dict,
    settlement_key: int | None = None,
    region_key: int | None = None,
    created_at: str | None = None,
    place_cache: _EventPlaceKeyCache | None = None,
) -> None:
    event_type_s = str(event_type or "").strip()
    if (
        event_type_s not in {"knowledge_culture", "religious_cultural_conflict"}
        and not event_type_s.startswith("city_state_")
    ):
        return
    primary_state = _knowledge_state_delta_from_payload(payload)
    diffusion_states = _knowledge_state_diffusion_from_payload(payload)
    if primary_state is None and not diffusion_states:
        return
    rows: list[tuple[int, int | None, str, float]] = []
    if primary_state is not None:
        domain, delta = primary_state
        primary_region_key = region_key
        primary_settlement_key = settlement_key
        if primary_region_key is None:
            _primary, _secondary, settlement_id, region_id = _event_common_columns(payload)
            if place_cache is not None:
                primary_settlement_key = place_cache.settlement_key(
                    settlement_id,
                    region_id,
                )
                primary_region_key = place_cache.region_key(region_id)
            else:
                primary_settlement_key = _lookup_or_insert_settlement_key(
                    conn, settlement_id, region_id
                )
                primary_region_key = _lookup_or_insert_region_key(conn, region_id)
        if primary_region_key is not None:
            rows.append(
                (
                    int(primary_region_key),
                    primary_settlement_key,
                    domain,
                    delta,
                )
            )
    for region_id, settlement_id, domain, delta in diffusion_states:
        diffuse_region_key = (
            place_cache.region_key(region_id)
            if place_cache is not None
            else _lookup_or_insert_region_key(conn, region_id)
        )
        if diffuse_region_key is None:
            continue
        diffuse_settlement_key = (
            (
                place_cache.settlement_key(settlement_id, region_id)
                if place_cache is not None
                else _lookup_or_insert_settlement_key(conn, settlement_id, region_id)
            )
            if settlement_id
            else None
        )
        rows.append((int(diffuse_region_key), diffuse_settlement_key, domain, delta))
    if not rows:
        return
    ts = created_at or _utc_now_iso()
    creator_id = _coerce_event_person_id(payload.get("creator_person_id"))
    incident_kind = str(payload.get("incident_kind") or "").strip() or None

    for target_region_key, target_settlement_key, domain, delta in rows:
        conn.execute(
            """
            INSERT INTO simulation_domain_states (
                region_key, domain, domain_score, breakthrough_count,
                first_event_year, latest_event_year,
                first_event_id, latest_event_id,
                latest_incident_kind, latest_creator_person_id, latest_settlement_key,
                created_at, updated_at
            )
            VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(region_key, domain) DO UPDATE SET
                domain_score = round(
                    simulation_domain_states.domain_score + excluded.domain_score,
                    5
                ),
                breakthrough_count = simulation_domain_states.breakthrough_count + 1,
                first_event_year = COALESCE(
                    simulation_domain_states.first_event_year,
                    excluded.first_event_year
                ),
                latest_event_year = excluded.latest_event_year,
                first_event_id = COALESCE(
                    simulation_domain_states.first_event_id,
                    excluded.first_event_id
                ),
                latest_event_id = excluded.latest_event_id,
                latest_incident_kind = excluded.latest_incident_kind,
                latest_creator_person_id = excluded.latest_creator_person_id,
                latest_settlement_key = COALESCE(
                    excluded.latest_settlement_key,
                    simulation_domain_states.latest_settlement_key
                ),
                updated_at = excluded.updated_at
            """,
            (
                int(target_region_key),
                domain,
                delta,
                sim_year,
                sim_year,
                int(event_id),
                int(event_id),
                incident_kind,
                creator_id,
                target_settlement_key,
                ts,
                ts,
            ),
        )


def _backfill_simulation_domain_states(conn: sqlite3.Connection) -> None:
    processed_event_id = _domain_states_processed_event_id(conn)
    max_row = conn.execute("SELECT MAX(id) FROM simulation_events").fetchone()
    max_event_id = int(max_row[0] or 0) if max_row is not None else 0
    if max_event_id <= processed_event_id:
        return
    rows = conn.execute(
        """
        SELECT id, sim_year, event_type, settlement_key, region_key,
               payload_json, created_at
        FROM simulation_events
        WHERE id > ?
        ORDER BY id
        """,
        (processed_event_id,),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        _upsert_simulation_domain_state_from_event(
            conn,
            event_id=int(row["id"]),
            sim_year=row["sim_year"],
            event_type=str(row["event_type"] or ""),
            payload=payload,
            settlement_key=(
                int(row["settlement_key"]) if row["settlement_key"] is not None else None
            ),
            region_key=int(row["region_key"]) if row["region_key"] is not None else None,
            created_at=str(row["created_at"] or "") or None,
        )
    _set_domain_states_processed_event_id(conn, max_event_id)


def _ensure_simulation_obligation_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS simulation_obligations (
            obligation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_event_id INTEGER NOT NULL,
            obligation_key TEXT NOT NULL,
            obligation_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            owed_by_person_id INTEGER NOT NULL,
            owed_to_person_id INTEGER NOT NULL,
            region_key INTEGER,
            settlement_key INTEGER,
            strength REAL NOT NULL DEFAULT 0.0,
            start_year INTEGER,
            expected_end_year INTEGER,
            resolved_year INTEGER,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (source_event_id, obligation_key)
        );
        CREATE INDEX IF NOT EXISTS idx_simulation_obligations_owed_by
        ON simulation_obligations (owed_by_person_id, status);
        CREATE INDEX IF NOT EXISTS idx_simulation_obligations_owed_to
        ON simulation_obligations (owed_to_person_id, status);
        CREATE INDEX IF NOT EXISTS idx_simulation_obligations_place
        ON simulation_obligations (region_key, settlement_key, status);
        CREATE INDEX IF NOT EXISTS idx_simulation_obligations_type
        ON simulation_obligations (obligation_type, status);
        """
    )
    _backfill_simulation_obligations(conn)


def _obligations_processed_event_id(conn: sqlite3.Connection) -> int:
    _ensure_save_metadata_schema(conn)
    row = conn.execute(
        """
        SELECT meta_value FROM save_metadata
        WHERE meta_key = ?
        """,
        (OBLIGATIONS_BACKFILLED_META_KEY,),
    ).fetchone()
    if row is None or row[0] is None:
        return 0
    try:
        return max(0, int(str(row[0]).strip()))
    except (TypeError, ValueError):
        return 0


def _set_obligations_processed_event_id(
    conn: sqlite3.Connection, event_id: int | None
) -> None:
    _ensure_save_metadata_schema(conn)
    processed = max(0, int(event_id or 0))
    conn.execute(
        """
        INSERT OR REPLACE INTO save_metadata (meta_key, meta_value)
        VALUES (?, ?)
        """,
        (OBLIGATIONS_BACKFILLED_META_KEY, str(processed)),
    )


def _event_obligation_rows_from_payload(
    event_type: str,
    payload: dict,
    *,
    sim_year: int | None,
) -> list[dict[str, object]]:
    consequences = payload.get("consequences")
    if not isinstance(consequences, dict):
        consequences = {}
    raw_obligations = consequences.get("obligations")
    rows: list[dict[str, object]] = []
    if isinstance(raw_obligations, list):
        for item in raw_obligations:
            if isinstance(item, dict):
                rows.append(dict(item))
    if rows:
        return rows

    et = str(event_type or "").strip()
    if et == "knowledge_culture" and isinstance(consequences.get("patronage"), dict):
        creator_id = _coerce_event_person_id(payload.get("creator_person_id"))
        patron_id = _coerce_event_person_id(payload.get("patron_person_id"))
        if creator_id is not None and patron_id is not None:
            novelty = _coerce_event_float(payload.get("novelty_value")) or 0.0
            rows.append(
                {
                    "obligation_key": "creator_to_patron",
                    "obligation_type": "patronage_debt",
                    "owed_by_person_id": creator_id,
                    "owed_to_person_id": patron_id,
                    "strength": round(min(1.0, max(0.05, novelty * 1.8)), 5),
                    "start_year": sim_year,
                    "expected_duration_years": 20,
                    "source_role": "knowledge_patronage",
                }
            )
    elif et == "public_virtue" and isinstance(consequences.get("relief"), dict):
        benefactor_id = _coerce_event_person_id(payload.get("benefactor_person_id"))
        beneficiary_id = _coerce_event_person_id(payload.get("beneficiary_person_id"))
        if benefactor_id is not None and beneficiary_id is not None:
            relief = _coerce_event_float(payload.get("relief_value")) or 0.0
            rows.append(
                {
                    "obligation_key": "beneficiary_to_benefactor",
                    "obligation_type": "relief_debt",
                    "owed_by_person_id": beneficiary_id,
                    "owed_to_person_id": benefactor_id,
                    "strength": round(min(1.0, max(0.04, relief * 1.4)), 5),
                    "start_year": sim_year,
                    "expected_duration_years": 12,
                    "source_role": "public_virtue_relief",
                }
            )
    return rows


def _insert_simulation_obligation_rows(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    sim_year: int | None,
    event_type: str,
    payload: dict,
    settlement_key: int | None = None,
    region_key: int | None = None,
    created_at: str | None = None,
    place_cache: _EventPlaceKeyCache | None = None,
) -> None:
    obligations = _event_obligation_rows_from_payload(
        event_type,
        payload,
        sim_year=sim_year,
    )
    if not obligations:
        return
    _primary, _secondary, payload_settlement_id, payload_region_id = _event_common_columns(
        payload
    )
    ts = created_at or _utc_now_iso()
    for idx, obligation in enumerate(obligations):
        owed_by = _coerce_event_person_id(obligation.get("owed_by_person_id"))
        owed_to = _coerce_event_person_id(obligation.get("owed_to_person_id"))
        if owed_by is None or owed_to is None:
            continue
        obligation_type = str(
            obligation.get("obligation_type") or obligation.get("kind") or ""
        ).strip()
        if not obligation_type:
            continue
        obligation_key = str(
            obligation.get("obligation_key")
            or obligation.get("source_role")
            or f"{obligation_type}:{owed_by}:{owed_to}:{idx}"
        ).strip()
        status = str(obligation.get("status") or "active").strip() or "active"
        strength_raw = _coerce_event_float(obligation.get("strength"))
        strength = round(min(1.0, max(0.0, float(strength_raw or 0.0))), 5)
        start_year = _coerce_event_int(obligation.get("start_year"))
        if start_year is None:
            start_year = sim_year
        expected_end_year = _coerce_event_int(obligation.get("expected_end_year"))
        if expected_end_year is None and start_year is not None:
            duration = _coerce_event_int(obligation.get("expected_duration_years"))
            if duration is not None and duration > 0:
                expected_end_year = int(start_year) + int(duration)
        resolved_year = _coerce_event_int(obligation.get("resolved_year"))
        obligation_region_id = str(
            obligation.get("region_id") or payload_region_id or ""
        ).strip()
        obligation_settlement_id = str(
            obligation.get("settlement_id") or payload_settlement_id or ""
        ).strip()
        obligation_region_key = region_key
        if obligation_region_id:
            obligation_region_key = (
                place_cache.region_key(obligation_region_id)
                if place_cache is not None
                else _lookup_or_insert_region_key(conn, obligation_region_id)
            )
        obligation_settlement_key = settlement_key
        if obligation_settlement_id:
            obligation_settlement_key = (
                place_cache.settlement_key(
                    obligation_settlement_id,
                    obligation_region_id,
                )
                if place_cache is not None
                else _lookup_or_insert_settlement_key(
                    conn,
                    obligation_settlement_id,
                    obligation_region_id,
                )
            )
        details = {
            str(k): v
            for k, v in obligation.items()
            if str(k)
            not in {
                "obligation_key",
                "obligation_type",
                "kind",
                "status",
                "owed_by_person_id",
                "owed_to_person_id",
                "strength",
                "start_year",
                "expected_end_year",
                "expected_duration_years",
                "resolved_year",
                "settlement_id",
                "region_id",
            }
        }
        conn.execute(
            """
            INSERT OR IGNORE INTO simulation_obligations (
                source_event_id, obligation_key, obligation_type, status,
                owed_by_person_id, owed_to_person_id, region_key, settlement_key,
                strength, start_year, expected_end_year, resolved_year,
                details_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(event_id),
                obligation_key,
                obligation_type,
                status,
                int(owed_by),
                int(owed_to),
                obligation_region_key,
                obligation_settlement_key,
                strength,
                start_year,
                expected_end_year,
                resolved_year,
                json.dumps(details, separators=(",", ":")),
                ts,
                ts,
            ),
        )


def _backfill_simulation_obligations(conn: sqlite3.Connection) -> None:
    processed_event_id = _obligations_processed_event_id(conn)
    max_row = conn.execute("SELECT MAX(id) FROM simulation_events").fetchone()
    max_event_id = int(max_row[0] or 0) if max_row is not None else 0
    if max_event_id <= processed_event_id:
        return
    rows = conn.execute(
        """
        SELECT id, sim_year, event_type, settlement_key, region_key,
               payload_json, created_at
        FROM simulation_events
        WHERE id > ?
        ORDER BY id
        """,
        (processed_event_id,),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        _insert_simulation_obligation_rows(
            conn,
            event_id=int(row["id"]),
            sim_year=row["sim_year"],
            event_type=str(row["event_type"] or ""),
            payload=payload,
            settlement_key=(
                int(row["settlement_key"]) if row["settlement_key"] is not None else None
            ),
            region_key=int(row["region_key"]) if row["region_key"] is not None else None,
            created_at=str(row["created_at"] or "") or None,
        )
    _set_obligations_processed_event_id(conn, max_event_id)


def _ensure_simulation_reputation_mark_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS simulation_reputation_marks (
            reputation_mark_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_event_id INTEGER NOT NULL,
            mark_key TEXT NOT NULL,
            person_id INTEGER NOT NULL,
            reputation_axis TEXT NOT NULL,
            reputation_before TEXT,
            reputation_after TEXT,
            direction TEXT NOT NULL DEFAULT 'positive',
            mark_strength REAL NOT NULL DEFAULT 0.0,
            region_key INTEGER,
            settlement_key INTEGER,
            mark_year INTEGER,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (source_event_id, mark_key)
        );
        CREATE INDEX IF NOT EXISTS idx_simulation_reputation_marks_person
        ON simulation_reputation_marks (person_id, reputation_axis, mark_year);
        CREATE INDEX IF NOT EXISTS idx_simulation_reputation_marks_axis
        ON simulation_reputation_marks (reputation_axis, direction);
        CREATE INDEX IF NOT EXISTS idx_simulation_reputation_marks_place
        ON simulation_reputation_marks (region_key, settlement_key, mark_year);
        """
    )
    _backfill_simulation_reputation_marks(conn)


def _reputation_marks_processed_event_id(conn: sqlite3.Connection) -> int:
    _ensure_save_metadata_schema(conn)
    row = conn.execute(
        """
        SELECT meta_value FROM save_metadata
        WHERE meta_key = ?
        """,
        (REPUTATION_MARKS_BACKFILLED_META_KEY,),
    ).fetchone()
    if row is None or row[0] is None:
        return 0
    try:
        return max(0, int(str(row[0]).strip()))
    except (TypeError, ValueError):
        return 0


def _set_reputation_marks_processed_event_id(
    conn: sqlite3.Connection, event_id: int | None
) -> None:
    _ensure_save_metadata_schema(conn)
    processed = max(0, int(event_id or 0))
    conn.execute(
        """
        INSERT OR REPLACE INTO save_metadata (meta_key, meta_value)
        VALUES (?, ?)
        """,
        (REPUTATION_MARKS_BACKFILLED_META_KEY, str(processed)),
    )


def _reputation_value_rank(value: object) -> int:
    text = str(value or "").strip().lower()
    if text in {"high", "strong", "middle-high", "volatile-high"}:
        return 3
    if text in {"medium", "middle-variable", "middle-high but brittle", "middle-high but cold"}:
        return 2
    if text in {"low", "weak", "middle-low", "low-middle", "low-variable", "volatile-low"}:
        return 1
    return 0


def _reputation_direction(before: object, after: object) -> str:
    before_rank = _reputation_value_rank(before)
    after_rank = _reputation_value_rank(after)
    if after_rank > before_rank:
        return "positive"
    if after_rank < before_rank:
        return "negative"
    return "stable"


def _reputation_strength(before: object, after: object) -> float:
    before_rank = _reputation_value_rank(before)
    after_rank = _reputation_value_rank(after)
    if before_rank == after_rank:
        return 0.05
    return round(min(1.0, max(0.05, abs(after_rank - before_rank) / 3.0)), 5)


def _event_reputation_mark_rows_from_payload(
    event_type: str,
    payload: dict,
    *,
    sim_year: int | None,
) -> list[dict[str, object]]:
    consequences = payload.get("consequences")
    if not isinstance(consequences, dict):
        consequences = {}
    raw_marks = consequences.get("reputation_marks")
    rows: list[dict[str, object]] = []
    if isinstance(raw_marks, list):
        for item in raw_marks:
            if isinstance(item, dict):
                rows.append(dict(item))
    if rows:
        return rows

    public_reputation = consequences.get("public_reputation")
    if not isinstance(public_reputation, dict):
        return []
    person_id = _coerce_event_person_id(public_reputation.get("person_id"))
    if person_id is None:
        person_id = _coerce_event_person_id(payload.get("benefactor_person_id"))
    if person_id is None:
        person_id = _coerce_event_person_id(payload.get("creator_person_id"))
    if person_id is None:
        return []
    source_role = {
        "public_virtue": "public_virtue_reputation",
        "knowledge_culture": "knowledge_reputation",
    }.get(str(event_type or "").strip(), "event_reputation")
    axis_fields = (
        ("leader_tendency", "leadership"),
        ("status_tendency", "status"),
    )
    for field_name, axis in axis_fields:
        before_key = f"{field_name}_before"
        after_key = f"{field_name}_after"
        if before_key not in public_reputation and after_key not in public_reputation:
            continue
        before_value = public_reputation.get(before_key)
        after_value = public_reputation.get(after_key)
        rows.append(
            {
                "mark_key": f"{axis}:{person_id}",
                "person_id": person_id,
                "reputation_axis": axis,
                "reputation_before": before_value,
                "reputation_after": after_value,
                "direction": _reputation_direction(before_value, after_value),
                "mark_strength": _reputation_strength(before_value, after_value),
                "mark_year": sim_year,
                "source_role": source_role,
            }
        )
    return rows


def _insert_simulation_reputation_mark_rows(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    sim_year: int | None,
    event_type: str,
    payload: dict,
    settlement_key: int | None = None,
    region_key: int | None = None,
    created_at: str | None = None,
    place_cache: _EventPlaceKeyCache | None = None,
) -> None:
    marks = _event_reputation_mark_rows_from_payload(
        event_type,
        payload,
        sim_year=sim_year,
    )
    if not marks:
        return
    _primary, _secondary, payload_settlement_id, payload_region_id = _event_common_columns(
        payload
    )
    ts = created_at or _utc_now_iso()
    for idx, mark in enumerate(marks):
        person_id = _coerce_event_person_id(mark.get("person_id"))
        if person_id is None:
            continue
        axis = str(mark.get("reputation_axis") or mark.get("axis") or "").strip()
        if not axis:
            continue
        before_value = mark.get("reputation_before")
        after_value = mark.get("reputation_after")
        direction = str(
            mark.get("direction") or _reputation_direction(before_value, after_value)
        ).strip() or "stable"
        strength_raw = _coerce_event_float(mark.get("mark_strength"))
        if strength_raw is None:
            strength_raw = _coerce_event_float(mark.get("strength"))
        strength = (
            round(min(1.0, max(0.0, float(strength_raw))), 5)
            if strength_raw is not None
            else _reputation_strength(before_value, after_value)
        )
        mark_key = str(
            mark.get("mark_key") or f"{axis}:{person_id}:{idx}"
        ).strip()
        mark_year = _coerce_event_int(mark.get("mark_year"))
        if mark_year is None:
            mark_year = sim_year
        mark_region_id = str(mark.get("region_id") or payload_region_id or "").strip()
        mark_settlement_id = str(
            mark.get("settlement_id") or payload_settlement_id or ""
        ).strip()
        mark_region_key = region_key
        if mark_region_id:
            mark_region_key = (
                place_cache.region_key(mark_region_id)
                if place_cache is not None
                else _lookup_or_insert_region_key(conn, mark_region_id)
            )
        mark_settlement_key = settlement_key
        if mark_settlement_id:
            mark_settlement_key = (
                place_cache.settlement_key(mark_settlement_id, mark_region_id)
                if place_cache is not None
                else _lookup_or_insert_settlement_key(
                    conn,
                    mark_settlement_id,
                    mark_region_id,
                )
            )
        details = {
            str(k): v
            for k, v in mark.items()
            if str(k)
            not in {
                "mark_key",
                "person_id",
                "reputation_axis",
                "axis",
                "reputation_before",
                "reputation_after",
                "direction",
                "mark_strength",
                "strength",
                "mark_year",
                "settlement_id",
                "region_id",
            }
        }
        conn.execute(
            """
            INSERT OR IGNORE INTO simulation_reputation_marks (
                source_event_id, mark_key, person_id, reputation_axis,
                reputation_before, reputation_after, direction, mark_strength,
                region_key, settlement_key, mark_year, details_json,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(event_id),
                mark_key,
                int(person_id),
                axis,
                str(before_value) if before_value is not None else None,
                str(after_value) if after_value is not None else None,
                direction,
                strength,
                mark_region_key,
                mark_settlement_key,
                mark_year,
                json.dumps(details, separators=(",", ":")),
                ts,
                ts,
            ),
        )


def _backfill_simulation_reputation_marks(conn: sqlite3.Connection) -> None:
    processed_event_id = _reputation_marks_processed_event_id(conn)
    max_row = conn.execute("SELECT MAX(id) FROM simulation_events").fetchone()
    max_event_id = int(max_row[0] or 0) if max_row is not None else 0
    if max_event_id <= processed_event_id:
        return
    rows = conn.execute(
        """
        SELECT id, sim_year, event_type, settlement_key, region_key,
               payload_json, created_at
        FROM simulation_events
        WHERE id > ?
        ORDER BY id
        """,
        (processed_event_id,),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        _insert_simulation_reputation_mark_rows(
            conn,
            event_id=int(row["id"]),
            sim_year=row["sim_year"],
            event_type=str(row["event_type"] or ""),
            payload=payload,
            settlement_key=(
                int(row["settlement_key"]) if row["settlement_key"] is not None else None
            ),
            region_key=int(row["region_key"]) if row["region_key"] is not None else None,
            created_at=str(row["created_at"] or "") or None,
        )
    _set_reputation_marks_processed_event_id(conn, max_event_id)


def _ensure_simulation_legal_fallout_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS simulation_legal_fallout (
            fallout_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_event_id INTEGER NOT NULL,
            fallout_key TEXT NOT NULL,
            fallout_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            principal_person_id INTEGER NOT NULL,
            opposing_person_id INTEGER,
            related_person_id INTEGER,
            region_key INTEGER,
            settlement_key INTEGER,
            severity REAL NOT NULL DEFAULT 0.0,
            start_year INTEGER,
            expected_resolution_year INTEGER,
            resolved_year INTEGER,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (source_event_id, fallout_key)
        );
        CREATE INDEX IF NOT EXISTS idx_simulation_legal_fallout_principal
        ON simulation_legal_fallout (principal_person_id, status);
        CREATE INDEX IF NOT EXISTS idx_simulation_legal_fallout_opposing
        ON simulation_legal_fallout (opposing_person_id, status);
        CREATE INDEX IF NOT EXISTS idx_simulation_legal_fallout_type
        ON simulation_legal_fallout (fallout_type, status);
        CREATE INDEX IF NOT EXISTS idx_simulation_legal_fallout_place
        ON simulation_legal_fallout (region_key, settlement_key, status);
        """
    )
    _backfill_simulation_legal_fallout(conn)


def _legal_fallout_processed_event_id(conn: sqlite3.Connection) -> int:
    _ensure_save_metadata_schema(conn)
    row = conn.execute(
        """
        SELECT meta_value FROM save_metadata
        WHERE meta_key = ?
        """,
        (LEGAL_FALLOUT_BACKFILLED_META_KEY,),
    ).fetchone()
    if row is None or row[0] is None:
        return 0
    try:
        return max(0, int(str(row[0]).strip()))
    except (TypeError, ValueError):
        return 0


def _set_legal_fallout_processed_event_id(
    conn: sqlite3.Connection, event_id: int | None
) -> None:
    _ensure_save_metadata_schema(conn)
    processed = max(0, int(event_id or 0))
    conn.execute(
        """
        INSERT OR REPLACE INTO save_metadata (meta_key, meta_value)
        VALUES (?, ?)
        """,
        (LEGAL_FALLOUT_BACKFILLED_META_KEY, str(processed)),
    )


def _event_betrayed_partner_ids(payload: dict) -> list[int]:
    betrayed_ids = _coerce_event_person_id_list(
        payload.get("betrayed_partner_person_ids")
    )
    primary_betrayed = _coerce_event_person_id(
        payload.get("betrayed_partner_person_id")
    )
    if primary_betrayed is not None and primary_betrayed not in betrayed_ids:
        betrayed_ids.insert(0, primary_betrayed)
    return betrayed_ids


def _event_legal_fallout_rows_from_payload(
    event_type: str,
    payload: dict,
    *,
    sim_year: int | None,
) -> list[dict[str, object]]:
    consequences = payload.get("consequences")
    if not isinstance(consequences, dict):
        consequences = {}
    raw_fallout = consequences.get("legal_fallout")
    rows: list[dict[str, object]] = []
    if isinstance(raw_fallout, list):
        for item in raw_fallout:
            if isinstance(item, dict):
                rows.append(dict(item))
    if rows:
        return rows

    if str(event_type or "").strip() != "affair_scandal":
        return []
    incident_kind = str(payload.get("incident_kind") or "").strip()
    if incident_kind not in LEGAL_FALLOUT_SCANDAL_KINDS:
        return []
    accused_id = _coerce_event_person_id(payload.get("accused_person_id"))
    paramour_id = _coerce_event_person_id(payload.get("paramour_person_id"))
    if accused_id is None or paramour_id is None:
        return []
    betrayed_ids = _event_betrayed_partner_ids(payload)
    opposing_id = betrayed_ids[0] if betrayed_ids else None
    historical_importance = _coerce_event_float(payload.get("historical_importance")) or 0.0
    severity = round(
        min(
            1.0,
            max(
                0.06,
                0.22
                + float(historical_importance) * 0.75
                + min(0.15, 0.05 * len(betrayed_ids)),
            ),
        ),
        5,
    )
    if incident_kind == "heir_legitimacy_rumor":
        fallout_type = "heir_legitimacy_challenge"
        fallout_key = f"heir_legitimacy:{accused_id}:{paramour_id}:{opposing_id or 0}"
        duration_years = 18
    else:
        fallout_type = "inheritance_dispute"
        fallout_key = f"inheritance:{accused_id}:{opposing_id or 0}:{paramour_id}"
        duration_years = 8
    return [
        {
            "fallout_key": fallout_key,
            "fallout_type": fallout_type,
            "status": "active",
            "principal_person_id": accused_id,
            "opposing_person_id": opposing_id,
            "related_person_id": paramour_id,
            "severity": severity,
            "start_year": sim_year,
            "expected_duration_years": duration_years,
            "source_role": "affair_scandal_legal_fallout",
            "incident_kind": incident_kind,
            "betrayed_partner_person_ids": betrayed_ids,
        }
    ]


def _insert_simulation_legal_fallout_rows(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    sim_year: int | None,
    event_type: str,
    payload: dict,
    settlement_key: int | None = None,
    region_key: int | None = None,
    created_at: str | None = None,
    place_cache: _EventPlaceKeyCache | None = None,
) -> None:
    fallout_rows = _event_legal_fallout_rows_from_payload(
        event_type,
        payload,
        sim_year=sim_year,
    )
    if not fallout_rows:
        return
    _primary, _secondary, payload_settlement_id, payload_region_id = _event_common_columns(
        payload
    )
    ts = created_at or _utc_now_iso()
    for idx, fallout in enumerate(fallout_rows):
        principal_id = _coerce_event_person_id(fallout.get("principal_person_id"))
        if principal_id is None:
            continue
        fallout_type = str(
            fallout.get("fallout_type") or fallout.get("kind") or ""
        ).strip()
        if not fallout_type:
            continue
        opposing_id = _coerce_event_person_id(fallout.get("opposing_person_id"))
        related_id = _coerce_event_person_id(fallout.get("related_person_id"))
        fallout_key = str(
            fallout.get("fallout_key")
            or fallout.get("source_role")
            or f"{fallout_type}:{principal_id}:{opposing_id or 0}:{related_id or 0}:{idx}"
        ).strip()
        status = str(fallout.get("status") or "active").strip() or "active"
        severity_raw = _coerce_event_float(fallout.get("severity"))
        severity = round(min(1.0, max(0.0, float(severity_raw or 0.0))), 5)
        start_year = _coerce_event_int(fallout.get("start_year"))
        if start_year is None:
            start_year = sim_year
        expected_resolution_year = _coerce_event_int(
            fallout.get("expected_resolution_year")
        )
        if expected_resolution_year is None and start_year is not None:
            duration = _coerce_event_int(fallout.get("expected_duration_years"))
            if duration is not None and duration > 0:
                expected_resolution_year = int(start_year) + int(duration)
        resolved_year = _coerce_event_int(fallout.get("resolved_year"))
        fallout_region_id = str(
            fallout.get("region_id") or payload_region_id or ""
        ).strip()
        fallout_settlement_id = str(
            fallout.get("settlement_id") or payload_settlement_id or ""
        ).strip()
        fallout_region_key = region_key
        if fallout_region_id:
            fallout_region_key = (
                place_cache.region_key(fallout_region_id)
                if place_cache is not None
                else _lookup_or_insert_region_key(conn, fallout_region_id)
            )
        fallout_settlement_key = settlement_key
        if fallout_settlement_id:
            fallout_settlement_key = (
                place_cache.settlement_key(fallout_settlement_id, fallout_region_id)
                if place_cache is not None
                else _lookup_or_insert_settlement_key(
                    conn,
                    fallout_settlement_id,
                    fallout_region_id,
                )
            )
        details = {
            str(k): v
            for k, v in fallout.items()
            if str(k)
            not in {
                "fallout_key",
                "fallout_type",
                "kind",
                "status",
                "principal_person_id",
                "opposing_person_id",
                "related_person_id",
                "severity",
                "start_year",
                "expected_resolution_year",
                "expected_duration_years",
                "resolved_year",
                "settlement_id",
                "region_id",
            }
        }
        conn.execute(
            """
            INSERT OR IGNORE INTO simulation_legal_fallout (
                source_event_id, fallout_key, fallout_type, status,
                principal_person_id, opposing_person_id, related_person_id,
                region_key, settlement_key, severity, start_year,
                expected_resolution_year, resolved_year, details_json,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(event_id),
                fallout_key,
                fallout_type,
                status,
                int(principal_id),
                int(opposing_id) if opposing_id is not None else None,
                int(related_id) if related_id is not None else None,
                fallout_region_key,
                fallout_settlement_key,
                severity,
                start_year,
                expected_resolution_year,
                resolved_year,
                json.dumps(details, separators=(",", ":")),
                ts,
                ts,
            ),
        )


def _backfill_simulation_legal_fallout(conn: sqlite3.Connection) -> None:
    processed_event_id = _legal_fallout_processed_event_id(conn)
    max_row = conn.execute("SELECT MAX(id) FROM simulation_events").fetchone()
    max_event_id = int(max_row[0] or 0) if max_row is not None else 0
    if max_event_id <= processed_event_id:
        return
    rows = conn.execute(
        """
        SELECT id, sim_year, event_type, settlement_key, region_key,
               payload_json, created_at
        FROM simulation_events
        WHERE id > ?
        ORDER BY id
        """,
        (processed_event_id,),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        _insert_simulation_legal_fallout_rows(
            conn,
            event_id=int(row["id"]),
            sim_year=row["sim_year"],
            event_type=str(row["event_type"] or ""),
            payload=payload,
            settlement_key=(
                int(row["settlement_key"]) if row["settlement_key"] is not None else None
            ),
            region_key=int(row["region_key"]) if row["region_key"] is not None else None,
            created_at=str(row["created_at"] or "") or None,
        )
    _set_legal_fallout_processed_event_id(conn, max_event_id)


def _ensure_simulation_faction_memory_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS simulation_faction_memory (
            memory_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_event_id INTEGER NOT NULL,
            memory_key TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            faction_a_key TEXT NOT NULL,
            faction_b_key TEXT,
            principal_person_id INTEGER,
            opposing_person_id INTEGER,
            region_key INTEGER,
            settlement_key INTEGER,
            polarity TEXT NOT NULL DEFAULT 'negative',
            strength REAL NOT NULL DEFAULT 0.0,
            start_year INTEGER,
            expected_decay_year INTEGER,
            resolved_year INTEGER,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (source_event_id, memory_key)
        );
        CREATE INDEX IF NOT EXISTS idx_simulation_faction_memory_person
        ON simulation_faction_memory (principal_person_id, opposing_person_id, status);
        CREATE INDEX IF NOT EXISTS idx_simulation_faction_memory_type
        ON simulation_faction_memory (memory_type, status);
        CREATE INDEX IF NOT EXISTS idx_simulation_faction_memory_place
        ON simulation_faction_memory (region_key, settlement_key, status);
        """
    )
    _backfill_simulation_faction_memory(conn)


def _processed_event_id_for_meta(conn: sqlite3.Connection, meta_key: str) -> int:
    _ensure_save_metadata_schema(conn)
    row = conn.execute(
        """
        SELECT meta_value FROM save_metadata
        WHERE meta_key = ?
        """,
        (meta_key,),
    ).fetchone()
    if row is None or row[0] is None:
        return 0
    try:
        return max(0, int(str(row[0]).strip()))
    except (TypeError, ValueError):
        return 0


def _set_processed_event_id_for_meta(
    conn: sqlite3.Connection, meta_key: str, event_id: int | None
) -> None:
    _ensure_save_metadata_schema(conn)
    processed = max(0, int(event_id or 0))
    conn.execute(
        """
        INSERT OR REPLACE INTO save_metadata (meta_key, meta_value)
        VALUES (?, ?)
        """,
        (meta_key, str(processed)),
    )


def _event_faction_memory_rows_from_payload(
    event_type: str,
    payload: dict,
    *,
    sim_year: int | None,
) -> list[dict[str, object]]:
    consequences = payload.get("consequences")
    if not isinstance(consequences, dict):
        consequences = {}
    raw = consequences.get("faction_memory")
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, dict)]
    etype = str(event_type or "").strip()
    if etype == "murder":
        killer_id = _coerce_event_person_id(payload.get("killer_person_id"))
        victim_id = _coerce_event_person_id(payload.get("victim_person_id"))
        if killer_id is None or victim_id is None:
            return []
        kind = str(payload.get("incident_kind") or "murder").strip()
        importance = _coerce_event_float(payload.get("historical_importance")) or 0.0
        return [
            {
                "memory_key": f"violent_grievance:{victim_id}:{killer_id}:{kind}",
                "memory_type": "violent_grievance",
                "principal_person_id": victim_id,
                "opposing_person_id": killer_id,
                "faction_a_key": f"person:{victim_id}",
                "faction_b_key": f"person:{killer_id}",
                "polarity": "negative",
                "strength": 0.45 + float(importance) * 0.45,
                "start_year": sim_year,
                "expected_duration_years": 18,
                "source_role": "murder_grievance",
                "incident_kind": kind,
            }
        ]
    return []


def _insert_simulation_faction_memory_rows(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    sim_year: int | None,
    event_type: str,
    payload: dict,
    settlement_key: int | None = None,
    region_key: int | None = None,
    created_at: str | None = None,
    place_cache: _EventPlaceKeyCache | None = None,
) -> None:
    rows = _event_faction_memory_rows_from_payload(
        event_type, payload, sim_year=sim_year
    )
    if not rows:
        return
    _primary, _secondary, payload_settlement_id, payload_region_id = _event_common_columns(
        payload
    )
    ts = created_at or _utc_now_iso()
    for idx, item in enumerate(rows):
        memory_type = str(item.get("memory_type") or item.get("kind") or "").strip()
        if not memory_type:
            continue
        principal_id = _coerce_event_person_id(item.get("principal_person_id"))
        opposing_id = _coerce_event_person_id(item.get("opposing_person_id"))
        faction_a = str(
            item.get("faction_a_key")
            or (f"person:{principal_id}" if principal_id is not None else "")
        ).strip()
        if not faction_a:
            continue
        faction_b = str(item.get("faction_b_key") or "").strip() or None
        memory_key = str(
            item.get("memory_key")
            or f"{memory_type}:{faction_a}:{faction_b or 'none'}:{idx}"
        ).strip()
        status = str(item.get("status") or "active").strip() or "active"
        polarity = str(item.get("polarity") or "negative").strip() or "negative"
        strength_raw = _coerce_event_float(item.get("strength"))
        strength = round(min(1.0, max(0.0, float(strength_raw or 0.0))), 5)
        start_year = _coerce_event_int(item.get("start_year"))
        if start_year is None:
            start_year = sim_year
        expected_decay_year = _coerce_event_int(item.get("expected_decay_year"))
        if expected_decay_year is None and start_year is not None:
            duration = _coerce_event_int(item.get("expected_duration_years"))
            if duration is not None and duration > 0:
                expected_decay_year = int(start_year) + int(duration)
        resolved_year = _coerce_event_int(item.get("resolved_year"))
        region_id = str(item.get("region_id") or payload_region_id or "").strip()
        settlement_id = str(
            item.get("settlement_id") or payload_settlement_id or ""
        ).strip()
        item_region_key = region_key
        if region_id:
            item_region_key = (
                place_cache.region_key(region_id)
                if place_cache is not None
                else _lookup_or_insert_region_key(conn, region_id)
            )
        item_settlement_key = settlement_key
        if settlement_id:
            item_settlement_key = (
                place_cache.settlement_key(settlement_id, region_id)
                if place_cache is not None
                else _lookup_or_insert_settlement_key(conn, settlement_id, region_id)
            )
        details = {
            str(k): v
            for k, v in item.items()
            if str(k)
            not in {
                "memory_key",
                "memory_type",
                "kind",
                "status",
                "faction_a_key",
                "faction_b_key",
                "principal_person_id",
                "opposing_person_id",
                "polarity",
                "strength",
                "start_year",
                "expected_decay_year",
                "expected_duration_years",
                "resolved_year",
                "settlement_id",
                "region_id",
            }
        }
        conn.execute(
            """
            INSERT OR IGNORE INTO simulation_faction_memory (
                source_event_id, memory_key, memory_type, status,
                faction_a_key, faction_b_key, principal_person_id,
                opposing_person_id, region_key, settlement_key, polarity,
                strength, start_year, expected_decay_year, resolved_year,
                details_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(event_id),
                memory_key,
                memory_type,
                status,
                faction_a,
                faction_b,
                int(principal_id) if principal_id is not None else None,
                int(opposing_id) if opposing_id is not None else None,
                item_region_key,
                item_settlement_key,
                polarity,
                strength,
                start_year,
                expected_decay_year,
                resolved_year,
                json.dumps(details, separators=(",", ":")),
                ts,
                ts,
            ),
        )


def _backfill_simulation_faction_memory(conn: sqlite3.Connection) -> None:
    processed_event_id = _processed_event_id_for_meta(
        conn, FACTION_MEMORY_BACKFILLED_META_KEY
    )
    max_row = conn.execute("SELECT MAX(id) FROM simulation_events").fetchone()
    max_event_id = int(max_row[0] or 0) if max_row is not None else 0
    if max_event_id <= processed_event_id:
        return
    rows = conn.execute(
        """
        SELECT id, sim_year, event_type, settlement_key, region_key,
               payload_json, created_at
        FROM simulation_events
        WHERE id > ?
        ORDER BY id
        """,
        (processed_event_id,),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        _insert_simulation_faction_memory_rows(
            conn,
            event_id=int(row["id"]),
            sim_year=row["sim_year"],
            event_type=str(row["event_type"] or ""),
            payload=payload,
            settlement_key=(
                int(row["settlement_key"]) if row["settlement_key"] is not None else None
            ),
            region_key=int(row["region_key"]) if row["region_key"] is not None else None,
            created_at=str(row["created_at"] or "") or None,
        )
    _set_processed_event_id_for_meta(
        conn, FACTION_MEMORY_BACKFILLED_META_KEY, max_event_id
    )


def _ensure_simulation_institution_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS simulation_institutions (
            institution_id INTEGER PRIMARY KEY AUTOINCREMENT,
            institution_key TEXT NOT NULL UNIQUE,
            institution_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            region_key INTEGER,
            settlement_key INTEGER,
            focus_domain TEXT NOT NULL DEFAULT '',
            founded_year INTEGER,
            latest_year INTEGER,
            founding_event_id INTEGER,
            latest_event_id INTEGER,
            founder_person_id INTEGER,
            patron_person_id INTEGER,
            strength REAL NOT NULL DEFAULT 0.0,
            influence_score REAL NOT NULL DEFAULT 0.0,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_simulation_institutions_type
        ON simulation_institutions (institution_type, status);
        CREATE INDEX IF NOT EXISTS idx_simulation_institutions_place
        ON simulation_institutions (region_key, settlement_key, status);
        CREATE INDEX IF NOT EXISTS idx_simulation_institutions_domain
        ON simulation_institutions (focus_domain, institution_type);
        """
    )
    _backfill_simulation_institutions(conn)


def _event_institution_rows_from_payload(payload: dict) -> list[dict[str, object]]:
    consequences = payload.get("consequences")
    if not isinstance(consequences, dict):
        return []
    raw = consequences.get("institutions")
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _upsert_simulation_institution_rows(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    sim_year: int | None,
    event_type: str,
    payload: dict,
    settlement_key: int | None = None,
    region_key: int | None = None,
    created_at: str | None = None,
    place_cache: _EventPlaceKeyCache | None = None,
) -> None:
    event_type_s = str(event_type or "").strip()
    if event_type_s != "knowledge_culture" and not event_type_s.startswith("city_state_"):
        return
    rows = _event_institution_rows_from_payload(payload)
    if not rows:
        return
    _primary, _secondary, payload_settlement_id, payload_region_id = _event_common_columns(
        payload
    )
    ts = created_at or _utc_now_iso()
    for idx, item in enumerate(rows):
        inst_type = str(
            item.get("institution_type") or item.get("kind") or ""
        ).strip()
        if not inst_type:
            continue
        focus_domain = str(
            item.get("focus_domain")
            or item.get("knowledge_domain")
            or payload.get("knowledge_domain")
            or ""
        ).strip()
        inst_key = str(
            item.get("institution_key")
            or f"{payload_region_id or 'unknown'}:{inst_type}:{focus_domain or idx}"
        ).strip()
        if not inst_key:
            continue
        status = str(item.get("status") or "active").strip() or "active"
        founder_id = _coerce_event_person_id(
            item.get("founder_person_id")
            or payload.get("creator_person_id")
            or payload.get("actor_person_id")
            or payload.get("person_id")
        )
        patron_id = _coerce_event_person_id(
            item.get("patron_person_id") or payload.get("patron_person_id")
        )
        founded_year = _coerce_event_int(item.get("founded_year"))
        if founded_year is None:
            founded_year = sim_year
        latest_year = _coerce_event_int(item.get("latest_year"))
        if latest_year is None:
            latest_year = sim_year
        strength_delta = _coerce_event_float(item.get("strength_delta"))
        strength = round(max(0.0, float(strength_delta or 0.0)), 5)
        influence_delta = _coerce_event_float(item.get("influence_delta"))
        influence = round(max(0.0, float(influence_delta or strength * 0.75)), 5)
        region_id = str(item.get("region_id") or payload_region_id or "").strip()
        settlement_id = str(
            item.get("settlement_id") or payload_settlement_id or ""
        ).strip()
        item_region_key = region_key
        if region_id:
            item_region_key = (
                place_cache.region_key(region_id)
                if place_cache is not None
                else _lookup_or_insert_region_key(conn, region_id)
            )
        item_settlement_key = settlement_key
        if settlement_id:
            item_settlement_key = (
                place_cache.settlement_key(settlement_id, region_id)
                if place_cache is not None
                else _lookup_or_insert_settlement_key(conn, settlement_id, region_id)
            )
        details = {
            str(k): v
            for k, v in item.items()
            if str(k)
            not in {
                "institution_key",
                "institution_type",
                "kind",
                "status",
                "region_id",
                "settlement_id",
                "focus_domain",
                "founded_year",
                "latest_year",
                "founder_person_id",
                "patron_person_id",
                "strength_delta",
                "influence_delta",
            }
        }
        conn.execute(
            """
            INSERT INTO simulation_institutions (
                institution_key, institution_type, status, region_key,
                settlement_key, focus_domain, founded_year, latest_year,
                founding_event_id, latest_event_id, founder_person_id,
                patron_person_id, strength, influence_score, details_json,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(institution_key) DO UPDATE SET
                status = excluded.status,
                latest_year = excluded.latest_year,
                latest_event_id = excluded.latest_event_id,
                patron_person_id = COALESCE(
                    excluded.patron_person_id,
                    simulation_institutions.patron_person_id
                ),
                strength = round(simulation_institutions.strength + excluded.strength, 5),
                influence_score = round(
                    simulation_institutions.influence_score + excluded.influence_score,
                    5
                ),
                details_json = excluded.details_json,
                updated_at = excluded.updated_at
            """,
            (
                inst_key,
                inst_type,
                status,
                item_region_key,
                item_settlement_key,
                focus_domain,
                founded_year,
                latest_year,
                int(event_id),
                int(event_id),
                int(founder_id) if founder_id is not None else None,
                int(patron_id) if patron_id is not None else None,
                strength,
                influence,
                json.dumps(details, separators=(",", ":")),
                ts,
                ts,
            ),
        )


def _backfill_simulation_institutions(conn: sqlite3.Connection) -> None:
    processed_event_id = _processed_event_id_for_meta(
        conn, INSTITUTIONS_BACKFILLED_META_KEY
    )
    max_row = conn.execute("SELECT MAX(id) FROM simulation_events").fetchone()
    max_event_id = int(max_row[0] or 0) if max_row is not None else 0
    if max_event_id <= processed_event_id:
        return
    rows = conn.execute(
        """
        SELECT id, sim_year, event_type, settlement_key, region_key,
               payload_json, created_at
        FROM simulation_events
        WHERE id > ?
        ORDER BY id
        """,
        (processed_event_id,),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        _upsert_simulation_institution_rows(
            conn,
            event_id=int(row["id"]),
            sim_year=row["sim_year"],
            event_type=str(row["event_type"] or ""),
            payload=payload,
            settlement_key=(
                int(row["settlement_key"]) if row["settlement_key"] is not None else None
            ),
            region_key=int(row["region_key"]) if row["region_key"] is not None else None,
            created_at=str(row["created_at"] or "") or None,
        )
    _set_processed_event_id_for_meta(conn, INSTITUTIONS_BACKFILLED_META_KEY, max_event_id)


def _ensure_simulation_innovation_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS simulation_innovation_discoveries (
            discovery_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_event_id INTEGER,
            innovation_id TEXT NOT NULL,
            innovation_name TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '',
            domain TEXT NOT NULL DEFAULT '',
            era_id TEXT NOT NULL DEFAULT '',
            discovery_year INTEGER,
            historical_year INTEGER,
            discoverer_person_id INTEGER,
            patron_person_id INTEGER,
            polity_id INTEGER,
            region_key INTEGER,
            settlement_key INTEGER,
            novelty_score REAL NOT NULL DEFAULT 0.0,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(source_event_id, innovation_id)
        );
        CREATE INDEX IF NOT EXISTS idx_simulation_innovation_discoveries_innovation
        ON simulation_innovation_discoveries (innovation_id, discovery_year);
        CREATE INDEX IF NOT EXISTS idx_simulation_innovation_discoveries_place
        ON simulation_innovation_discoveries (polity_id, region_key, settlement_key);

        CREATE TABLE IF NOT EXISTS simulation_innovation_knowledge (
            knowledge_id INTEGER PRIMARY KEY AUTOINCREMENT,
            innovation_id TEXT NOT NULL,
            innovation_name TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '',
            domain TEXT NOT NULL DEFAULT '',
            era_id TEXT NOT NULL DEFAULT '',
            scope_kind TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'known',
            adoption_score REAL NOT NULL DEFAULT 0.0,
            first_known_year INTEGER,
            latest_known_year INTEGER,
            first_event_id INTEGER,
            latest_event_id INTEGER,
            source_kind TEXT NOT NULL DEFAULT 'generated',
            polity_id INTEGER,
            region_key INTEGER,
            settlement_key INTEGER,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(innovation_id, scope_kind, scope_key)
        );
        CREATE INDEX IF NOT EXISTS idx_simulation_innovation_knowledge_scope
        ON simulation_innovation_knowledge (scope_kind, scope_key, status);
        CREATE INDEX IF NOT EXISTS idx_simulation_innovation_knowledge_place
        ON simulation_innovation_knowledge (region_key, settlement_key, polity_id);
        CREATE INDEX IF NOT EXISTS idx_simulation_innovation_knowledge_category
        ON simulation_innovation_knowledge (category, era_id, adoption_score);

        CREATE TABLE IF NOT EXISTS simulation_innovation_era_state (
            state_id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope_kind TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            era_id TEXT NOT NULL,
            era_rank INTEGER NOT NULL DEFAULT 0,
            adopted_count INTEGER NOT NULL DEFAULT 0,
            next_era_adopted_count INTEGER NOT NULL DEFAULT 0,
            latest_year INTEGER,
            polity_id INTEGER,
            region_key INTEGER,
            settlement_key INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(scope_kind, scope_key)
        );
        CREATE INDEX IF NOT EXISTS idx_simulation_innovation_era_scope
        ON simulation_innovation_era_state (scope_kind, scope_key);
        CREATE INDEX IF NOT EXISTS idx_simulation_innovation_era_place
        ON simulation_innovation_era_state (polity_id, region_key, settlement_key);
        """
    )
    _backfill_simulation_innovations(conn)


def _event_innovation_adoption(payload: dict) -> dict[str, object]:
    consequences = payload.get("consequences")
    if not isinstance(consequences, dict):
        consequences = {}
    adoption = consequences.get("innovation_adoption")
    if not isinstance(adoption, dict):
        adoption = {}
    innovation_id = str(
        payload.get("innovation_id") or adoption.get("innovation_id") or ""
    ).strip()
    if not innovation_id:
        return {}
    name = str(
        payload.get("innovation_analogue_name")
        or adoption.get("analogue_name")
        or adoption.get("innovation_name")
        or payload.get("innovation_name")
        or innovation_id
    ).strip()
    category = str(
        payload.get("innovation_category") or adoption.get("category") or ""
    ).strip()
    domain = str(
        payload.get("knowledge_domain") or adoption.get("domain") or ""
    ).strip()
    era_id = str(payload.get("innovation_era_id") or adoption.get("era_id") or "").strip()
    novelty = _coerce_event_float(payload.get("novelty_value"))
    adoption_score = _coerce_event_float(adoption.get("adoption_score"))
    if adoption_score is None:
        knowledge_state = consequences.get("knowledge_state")
        if isinstance(knowledge_state, dict):
            state_delta = _coerce_event_float(knowledge_state.get("state_delta"))
        else:
            state_delta = None
        adoption_score = max(0.08, float(novelty or 0.0) * 2.5, float(state_delta or 0.0) * 4.0)
    return {
        "innovation_id": innovation_id,
        "innovation_name": name or innovation_id,
        "category": category,
        "domain": domain,
        "era_id": era_id,
        "historical_year": _coerce_event_int(
            payload.get("historical_year") or adoption.get("history_year")
        ),
        "discoverer_person_id": _coerce_event_person_id(payload.get("creator_person_id")),
        "patron_person_id": _coerce_event_person_id(payload.get("patron_person_id")),
        "polity_id": _coerce_event_int(payload.get("polity_id") or adoption.get("polity_id")),
        "novelty_score": round(max(0.0, float(novelty or 0.0)), 5),
        "adoption_score": round(max(0.0, min(1.0, float(adoption_score or 0.0))), 5),
        "source_title": str(payload.get("source_innovation_title") or "").strip(),
    }


def _innovation_scope_key(scope_kind: str, key: int) -> str:
    return f"{scope_kind}:{int(key)}"


def _upsert_innovation_knowledge_row(
    conn: sqlite3.Connection,
    *,
    data: dict[str, object],
    scope_kind: str,
    scope_key: str,
    sim_year: int | None,
    event_id: int | None,
    source_kind: str,
    polity_id: int | None = None,
    region_key: int | None = None,
    settlement_key: int | None = None,
    created_at: str | None = None,
) -> None:
    ts = created_at or _utc_now_iso()
    details = {
        "source_title": data.get("source_title"),
        "historical_year": data.get("historical_year"),
    }
    conn.execute(
        """
        INSERT INTO simulation_innovation_knowledge (
            innovation_id, innovation_name, category, domain, era_id,
            scope_kind, scope_key, status, adoption_score,
            first_known_year, latest_known_year, first_event_id, latest_event_id,
            source_kind, polity_id, region_key, settlement_key, details_json,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(innovation_id, scope_kind, scope_key) DO UPDATE SET
            innovation_name = excluded.innovation_name,
            category = excluded.category,
            domain = excluded.domain,
            era_id = excluded.era_id,
            adoption_score = max(
                simulation_innovation_knowledge.adoption_score,
                excluded.adoption_score
            ),
            latest_known_year = excluded.latest_known_year,
            latest_event_id = COALESCE(
                excluded.latest_event_id,
                simulation_innovation_knowledge.latest_event_id
            ),
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
            str(data["innovation_id"]),
            str(data.get("innovation_name") or ""),
            str(data.get("category") or ""),
            str(data.get("domain") or ""),
            str(data.get("era_id") or ""),
            scope_kind,
            scope_key,
            "adopted" if float(data.get("adoption_score") or 0.0) >= 0.35 else "known",
            float(data.get("adoption_score") or 0.0),
            sim_year,
            sim_year,
            int(event_id) if event_id is not None else None,
            int(event_id) if event_id is not None else None,
            source_kind,
            int(polity_id) if polity_id is not None else None,
            int(region_key) if region_key is not None else None,
            int(settlement_key) if settlement_key is not None else None,
            json.dumps(details, separators=(",", ":")),
            ts,
            ts,
        ),
    )


def _upsert_simulation_innovation_rows_from_event(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    sim_year: int | None,
    event_type: str,
    payload: dict,
    settlement_key: int | None = None,
    region_key: int | None = None,
    created_at: str | None = None,
    place_cache: _EventPlaceKeyCache | None = None,
) -> None:
    if str(event_type or "").strip() != "knowledge_culture":
        return
    data = _event_innovation_adoption(payload)
    if not data:
        return
    _primary, _secondary, settlement_id, region_id = _event_common_columns(payload)
    item_settlement_key = settlement_key
    item_region_key = region_key
    if item_settlement_key is None and settlement_id:
        item_settlement_key = (
            place_cache.settlement_key(settlement_id, region_id)
            if place_cache is not None
            else _lookup_or_insert_settlement_key(conn, settlement_id, region_id)
        )
    if item_region_key is None and region_id:
        item_region_key = (
            place_cache.region_key(region_id)
            if place_cache is not None
            else _lookup_or_insert_region_key(conn, region_id)
        )
    ts = created_at or _utc_now_iso()
    polity_id = data.get("polity_id")
    details = {
        "source_title": data.get("source_title"),
        "historical_year": data.get("historical_year"),
    }
    conn.execute(
        """
        INSERT OR IGNORE INTO simulation_innovation_discoveries (
            source_event_id, innovation_id, innovation_name, category, domain, era_id,
            discovery_year, historical_year, discoverer_person_id, patron_person_id,
            polity_id, region_key, settlement_key, novelty_score, details_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(event_id),
            str(data["innovation_id"]),
            str(data.get("innovation_name") or ""),
            str(data.get("category") or ""),
            str(data.get("domain") or ""),
            str(data.get("era_id") or ""),
            sim_year,
            data.get("historical_year"),
            data.get("discoverer_person_id"),
            data.get("patron_person_id"),
            int(polity_id) if polity_id is not None else None,
            int(item_region_key) if item_region_key is not None else None,
            int(item_settlement_key) if item_settlement_key is not None else None,
            float(data.get("novelty_score") or 0.0),
            json.dumps(details, separators=(",", ":")),
            ts,
        ),
    )
    if item_settlement_key is not None:
        _upsert_innovation_knowledge_row(
            conn,
            data=data,
            scope_kind="settlement",
            scope_key=_innovation_scope_key("settlement", item_settlement_key),
            sim_year=sim_year,
            event_id=event_id,
            source_kind="discovery",
            polity_id=int(polity_id) if polity_id is not None else None,
            region_key=item_region_key,
            settlement_key=item_settlement_key,
            created_at=ts,
        )
    if item_region_key is not None:
        _upsert_innovation_knowledge_row(
            conn,
            data=data,
            scope_kind="region",
            scope_key=_innovation_scope_key("region", item_region_key),
            sim_year=sim_year,
            event_id=event_id,
            source_kind="discovery",
            polity_id=int(polity_id) if polity_id is not None else None,
            region_key=item_region_key,
            settlement_key=item_settlement_key,
            created_at=ts,
        )
    if polity_id is not None:
        _upsert_innovation_knowledge_row(
            conn,
            data=data,
            scope_kind="polity",
            scope_key=_innovation_scope_key("polity", int(polity_id)),
            sim_year=sim_year,
            event_id=event_id,
            source_kind="discovery",
            polity_id=int(polity_id),
            region_key=item_region_key,
            settlement_key=item_settlement_key,
            created_at=ts,
        )


def _backfill_simulation_innovations(conn: sqlite3.Connection) -> None:
    processed_event_id = _processed_event_id_for_meta(
        conn, INNOVATIONS_BACKFILLED_META_KEY
    )
    max_row = conn.execute("SELECT MAX(id) FROM simulation_events").fetchone()
    max_event_id = int(max_row[0] or 0) if max_row is not None else 0
    if max_event_id <= processed_event_id:
        return
    rows = conn.execute(
        """
        SELECT id, sim_year, event_type, settlement_key, region_key,
               payload_json, created_at
        FROM simulation_events
        WHERE id > ?
        ORDER BY id
        """,
        (processed_event_id,),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        _upsert_simulation_innovation_rows_from_event(
            conn,
            event_id=int(row["id"]),
            sim_year=row["sim_year"],
            event_type=str(row["event_type"] or ""),
            payload=payload,
            settlement_key=(
                int(row["settlement_key"]) if row["settlement_key"] is not None else None
            ),
            region_key=int(row["region_key"]) if row["region_key"] is not None else None,
            created_at=str(row["created_at"] or "") or None,
        )
    _set_processed_event_id_for_meta(conn, INNOVATIONS_BACKFILLED_META_KEY, max_event_id)


def _ensure_simulation_legal_adjudication_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS simulation_legal_adjudications (
            adjudication_id INTEGER PRIMARY KEY AUTOINCREMENT,
            fallout_id INTEGER NOT NULL,
            source_event_id INTEGER NOT NULL,
            adjudication_key TEXT NOT NULL,
            adjudication_type TEXT NOT NULL,
            outcome TEXT NOT NULL,
            principal_result TEXT,
            opposing_result TEXT,
            adjudication_year INTEGER NOT NULL,
            principal_person_id INTEGER,
            opposing_person_id INTEGER,
            related_person_id INTEGER,
            region_key INTEGER,
            settlement_key INTEGER,
            severity REAL NOT NULL DEFAULT 0.0,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (fallout_id, adjudication_key)
        );
        CREATE INDEX IF NOT EXISTS idx_simulation_legal_adjudications_fallout
        ON simulation_legal_adjudications (fallout_id);
        CREATE INDEX IF NOT EXISTS idx_simulation_legal_adjudications_type
        ON simulation_legal_adjudications (adjudication_type, outcome);
        CREATE INDEX IF NOT EXISTS idx_simulation_legal_adjudications_place
        ON simulation_legal_adjudications (region_key, settlement_key, adjudication_year);
        """
    )


def _legal_adjudication_outcome(
    fallout_type: str, severity: float
) -> tuple[str, str, str]:
    ftype = str(fallout_type or "").strip()
    sev = float(severity or 0.0)
    if ftype == "heir_legitimacy_challenge":
        if sev >= 0.68:
            return "claim_limited", "legitimacy_clouded", "challenge_partly_upheld"
        return "claim_dismissed", "legitimacy_reaffirmed", "challenge_rejected"
    if ftype == "inheritance_dispute":
        if sev >= 0.58:
            return "inheritance_split", "share_reduced", "share_recognized"
        return "inheritance_confirmed", "claim_confirmed", "challenge_rejected"
    if sev >= 0.65:
        return "settlement_compromise", "claim_limited", "opposition_partly_upheld"
    return "case_dismissed", "claim_reaffirmed", "opposition_rejected"


def resolve_due_legal_fallout(
    conn: sqlite3.Connection, *, year: int, limit: int = 200
) -> int:
    """Resolve active legal fallout whose expected year has arrived."""
    _ensure_simulation_legal_adjudication_tables(conn)
    y = int(year)
    rows = conn.execute(
        """
        SELECT *
        FROM simulation_legal_fallout
        WHERE status = 'active'
          AND expected_resolution_year IS NOT NULL
          AND expected_resolution_year <= ?
        ORDER BY expected_resolution_year, fallout_id
        LIMIT ?
        """,
        (y, max(1, int(limit))),
    ).fetchall()
    if not rows:
        return 0
    ts = _utc_now_iso()
    resolved = 0
    for row in rows:
        fallout_id = int(row["fallout_id"])
        fallout_type = str(row["fallout_type"] or "")
        severity = float(row["severity"] or 0.0)
        outcome, principal_result, opposing_result = _legal_adjudication_outcome(
            fallout_type, severity
        )
        adjudication_key = f"fallout:{fallout_id}:resolution"
        details = {
            "fallout_key": str(row["fallout_key"] or ""),
            "expected_resolution_year": (
                int(row["expected_resolution_year"])
                if row["expected_resolution_year"] is not None
                else None
            ),
        }
        conn.execute(
            """
            INSERT OR IGNORE INTO simulation_legal_adjudications (
                fallout_id, source_event_id, adjudication_key, adjudication_type,
                outcome, principal_result, opposing_result, adjudication_year,
                principal_person_id, opposing_person_id, related_person_id,
                region_key, settlement_key, severity, details_json,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fallout_id,
                int(row["source_event_id"]),
                adjudication_key,
                f"{fallout_type}_resolution",
                outcome,
                principal_result,
                opposing_result,
                y,
                row["principal_person_id"],
                row["opposing_person_id"],
                row["related_person_id"],
                row["region_key"],
                row["settlement_key"],
                severity,
                json.dumps(details, separators=(",", ":")),
                ts,
                ts,
            ),
        )
        if conn.execute("SELECT changes()").fetchone()[0]:
            resolved += 1
        conn.execute(
            """
            UPDATE simulation_legal_fallout
            SET status = 'resolved',
                resolved_year = ?,
                updated_at = ?
            WHERE fallout_id = ?
            """,
            (y, ts, fallout_id),
        )
    return resolved


def _ensure_simulation_domain_diffusion_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS simulation_domain_diffusion (
            diffusion_id INTEGER PRIMARY KEY AUTOINCREMENT,
            diffusion_year INTEGER NOT NULL,
            domain TEXT NOT NULL,
            source_region_key INTEGER NOT NULL,
            target_region_key INTEGER NOT NULL,
            route_type TEXT,
            route_friction REAL NOT NULL DEFAULT 0.0,
            source_domain_score REAL NOT NULL DEFAULT 0.0,
            target_domain_score_before REAL NOT NULL DEFAULT 0.0,
            target_domain_score_after REAL NOT NULL DEFAULT 0.0,
            state_delta REAL NOT NULL DEFAULT 0.0,
            source_latest_event_id INTEGER,
            created_at TEXT NOT NULL,
            UNIQUE (diffusion_year, domain, source_region_key, target_region_key)
        );
        CREATE INDEX IF NOT EXISTS idx_simulation_domain_diffusion_domain
        ON simulation_domain_diffusion (domain, diffusion_year);
        CREATE INDEX IF NOT EXISTS idx_simulation_domain_diffusion_target
        ON simulation_domain_diffusion (target_region_key, domain, diffusion_year);
        """
    )


def diffuse_domain_states_across_routes(
    conn: sqlite3.Connection,
    *,
    year: int,
    world: str,
    config_db_path: Path | str,
    max_sources: int = 60,
    max_destinations_per_source: int = 2,
) -> int:
    """Spread accumulated domain state to nearby route-connected regions."""
    _ensure_simulation_domain_diffusion_tables(conn)
    y = int(year)
    sources = conn.execute(
        """
        SELECT d.region_key, rl.region_id, d.domain, d.domain_score,
               d.latest_event_id
        FROM simulation_domain_states d
        JOIN simulation_region_lookup rl ON rl.region_key = d.region_key
        WHERE d.domain_score >= 0.05
        ORDER BY d.domain_score DESC, rl.region_id, d.domain
        LIMIT ?
        """,
        (max(1, int(max_sources)),),
    ).fetchall()
    ts = _utc_now_iso()
    inserted = 0
    for source in sources:
        source_region_id = str(source["region_id"] or "").strip()
        domain = str(source["domain"] or "").strip()
        if not source_region_id or not domain:
            continue
        try:
            routes = list_routes_from(
                source_region_id,
                world=world,
                db_path=config_db_path,
                simulation_year=y,
            )
        except LookupError:
            continue
        routes.sort(key=lambda route: (float(route.friction), route.to_region_id))
        used = 0
        source_score = float(source["domain_score"] or 0.0)
        for route in routes:
            target_region_id = str(route.to_region_id or "").strip()
            if not target_region_id or target_region_id == source_region_id:
                continue
            target_region_key = _lookup_or_insert_region_key(conn, target_region_id)
            if target_region_key is None:
                continue
            target_row = conn.execute(
                """
                SELECT domain_score
                FROM simulation_domain_states
                WHERE region_key = ? AND domain = ?
                """,
                (target_region_key, domain),
            ).fetchone()
            target_before = (
                float(target_row["domain_score"] or 0.0)
                if target_row is not None
                else 0.0
            )
            score_gap = source_score - target_before
            if score_gap <= 0.01:
                continue
            friction = max(0.0, float(route.friction))
            delta = round(
                min(0.025, max(0.001, score_gap * (0.035 / (1.0 + friction * 0.08)))),
                5,
            )
            target_after = round(target_before + delta, 5)
            conn.execute(
                """
                INSERT OR IGNORE INTO simulation_domain_diffusion (
                    diffusion_year, domain, source_region_key, target_region_key,
                    route_type, route_friction, source_domain_score,
                    target_domain_score_before, target_domain_score_after,
                    state_delta, source_latest_event_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    y,
                    domain,
                    int(source["region_key"]),
                    target_region_key,
                    route.route_type,
                    round(friction, 5),
                    round(source_score, 5),
                    round(target_before, 5),
                    target_after,
                    delta,
                    source["latest_event_id"],
                    ts,
                ),
            )
            if not conn.execute("SELECT changes()").fetchone()[0]:
                continue
            conn.execute(
                """
                INSERT INTO simulation_domain_states (
                    region_key, domain, domain_score, breakthrough_count,
                    first_event_year, latest_event_year, first_event_id,
                    latest_event_id, latest_incident_kind,
                    latest_creator_person_id, latest_settlement_key,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, 0, ?, ?, NULL, ?, 'inter_region_diffusion',
                        NULL, NULL, ?, ?)
                ON CONFLICT(region_key, domain) DO UPDATE SET
                    domain_score = round(
                        simulation_domain_states.domain_score + excluded.domain_score,
                        5
                    ),
                    latest_event_year = excluded.latest_event_year,
                    latest_event_id = COALESCE(
                        excluded.latest_event_id,
                        simulation_domain_states.latest_event_id
                    ),
                    latest_incident_kind = excluded.latest_incident_kind,
                    updated_at = excluded.updated_at
                """,
                (
                    target_region_key,
                    domain,
                    delta,
                    y,
                    y,
                    source["latest_event_id"],
                    ts,
                    ts,
                ),
            )
            inserted += 1
            used += 1
            if used >= max(1, int(max_destinations_per_source)):
                break
    return inserted


def event_consequence_annual_tick_for_save(
    save_db_path: Path | str,
    *,
    config_db_path: Path | str,
    year: int,
    world: str,
) -> dict[str, int]:
    """Run bounded annual maintenance for durable event consequences."""
    with _open_save(save_db_path) as conn:
        ensure_checkpoint_schema(conn)
        legal_resolved = resolve_due_legal_fallout(conn, year=int(year))
        domain_diffusions = diffuse_domain_states_across_routes(
            conn,
            year=int(year),
            world=str(world).strip() or "default",
            config_db_path=config_db_path,
        )
        conn.commit()
    return {
        "legal_adjudications": int(legal_resolved),
        "domain_diffusions": int(domain_diffusions),
    }


_PUBLIC_RECORD_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "founder_created",
        "death",
        "settlement_move_planned",
        "settlement_move_dropped",
        "settlement_moved",
        "polity_promoted",
        "polity_split_vassal",
        "polity_named",
        "polity_dissolved",
        "office_selection",
        "office_succession",
        "campaign_started",
        "campaign_ended",
        "battle_fought",
        "dynastic_marriage_alliance",
    }
)

_PRIVATE_RECORD_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "birth",
        "couple_formed",
        "couple_dissolved",
        "same_sex_couple_formed",
        "job_assigned",
        "job_lost",
        "unemployment_started",
        "unemployment_ended",
        "job_seeker_migration",
        "household_service_started",
        "household_childcare_shortfall",
        "household_prosperity_crisis",
        "partner_residence_reconciled",
        "vagrancy",
        "begging",
        "street_vice_scandal",
    }
)

_SECRET_RECORD_EVENT_TYPES: frozenset[str] = frozenset(
    {"paramour_formed", "paramour_ended"}
)

_ADMIN_RECORD_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "career_fitness_updated",
        "settlement_job_market_effect",
        "place_debug_probe",
    }
)

_EVENT_RECORD_VISIBILITY_STATES: frozenset[str] = frozenset(
    {
        "admin_known",
        "public_unknown",
        "public_known",
        "private_known",
        "rumored",
        "sealed",
        "lost",
        "rediscovered",
        "misattributed",
    }
)


def _event_record_kind_for_type(
    event_type: str, event_origin: str
) -> tuple[str, str, float]:
    """Return ``(record_type, visibility_state, confidence)`` for a default memory row."""
    et = str(event_type or "").strip()
    origin = str(event_origin or "").strip().lower()
    if (
        origin in {"inferred", "backfilled"}
        or et.startswith("promotion_backfill_")
        or et in _ADMIN_RECORD_EVENT_TYPES
    ):
        return "admin_note", "admin_known", 0.75 if origin != "generated" else 1.0
    if et == "event_rediscovered":
        return "rediscovery_record", "public_known", 1.0
    if et in {"murder", "feud_killing"}:
        return "violent_crime_record", "rumored", 0.55
    if et in {"property_crime", "theft", "fraud", "extortion"}:
        return "property_crime_record", "rumored", 0.5
    if et.startswith("outlaw_"):
        visibility = "public_known" if et in {"outlaw_captured", "outlaw_killed"} else "rumored"
        return "outlaw_record", visibility, 0.6
    if et in {"affair_scandal", "affair_exposed", "disputed_parentage"}:
        return "scandal_record", "rumored", 0.55
    if et in {"status_rise", "elite_job_promoted", "guild_admission"}:
        return "public_status_record", "public_known", 0.8
    if et == "patronage_granted":
        return "household_memory", "rumored", 0.65
    if et == "elite_household_investment":
        return "settlement_chronicle", "public_known", 0.8
    if et == "political_crime":
        return "court_chronicle", "rumored", 0.55
    if et == "religious_cultural_conflict":
        return "temple_chronicle", "rumored", 0.55
    if et == "private_life":
        return "household_memory", "private_known", 0.7
    if et in {"public_virtue", "heroic_rescue", "public_mercy"}:
        return "public_virtue_record", "public_known", 0.85
    if et in {"knowledge_culture", "invention", "discovery", "legal_precedent"}:
        return "knowledge_record", "public_known", 0.8
    if et.startswith("city_state_"):
        return "city_chronicle", "public_known", 0.9
    if et in _SECRET_RECORD_EVENT_TYPES:
        return "household_secret", "private_known", 1.0
    if et == "birth":
        return "lineage_memory", "private_known", 1.0
    if et == "death":
        return "mortuary_memory", "public_known", 1.0
    if et.startswith("office_") or et.startswith("polity_"):
        return "court_chronicle", "public_known", 1.0
    if (
        et.startswith("campaign_")
        or et.startswith("battle_")
        or et.startswith("dynastic_")
    ):
        return "war_chronicle", "public_known", 1.0
    if et.startswith("settlement_"):
        return "settlement_chronicle", "public_known", 1.0
    if et.startswith("job_") or et.startswith("unemployment_"):
        return "work_record", "private_known", 1.0
    if et.startswith("household_") or et in _PRIVATE_RECORD_EVENT_TYPES:
        return "household_memory", "private_known", 1.0
    if et in _PUBLIC_RECORD_EVENT_TYPES:
        return "public_chronicle", "public_known", 1.0
    return "event_memory", "private_known", 1.0


def _event_record_public_people(
    event_type: str,
    primary_person_id: int | None,
    secondary_person_id: int | None,
    payload: dict | None = None,
) -> tuple[int | None, int | None]:
    et = str(event_type or "").strip()
    if et == "death":
        return None, primary_person_id
    if et == "murder":
        p = payload if isinstance(payload, dict) else {}
        identified = bool(p.get("offender_identified")) or (
            _safe_float(p.get("offender_identity_confidence")) >= 0.60
        )
        return (primary_person_id if identified else None), secondary_person_id
    if et in {"household_childcare_shortfall", "household_prosperity_crisis"}:
        return None, primary_person_id
    return primary_person_id, secondary_person_id


_DEFAULT_PUBLIC_STAGE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "death",
        "settlement_move_planned",
        "settlement_move_dropped",
        "settlement_moved",
        "office_selection",
        "office_succession",
        "polity_promoted",
        "polity_split_vassal",
        "polity_named",
        "polity_dissolved",
        "campaign_started",
        "campaign_ended",
        "battle_fought",
        "dynastic_marriage_alliance",
        "murder",
    }
)


def _clean_stage_payload(payload: dict | None) -> dict:
    return dict(payload) if isinstance(payload, dict) else {}


def _json_loads_dict(raw: object) -> dict[str, object]:
    if isinstance(raw, dict):
        return dict(raw)
    if raw in (None, ""):
        return {}
    try:
        parsed = json.loads(str(raw))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(number):
        return float(default)
    return number


def _safe_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _stage_label(value: object) -> str:
    return str(value or "").strip().replace("_", " ")


def _default_public_stage_record_specs(
    event_type: str,
    event_origin: str,
    payload: dict | None,
) -> list[dict[str, object]]:
    et = str(event_type or "").strip()
    origin = str(event_origin or "").strip().lower()
    if origin in {"inferred", "backfilled"} or (
        et not in _DEFAULT_PUBLIC_STAGE_EVENT_TYPES
        and not et.startswith("city_state_")
    ):
        return []
    p = _clean_stage_payload(payload)
    specs: list[dict[str, object]] = []

    def add(
        *,
        record_key: str,
        public_stage: str,
        record_type: str,
        confidence: float,
        distortion: dict[str, object],
        public_actor_person_id: int | None = None,
        public_victim_person_id: int | None = None,
    ) -> None:
        specs.append(
            {
                "record_key": record_key,
                "public_stage": public_stage,
                "record_type": record_type,
                "confidence": confidence,
                "distortion": distortion,
                "public_actor_person_id": public_actor_person_id,
                "public_victim_person_id": public_victim_person_id,
            }
        )

    if et == "death":
        add(
            record_key="public_cause_unknown",
            public_stage="unknown",
            record_type="mortuary_uncertainty",
            confidence=0.35,
            distortion={"uncertain_fields": ["cause"], "public_cause": "unknown"},
        )
    elif et == "murder":
        identified = bool(p.get("offender_identified")) or (
            _safe_float(p.get("offender_identity_confidence")) >= 0.60
        )
        actor_id = _safe_int(p.get("killer_person_id")) if identified else None
        victim_id = _safe_int(p.get("victim_person_id"))
        suspicion = _safe_float(p.get("public_suspicion_score"))
        evidence = _safe_float(p.get("evidence_strength"))
        pattern = bool(p.get("pattern_recognized"))
        confidence = max(0.25, min(0.75, suspicion or evidence or 0.35))
        add(
            record_key="public_suspicious_death",
            public_stage="unknown",
            record_type="violent_crime_uncertainty",
            confidence=confidence,
            public_actor_person_id=actor_id,
            public_victim_person_id=victim_id,
            distortion={
                "uncertain_fields": ["offender", "motive"],
                "public_cause": "suspicious death",
            },
        )
        if suspicion >= 0.35 or evidence >= 0.35:
            add(
                record_key="public_murder_rumor",
                public_stage="rumored",
                record_type="violent_crime_rumor",
                confidence=max(0.35, min(0.72, suspicion)),
                public_actor_person_id=actor_id,
                public_victim_person_id=victim_id,
                distortion={
                    "rumored_cause": "suspected murder",
                    "uncertain_fields": ["offender"],
                },
            )
        if pattern:
            add(
                record_key="public_pattern_rumor",
                public_stage="rumored",
                record_type="violent_pattern_rumor",
                confidence=max(0.45, min(0.80, suspicion)),
                public_actor_person_id=actor_id,
                public_victim_person_id=victim_id,
                distortion={
                    "rumored_cause": "linked to earlier deaths",
                    "pattern_recognized": True,
                    "uncertain_fields": ["offender", "pattern"],
                },
            )
        if identified:
            add(
                record_key="public_identified_killing",
                public_stage="known",
                record_type="public_chronicle",
                confidence=max(0.60, min(1.0, _safe_float(p.get("offender_identity_confidence")))),
                public_actor_person_id=actor_id,
                public_victim_person_id=victim_id,
                distortion={},
            )
    elif et in {"settlement_move_planned", "settlement_move_dropped", "settlement_moved"}:
        add(
            record_key="public_move_unclear",
            public_stage="unknown",
            record_type="settlement_movement_notice",
            confidence=0.45,
            distortion={
                "uncertain_fields": ["route", "cause"],
                "public_cause": "unknown",
            },
        )
        reason = _stage_label(p.get("move_reason") or p.get("source_event"))
        if reason and reason != "unknown":
            add(
                record_key="public_move_rumor",
                public_stage="rumored",
                record_type="settlement_movement_rumor",
                confidence=0.5,
                distortion={"rumored_cause": reason},
            )
    elif et in {"office_selection", "office_succession"}:
        add(
            record_key="public_succession_unclear",
            public_stage="unknown",
            record_type="court_uncertainty",
            confidence=0.5,
            distortion={
                "uncertain_fields": ["claim", "selection_rule"],
                "public_cause": "unknown",
            },
        )
        via = _stage_label(p.get("via") or p.get("selection_rule"))
        if via and via != "unknown":
            add(
                record_key="public_succession_rumor",
                public_stage="rumored",
                record_type="court_rumor",
                confidence=0.55,
                distortion={"rumored_cause": via},
            )
    elif et.startswith("polity_") or et == "dynastic_marriage_alliance":
        add(
            record_key="public_polity_change_unclear",
            public_stage="unknown",
            record_type="court_uncertainty",
            confidence=0.55,
            distortion={
                "uncertain_fields": ["cause", "terms"],
                "public_cause": "unknown",
            },
        )
        reason = _stage_label(
            p.get("reason")
            or p.get("from_polity_type_id")
            or p.get("to_polity_type_id")
            or p.get("name")
        )
        if reason and reason != "unknown":
            add(
                record_key="public_polity_change_rumor",
                public_stage="rumored",
                record_type="court_rumor",
                confidence=0.55,
                distortion={"rumored_cause": reason},
            )
    elif et.startswith("city_state_"):
        add(
            record_key="public_city_state_terms_unclear",
            public_stage="unknown",
            record_type="city_chronicle_uncertainty",
            confidence=0.55,
            distortion={
                "uncertain_fields": ["cause", "terms"],
                "public_cause": "unknown",
            },
        )
        reason = _stage_label(
            p.get("reason")
            or p.get("city_state_pattern")
            or p.get("civic_project")
            or p.get("crisis_reason")
            or p.get("dispute_kind")
            or p.get("autonomy_state")
            or p.get("league_status")
        )
        if reason and reason != "unknown":
            add(
                record_key="public_city_state_rumor",
                public_stage="rumored",
                record_type="city_chronicle_rumor",
                confidence=0.58,
                distortion={"rumored_cause": reason},
            )
    elif et.startswith("campaign_") or et == "battle_fought":
        uncertain = "outcome" if et == "campaign_ended" else "cause"
        if et == "battle_fought":
            uncertain = "casualties"
        add(
            record_key=f"public_war_{uncertain}_unclear",
            public_stage="unknown",
            record_type="war_uncertainty",
            confidence=0.5,
            distortion={
                "uncertain_fields": [uncertain],
                "public_cause": "unknown",
            },
        )
        rumor_value = _stage_label(
            p.get("outcome")
            if et == "campaign_ended"
            else p.get("kind") or p.get("battle_outcome")
        )
        if rumor_value and rumor_value != "unknown":
            add(
                record_key="public_war_rumor",
                public_stage="rumored",
                record_type="war_rumor",
                confidence=0.55,
                distortion={
                    "rumored_outcome" if et == "campaign_ended" else "rumored_cause": rumor_value
                },
            )
    return specs


def _insert_default_public_stage_record_rows(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    event_type: str,
    event_origin: str,
    payload: dict | None,
) -> None:
    for spec in _default_public_stage_record_specs(event_type, event_origin, payload):
        upsert_public_event_record(
            conn,
            int(event_id),
            public_stage=str(spec["public_stage"]),
            record_key=str(spec["record_key"]),
            record_type=str(spec["record_type"]),
            confidence=float(spec["confidence"]),
            distortion=dict(spec["distortion"]),
            public_actor_person_id=(
                int(spec["public_actor_person_id"])
                if spec.get("public_actor_person_id") is not None
                else None
            ),
            public_victim_person_id=(
                int(spec["public_victim_person_id"])
                if spec.get("public_victim_person_id") is not None
                else None
            ),
        )


def _insert_default_event_record_row(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    event_type: str,
    sim_year: int | None,
    primary_person_id: int | None,
    secondary_person_id: int | None,
    settlement_key: int | None,
    region_key: int | None,
    event_origin: str,
    created_at: str,
    payload: dict | None = None,
) -> None:
    record_type, visibility_state, confidence = _event_record_kind_for_type(
        event_type, event_origin
    )
    public_actor, public_victim = _event_record_public_people(
        event_type, primary_person_id, secondary_person_id, payload
    )
    prose_variant_key = f"{record_type}.{visibility_state}.default"
    conn.execute(
        """
        INSERT OR IGNORE INTO simulation_event_records (
            event_id, record_key, record_type, visibility_state,
            known_since_year, confidence, preserving_settlement_key,
            preserving_region_key, public_actor_person_id,
            public_victim_person_id, prose_variant_key, created_at, updated_at
        )
        VALUES (?, 'default', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(event_id),
            record_type,
            visibility_state,
            sim_year,
            float(confidence),
            settlement_key,
            region_key,
            public_actor,
            public_victim,
            prose_variant_key,
            created_at,
            created_at,
        ),
    )
    _insert_default_public_stage_record_rows(
        conn,
        event_id=int(event_id),
        event_type=event_type,
        event_origin=event_origin,
        payload=payload,
    )


def _event_record_row(
    conn: sqlite3.Connection, event_id: int, record_key: str
) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT *
        FROM simulation_event_records
        WHERE event_id = ? AND record_key = ?
        """,
        (int(event_id), str(record_key or "default").strip() or "default"),
    ).fetchone()
    if row is None:
        raise ValueError(
            f"event record not found for event_id={int(event_id)} "
            f"record_key={str(record_key or 'default').strip() or 'default'}"
        )
    return row


def _event_record_distortion_json(distortion: dict | None) -> str | None:
    if distortion is None:
        return None
    if not isinstance(distortion, dict):
        raise TypeError("distortion must be a dict or None")
    return json.dumps(distortion, sort_keys=True, separators=(",", ":"))


def _public_event_record_stage_defaults(
    public_stage: str,
) -> tuple[str, str, str]:
    stage = str(public_stage or "").strip().lower()
    stage_map = {
        "unknown": ("public_unknown", "public_unknown_notice"),
        "public_unknown": ("public_unknown", "public_unknown_notice"),
        "rumor": ("rumored", "public_rumor"),
        "rumored": ("rumored", "public_rumor"),
        "misattributed": ("misattributed", "public_misattribution"),
        "false_attribution": ("misattributed", "public_misattribution"),
        "known": ("public_known", "public_chronicle"),
        "public_known": ("public_known", "public_chronicle"),
    }
    if stage not in stage_map:
        raise ValueError(f"unknown public event-record stage: {public_stage!r}")
    visibility, default_record_type = stage_map[stage]
    key_stage = {
        "public_unknown": "unknown",
        "rumored": "rumor",
        "misattributed": "misattributed",
        "public_known": "known",
    }[visibility]
    return visibility, default_record_type, key_stage


def _validate_event_record_visibility_state(visibility_state: str) -> str:
    state = str(visibility_state or "").strip().lower()
    if state not in _EVENT_RECORD_VISIBILITY_STATES:
        raise ValueError(f"unknown event-record visibility state: {visibility_state!r}")
    return state


def _event_record_state_update(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    record_key: str = "default",
    visibility_state: str,
    sim_year: int | None = None,
    confidence: float | None = None,
    source_person_id: int | None = None,
    source_institution_id: str | None = None,
    preserving_settlement_id: str | None = None,
    preserving_region_id: str | None = None,
    public_actor_person_id: int | None = None,
    public_victim_person_id: int | None = None,
    distortion: dict | None = None,
    set_lost_year: bool = False,
    set_rediscovered_year: bool = False,
) -> None:
    state = _validate_event_record_visibility_state(visibility_state)
    key = str(record_key or "default").strip() or "default"
    row = _event_record_row(conn, int(event_id), key)
    now = _utc_now_iso()
    updates: dict[str, object] = {
        "visibility_state": state,
        "updated_at": now,
        "prose_variant_key": f"{row['record_type']}.{state}.default",
    }
    if sim_year is not None:
        if row["known_since_year"] is None:
            updates["known_since_year"] = int(sim_year)
        if set_lost_year:
            updates["lost_year"] = int(sim_year)
        if set_rediscovered_year:
            updates["rediscovered_year"] = int(sim_year)
    if confidence is not None:
        updates["confidence"] = max(0.0, min(1.0, float(confidence)))
    if source_person_id is not None:
        updates["source_person_id"] = int(source_person_id)
    if source_institution_id is not None:
        text = str(source_institution_id).strip()
        updates["source_institution_id"] = text or None
    if preserving_settlement_id is not None or preserving_region_id is not None:
        region_hint = str(preserving_region_id or "").strip()
        settlement_hint = str(preserving_settlement_id or "").strip()
        if not region_hint and ":" in settlement_hint:
            region_hint = settlement_hint.split(":", 1)[0].strip()
        updates["preserving_region_key"] = _lookup_or_insert_region_key(
            conn, region_hint
        )
        updates["preserving_settlement_key"] = _lookup_or_insert_settlement_key(
            conn, settlement_hint, region_hint
        )
    if public_actor_person_id is not None:
        updates["public_actor_person_id"] = int(public_actor_person_id)
    if public_victim_person_id is not None:
        updates["public_victim_person_id"] = int(public_victim_person_id)
    if distortion is not None:
        updates["distortion_json"] = _event_record_distortion_json(distortion)
    assignments = ", ".join(f"{_quote_identifier(k)} = ?" for k in updates)
    conn.execute(
        f"""
        UPDATE simulation_event_records
        SET {assignments}
        WHERE event_id = ? AND record_key = ?
        """,
        (*updates.values(), int(event_id), key),
    )


def mark_event_record_lost(
    conn: sqlite3.Connection,
    event_id: int,
    *,
    lost_year: int,
    record_key: str = "default",
) -> None:
    """Mark an in-world event record as no longer actively remembered."""
    _event_record_state_update(
        conn,
        event_id=int(event_id),
        record_key=record_key,
        visibility_state="lost",
        sim_year=int(lost_year),
        set_lost_year=True,
    )


def seal_event_record(
    conn: sqlite3.Connection,
    event_id: int,
    *,
    sealed_year: int | None = None,
    record_key: str = "default",
    source_institution_id: str | None = None,
) -> None:
    """Hide a record in-world without losing its preserving institution/archive."""
    _event_record_state_update(
        conn,
        event_id=int(event_id),
        record_key=record_key,
        visibility_state="sealed",
        sim_year=sealed_year,
        source_institution_id=source_institution_id,
    )


def mark_event_record_rumored(
    conn: sqlite3.Connection,
    event_id: int,
    *,
    rumor_year: int | None = None,
    record_key: str = "default",
    confidence: float = 0.45,
    source_person_id: int | None = None,
    distortion: dict | None = None,
) -> None:
    """Turn a preserved/private record into an uncertain public or local rumor."""
    _event_record_state_update(
        conn,
        event_id=int(event_id),
        record_key=record_key,
        visibility_state="rumored",
        sim_year=rumor_year,
        confidence=confidence,
        source_person_id=source_person_id,
        distortion=distortion,
    )


def mark_event_record_public_unknown(
    conn: sqlite3.Connection,
    event_id: int,
    *,
    known_year: int | None = None,
    record_key: str = "default",
    confidence: float = 0.2,
    source_person_id: int | None = None,
    public_actor_person_id: int | None = None,
    public_victim_person_id: int | None = None,
    distortion: dict | None = None,
) -> None:
    """Expose a public notice that something happened but key facts are unknown."""
    _event_record_state_update(
        conn,
        event_id=int(event_id),
        record_key=record_key,
        visibility_state="public_unknown",
        sim_year=known_year,
        confidence=confidence,
        source_person_id=source_person_id,
        public_actor_person_id=public_actor_person_id,
        public_victim_person_id=public_victim_person_id,
        distortion=distortion,
    )


def mark_event_record_misattributed(
    conn: sqlite3.Connection,
    event_id: int,
    *,
    attribution_year: int | None = None,
    record_key: str = "default",
    confidence: float = 0.35,
    source_person_id: int | None = None,
    public_actor_person_id: int | None = None,
    public_victim_person_id: int | None = None,
    distortion: dict | None = None,
) -> None:
    """Expose a public version that names the wrong actor, victim, or cause."""
    _event_record_state_update(
        conn,
        event_id=int(event_id),
        record_key=record_key,
        visibility_state="misattributed",
        sim_year=attribution_year,
        confidence=confidence,
        source_person_id=source_person_id,
        public_actor_person_id=public_actor_person_id,
        public_victim_person_id=public_victim_person_id,
        distortion=distortion,
    )


def _event_record_event_row(conn: sqlite3.Connection, event_id: int) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT id, sim_year, event_type, payload_json,
               primary_person_id, secondary_person_id,
               settlement_key, region_key, event_origin, created_at
        FROM simulation_events
        WHERE id = ?
        """,
        (int(event_id),),
    ).fetchone()
    if row is None:
        raise ValueError(f"event not found for event_id={int(event_id)}")
    return row


def upsert_event_record(
    conn: sqlite3.Connection,
    event_id: int,
    *,
    record_key: str,
    visibility_state: str,
    record_type: str | None = None,
    known_since_year: int | None = None,
    lost_year: int | None = None,
    rediscovered_year: int | None = None,
    confidence: float = 1.0,
    source_person_id: int | None = None,
    source_institution_id: str | None = None,
    preserving_settlement_id: str | None = None,
    preserving_region_id: str | None = None,
    public_actor_person_id: int | None = None,
    public_victim_person_id: int | None = None,
    distortion: dict | None = None,
) -> int:
    """Create or replace one named memory/public record for a factual event."""
    event = _event_record_event_row(conn, int(event_id))
    state = _validate_event_record_visibility_state(visibility_state)
    key = str(record_key or "").strip() or "default"
    base_type, _base_state, base_confidence = _event_record_kind_for_type(
        str(event["event_type"] or ""), str(event["event_origin"] or "generated")
    )
    clean_record_type = (
        str(record_type or base_type or "event_memory").strip() or "event_memory"
    )
    public_actor, public_victim = _event_record_public_people(
        str(event["event_type"] or ""),
        int(event["primary_person_id"]) if event["primary_person_id"] is not None else None,
        int(event["secondary_person_id"]) if event["secondary_person_id"] is not None else None,
        _json_loads_dict(event["payload_json"] if "payload_json" in event.keys() else None),
    )
    if public_actor_person_id is not None:
        public_actor = int(public_actor_person_id)
    if public_victim_person_id is not None:
        public_victim = int(public_victim_person_id)
    settlement_key = (
        int(event["settlement_key"]) if event["settlement_key"] is not None else None
    )
    region_key = int(event["region_key"]) if event["region_key"] is not None else None
    if preserving_settlement_id is not None or preserving_region_id is not None:
        region_hint = str(preserving_region_id or "").strip()
        settlement_hint = str(preserving_settlement_id or "").strip()
        if not region_hint and ":" in settlement_hint:
            region_hint = settlement_hint.split(":", 1)[0].strip()
        region_key = _lookup_or_insert_region_key(conn, region_hint)
        settlement_key = _lookup_or_insert_settlement_key(
            conn, settlement_hint, region_hint
        )
    known_year = (
        int(known_since_year)
        if known_since_year is not None
        else (int(event["sim_year"]) if event["sim_year"] is not None else None)
    )
    now = _utc_now_iso()
    prose_variant_key = f"{clean_record_type}.{state}.default"
    conf = max(0.0, min(1.0, float(confidence if confidence is not None else base_confidence)))
    conn.execute(
        """
        INSERT INTO simulation_event_records (
            event_id, record_key, record_type, visibility_state,
            known_since_year, lost_year, rediscovered_year, confidence,
            source_person_id, source_institution_id,
            preserving_settlement_key, preserving_region_key,
            public_actor_person_id, public_victim_person_id,
            distortion_json, prose_variant_key, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id, record_key) DO UPDATE SET
            record_type = excluded.record_type,
            visibility_state = excluded.visibility_state,
            known_since_year = excluded.known_since_year,
            lost_year = excluded.lost_year,
            rediscovered_year = excluded.rediscovered_year,
            confidence = excluded.confidence,
            source_person_id = excluded.source_person_id,
            source_institution_id = excluded.source_institution_id,
            preserving_settlement_key = excluded.preserving_settlement_key,
            preserving_region_key = excluded.preserving_region_key,
            public_actor_person_id = excluded.public_actor_person_id,
            public_victim_person_id = excluded.public_victim_person_id,
            distortion_json = excluded.distortion_json,
            prose_variant_key = excluded.prose_variant_key,
            updated_at = excluded.updated_at
        """,
        (
            int(event_id),
            key,
            clean_record_type,
            state,
            known_year,
            int(lost_year) if lost_year is not None else None,
            int(rediscovered_year) if rediscovered_year is not None else None,
            conf,
            int(source_person_id) if source_person_id is not None else None,
            str(source_institution_id).strip()
            if source_institution_id is not None and str(source_institution_id).strip()
            else None,
            settlement_key,
            region_key,
            public_actor,
            public_victim,
            _event_record_distortion_json(distortion),
            prose_variant_key,
            now,
            now,
        ),
    )
    row = _event_record_row(conn, int(event_id), key)
    return int(row["record_id"])


def upsert_public_event_record(
    conn: sqlite3.Connection,
    event_id: int,
    *,
    public_stage: str,
    record_key: str | None = None,
    record_type: str | None = None,
    **kwargs: object,
) -> int:
    """Create a public unknown, rumor, or known record for one factual event."""
    visibility, default_record_type, key_stage = _public_event_record_stage_defaults(
        public_stage
    )
    return upsert_event_record(
        conn,
        int(event_id),
        record_key=record_key or f"public_{key_stage}",
        record_type=record_type or default_record_type,
        visibility_state=visibility,
        **kwargs,
    )


def rediscover_event_record(
    conn: sqlite3.Connection,
    event_id: int,
    *,
    rediscovered_year: int,
    world: str = "default",
    record_key: str = "default",
    source_person_id: int | None = None,
    source_institution_id: str | None = None,
    preserving_settlement_id: str | None = None,
    preserving_region_id: str | None = None,
    confidence: float = 0.85,
    distortion: dict | None = None,
    create_event: bool = True,
) -> int | None:
    """Mark a lost/hidden record as rediscovered and optionally log that discovery."""
    _event_record_state_update(
        conn,
        event_id=int(event_id),
        record_key=record_key,
        visibility_state="rediscovered",
        sim_year=int(rediscovered_year),
        confidence=confidence,
        source_person_id=source_person_id,
        source_institution_id=source_institution_id,
        preserving_settlement_id=preserving_settlement_id,
        preserving_region_id=preserving_region_id,
        distortion=distortion,
        set_rediscovered_year=True,
    )
    if not create_event:
        return None
    payload: dict[str, object] = {
        "original_event_id": int(event_id),
        "original_record_key": str(record_key or "default").strip() or "default",
        "confidence": max(0.0, min(1.0, float(confidence))),
    }
    if source_person_id is not None:
        payload["source_person_id"] = int(source_person_id)
    if source_institution_id is not None:
        payload["source_institution_id"] = str(source_institution_id).strip()
    region_hint = str(preserving_region_id or "").strip()
    settlement_hint = str(preserving_settlement_id or "").strip()
    if not region_hint and ":" in settlement_hint:
        region_hint = settlement_hint.split(":", 1)[0].strip()
    if settlement_hint:
        payload["settlement_id"] = settlement_hint
    if region_hint:
        payload["region_id"] = region_hint
    if distortion is not None:
        payload["distortion"] = distortion
    inserted = append_simulation_event_rows(
        conn,
        world,
        [(int(rediscovered_year), "event_rediscovered", payload)],
    )
    return inserted[0] if inserted else None


def _ensure_simulation_events_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS simulation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sim_year INTEGER,
            event_type TEXT NOT NULL,
            primary_person_id INTEGER,
            secondary_person_id INTEGER,
            settlement_key INTEGER,
            region_key INTEGER,
            event_origin TEXT NOT NULL DEFAULT 'generated',
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS simulation_event_people (
            event_id INTEGER NOT NULL,
            person_id INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT 'related',
            PRIMARY KEY (event_id, person_id, role)
        );
        CREATE TABLE IF NOT EXISTS simulation_event_moves (
            event_id INTEGER NOT NULL,
            moved_person_id INTEGER NOT NULL,
            from_settlement_key INTEGER,
            to_settlement_key INTEGER,
            from_region_key INTEGER,
            to_region_key INTEGER,
            cross_region INTEGER NOT NULL DEFAULT 0,
            move_reason TEXT,
            requested_year INTEGER,
            planned_apply_year INTEGER,
            source_event TEXT,
            group_id TEXT,
            PRIMARY KEY (event_id, moved_person_id)
        );
        CREATE TABLE IF NOT EXISTS simulation_event_records (
            record_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            record_key TEXT NOT NULL DEFAULT 'default',
            record_type TEXT NOT NULL DEFAULT 'event_memory',
            visibility_state TEXT NOT NULL DEFAULT 'public_known',
            known_since_year INTEGER,
            lost_year INTEGER,
            rediscovered_year INTEGER,
            confidence REAL NOT NULL DEFAULT 1.0,
            source_person_id INTEGER,
            source_institution_id TEXT,
            preserving_settlement_key INTEGER,
            preserving_region_key INTEGER,
            public_actor_person_id INTEGER,
            public_victim_person_id INTEGER,
            distortion_json TEXT,
            prose_variant_key TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (event_id, record_key)
        );
        """
    )
    event_cols = set(_table_columns(conn, "simulation_events"))
    for column_sql in (
        "primary_person_id INTEGER",
        "secondary_person_id INTEGER",
        "settlement_key INTEGER",
        "region_key INTEGER",
        "event_origin TEXT NOT NULL DEFAULT 'generated'",
    ):
        col = column_sql.split()[0]
        if col not in event_cols:
            conn.execute(f"ALTER TABLE simulation_events ADD COLUMN {column_sql}")
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_simulation_events_year
        ON simulation_events (sim_year);
        CREATE INDEX IF NOT EXISTS idx_simulation_events_type
        ON simulation_events (event_type);
        CREATE INDEX IF NOT EXISTS idx_simulation_events_primary_person
        ON simulation_events (primary_person_id);
        CREATE INDEX IF NOT EXISTS idx_simulation_events_settlement
        ON simulation_events (settlement_key);
        CREATE INDEX IF NOT EXISTS idx_simulation_events_region
        ON simulation_events (region_key);
        CREATE INDEX IF NOT EXISTS idx_simulation_event_people_person
        ON simulation_event_people (person_id, event_id);
        CREATE INDEX IF NOT EXISTS idx_simulation_event_moves_person
        ON simulation_event_moves (moved_person_id, event_id);
        CREATE INDEX IF NOT EXISTS idx_simulation_event_moves_to_settlement
        ON simulation_event_moves (to_settlement_key, event_id);
        CREATE INDEX IF NOT EXISTS idx_simulation_event_moves_to_region
        ON simulation_event_moves (to_region_key, event_id);
        CREATE INDEX IF NOT EXISTS idx_simulation_event_records_event
        ON simulation_event_records (event_id, record_key);
        CREATE INDEX IF NOT EXISTS idx_simulation_event_records_visibility
        ON simulation_event_records (visibility_state, event_id);
        CREATE INDEX IF NOT EXISTS idx_simulation_event_records_place
        ON simulation_event_records (preserving_region_key, preserving_settlement_key);
        CREATE INDEX IF NOT EXISTS idx_simulation_event_records_actor
        ON simulation_event_records (public_actor_person_id, event_id);
        CREATE INDEX IF NOT EXISTS idx_simulation_event_records_victim
        ON simulation_event_records (public_victim_person_id, event_id);
        """
    )
    _backfill_simulation_event_people(conn)
    _backfill_simulation_event_moves(conn)
    _backfill_simulation_event_records(conn)
    _backfill_simulation_event_public_stage_records(conn)


def _backfill_simulation_event_people(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "save_metadata"):
        done = conn.execute(
            """
            SELECT meta_value FROM save_metadata
            WHERE meta_key = ?
            """,
            (EVENT_PEOPLE_BACKFILLED_META_KEY,),
        ).fetchone()
        if done is not None and str(done[0]).strip() == "1":
            return
    rows = conn.execute(
        """
        SELECT id, event_type, payload_json, primary_person_id, secondary_person_id,
               settlement_key, region_key, event_origin
        FROM simulation_events
        """
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        links = _event_person_links_from_payload(payload, event_type=row["event_type"])
        primary, secondary, settlement_id, region_id = _event_common_columns_from_links(
            payload,
            links,
        )
        updates: dict[str, object] = {}
        if row["primary_person_id"] is None and primary is not None:
            updates["primary_person_id"] = primary
        if row["secondary_person_id"] is None and secondary is not None:
            updates["secondary_person_id"] = secondary
        settlement_key = _lookup_or_insert_settlement_key(conn, settlement_id, region_id)
        region_key = _lookup_or_insert_region_key(conn, region_id)
        if row["settlement_key"] is None and settlement_key is not None:
            updates["settlement_key"] = settlement_key
        if row["region_key"] is None and region_key is not None:
            updates["region_key"] = region_key
        origin = _event_origin_from_payload(payload)
        if str(row["event_origin"] or "").strip().lower() != origin:
            updates["event_origin"] = origin
        if updates:
            assignments = ", ".join(f"{_quote_identifier(k)} = ?" for k in updates)
            conn.execute(
                f"UPDATE simulation_events SET {assignments} WHERE id = ?",
                (*updates.values(), row["id"]),
            )
        for person_id, role in links:
            conn.execute(
                """
                INSERT OR IGNORE INTO simulation_event_people (event_id, person_id, role)
                VALUES (?, ?, ?)
                """,
                (row["id"], person_id, role),
            )
    if _table_exists(conn, "save_metadata"):
        conn.execute(
            """
            INSERT OR REPLACE INTO save_metadata (meta_key, meta_value)
            VALUES (?, '1')
            """,
            (EVENT_PEOPLE_BACKFILLED_META_KEY,),
        )


def _backfill_simulation_event_moves(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT e.id, e.event_type, e.payload_json
        FROM simulation_events e
        LEFT JOIN simulation_event_moves m ON m.event_id = e.id
        WHERE e.event_type = 'settlement_moved'
          AND m.event_id IS NULL
        """
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        _insert_simulation_event_move_rows(
            conn,
            event_id=int(row["id"]),
            event_type=str(row["event_type"] or ""),
            payload=payload,
        )


def _backfill_simulation_event_records(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "save_metadata"):
        done = conn.execute(
            """
            SELECT meta_value FROM save_metadata
            WHERE meta_key = ?
            """,
            (EVENT_RECORDS_BACKFILLED_META_KEY,),
        ).fetchone()
        if done is not None and str(done[0]).strip() == "1":
            missing = conn.execute(
                """
                SELECT e.id
                FROM simulation_events e
                LEFT JOIN simulation_event_records r
                  ON r.event_id = e.id AND r.record_key = 'default'
                WHERE r.record_id IS NULL
                LIMIT 1
                """
            ).fetchone()
            if missing is None:
                return
    rows = conn.execute(
        """
        SELECT e.id, e.sim_year, e.event_type, e.primary_person_id,
               e.secondary_person_id, e.settlement_key, e.region_key,
               e.event_origin, e.payload_json, e.created_at
        FROM simulation_events e
        LEFT JOIN simulation_event_records r
          ON r.event_id = e.id AND r.record_key = 'default'
        WHERE r.record_id IS NULL
        """
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        _insert_default_event_record_row(
            conn,
            event_id=int(row["id"]),
            event_type=str(row["event_type"] or ""),
            sim_year=(
                int(row["sim_year"]) if row["sim_year"] is not None else None
            ),
            primary_person_id=(
                int(row["primary_person_id"])
                if row["primary_person_id"] is not None
                else None
            ),
            secondary_person_id=(
                int(row["secondary_person_id"])
                if row["secondary_person_id"] is not None
                else None
            ),
            settlement_key=(
                int(row["settlement_key"]) if row["settlement_key"] is not None else None
            ),
            region_key=int(row["region_key"]) if row["region_key"] is not None else None,
            event_origin=str(row["event_origin"] or "generated"),
            created_at=str(row["created_at"] or _utc_now_iso()),
            payload=payload,
        )
    if _table_exists(conn, "save_metadata"):
        conn.execute(
            """
            INSERT OR REPLACE INTO save_metadata (meta_key, meta_value)
            VALUES (?, '1')
            """,
            (EVENT_RECORDS_BACKFILLED_META_KEY,),
        )


def _backfill_simulation_event_public_stage_records(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "save_metadata"):
        done = conn.execute(
            """
            SELECT meta_value FROM save_metadata
            WHERE meta_key = ?
            """,
            (EVENT_PUBLIC_STAGE_RECORDS_BACKFILLED_META_KEY,),
        ).fetchone()
        if done is not None and str(done[0]).strip() == "1":
            return
    placeholders = ", ".join("?" for _ in _DEFAULT_PUBLIC_STAGE_EVENT_TYPES)
    rows = conn.execute(
        f"""
        SELECT e.id, e.event_type, e.event_origin, e.payload_json
        FROM simulation_events e
        WHERE e.event_type IN ({placeholders})
           OR e.event_type LIKE 'city_state_%'
        ORDER BY e.id
        """,
        tuple(sorted(_DEFAULT_PUBLIC_STAGE_EVENT_TYPES)),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        _insert_default_public_stage_record_rows(
            conn,
            event_id=int(row["id"]),
            event_type=str(row["event_type"] or ""),
            event_origin=str(row["event_origin"] or "generated"),
            payload=payload,
        )
    if _table_exists(conn, "save_metadata"):
        conn.execute(
            """
            INSERT OR REPLACE INTO save_metadata (meta_key, meta_value)
            VALUES (?, '1')
            """,
            (EVENT_PUBLIC_STAGE_RECORDS_BACKFILLED_META_KEY,),
        )


def _copy_common_table_columns_from_attached_source(
    conn: sqlite3.Connection, table: str
) -> int:
    """Copy columns shared by source and destination table.

    This is the safe rebuild spine for storage migrations: v2 can keep this
    common-column copy for unchanged tables and add explicit transforms for
    tables whose shape changes.
    """
    if not _table_exists(conn, table, schema=_SAVE_REBUILD_SOURCE_SCHEMA):
        return 0
    if not _table_exists(conn, table):
        return 0
    source_cols = set(
        _table_columns(conn, table, schema=_SAVE_REBUILD_SOURCE_SCHEMA)
    )
    cols = [c for c in _table_columns(conn, table) if c in source_cols]
    if not cols:
        return 0
    table_sql = _quote_identifier(table)
    cols_sql = ", ".join(_quote_identifier(c) for c in cols)
    cur = conn.execute(
        f"""
        INSERT INTO main.{table_sql} ({cols_sql})
        SELECT {cols_sql}
        FROM {_quote_identifier(_SAVE_REBUILD_SOURCE_SCHEMA)}.{table_sql}
        """
    )
    return int(cur.rowcount if cur.rowcount is not None else 0)


def _copy_place_lookups_from_attached_source(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "simulation_region_lookup", schema=_SAVE_REBUILD_SOURCE_SCHEMA):
        _copy_common_table_columns_from_attached_source(conn, "simulation_region_lookup")
    if _table_exists(conn, "simulation_settlement_lookup", schema=_SAVE_REBUILD_SOURCE_SCHEMA):
        _copy_common_table_columns_from_attached_source(conn, "simulation_settlement_lookup")
    for table in ("simulation_regions", "simulation_settlements", "simulation_people", "simulation_events"):
        if not _table_exists(conn, table, schema=_SAVE_REBUILD_SOURCE_SCHEMA):
            continue
        source_cols = set(_table_columns(conn, table, schema=_SAVE_REBUILD_SOURCE_SCHEMA))
        if "region_id" in source_cols:
            for row in conn.execute(
                f"""
                SELECT DISTINCT region_id
                FROM {_quote_identifier(_SAVE_REBUILD_SOURCE_SCHEMA)}.{_quote_identifier(table)}
                WHERE region_id IS NOT NULL AND trim(region_id) <> ''
                """
            ):
                _lookup_or_insert_region_key(conn, row[0])
        for col in ("birthplace_region_id", "current_region_id", "from_region_id", "to_region_id"):
            if col not in source_cols:
                continue
            for row in conn.execute(
                f"""
                SELECT DISTINCT {_quote_identifier(col)}
                FROM {_quote_identifier(_SAVE_REBUILD_SOURCE_SCHEMA)}.{_quote_identifier(table)}
                WHERE {_quote_identifier(col)} IS NOT NULL
                  AND trim({_quote_identifier(col)}) <> ''
                """
            ):
                _lookup_or_insert_region_key(conn, row[0])
        if "settlement_id" in source_cols:
            region_col = "region_id" if "region_id" in source_cols else "NULL"
            for row in conn.execute(
                f"""
                SELECT DISTINCT settlement_id, {region_col}
                FROM {_quote_identifier(_SAVE_REBUILD_SOURCE_SCHEMA)}.{_quote_identifier(table)}
                WHERE settlement_id IS NOT NULL AND trim(settlement_id) <> ''
                """
            ):
                _lookup_or_insert_settlement_key(conn, row[0], row[1])
        for col in ("birthplace_settlement_id", "current_settlement_id", "from_settlement_id", "to_settlement_id"):
            if col not in source_cols:
                continue
            for row in conn.execute(
                f"""
                SELECT DISTINCT {_quote_identifier(col)}
                FROM {_quote_identifier(_SAVE_REBUILD_SOURCE_SCHEMA)}.{_quote_identifier(table)}
                WHERE {_quote_identifier(col)} IS NOT NULL
                  AND trim({_quote_identifier(col)}) <> ''
                """
            ):
                _lookup_or_insert_settlement_key(conn, row[0], None)


def _copy_regions_from_attached_source(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "simulation_regions", schema=_SAVE_REBUILD_SOURCE_SCHEMA):
        return 0
    source_cols = set(_table_columns(conn, "simulation_regions", schema=_SAVE_REBUILD_SOURCE_SCHEMA))
    if "region_key" in source_cols:
        return _copy_common_table_columns_from_attached_source(conn, "simulation_regions")
    if "region_id" not in source_cols:
        return 0
    copied = 0
    for row in conn.execute(
        f"""
        SELECT *
        FROM {_quote_identifier(_SAVE_REBUILD_SOURCE_SCHEMA)}.simulation_regions
        """
    ):
        rkey = _lookup_or_insert_region_key(conn, row["region_id"])
        if rkey is None:
            continue
        conn.execute(
            """
            INSERT OR REPLACE INTO simulation_regions (
                region_key, region_display_name, total_population_cap,
                total_household_cap, food_pressure, stability, market_pull,
                prosperity_pool, treasury_balance
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rkey,
                row["region_display_name"] if "region_display_name" in source_cols else "",
                int(row["total_population_cap"] or 0),
                int(row["total_household_cap"] or 0),
                float(row["food_pressure"] or 0.0),
                float(row["stability"] or 0.0),
                float(row["market_pull"] or 0.0),
                float(row["prosperity_pool"] if "prosperity_pool" in source_cols and row["prosperity_pool"] is not None else 1.0),
                float(row["treasury_balance"] if "treasury_balance" in source_cols and row["treasury_balance"] is not None else 0.0),
            ),
        )
        copied += 1
    return copied


def _copy_settlements_from_attached_source(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "simulation_settlements", schema=_SAVE_REBUILD_SOURCE_SCHEMA):
        return 0
    source_cols = set(_table_columns(conn, "simulation_settlements", schema=_SAVE_REBUILD_SOURCE_SCHEMA))
    if "settlement_key" in source_cols:
        return _copy_common_table_columns_from_attached_source(conn, "simulation_settlements")
    if "settlement_id" not in source_cols:
        return 0
    copied = 0
    for row in conn.execute(
        f"""
        SELECT *
        FROM {_quote_identifier(_SAVE_REBUILD_SOURCE_SCHEMA)}.simulation_settlements
        """
    ):
        skey = _lookup_or_insert_settlement_key(conn, row["settlement_id"], row["region_id"])
        rkey = _lookup_or_insert_region_key(conn, row["region_id"])
        if skey is None or rkey is None:
            continue
        conn.execute(
            """
            INSERT OR REPLACE INTO simulation_settlements (
                settlement_key, region_key, level, population_cap, household_cap,
                food_pressure, prosperity_pool, stability, market_pull,
                display_name, etymology, name_category_primary, name_category_secondary,
                name_culture_primary, name_culture_secondary, local_geography_json,
                founded_sim_year, abandoned_sim_year, status, consecutive_empty_years, site_slot,
                founding_reason, mother_settlement_id, trade_network_id, autonomy_level
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                skey,
                rkey,
                row["level"],
                int(row["population_cap"] or 0),
                int(row["household_cap"] or 0),
                float(row["food_pressure"] or 0.0),
                float(row["prosperity_pool"] if "prosperity_pool" in source_cols and row["prosperity_pool"] is not None else 1.0),
                float(row["stability"] or 0.0),
                float(row["market_pull"] or 0.0),
                row["display_name"] if "display_name" in source_cols else None,
                row["etymology"] if "etymology" in source_cols else None,
                row["name_category_primary"] if "name_category_primary" in source_cols else None,
                row["name_category_secondary"] if "name_category_secondary" in source_cols else None,
                row["name_culture_primary"] if "name_culture_primary" in source_cols else None,
                row["name_culture_secondary"] if "name_culture_secondary" in source_cols else None,
                row["local_geography_json"] if "local_geography_json" in source_cols else None,
                row["founded_sim_year"] if "founded_sim_year" in source_cols else None,
                row["abandoned_sim_year"] if "abandoned_sim_year" in source_cols else None,
                row["status"] if "status" in source_cols else "active",
                int(row["consecutive_empty_years"] if "consecutive_empty_years" in source_cols and row["consecutive_empty_years"] is not None else 0),
                int(row["site_slot"] if "site_slot" in source_cols and row["site_slot"] is not None else 1),
                row["founding_reason"] if "founding_reason" in source_cols else "organic",
                row["mother_settlement_id"] if "mother_settlement_id" in source_cols else None,
                row["trade_network_id"] if "trade_network_id" in source_cols and row["trade_network_id"] is not None else row["settlement_id"],
                row["autonomy_level"] if "autonomy_level" in source_cols else "autonomous",
            ),
        )
        copied += 1
    return copied


def _copy_people_from_attached_source(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "simulation_people", schema=_SAVE_REBUILD_SOURCE_SCHEMA):
        return 0
    source_cols = set(_table_columns(conn, "simulation_people", schema=_SAVE_REBUILD_SOURCE_SCHEMA))
    if "first_name" not in source_cols:
        raise RuntimeError(
            "source simulation_people is older than compact schema v3; "
            "delete and regenerate the pre-alpha save.sqlite"
        )
    if "birthplace_region_key" in source_cols:
        return _copy_common_table_columns_from_attached_source(conn, "simulation_people")
    target_cols = _table_columns(conn, "simulation_people")
    direct_cols = [
        c
        for c in target_cols
        if c in source_cols
        and c
        not in {
            "birthplace_region_key",
            "birthplace_settlement_key",
            "current_settlement_key",
        }
    ]
    copied = 0
    for row in conn.execute(
        f"""
        SELECT *
        FROM {_quote_identifier(_SAVE_REBUILD_SOURCE_SCHEMA)}.simulation_people
        """
    ):
        values = {c: row[c] for c in direct_cols}
        values["birthplace_region_key"] = _lookup_or_insert_region_key(
            conn, row["birthplace_region_id"] if "birthplace_region_id" in source_cols else None
        )
        values["birthplace_settlement_key"] = _lookup_or_insert_settlement_key(
            conn,
            row["birthplace_settlement_id"] if "birthplace_settlement_id" in source_cols else None,
            row["birthplace_region_id"] if "birthplace_region_id" in source_cols else None,
        )
        values["current_settlement_key"] = _lookup_or_insert_settlement_key(
            conn,
            row["current_settlement_id"] if "current_settlement_id" in source_cols else None,
            row["birthplace_region_id"] if "birthplace_region_id" in source_cols else None,
        )
        cols = [c for c in target_cols if c in values]
        conn.execute(
            f"""
            INSERT OR REPLACE INTO simulation_people (
                {", ".join(_quote_identifier(c) for c in cols)}
            )
            VALUES ({", ".join("?" for _ in cols)})
            """,
            tuple(values[c] for c in cols),
        )
        copied += 1
    return copied


def _copy_events_from_attached_source(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "simulation_events", schema=_SAVE_REBUILD_SOURCE_SCHEMA):
        return 0
    source_cols = set(_table_columns(conn, "simulation_events", schema=_SAVE_REBUILD_SOURCE_SCHEMA))
    if "settlement_key" in source_cols:
        return _copy_common_table_columns_from_attached_source(conn, "simulation_events")
    direct_cols = [
        c
        for c in ("id", "sim_year", "event_type", "primary_person_id", "secondary_person_id", "payload_json", "created_at")
        if c in source_cols
    ]
    copied = 0
    for row in conn.execute(
        f"""
        SELECT *
        FROM {_quote_identifier(_SAVE_REBUILD_SOURCE_SCHEMA)}.simulation_events
        """
    ):
        payload: dict = {}
        try:
            loaded = json.loads(row["payload_json"] or "{}")
            if isinstance(loaded, dict):
                payload = loaded
        except (KeyError, json.JSONDecodeError, TypeError):
            payload = {}
        _primary, _secondary, settlement_id, region_id = _event_common_columns(payload)
        if "settlement_id" in source_cols and row["settlement_id"] is not None:
            settlement_id = row["settlement_id"]
        if "region_id" in source_cols and row["region_id"] is not None:
            region_id = row["region_id"]
        values = {c: row[c] for c in direct_cols}
        values["settlement_key"] = _lookup_or_insert_settlement_key(conn, settlement_id, region_id)
        values["region_key"] = _lookup_or_insert_region_key(conn, region_id)
        values["event_origin"] = (
            row["event_origin"]
            if "event_origin" in source_cols and row["event_origin"] is not None
            else _event_origin_from_payload(payload)
        )
        cols = [c for c in ("id", "sim_year", "event_type", "primary_person_id", "secondary_person_id", "settlement_key", "region_key", "event_origin", "payload_json", "created_at") if c in values]
        conn.execute(
            f"""
            INSERT OR REPLACE INTO simulation_events (
                {", ".join(_quote_identifier(c) for c in cols)}
            )
            VALUES ({", ".join("?" for _ in cols)})
            """,
            tuple(values[c] for c in cols),
        )
        copied += 1
    return copied


def _write_rebuilt_save_sqlite(
    source_db_path: Path,
    rebuilt_db_path: Path,
    *,
    target_schema_version: int,
) -> None:
    if int(target_schema_version) != SAVE_SCHEMA_VERSION:
        raise ValueError(
            f"no transform is registered for save schema version {target_schema_version}; "
            f"current supported version is {SAVE_SCHEMA_VERSION}"
        )
    with _open_save(rebuilt_db_path) as conn:
        conn.executescript(_CREATE_WORLD_STATE)
        ensure_checkpoint_schema(conn)
        conn.execute(
            f"ATTACH DATABASE ? AS {_quote_identifier(_SAVE_REBUILD_SOURCE_SCHEMA)}",
            (str(source_db_path),),
        )
        try:
            _copy_place_lookups_from_attached_source(conn)
            for table in _SAVE_REBUILD_TABLES:
                if table in {
                    "simulation_region_lookup",
                    "simulation_settlement_lookup",
                }:
                    continue
                if table == "simulation_people":
                    _copy_people_from_attached_source(conn)
                elif table == "simulation_events":
                    _copy_events_from_attached_source(conn)
                elif table == "simulation_regions":
                    _copy_regions_from_attached_source(conn)
                elif table == "simulation_settlements":
                    _copy_settlements_from_attached_source(conn)
                else:
                    _copy_common_table_columns_from_attached_source(conn, table)
            _backfill_simulation_event_people(conn)
            _backfill_simulation_event_moves(conn)
            _backfill_simulation_event_records(conn)
            _backfill_simulation_event_public_stage_records(conn)
            _backfill_simulation_domain_states(conn)
            _backfill_simulation_obligations(conn)
            _backfill_simulation_reputation_marks(conn)
            _backfill_simulation_legal_fallout(conn)
            _backfill_simulation_faction_memory(conn)
            _backfill_simulation_institutions(conn)
            _backfill_simulation_innovations(conn)
            from library.person_almanack import refresh_person_almanack

            refresh_person_almanack(conn)
            _stamp_save_schema_version(conn, int(target_schema_version))
            conn.commit()
        finally:
            conn.execute(f"DETACH DATABASE {_quote_identifier(_SAVE_REBUILD_SOURCE_SCHEMA)}")


def rebuild_save_sqlite_for_schema_upgrade(
    save_db_path: Path | str,
    *,
    output_path: Path | str | None = None,
    replace_original: bool = False,
    backup_path: Path | str | None = None,
    overwrite: bool = False,
    target_schema_version: int = SAVE_SCHEMA_VERSION,
) -> Path:
    """Rebuild ``save.sqlite`` through a new file before an optional swap.

    The current implementation copies the current schema losslessly while stamping a
    schema version. Future storage changes should plug their transforms into
    ``_write_rebuilt_save_sqlite`` so migrations never rewrite the live DB in
    place.
    """
    source = Path(save_db_path)
    if not source.exists():
        raise FileNotFoundError(source)
    target_version = int(target_schema_version)
    if replace_original and output_path is not None:
        raise ValueError("output_path cannot be used with replace_original=True")

    if replace_original:
        final_path = source
    else:
        final_path = (
            Path(output_path)
            if output_path is not None
            else source.with_name(f"{source.stem}.schema{target_version}{source.suffix}")
        )
        if final_path.exists() and not overwrite:
            raise FileExistsError(final_path)

    temp_path = final_path.with_name(f".{final_path.name}.tmp")
    if temp_path.exists():
        temp_path.unlink()

    _write_rebuilt_save_sqlite(
        source,
        temp_path,
        target_schema_version=target_version,
    )

    if replace_original:
        with _open_save(source) as source_conn:
            source_version = save_schema_version(source_conn)
        backup = (
            Path(backup_path)
            if backup_path is not None
            else source.with_name(f"{source.name}.schema{source_version}.bak")
        )
        if backup.exists() and not overwrite:
            temp_path.unlink()
            raise FileExistsError(backup)
        source.replace(backup)
        temp_path.replace(source)
        return source

    temp_path.replace(final_path)
    return final_path


_CREATE_SIMULATION_REGIONS = """
    CREATE TABLE IF NOT EXISTS simulation_regions (
        region_key INTEGER PRIMARY KEY,
        region_display_name TEXT NOT NULL DEFAULT '',
        total_population_cap INTEGER NOT NULL,
        total_household_cap INTEGER NOT NULL,
        food_pressure REAL NOT NULL,
        stability REAL NOT NULL,
        market_pull REAL NOT NULL
    );
"""

_CREATE_SIMULATION_SETTLEMENTS_V2 = """
    CREATE TABLE simulation_settlements (
        settlement_key INTEGER PRIMARY KEY,
        region_key INTEGER NOT NULL,
        level TEXT NOT NULL,
        population_cap INTEGER NOT NULL,
        household_cap INTEGER NOT NULL,
        food_pressure REAL NOT NULL,
        stability REAL NOT NULL,
        market_pull REAL NOT NULL,
        display_name TEXT,
        etymology TEXT,
        name_category_primary TEXT,
        name_category_secondary TEXT,
        name_culture_primary TEXT,
        name_culture_secondary TEXT,
        local_geography_json TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_simulation_settlements_region
    ON simulation_settlements (region_key);
"""


def ensure_checkpoint_schema(conn: sqlite3.Connection) -> None:
    try:
        cached = conn.execute(
            """
            SELECT 1
            FROM sqlite_temp_master
            WHERE type = 'table'
              AND name = '_progen_checkpoint_schema_ensured'
            """
        ).fetchone()
    except sqlite3.Error:
        cached = None
    if cached is not None:
        conn.commit()
        return

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS simulation_meta (
            meta_key TEXT PRIMARY KEY,
            meta_value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS simulation_couples (
            sort_order INTEGER PRIMARY KEY,
            person_a_id INTEGER NOT NULL,
            person_b_id INTEGER NOT NULL,
            surname_convention TEXT
        );
        CREATE TABLE IF NOT EXISTS simulation_paramours (
            sort_order INTEGER PRIMARY KEY,
            person_a_id INTEGER NOT NULL,
            person_b_id INTEGER NOT NULL,
            surname_convention TEXT
        );
        """
    )
    _ensure_place_lookup_schema(conn)
    _ensure_simulation_events_tables(conn)
    _ensure_simulation_domain_state_tables(conn)
    _ensure_simulation_domain_diffusion_tables(conn)
    _ensure_simulation_obligation_tables(conn)
    _ensure_simulation_reputation_mark_tables(conn)
    _ensure_simulation_legal_fallout_tables(conn)
    _ensure_simulation_legal_adjudication_tables(conn)
    _ensure_simulation_faction_memory_tables(conn)
    _ensure_simulation_institution_tables(conn)
    _ensure_simulation_innovation_tables(conn)
    _ensure_simulation_people_table(conn)
    _ensure_household_service_contracts_table(conn)
    _ensure_patronage_ties_table(conn)
    _ensure_outlaw_tables(conn)
    _ensure_serial_predation_candidate_table(conn)
    from library.person_archive_scores import ensure_person_archive_score_schema
    from library.person_almanack import ensure_person_almanack_schema

    ensure_person_archive_score_schema(conn)
    _ensure_hybrid_population_tables(conn)
    ensure_person_almanack_schema(conn)
    conn.executescript(_CREATE_SIMULATION_REGIONS)
    _ensure_simulation_settlements_table(conn)
    _migrate_cap_named_columns(conn)
    _migrate_simulation_regions_region_display_name(conn)
    _migrate_simulation_settlements_lifecycle_columns(conn)
    _migrate_simulation_settlements_empty_site_columns(conn)
    _migrate_simulation_settlements_prosperity_pool(conn)
    _migrate_simulation_settlements_trade_network_columns(conn)
    _migrate_simulation_regions_economy_columns(conn)
    _migrate_relationship_surname_convention_columns(conn)
    from library import government_checkpoint as _gov_ckpt

    _gov_ckpt.ensure_government_schema(conn)
    _ensure_readable_place_views(conn)
    _ensure_supported_save_schema(conn)
    conn.execute(
        """
        CREATE TEMP TABLE IF NOT EXISTS _progen_checkpoint_schema_ensured (
            marker INTEGER PRIMARY KEY
        )
        """
    )
    conn.commit()


def _ensure_simulation_settlements_table(conn: sqlite3.Connection) -> None:
    """Create ``simulation_settlements`` with per-settlement primary keys."""
    exists = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='simulation_settlements'
        """
    ).fetchone()
    if exists is None:
        conn.executescript(_CREATE_SIMULATION_SETTLEMENTS_V2)
        return

    cols = {
        str(r[1])
        for r in conn.execute("PRAGMA table_info(simulation_settlements)").fetchall()
    }
    if "settlement_key" not in cols:
        raise RuntimeError(
            "simulation_settlements uses a pre-v5 text-id schema. Delete or rebuild "
            "save.sqlite before opening it with the surrogate place-key schema."
        )

    if "display_name" not in cols:
        conn.execute(
            "ALTER TABLE simulation_settlements ADD COLUMN display_name TEXT"
        )
    if "etymology" not in cols:
        conn.execute(
            "ALTER TABLE simulation_settlements ADD COLUMN etymology TEXT"
        )
    if "local_geography_json" not in cols:
        conn.execute(
            "ALTER TABLE simulation_settlements ADD COLUMN local_geography_json TEXT"
        )
    _migrate_settlement_placename_meta_columns(conn)


def _migrate_simulation_settlements_lifecycle_columns(conn: sqlite3.Connection) -> None:
    """Add founding / abandonment / status for multi-settlement lifecycle."""
    st = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='simulation_settlements'
        """
    ).fetchone()
    if st is None:
        return
    cols = {
        str(r[1])
        for r in conn.execute("PRAGMA table_info(simulation_settlements)").fetchall()
    }
    if "founded_sim_year" not in cols:
        conn.execute(
            "ALTER TABLE simulation_settlements ADD COLUMN founded_sim_year INTEGER"
        )
    if "abandoned_sim_year" not in cols:
        conn.execute(
            "ALTER TABLE simulation_settlements ADD COLUMN abandoned_sim_year INTEGER"
        )
    if "status" not in cols:
        conn.execute(
            """
            ALTER TABLE simulation_settlements
            ADD COLUMN status TEXT NOT NULL DEFAULT 'active'
            """
        )


def _migrate_simulation_settlements_empty_site_columns(conn: sqlite3.Connection) -> None:
    """Track vacancy streak and physical site slot for abandonment / re-establishment."""
    st = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='simulation_settlements'
        """
    ).fetchone()
    if st is None:
        return
    cols = {
        str(r[1])
        for r in conn.execute("PRAGMA table_info(simulation_settlements)").fetchall()
    }
    if "consecutive_empty_years" not in cols:
        conn.execute(
            """
            ALTER TABLE simulation_settlements
            ADD COLUMN consecutive_empty_years INTEGER NOT NULL DEFAULT 0
            """
        )
    if "site_slot" not in cols:
        conn.execute(
            """
            ALTER TABLE simulation_settlements ADD COLUMN site_slot INTEGER NOT NULL DEFAULT 1
            """
        )


def _migrate_simulation_settlements_prosperity_pool(conn: sqlite3.Connection) -> None:
    st = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='simulation_settlements'
        """
    ).fetchone()
    if st is None:
        return
    cols = {
        str(r[1])
        for r in conn.execute("PRAGMA table_info(simulation_settlements)").fetchall()
    }
    if "prosperity_pool" not in cols:
        conn.execute(
            """
            ALTER TABLE simulation_settlements
            ADD COLUMN prosperity_pool REAL NOT NULL DEFAULT 1.0
            """
        )


def _migrate_simulation_settlements_trade_network_columns(conn: sqlite3.Connection) -> None:
    st = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='simulation_settlements'
        """
    ).fetchone()
    if st is None:
        return
    cols = {
        str(r[1])
        for r in conn.execute("PRAGMA table_info(simulation_settlements)").fetchall()
    }
    if "founding_reason" not in cols:
        conn.execute(
            """
            ALTER TABLE simulation_settlements
            ADD COLUMN founding_reason TEXT NOT NULL DEFAULT 'organic'
            """
        )
    if "mother_settlement_id" not in cols:
        conn.execute(
            "ALTER TABLE simulation_settlements ADD COLUMN mother_settlement_id TEXT"
        )
    if "trade_network_id" not in cols:
        conn.execute(
            "ALTER TABLE simulation_settlements ADD COLUMN trade_network_id TEXT"
        )
    if "autonomy_level" not in cols:
        conn.execute(
            """
            ALTER TABLE simulation_settlements
            ADD COLUMN autonomy_level TEXT NOT NULL DEFAULT 'autonomous'
            """
        )
    conn.execute(
        """
        UPDATE simulation_settlements
        SET founding_reason = 'organic'
        WHERE founding_reason IS NULL OR trim(founding_reason) = ''
        """
    )
    conn.execute(
        """
        UPDATE simulation_settlements
        SET autonomy_level = 'autonomous'
        WHERE autonomy_level IS NULL OR trim(autonomy_level) = ''
        """
    )
    conn.execute(
        """
        UPDATE simulation_settlements
        SET trade_network_id = (
            SELECT sl.settlement_id
            FROM simulation_settlement_lookup sl
            WHERE sl.settlement_key = simulation_settlements.settlement_key
        )
        WHERE trade_network_id IS NULL OR trim(trade_network_id) = ''
        """
    )


def _migrate_simulation_regions_economy_columns(conn: sqlite3.Connection) -> None:
    reg = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='simulation_regions'
        """
    ).fetchone()
    if reg is None:
        return
    cols = {
        str(r[1])
        for r in conn.execute("PRAGMA table_info(simulation_regions)").fetchall()
    }
    if "prosperity_pool" not in cols:
        conn.execute(
            """
            ALTER TABLE simulation_regions
            ADD COLUMN prosperity_pool REAL NOT NULL DEFAULT 1.0
            """
        )
    if "treasury_balance" not in cols:
        conn.execute(
            """
            ALTER TABLE simulation_regions
            ADD COLUMN treasury_balance REAL NOT NULL DEFAULT 0.0
            """
        )


def _migrate_relationship_surname_convention_columns(conn: sqlite3.Connection) -> None:
    for table in ("simulation_couples", "simulation_paramours"):
        cols = {
            str(r[1])
            for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if "surname_convention" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN surname_convention TEXT")


_PLACENAME_META_COLS = (
    "name_category_primary",
    "name_category_secondary",
    "name_culture_primary",
    "name_culture_secondary",
)


def _migrate_settlement_placename_meta_columns(conn: sqlite3.Connection) -> None:
    """Add placename lexicon metadata columns (category + placenames culture/ethnic layer)."""
    st = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='simulation_settlements'
        """
    ).fetchone()
    if st is None:
        return
    cols = {
        str(r[1])
        for r in conn.execute("PRAGMA table_info(simulation_settlements)").fetchall()
    }
    for c in _PLACENAME_META_COLS:
        if c not in cols:
            conn.execute(f"ALTER TABLE simulation_settlements ADD COLUMN {c} TEXT")


def _migrate_cap_named_columns(conn: sqlite3.Connection) -> None:
    """Rename legacy ``population`` / ``households`` columns to *_cap (SQLite 3.25+)."""
    reg = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='simulation_regions'
        """
    ).fetchone()
    if reg is not None:
        rcols = {
            str(r[1])
            for r in conn.execute("PRAGMA table_info(simulation_regions)").fetchall()
        }
        if "total_population" in rcols and "total_population_cap" not in rcols:
            conn.execute(
                "ALTER TABLE simulation_regions RENAME COLUMN total_population TO total_population_cap"
            )
        if "total_households" in rcols and "total_household_cap" not in rcols:
            conn.execute(
                "ALTER TABLE simulation_regions RENAME COLUMN total_households TO total_household_cap"
            )

    st = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='simulation_settlements'
        """
    ).fetchone()
    if st is not None:
        scols = {
            str(r[1])
            for r in conn.execute("PRAGMA table_info(simulation_settlements)").fetchall()
        }
        if "population" in scols and "population_cap" not in scols:
            conn.execute(
                "ALTER TABLE simulation_settlements RENAME COLUMN population TO population_cap"
            )
        if "households" in scols and "household_cap" not in scols:
            conn.execute(
                "ALTER TABLE simulation_settlements RENAME COLUMN households TO household_cap"
            )


def _migrate_simulation_regions_region_display_name(conn: sqlite3.Connection) -> None:
    """Add ``region_display_name`` when upgrading older checkpoints."""
    reg = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='simulation_regions'
        """
    ).fetchone()
    if reg is None:
        return
    rcols = {
        str(r[1])
        for r in conn.execute("PRAGMA table_info(simulation_regions)").fetchall()
    }
    if "region_display_name" not in rcols:
        conn.execute(
            """
            ALTER TABLE simulation_regions ADD COLUMN region_display_name TEXT NOT NULL DEFAULT ''
            """
        )


def _ensure_readable_place_views(conn: sqlite3.Connection) -> None:
    """Inspection views that expose normalized place keys as readable slugs."""
    conn.execute("DROP VIEW IF EXISTS simulation_settlements_readable")
    conn.execute("DROP VIEW IF EXISTS simulation_events_readable")
    conn.execute("DROP VIEW IF EXISTS simulation_event_records_readable")
    conn.execute("DROP VIEW IF EXISTS simulation_event_moves_readable")
    conn.execute("DROP VIEW IF EXISTS simulation_innovation_discoveries_readable")
    conn.execute("DROP VIEW IF EXISTS simulation_innovation_knowledge_readable")
    conn.execute("DROP VIEW IF EXISTS simulation_innovation_era_state_readable")
    conn.execute("DROP VIEW IF EXISTS simulation_outlaw_cases_readable")
    conn.execute("DROP VIEW IF EXISTS simulation_outlaw_refuges_readable")
    conn.execute("DROP VIEW IF EXISTS simulation_outlaw_custodies_readable")
    conn.executescript(
        """
        CREATE VIEW IF NOT EXISTS simulation_regions_readable AS
        SELECT
            rl.region_id,
            r.region_key,
            r.region_display_name,
            r.total_population_cap,
            r.total_household_cap,
            r.food_pressure,
            r.stability,
            r.market_pull,
            r.prosperity_pool,
            r.treasury_balance
        FROM simulation_regions r
        JOIN simulation_region_lookup rl ON rl.region_key = r.region_key;

        CREATE VIEW IF NOT EXISTS simulation_settlements_readable AS
        SELECT
            sl.settlement_id,
            rl.region_id,
            s.settlement_key,
            s.region_key,
            s.level,
            s.population_cap,
            s.household_cap,
            s.food_pressure,
            s.prosperity_pool,
            s.stability,
            s.market_pull,
            s.display_name,
            s.etymology,
            s.name_category_primary,
            s.name_category_secondary,
            s.name_culture_primary,
            s.name_culture_secondary,
            s.local_geography_json,
            s.founded_sim_year,
            s.abandoned_sim_year,
            s.status,
            s.consecutive_empty_years,
            s.site_slot,
            s.founding_reason,
            s.mother_settlement_id,
            s.trade_network_id,
            s.autonomy_level
        FROM simulation_settlements s
        JOIN simulation_settlement_lookup sl ON sl.settlement_key = s.settlement_key
        JOIN simulation_region_lookup rl ON rl.region_key = s.region_key;

        CREATE VIEW IF NOT EXISTS simulation_events_readable AS
        SELECT
            e.id,
            e.sim_year,
            e.event_type,
            e.primary_person_id,
            e.secondary_person_id,
            sl.settlement_id,
            rl.region_id,
            e.event_origin,
            e.payload_json,
            e.created_at
        FROM simulation_events e
        LEFT JOIN simulation_settlement_lookup sl ON sl.settlement_key = e.settlement_key
        LEFT JOIN simulation_region_lookup rl ON rl.region_key = e.region_key;

        CREATE VIEW IF NOT EXISTS simulation_event_records_readable AS
        SELECT
            r.record_id,
            r.event_id,
            e.sim_year,
            e.event_type,
            e.event_origin,
            r.record_key,
            r.record_type,
            r.visibility_state,
            CASE
                WHEN r.visibility_state = 'public_unknown' THEN 'unknown'
                WHEN r.visibility_state IN ('rumored', 'misattributed') THEN 'rumored'
                WHEN r.visibility_state IN ('public_known', 'rediscovered') THEN 'known'
                WHEN r.visibility_state = 'admin_known' THEN 'admin'
                ELSE 'not_public'
            END AS public_knowledge_stage,
            r.known_since_year,
            r.lost_year,
            r.rediscovered_year,
            r.confidence,
            r.source_person_id,
            r.source_institution_id,
            sl.settlement_id AS preserving_settlement_id,
            rl.region_id AS preserving_region_id,
            r.public_actor_person_id,
            r.public_victim_person_id,
            r.distortion_json,
            r.prose_variant_key,
            r.created_at,
            r.updated_at
        FROM simulation_event_records r
        JOIN simulation_events e ON e.id = r.event_id
        LEFT JOIN simulation_settlement_lookup sl
            ON sl.settlement_key = r.preserving_settlement_key
        LEFT JOIN simulation_region_lookup rl
            ON rl.region_key = r.preserving_region_key;

        CREATE VIEW IF NOT EXISTS simulation_event_moves_readable AS
        SELECT
            m.event_id,
            e.sim_year,
            e.event_type,
            m.moved_person_id,
            from_sl.settlement_id AS from_settlement_id,
            to_sl.settlement_id AS to_settlement_id,
            from_rl.region_id AS from_region_id,
            to_rl.region_id AS to_region_id,
            m.cross_region,
            m.move_reason,
            m.requested_year,
            m.planned_apply_year,
            m.source_event,
            m.group_id,
            e.created_at
        FROM simulation_event_moves m
        JOIN simulation_events e ON e.id = m.event_id
        LEFT JOIN simulation_settlement_lookup from_sl
            ON from_sl.settlement_key = m.from_settlement_key
        LEFT JOIN simulation_settlement_lookup to_sl
            ON to_sl.settlement_key = m.to_settlement_key
        LEFT JOIN simulation_region_lookup from_rl
            ON from_rl.region_key = m.from_region_key
        LEFT JOIN simulation_region_lookup to_rl
            ON to_rl.region_key = m.to_region_key;

        CREATE VIEW IF NOT EXISTS simulation_domain_states_readable AS
        SELECT
            rl.region_id,
            d.region_key,
            d.domain,
            d.domain_score,
            d.breakthrough_count,
            d.first_event_year,
            d.latest_event_year,
            d.first_event_id,
            d.latest_event_id,
            d.latest_incident_kind,
            d.latest_creator_person_id,
            sl.settlement_id AS latest_settlement_id,
            d.created_at,
            d.updated_at
        FROM simulation_domain_states d
        JOIN simulation_region_lookup rl ON rl.region_key = d.region_key
        LEFT JOIN simulation_settlement_lookup sl
            ON sl.settlement_key = d.latest_settlement_key;

        CREATE VIEW IF NOT EXISTS simulation_domain_diffusion_readable AS
        SELECT
            dd.diffusion_id,
            dd.diffusion_year,
            dd.domain,
            src.region_id AS source_region_id,
            dd.source_region_key,
            dst.region_id AS target_region_id,
            dd.target_region_key,
            dd.route_type,
            dd.route_friction,
            dd.source_domain_score,
            dd.target_domain_score_before,
            dd.target_domain_score_after,
            dd.state_delta,
            dd.source_latest_event_id,
            dd.created_at
        FROM simulation_domain_diffusion dd
        JOIN simulation_region_lookup src
            ON src.region_key = dd.source_region_key
        JOIN simulation_region_lookup dst
            ON dst.region_key = dd.target_region_key;

        CREATE VIEW IF NOT EXISTS simulation_obligations_readable AS
        SELECT
            o.obligation_id,
            o.source_event_id,
            e.sim_year AS source_event_year,
            e.event_type AS source_event_type,
            o.obligation_key,
            o.obligation_type,
            o.status,
            o.owed_by_person_id,
            o.owed_to_person_id,
            rl.region_id,
            sl.settlement_id,
            o.strength,
            o.start_year,
            o.expected_end_year,
            o.resolved_year,
            o.details_json,
            o.created_at,
            o.updated_at
        FROM simulation_obligations o
        JOIN simulation_events e ON e.id = o.source_event_id
        LEFT JOIN simulation_region_lookup rl ON rl.region_key = o.region_key
        LEFT JOIN simulation_settlement_lookup sl
            ON sl.settlement_key = o.settlement_key;

        CREATE VIEW IF NOT EXISTS simulation_reputation_marks_readable AS
        SELECT
            m.reputation_mark_id,
            m.source_event_id,
            e.sim_year AS source_event_year,
            e.event_type AS source_event_type,
            m.mark_key,
            m.person_id,
            m.reputation_axis,
            m.reputation_before,
            m.reputation_after,
            m.direction,
            m.mark_strength,
            rl.region_id,
            sl.settlement_id,
            m.mark_year,
            m.details_json,
            m.created_at,
            m.updated_at
        FROM simulation_reputation_marks m
        JOIN simulation_events e ON e.id = m.source_event_id
        LEFT JOIN simulation_region_lookup rl ON rl.region_key = m.region_key
        LEFT JOIN simulation_settlement_lookup sl
            ON sl.settlement_key = m.settlement_key;

        CREATE VIEW IF NOT EXISTS simulation_legal_fallout_readable AS
        SELECT
            f.fallout_id,
            f.source_event_id,
            e.sim_year AS source_event_year,
            e.event_type AS source_event_type,
            f.fallout_key,
            f.fallout_type,
            f.status,
            f.principal_person_id,
            f.opposing_person_id,
            f.related_person_id,
            rl.region_id,
            sl.settlement_id,
            f.severity,
            f.start_year,
            f.expected_resolution_year,
            f.resolved_year,
            f.details_json,
            f.created_at,
            f.updated_at
        FROM simulation_legal_fallout f
        JOIN simulation_events e ON e.id = f.source_event_id
        LEFT JOIN simulation_region_lookup rl ON rl.region_key = f.region_key
        LEFT JOIN simulation_settlement_lookup sl
            ON sl.settlement_key = f.settlement_key;

        CREATE VIEW IF NOT EXISTS simulation_legal_adjudications_readable AS
        SELECT
            a.adjudication_id,
            a.fallout_id,
            a.source_event_id,
            e.sim_year AS source_event_year,
            e.event_type AS source_event_type,
            a.adjudication_key,
            a.adjudication_type,
            a.outcome,
            a.principal_result,
            a.opposing_result,
            a.adjudication_year,
            a.principal_person_id,
            a.opposing_person_id,
            a.related_person_id,
            rl.region_id,
            sl.settlement_id,
            a.severity,
            a.details_json,
            a.created_at,
            a.updated_at
        FROM simulation_legal_adjudications a
        JOIN simulation_events e ON e.id = a.source_event_id
        LEFT JOIN simulation_region_lookup rl ON rl.region_key = a.region_key
        LEFT JOIN simulation_settlement_lookup sl
            ON sl.settlement_key = a.settlement_key;

        CREATE VIEW IF NOT EXISTS simulation_outlaw_cases_readable AS
        SELECT
            c.case_key,
            c.accused_person_id,
            trim(coalesce(ap.first_name, '') || ' ' || coalesce(ap.last_name, '')) AS accused_name,
            c.offense_type,
            c.offense_kind,
            c.status,
            c.resolution,
            c.start_year,
            c.last_seen_year,
            c.expected_forget_year,
            c.resolved_year,
            c.severity_01,
            c.knownness_01,
            c.pursuit_pressure_01,
            c.buyoff_power_01,
            c.victim_person_id,
            trim(coalesce(vp.first_name, '') || ' ' || coalesce(vp.last_name, '')) AS victim_name,
            c.target_person_id,
            trim(coalesce(tp.first_name, '') || ' ' || coalesce(tp.last_name, '')) AS target_name,
            rl.region_id,
            sl.settlement_id,
            c.refuge_id,
            r.display_name AS refuge_display_name,
            r.status AS refuge_status,
            c.custody_id,
            cu.custody_type,
            cu.status AS custody_status,
            csl.settlement_id AS custody_site_settlement_id,
            crl.region_id AS custody_region_id,
            cu.start_year AS custody_start_year,
            cu.expected_release_year AS custody_expected_release_year,
            cu.release_year AS custody_release_year,
            c.source_event_id,
            e.sim_year AS source_event_year,
            e.event_type AS source_event_type,
            c.source_event_key,
            c.details_json,
            c.created_at,
            c.updated_at
        FROM simulation_outlaw_cases c
        LEFT JOIN simulation_people ap ON ap.person_id = c.accused_person_id
        LEFT JOIN simulation_people vp ON vp.person_id = c.victim_person_id
        LEFT JOIN simulation_people tp ON tp.person_id = c.target_person_id
        LEFT JOIN simulation_region_lookup rl ON rl.region_key = c.region_key
        LEFT JOIN simulation_settlement_lookup sl ON sl.settlement_key = c.settlement_key
        LEFT JOIN simulation_outlaw_refuges r ON r.refuge_id = c.refuge_id
        LEFT JOIN simulation_outlaw_custodies cu ON cu.custody_id = c.custody_id
        LEFT JOIN simulation_region_lookup crl ON crl.region_key = cu.region_key
        LEFT JOIN simulation_settlement_lookup csl ON csl.settlement_key = cu.site_settlement_key
        LEFT JOIN simulation_events e ON e.id = c.source_event_id;

        CREATE VIEW IF NOT EXISTS simulation_outlaw_refuges_readable AS
        SELECT
            r.refuge_id,
            r.display_name,
            rl.region_id,
            sl.settlement_id AS near_settlement_id,
            r.status,
            r.founded_year,
            r.discovered_year,
            r.abandoned_year,
            r.band_size,
            r.concealment_01,
            r.support_01,
            r.last_activity_year,
            (
                SELECT COUNT(*)
                FROM simulation_outlaw_cases c
                WHERE c.refuge_id = r.refuge_id AND c.status = 'active'
            ) AS active_case_count,
            r.details_json,
            r.created_at,
            r.updated_at
        FROM simulation_outlaw_refuges r
        LEFT JOIN simulation_region_lookup rl ON rl.region_key = r.region_key
        LEFT JOIN simulation_settlement_lookup sl ON sl.settlement_key = r.near_settlement_key;

        CREATE VIEW IF NOT EXISTS simulation_outlaw_custodies_readable AS
        SELECT
            cu.custody_id,
            cu.case_key,
            cu.person_id,
            trim(coalesce(ap.first_name, '') || ' ' || coalesce(ap.last_name, '')) AS person_name,
            cu.custody_type,
            cu.status,
            sl.settlement_id AS site_settlement_id,
            rl.region_id,
            cu.start_year,
            cu.expected_release_year,
            cu.release_year,
            cu.severity_01,
            c.offense_type,
            c.offense_kind,
            c.resolution,
            c.refuge_id,
            r.display_name AS refuge_display_name,
            cu.details_json,
            cu.created_at,
            cu.updated_at
        FROM simulation_outlaw_custodies cu
        LEFT JOIN simulation_people ap ON ap.person_id = cu.person_id
        LEFT JOIN simulation_outlaw_cases c ON c.case_key = cu.case_key
        LEFT JOIN simulation_outlaw_refuges r ON r.refuge_id = c.refuge_id
        LEFT JOIN simulation_region_lookup rl ON rl.region_key = cu.region_key
        LEFT JOIN simulation_settlement_lookup sl ON sl.settlement_key = cu.site_settlement_key;

        CREATE VIEW IF NOT EXISTS simulation_faction_memory_readable AS
        SELECT
            fm.memory_id,
            fm.source_event_id,
            e.sim_year AS source_event_year,
            e.event_type AS source_event_type,
            fm.memory_key,
            fm.memory_type,
            fm.status,
            fm.faction_a_key,
            fm.faction_b_key,
            fm.principal_person_id,
            fm.opposing_person_id,
            rl.region_id,
            sl.settlement_id,
            fm.polarity,
            fm.strength,
            fm.start_year,
            fm.expected_decay_year,
            fm.resolved_year,
            fm.details_json,
            fm.created_at,
            fm.updated_at
        FROM simulation_faction_memory fm
        JOIN simulation_events e ON e.id = fm.source_event_id
        LEFT JOIN simulation_region_lookup rl ON rl.region_key = fm.region_key
        LEFT JOIN simulation_settlement_lookup sl
            ON sl.settlement_key = fm.settlement_key;

        CREATE VIEW IF NOT EXISTS simulation_institutions_readable AS
        SELECT
            i.institution_id,
            i.institution_key,
            i.institution_type,
            i.status,
            rl.region_id,
            sl.settlement_id,
            i.focus_domain,
            i.founded_year,
            i.latest_year,
            i.founding_event_id,
            fe.event_type AS founding_event_type,
            i.latest_event_id,
            le.event_type AS latest_event_type,
            i.founder_person_id,
            i.patron_person_id,
            i.strength,
            i.influence_score,
            i.details_json,
            i.created_at,
            i.updated_at
        FROM simulation_institutions i
        LEFT JOIN simulation_region_lookup rl ON rl.region_key = i.region_key
        LEFT JOIN simulation_settlement_lookup sl
            ON sl.settlement_key = i.settlement_key
        LEFT JOIN simulation_events fe ON fe.id = i.founding_event_id
        LEFT JOIN simulation_events le ON le.id = i.latest_event_id;

        CREATE VIEW IF NOT EXISTS simulation_innovation_discoveries_readable AS
        SELECT
            d.discovery_id,
            d.source_event_id,
            e.event_type AS source_event_type,
            d.innovation_id,
            d.innovation_name,
            d.category,
            d.domain,
            d.era_id,
            d.discovery_year,
            d.historical_year,
            d.discoverer_person_id,
            d.patron_person_id,
            d.polity_id,
            rl.region_id,
            sl.settlement_id,
            d.novelty_score,
            d.details_json,
            d.created_at
        FROM simulation_innovation_discoveries d
        LEFT JOIN simulation_events e ON e.id = d.source_event_id
        LEFT JOIN simulation_region_lookup rl ON rl.region_key = d.region_key
        LEFT JOIN simulation_settlement_lookup sl
            ON sl.settlement_key = d.settlement_key;

        CREATE VIEW IF NOT EXISTS simulation_innovation_knowledge_readable AS
        SELECT
            k.knowledge_id,
            k.innovation_id,
            k.innovation_name,
            k.category,
            k.domain,
            k.era_id,
            k.scope_kind,
            k.scope_key,
            k.status,
            k.adoption_score,
            k.first_known_year,
            k.latest_known_year,
            k.first_event_id,
            fe.event_type AS first_event_type,
            k.latest_event_id,
            le.event_type AS latest_event_type,
            k.source_kind,
            k.polity_id,
            rl.region_id,
            sl.settlement_id,
            k.details_json,
            k.created_at,
            k.updated_at
        FROM simulation_innovation_knowledge k
        LEFT JOIN simulation_events fe ON fe.id = k.first_event_id
        LEFT JOIN simulation_events le ON le.id = k.latest_event_id
        LEFT JOIN simulation_region_lookup rl ON rl.region_key = k.region_key
        LEFT JOIN simulation_settlement_lookup sl
            ON sl.settlement_key = k.settlement_key;

        CREATE VIEW IF NOT EXISTS simulation_innovation_era_state_readable AS
        SELECT
            s.state_id,
            s.scope_kind,
            s.scope_key,
            s.era_id,
            s.era_rank,
            s.adopted_count,
            s.next_era_adopted_count,
            s.latest_year,
            s.polity_id,
            rl.region_id,
            sl.settlement_id,
            s.created_at,
            s.updated_at
        FROM simulation_innovation_era_state s
        LEFT JOIN simulation_region_lookup rl ON rl.region_key = s.region_key
        LEFT JOIN simulation_settlement_lookup sl
            ON sl.settlement_key = s.settlement_key;

        CREATE VIEW IF NOT EXISTS simulation_people_light_readable AS
        SELECT
            p.person_id,
            p.name,
            p.birthyear,
            p.deathyear,
            p.is_alive,
            p.gender,
            p.species,
            p.ethnic,
            br.region_id AS birthplace_region_id,
            bs.settlement_id AS birthplace_settlement_id,
            cs.settlement_id AS current_settlement_id,
            p.job_family,
            p.partner_person_id,
            p.partner_name,
            p.partner_birthyear,
            p.partner_deathyear,
            p.partnership_start_year,
            p.partnership_end_year,
            p.father_id,
            p.mother_id,
            p.child_count,
            p.child_person_ids_json,
            p.child_birthyears_json,
            p.status_bucket,
            p.prosperity_bucket
        FROM simulation_people_light p
        LEFT JOIN simulation_region_lookup br ON br.region_key = p.birthplace_region_key
        LEFT JOIN simulation_settlement_lookup bs ON bs.settlement_key = p.birthplace_settlement_key
        LEFT JOIN simulation_settlement_lookup cs ON cs.settlement_key = p.current_settlement_key;

        CREATE VIEW IF NOT EXISTS simulation_cohorts_readable AS
        SELECT
            c.cohort_id,
            c.sim_year,
            rl.region_id,
            sl.settlement_id,
            c.age_band,
            c.gender,
            c.species,
            c.culture,
            c.job_family,
            c.status_bucket,
            c.population_count,
            c.birth_count,
            c.death_count
        FROM simulation_cohorts c
        LEFT JOIN simulation_region_lookup rl ON rl.region_key = c.region_key
        LEFT JOIN simulation_settlement_lookup sl ON sl.settlement_key = c.settlement_key;

        CREATE VIEW IF NOT EXISTS simulation_people_nondetailed_readable AS
        SELECT
            p.person_id,
            p.birthyear,
            p.deathyear,
            p.is_alive,
            p.gender,
            p.species_key,
            p.culture_key,
            br.region_id AS birthplace_region_id,
            bs.settlement_id AS birthplace_settlement_id,
            cs.settlement_id AS current_settlement_id,
            p.job_family,
            p.is_partnered,
            p.partner_person_id,
            p.father_id,
            p.mother_id,
            p.child_count,
            p.name_key
        FROM simulation_people_nondetailed p
        LEFT JOIN simulation_region_lookup br ON br.region_key = p.birthplace_region_key
        LEFT JOIN simulation_settlement_lookup bs ON bs.settlement_key = p.birthplace_settlement_key
        LEFT JOIN simulation_settlement_lookup cs ON cs.settlement_key = p.current_settlement_key;
        """
    )


def _resolve_region_display_name_for_checkpoint(
    ctx: "SimulationContext", region_id: str, st: SettlementState
) -> str:
    label = (st.region_display_name or "").strip()
    if label:
        return label
    try:
        return (
            get_region(
                region_id, world=ctx.world.strip(), db_path=ctx.db_path
            ).region_name
            or ""
        ).strip() or region_id
    except LookupError:
        return region_id


def _enrich_settlements_region_display_names(
    ctx: "SimulationContext",
    settlements_by_id: dict[str, SettlementState],
    region_labels: dict[str, str],
) -> dict[str, SettlementState]:
    """Attach region display labels from checkpoint or config geography."""
    w = ctx.world.strip()
    out: dict[str, SettlementState] = {}
    for sid, st in settlements_by_id.items():
        rid = st.region_id
        label = (region_labels.get(rid) or "").strip()
        if not label:
            try:
                label = (
                    get_region(rid, world=w, db_path=ctx.db_path).region_name or ""
                ).strip()
            except LookupError:
                label = ""
        if not label:
            label = rid
        out[sid] = replace(st, region_display_name=label)
    return out


def _row_optional_str(row: sqlite3.Row, key: str) -> str | None:
    if key not in row.keys():
        return None
    v = row[key]
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _aggregate_region_metrics(
    states: list[SettlementState],
) -> tuple[int, int, float, float, float]:
    """Population-weighted averages for stability-like metrics; sums for counts."""
    if not states:
        raise ValueError("no settlements for region aggregate")
    pop = sum(s.resident_count for s in states)
    hh = sum(s.household_cap for s in states)
    n = len(states)
    if pop > 0:
        fp = sum(s.food_pressure * s.resident_count for s in states) / pop
        st = sum(s.stability * s.resident_count for s in states) / pop
        mp = sum(s.market_pull * s.resident_count for s in states) / pop
    else:
        fp = sum(s.food_pressure for s in states) / n
        st = sum(s.stability for s in states) / n
        mp = sum(s.market_pull for s in states) / n
    return pop, hh, fp, st, mp


def _settlement_state_from_db_row(row: sqlite3.Row) -> SettlementState:
    """Hydrate one settlement row (``population_cap`` column stores resident census snapshot)."""
    rid = str(row["region_id"] or "").strip()
    sid = str(row["settlement_id"] or "").strip()
    if not sid:
        sid = make_settlement_id(rid, 1)
    pop = int(row["population_cap"] or 0)
    hh = int(row["household_cap"] or 0)
    fp = float(row["food_pressure"] or 0.0)
    st = float(row["stability"] or 0.0)
    mp = float(row["market_pull"] or 0.0)
    pp = 1.0
    if "prosperity_pool" in row.keys() and row["prosperity_pool"] is not None:
        try:
            pp = float(row["prosperity_pool"])
        except (TypeError, ValueError):
            pp = 1.0
    dn = row["display_name"]
    et = row["etymology"]
    lj = row["local_geography_json"]
    founded = None
    if "founded_sim_year" in row.keys() and row["founded_sim_year"] is not None:
        founded = int(row["founded_sim_year"])
    abandoned = None
    if "abandoned_sim_year" in row.keys() and row["abandoned_sim_year"] is not None:
        abandoned = int(row["abandoned_sim_year"])
    status = "active"
    if "status" in row.keys() and row["status"] is not None:
        status = str(row["status"]).strip() or "active"

    ce = 0
    if "consecutive_empty_years" in row.keys() and row["consecutive_empty_years"] is not None:
        ce = int(row["consecutive_empty_years"])
    site_slot = 1
    if "site_slot" in row.keys() and row["site_slot"] is not None:
        site_slot = max(1, int(row["site_slot"]))
    founding_reason = _row_optional_str(row, "founding_reason") or "organic"
    mother_settlement_id = _row_optional_str(row, "mother_settlement_id")
    trade_network_id = _row_optional_str(row, "trade_network_id") or sid
    autonomy_level = _row_optional_str(row, "autonomy_level") or "autonomous"

    return SettlementState(
        region_id=rid,
        settlement_id=sid,
        level=str(row["level"] or "hamlet"),
        resident_count=pop,
        household_cap=hh,
        food_pressure=fp,
        prosperity_pool=pp,
        stability=st,
        market_pull=mp,
        display_name=str(dn).strip() if dn else None,
        etymology=str(et).strip() if et else None,
        name_category_primary=_row_optional_str(row, "name_category_primary"),
        name_category_secondary=_row_optional_str(row, "name_category_secondary"),
        name_culture_primary=_row_optional_str(row, "name_culture_primary"),
        name_culture_secondary=_row_optional_str(row, "name_culture_secondary"),
        local_geography_json=str(lj).strip() if lj else None,
        founded_sim_year=founded,
        abandoned_sim_year=abandoned,
        status=status,
        consecutive_empty_years=ce,
        site_slot=site_slot,
        founding_reason=founding_reason,
        mother_settlement_id=mother_settlement_id,
        trade_network_id=trade_network_id,
        autonomy_level=autonomy_level,
    )


def ensure_checkpoint_schema_for_file(save_db_path: Path | str) -> None:
    with _open_save(save_db_path) as conn:
        ensure_checkpoint_schema(conn)


def clear_world_checkpoint(save_db_path: Path | str, *, world: str) -> None:
    """Remove checkpoint rows from this single-world save (not ``world_state``)."""
    with _open_save(save_db_path) as conn:
        ensure_checkpoint_schema(conn)
        conn.execute("DELETE FROM simulation_people")
        conn.execute("DELETE FROM simulation_person_archive_scores")
        conn.execute("DELETE FROM simulation_person_archive_score_reasons")
        conn.execute("DELETE FROM simulation_person_almanack_metrics")
        conn.execute("DELETE FROM simulation_person_almanack_cache")
        conn.execute("DELETE FROM simulation_person_almanack_evidence")
        conn.execute("DELETE FROM simulation_people_light")
        conn.execute("DELETE FROM simulation_cohorts")
        conn.execute("DELETE FROM simulation_people_nondetailed")
        conn.execute("DELETE FROM simulation_promotion_log")
        conn.execute("DELETE FROM simulation_patronage_ties")
        conn.execute("DELETE FROM simulation_outlaw_cases")
        conn.execute("DELETE FROM simulation_outlaw_refuges")
        conn.execute("DELETE FROM simulation_outlaw_custodies")
        conn.execute("DELETE FROM simulation_serial_predation_candidates")
        conn.execute("DELETE FROM simulation_settlements")
        conn.execute("DELETE FROM simulation_regions")
        conn.execute("DELETE FROM simulation_couples")
        conn.execute("DELETE FROM simulation_paramours")
        conn.execute("DELETE FROM simulation_meta")
        conn.execute("DELETE FROM simulation_event_records")
        conn.execute("DELETE FROM simulation_event_moves")
        conn.execute("DELETE FROM simulation_event_people")
        conn.execute("DELETE FROM simulation_domain_states")
        conn.execute("DELETE FROM simulation_domain_diffusion")
        conn.execute("DELETE FROM simulation_obligations")
        conn.execute("DELETE FROM simulation_reputation_marks")
        conn.execute("DELETE FROM simulation_legal_fallout")
        conn.execute("DELETE FROM simulation_legal_adjudications")
        conn.execute("DELETE FROM simulation_faction_memory")
        conn.execute("DELETE FROM simulation_institutions")
        conn.execute("DELETE FROM simulation_innovation_discoveries")
        conn.execute("DELETE FROM simulation_innovation_knowledge")
        conn.execute("DELETE FROM simulation_innovation_era_state")
        conn.execute("DELETE FROM simulation_events")
        from library import government_checkpoint as _gov_ckpt

        _gov_ckpt.clear_government_tables(conn, world=world)
        conn.commit()


RUN_STORE_EVENTS_IMPORTED_META_KEY = "run_store_events_imported"

# JSON object: region_id -> float multiplier (see SimulationContext.region_effective_cap_multiplier).
REGION_EFFECTIVE_CAP_MULTIPLIER_META_KEY = "region_effective_cap_multiplier_json"

# JSON object: region_id -> display label (see SimulationContext.region_display_label_overrides).
REGION_DISPLAY_LABEL_OVERRIDES_META_KEY = "region_display_label_overrides_json"

# JSON object with keys ``label_source`` and ``miss_streak`` (region_id -> str/int).
REGION_NAMING_AUX_META_KEY = "region_naming_aux_json"

# JSON ``{"pending": {region_id: int}, "last": {region_id: int}}`` for settlement spinoff.
SETTLEMENT_SPINOFF_STATE_META_KEY = "settlement_spinoff_state_json"

# JSON list of deferred year-boundary residence moves.
PENDING_SETTLEMENT_MOVES_META_KEY = "pending_settlement_moves_json"

# Save-scoped seed for generated physical world-map geometry.
WORLD_MAP_SEED_META_KEY = "world_map_seed"


def read_world_map_seed(save_db_path: Path | str) -> str | None:
    """Return the persisted physical-map seed for a save, if present."""
    p = Path(save_db_path)
    if not p.exists():
        return None
    with _open_save(p) as conn:
        ensure_checkpoint_schema(conn)
        row = conn.execute(
            """
            SELECT meta_value FROM simulation_meta
            WHERE meta_key = ?
            """,
            (WORLD_MAP_SEED_META_KEY,),
        ).fetchone()
    if row is None or row["meta_value"] is None:
        return None
    seed = str(row["meta_value"]).strip()
    return seed or None


def write_world_map_seed(save_db_path: Path | str, map_seed: object) -> str:
    """Persist the physical-map seed for this save and return its string form."""
    seed = str(map_seed).strip()
    if not seed:
        raise ValueError("world map seed must not be blank")
    with _open_save(save_db_path) as conn:
        ensure_checkpoint_schema(conn)
        conn.execute(
            """
            INSERT OR REPLACE INTO simulation_meta (meta_key, meta_value)
            VALUES (?, ?)
            """,
            (WORLD_MAP_SEED_META_KEY, seed),
        )
        conn.commit()
    return seed


def _ctx_world_map_seed(ctx: "SimulationContext") -> str:
    seed = str(getattr(ctx, "world_map_seed", "") or "").strip()
    if seed:
        return seed
    salt = str(getattr(ctx, "placename_rng_salt", "") or "").strip()
    if salt and salt != "0":
        return salt
    return str(getattr(ctx, "world", "") or "default")


def serialize_region_display_label_overrides(ctx: "SimulationContext") -> str:
    d = {
        str(k).strip(): str(v).strip()
        for k, v in (getattr(ctx, "region_display_label_overrides", None) or {}).items()
        if str(k).strip() and str(v).strip()
    }
    return json.dumps(d, sort_keys=True)


def parse_region_display_label_overrides(meta_value: str | None) -> dict[str, str]:
    if meta_value is None or not str(meta_value).strip():
        return {}
    try:
        raw = json.loads(meta_value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        ks = str(k).strip()
        if not ks:
            continue
        out[ks] = str(v).strip()
    return out


def serialize_region_naming_aux(ctx: "SimulationContext") -> str:
    src = {
        str(k).strip(): str(v).strip()
        for k, v in (getattr(ctx, "region_label_source", None) or {}).items()
        if str(k).strip() and str(v).strip()
    }
    miss = {
        str(k).strip(): int(v)
        for k, v in (getattr(ctx, "region_city_rename_miss_streak", None) or {}).items()
        if str(k).strip()
    }
    return json.dumps({"label_source": src, "miss_streak": miss}, sort_keys=True)


def parse_region_naming_aux(meta_value: str | None) -> tuple[dict[str, str], dict[str, int]]:
    if meta_value is None or not str(meta_value).strip():
        return {}, {}
    try:
        raw = json.loads(meta_value)
    except json.JSONDecodeError:
        return {}, {}
    if not isinstance(raw, dict):
        return {}, {}
    src_raw = raw.get("label_source") or {}
    miss_raw = raw.get("miss_streak") or {}
    src: dict[str, str] = {}
    if isinstance(src_raw, dict):
        for k, v in src_raw.items():
            ks = str(k).strip()
            if ks:
                src[ks] = str(v).strip()
    miss: dict[str, int] = {}
    if isinstance(miss_raw, dict):
        for k, v in miss_raw.items():
            ks = str(k).strip()
            if not ks:
                continue
            try:
                miss[ks] = int(v)
            except (TypeError, ValueError):
                continue
    return src, miss


def serialize_region_effective_cap_multipliers(ctx: "SimulationContext") -> str:
    d = {
        str(k).strip(): float(v)
        for k, v in (getattr(ctx, "region_effective_cap_multiplier", None) or {}).items()
        if str(k).strip()
    }
    return json.dumps(d, sort_keys=True)


def parse_region_effective_cap_multipliers(meta_value: str | None) -> dict[str, float]:
    if meta_value is None or not str(meta_value).strip():
        return {}
    try:
        raw = json.loads(meta_value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in raw.items():
        ks = str(k).strip()
        if not ks:
            continue
        try:
            out[ks] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def serialize_settlement_spinoff_state(ctx: "SimulationContext") -> str:
    pending = {
        str(k).strip(): int(v)
        for k, v in (getattr(ctx, "spinoff_pending_families_by_region", None) or {}).items()
        if str(k).strip()
    }
    last_y = {
        str(k).strip(): int(v)
        for k, v in (getattr(ctx, "last_spinoff_sim_year_by_region", None) or {}).items()
        if str(k).strip()
    }
    return json.dumps({"pending": pending, "last": last_y}, sort_keys=True)


def parse_settlement_spinoff_state(
    meta_value: str | None,
) -> tuple[dict[str, int], dict[str, int]]:
    if meta_value is None or not str(meta_value).strip():
        return {}, {}
    try:
        raw = json.loads(meta_value)
    except json.JSONDecodeError:
        return {}, {}
    if not isinstance(raw, dict):
        return {}, {}
    out_p: dict[str, int] = {}
    out_l: dict[str, int] = {}
    pr = raw.get("pending")
    if isinstance(pr, dict):
        for k, v in pr.items():
            ks = str(k).strip()
            if not ks:
                continue
            try:
                out_p[ks] = int(v)
            except (TypeError, ValueError):
                continue
    lr = raw.get("last")
    if isinstance(lr, dict):
        for k, v in lr.items():
            ks = str(k).strip()
            if not ks:
                continue
            try:
                out_l[ks] = int(v)
            except (TypeError, ValueError):
                continue
    return out_p, out_l


def serialize_pending_settlement_moves(ctx: "SimulationContext") -> str:
    moves = []
    for m in getattr(ctx, "pending_settlement_moves", None) or []:
        moves.append(
            {
                "person_id": int(m.person_id),
                "to_settlement_id": str(m.to_settlement_id or "").strip(),
                "move_reason": str(m.move_reason or "").strip(),
                "requested_year": int(m.requested_year),
                "apply_year": int(m.apply_year),
                "from_settlement_id": (
                    str(m.from_settlement_id).strip()
                    if m.from_settlement_id is not None
                    else None
                ),
                "source_event": (
                    str(m.source_event).strip() if m.source_event is not None else None
                ),
                "group_id": str(m.group_id).strip() if m.group_id is not None else None,
            }
        )
    return json.dumps(moves, sort_keys=True)


def parse_pending_settlement_moves(meta_value: str | None) -> list["PendingSettlementMove"]:
    if meta_value is None or not str(meta_value).strip():
        return []
    try:
        raw = json.loads(meta_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    from library.simulation_context import PendingSettlementMove

    out: list[PendingSettlementMove] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            person_id = int(item.get("person_id"))
            to_sid = str(item.get("to_settlement_id") or "").strip()
            req_y = int(item.get("requested_year"))
            app_y = int(item.get("apply_year"))
        except (TypeError, ValueError):
            continue
        if person_id <= 0 or not to_sid:
            continue
        reason = str(item.get("move_reason") or "").strip() or "deferred_settlement_move"
        from_sid_raw = item.get("from_settlement_id")
        src_raw = item.get("source_event")
        group_raw = item.get("group_id")
        out.append(
            PendingSettlementMove(
                person_id=person_id,
                to_settlement_id=to_sid,
                move_reason=reason,
                requested_year=req_y,
                apply_year=app_y,
                from_settlement_id=(
                    str(from_sid_raw).strip() if from_sid_raw is not None else None
                ),
                source_event=str(src_raw).strip() if src_raw is not None else None,
                group_id=str(group_raw).strip() if group_raw is not None else None,
            )
        )
    return out


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def append_simulation_event_rows(
    conn: sqlite3.Connection,
    world: str,
    rows: list[tuple[int | None, str, dict]],
    *,
    created_at: str | None = None,
    verbose_payloads: bool = False,
) -> list[int]:
    """Insert append-only simulation event rows and return inserted event IDs."""
    if not rows:
        return []
    ts = created_at or _utc_now_iso()
    cur = conn.cursor()
    place_cache = _EventPlaceKeyCache(conn)
    prepared_rows: list[
        tuple[
            int | None,
            str,
            dict,
            list[tuple[int, str]],
            int | None,
            int | None,
            int | None,
            int | None,
            str,
        ]
    ] = []
    event_insert_rows: list[
        tuple[
            int | None,
            str,
            int | None,
            int | None,
            int | None,
            int | None,
            str,
            str,
            str,
        ]
    ] = []
    for sim_year, event_type, payload in rows:
        links = _event_person_links_from_payload(payload, event_type=event_type)
        primary, secondary, settlement_id, region_id = _event_common_columns_from_links(
            payload,
            links,
        )
        settlement_key = place_cache.settlement_key(settlement_id, region_id)
        region_key = place_cache.region_key(region_id)
        event_origin = _event_origin_from_payload(payload)
        stored_payload = (
            dict(payload)
            if verbose_payloads
            else _compact_event_payload(event_type, payload)
        )
        prepared_rows.append(
            (
                sim_year,
                event_type,
                payload,
                links,
                primary,
                secondary,
                settlement_key,
                region_key,
                event_origin,
            )
        )
        event_insert_rows.append(
            (
                sim_year,
                event_type,
                primary,
                secondary,
                settlement_key,
                region_key,
                event_origin,
                json.dumps(stored_payload, separators=(",", ":")),
                ts,
            )
        )

    cur.executemany(
        """
        INSERT INTO simulation_events (
            sim_year, event_type, primary_person_id, secondary_person_id,
            settlement_key, region_key, event_origin, payload_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        event_insert_rows,
    )
    last_id_row = conn.execute("SELECT last_insert_rowid()").fetchone()
    last_event_id = int(
        last_id_row[0] if not isinstance(last_id_row, sqlite3.Row) else last_id_row[0]
    )
    first_event_id = last_event_id - len(event_insert_rows) + 1
    event_ids = list(range(first_event_id, last_event_id + 1))

    event_people_rows: list[tuple[int, int, str]] = []
    default_record_rows: list[
        tuple[
            int,
            str,
            str,
            int | None,
            float,
            int | None,
            int | None,
            int | None,
            int | None,
            str,
            str,
            str,
        ]
    ] = []
    public_stage_rows: list[tuple[int, str, str, dict]] = []
    for (
        event_id,
        (
            sim_year,
            event_type,
            payload,
            links,
            primary,
            secondary,
            settlement_key,
            region_key,
            event_origin,
        ),
    ) in zip(event_ids, prepared_rows):
        for person_id, role in links:
            event_people_rows.append((int(event_id), int(person_id), role))
        record_type, visibility_state, confidence = _event_record_kind_for_type(
            event_type,
            event_origin,
        )
        public_actor, public_victim = _event_record_public_people(
            event_type,
            primary,
            secondary,
            payload,
        )
        default_record_rows.append(
            (
                int(event_id),
                record_type,
                visibility_state,
                sim_year,
                float(confidence),
                settlement_key,
                region_key,
                public_actor,
                public_victim,
                f"{record_type}.{visibility_state}.default",
                ts,
                ts,
            )
        )
        event_type_s = str(event_type or "").strip()
        if event_origin not in {"inferred", "backfilled"} and (
            event_type_s in _DEFAULT_PUBLIC_STAGE_EVENT_TYPES
            or event_type_s.startswith("city_state_")
        ):
            public_stage_rows.append((int(event_id), event_type, event_origin, payload))

    if event_people_rows:
        cur.executemany(
            """
            INSERT OR IGNORE INTO simulation_event_people (event_id, person_id, role)
            VALUES (?, ?, ?)
            """,
            event_people_rows,
        )
    if default_record_rows:
        cur.executemany(
            """
            INSERT OR IGNORE INTO simulation_event_records (
                event_id, record_key, record_type, visibility_state,
                known_since_year, confidence, preserving_settlement_key,
                preserving_region_key, public_actor_person_id,
                public_victim_person_id, prose_variant_key, created_at, updated_at
            )
            VALUES (?, 'default', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            default_record_rows,
        )
    for event_id, event_type, event_origin, payload in public_stage_rows:
        _insert_default_public_stage_record_rows(
            conn,
            event_id=event_id,
            event_type=event_type,
            event_origin=event_origin,
            payload=payload,
        )

    for (
        event_id,
        (
            sim_year,
            event_type,
            payload,
            _links,
            primary,
            secondary,
            settlement_key,
            region_key,
            event_origin,
        ),
    ) in zip(event_ids, prepared_rows):
        _insert_simulation_event_move_rows(
            conn,
            event_id=event_id,
            event_type=event_type,
            payload=payload,
            place_cache=place_cache,
        )
        _upsert_simulation_domain_state_from_event(
            conn,
            event_id=event_id,
            sim_year=sim_year,
            event_type=event_type,
            payload=payload,
            settlement_key=settlement_key,
            region_key=region_key,
            created_at=ts,
            place_cache=place_cache,
        )
        _insert_simulation_obligation_rows(
            conn,
            event_id=event_id,
            sim_year=sim_year,
            event_type=event_type,
            payload=payload,
            settlement_key=settlement_key,
            region_key=region_key,
            created_at=ts,
            place_cache=place_cache,
        )
        _insert_simulation_reputation_mark_rows(
            conn,
            event_id=event_id,
            sim_year=sim_year,
            event_type=event_type,
            payload=payload,
            settlement_key=settlement_key,
            region_key=region_key,
            created_at=ts,
            place_cache=place_cache,
        )
        _insert_simulation_legal_fallout_rows(
            conn,
            event_id=event_id,
            sim_year=sim_year,
            event_type=event_type,
            payload=payload,
            settlement_key=settlement_key,
            region_key=region_key,
            created_at=ts,
            place_cache=place_cache,
        )
        _insert_simulation_faction_memory_rows(
            conn,
            event_id=event_id,
            sim_year=sim_year,
            event_type=event_type,
            payload=payload,
            settlement_key=settlement_key,
            region_key=region_key,
            created_at=ts,
            place_cache=place_cache,
        )
        _upsert_simulation_institution_rows(
            conn,
            event_id=event_id,
            sim_year=sim_year,
            event_type=event_type,
            payload=payload,
            settlement_key=settlement_key,
            region_key=region_key,
            created_at=ts,
            place_cache=place_cache,
        )
        _upsert_simulation_innovation_rows_from_event(
            conn,
            event_id=event_id,
            sim_year=sim_year,
            event_type=event_type,
            payload=payload,
            settlement_key=settlement_key,
            region_key=region_key,
            created_at=ts,
            place_cache=place_cache,
        )
    if event_ids:
        _set_domain_states_processed_event_id(conn, max(event_ids))
        _set_obligations_processed_event_id(conn, max(event_ids))
        _set_reputation_marks_processed_event_id(conn, max(event_ids))
        _set_legal_fallout_processed_event_id(conn, max(event_ids))
        _set_processed_event_id_for_meta(
            conn, FACTION_MEMORY_BACKFILLED_META_KEY, max(event_ids)
        )
        _set_processed_event_id_for_meta(
            conn, INSTITUTIONS_BACKFILLED_META_KEY, max(event_ids)
        )
        _set_processed_event_id_for_meta(
            conn, INNOVATIONS_BACKFILLED_META_KEY, max(event_ids)
        )
    return event_ids


def _compact_event_payload(event_type: str, payload: dict) -> dict:
    """Drop place slugs duplicated by normalized event columns for normal runs."""
    drop_keys: set[str] = set(_EVENT_SETTLEMENT_KEYS)
    drop_keys.update(_EVENT_REGION_KEYS)
    drop_keys.update(_EVENT_PAYLOAD_META_KEYS)
    if str(event_type or "").strip() == "job_seeker_migration":
        drop_keys.difference_update(
            {
                "from_settlement_id",
                "to_settlement_id",
                "from_region_id",
                "to_region_id",
            }
        )
    if str(event_type or "").strip() == "settlement_moved":
        drop_keys.update(_EVENT_MOVE_DETAIL_KEYS)
    return {
        str(k): v
        for k, v in payload.items()
        if str(k) not in drop_keys
    }


def flush_pending_simulation_events(ctx: "SimulationContext") -> None:
    """Persist buffered domain events from ``ctx`` to ``save.sqlite``."""
    pending = ctx._pending_simulation_events
    if not pending:
        return
    year = int(ctx.current_year if ctx.current_year is not None else ctx.simulation_start_year)
    simulation_timing.record_gauge(
        year,
        "checkpoint",
        "flushed_events",
        len(pending),
    )
    t0 = _profile_t0(year)
    with _open_save(ctx.save_db_path) as conn:
        ensure_checkpoint_schema(conn)
        append_simulation_event_rows(
            conn,
            ctx.world.strip(),
            pending,
            verbose_payloads=bool(getattr(ctx, "verbose_event_logging", False)),
        )
        conn.commit()
    _profile_accumulate("checkpoint.flush_events", t0)
    pending.clear()


def flush_simulation_meta_checkpoint(ctx: "SimulationContext") -> None:
    """Persist ``next_person_id``, cap-multiplier JSON, spinoff state, etc. without full snapshot.

    Used when ``checkpoint_simulation_to_save(..., full_snapshot=False)`` so runs that
    flush events between sparse full snapshots still save resume-relevant meta.
    """
    t0 = _profile_t0(
        int(ctx.current_year if ctx.current_year is not None else ctx.simulation_start_year)
    )
    with _open_save(ctx.save_db_path) as conn:
        ensure_checkpoint_schema(conn)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO simulation_meta (meta_key, meta_value)
            VALUES (?, ?)
            """,
            ("next_person_id", str(ctx.next_person_id)),
        )
        cur.execute(
            """
            INSERT OR REPLACE INTO simulation_meta (meta_key, meta_value)
            VALUES (?, ?)
            """,
            (
                REGION_EFFECTIVE_CAP_MULTIPLIER_META_KEY,
                serialize_region_effective_cap_multipliers(ctx),
            ),
        )
        cur.execute(
            """
            INSERT OR REPLACE INTO simulation_meta (meta_key, meta_value)
            VALUES (?, ?)
            """,
            (
                REGION_DISPLAY_LABEL_OVERRIDES_META_KEY,
                serialize_region_display_label_overrides(ctx),
            ),
        )
        cur.execute(
            """
            INSERT OR REPLACE INTO simulation_meta (meta_key, meta_value)
            VALUES (?, ?)
            """,
            (REGION_NAMING_AUX_META_KEY, serialize_region_naming_aux(ctx)),
        )
        cur.execute(
            """
            INSERT OR REPLACE INTO simulation_meta (meta_key, meta_value)
            VALUES (?, ?)
            """,
            (SETTLEMENT_SPINOFF_STATE_META_KEY, serialize_settlement_spinoff_state(ctx)),
        )
        cur.execute(
            """
            INSERT OR REPLACE INTO simulation_meta (meta_key, meta_value)
            VALUES (?, ?)
            """,
            (
                PENDING_SETTLEMENT_MOVES_META_KEY,
                serialize_pending_settlement_moves(ctx),
            ),
        )
        cur.execute(
            """
            INSERT OR REPLACE INTO simulation_meta (meta_key, meta_value)
            VALUES (?, ?)
            """,
            (WORLD_MAP_SEED_META_KEY, _ctx_world_map_seed(ctx)),
        )
        _flush_passive_promotion_log_entries(
            conn, getattr(ctx, "passive_promotion_log", ())
        )
        _sync_outlaw_state(conn, ctx)
        _sync_serial_predation_candidates(conn, ctx)
        conn.commit()
    _profile_accumulate("checkpoint.meta_only", t0)


def maybe_import_run_store_events_csv(ctx: "SimulationContext") -> None:
    """Import ``events.csv`` only when no live rows exist (legacy CSV-only staging)."""
    store = ctx.file_store
    if store is None:
        return
    path = store.root_dir / "events.csv"
    if not path.is_file():
        return
    with _open_save(ctx.save_db_path) as conn:
        ensure_checkpoint_schema(conn)
        cnt_row = conn.execute(
            "SELECT COUNT(*) AS c FROM simulation_events",
        ).fetchone()
        if cnt_row is not None and int(cnt_row["c"] or 0) > 0:
            return
        row = conn.execute(
            """
            SELECT meta_value FROM simulation_meta
            WHERE meta_key = ?
            """,
            (RUN_STORE_EVENTS_IMPORTED_META_KEY,),
        ).fetchone()
        if row is not None and str(row["meta_value"] or "").strip() == "1":
            return
        imported: list[tuple[int | None, str, dict]] = []
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                payload = {k: r.get(k, "") for k in r}
                y_raw = str(r.get("year") or "").strip()
                sim_year = int(y_raw) if y_raw.isdigit() else None
                et = str(r.get("event_type") or "").strip() or "unknown"
                imported.append((sim_year, et, payload))
        if imported:
            append_simulation_event_rows(conn, ctx.world.strip(), imported)
        conn.execute(
            """
            INSERT OR REPLACE INTO simulation_meta (meta_key, meta_value)
            VALUES (?, ?)
            """,
            (RUN_STORE_EVENTS_IMPORTED_META_KEY, "1"),
        )
        conn.commit()


def _parse_genome_composite_names(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple)):
        return tuple(s for x in raw if (s := str(x).strip()))
    return ()


def _parse_genome_composite_scores(raw: object) -> dict[str, float]:
    if raw is None or not isinstance(raw, dict):
        return {}
    scores: dict[str, float] = {}
    for key, value in raw.items():
        rid = str(key).strip()
        if not rid:
            continue
        try:
            score = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(score):
            continue
        scores[rid] = max(0.0, score)
    return scores


def _parse_person_id_tuple(raw: object) -> tuple[int, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        return ()
    out: list[int] = []
    seen: set[int] = set()
    for value in raw:
        try:
            pid = int(value)
        except (TypeError, ValueError):
            continue
        if pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
    return tuple(out)


def _parse_genome_trait_phrases(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple)):
        return tuple(s for x in raw if (s := str(x).strip()))
    return ()


def _trait_slots_from_config(config_db_path: Path | str) -> tuple[str, ...]:
    """Return compact save slot -> trait order from config.

    ``config/genome_save_columns.csv`` is the normal source. Tiny test fixtures
    may not include it, so they fall back to the ``genome`` table order.
    """
    path = Path(config_db_path)
    if not path.exists():
        return ()
    try:
        with closing(sqlite3.connect(path)) as conn:
            conn.row_factory = sqlite3.Row
            has_map = conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='genome_save_columns'
                """
            ).fetchone()
            if has_map is not None:
                rows = conn.execute(
                    """
                    SELECT trait
                    FROM genome_save_columns
                    WHERE trait IS NOT NULL AND trim(trait) <> ''
                    ORDER BY CAST(sort_order AS INTEGER), slot
                    """
                ).fetchall()
                traits = tuple(str(r["trait"]).strip() for r in rows if str(r["trait"]).strip())
                if traits:
                    return traits
            has_genome = conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='genome'
                """
            ).fetchone()
            if has_genome is not None:
                rows = conn.execute(
                    """
                    SELECT trait
                    FROM genome
                    WHERE trait IS NOT NULL AND trim(trait) <> ''
                    ORDER BY rowid
                    """
                ).fetchall()
                return tuple(str(r["trait"]).strip() for r in rows if str(r["trait"]).strip())
    except sqlite3.Error:
        return ()
    return ()


def _trait_slots_for_checkpoint(ctx: "SimulationContext") -> tuple[str, ...]:
    slots = _trait_slots_from_config(ctx.db_path)
    if slots:
        known = set(slots)
        extra: set[str] = set()
        for rec in getattr(ctx, "people", []):
            extra.update(
                str(k) for k in (rec.person.genome or {}).keys() if str(k) not in known
            )
            extra.update(
                str(k)
                for k in (rec.person.mind_body or {}).keys()
                if str(k) not in known
            )
        if extra:
            names = ", ".join(sorted(extra))
            raise RuntimeError(
                "genome_save_columns is missing trait slot mappings for: "
                f"{names}"
            )
        return slots
    traits: set[str] = set()
    for rec in getattr(ctx, "people", []):
        traits.update(str(k) for k in (rec.person.genome or {}).keys())
        traits.update(str(k) for k in (rec.person.mind_body or {}).keys())
    return tuple(sorted(traits))


def _encode_trait_array(traits: dict[str, float], trait_slots: tuple[str, ...]) -> list[float | None]:
    encoded: list[float | None] = []
    for trait in trait_slots:
        raw = traits.get(trait)
        encoded.append(None if raw is None else float(raw))
    return encoded


def _decode_trait_array(
    values: object,
    trait_slots: tuple[str, ...],
) -> dict[str, float]:
    if not isinstance(values, list):
        return {}
    out: dict[str, float] = {}
    for trait, raw in zip(trait_slots, values):
        if raw is None:
            continue
        try:
            out[str(trait)] = float(raw)
        except (TypeError, ValueError):
            continue
    return out


def _person_checkpoint_payload(
    person: Person,
    trait_slots: tuple[str, ...],
    *,
    include_trait_slots: bool,
    conn: sqlite3.Connection | None = None,
) -> tuple[dict[str, object], str]:
    """Split a person into scalar columns plus compact extension JSON."""
    raw = asdict(person)
    cols = {k: raw.get(k) for k in _PERSON_CHECKPOINT_COLUMNS}
    if conn is not None:
        cols["birthplace_region_key"] = _lookup_or_insert_region_key(
            conn, raw.get("birthplace_region_id")
        )
        cols["birthplace_settlement_key"] = _lookup_or_insert_settlement_key(
            conn, raw.get("birthplace_settlement_id"), raw.get("birthplace_region_id")
        )
        current_sid = raw.get("current_settlement_id")
        current_rid = raw.get("birthplace_region_id")
        cols["current_settlement_key"] = _lookup_or_insert_settlement_key(
            conn, current_sid, current_rid
        )
    ext = {k: raw.get(k) for k in _PERSON_EXTENSION_KEYS if raw.get(k) not in (None, {}, (), [])}
    ext["v"] = 2
    if include_trait_slots:
        ext["ts"] = list(trait_slots)
    genome = person.genome or {}
    if genome:
        ext["g"] = _encode_trait_array(genome, trait_slots)
    mind_body = person.mind_body or {}
    if mind_body and mind_body != genome:
        ext["mb"] = _encode_trait_array(mind_body, trait_slots)
    return cols, json.dumps(ext, separators=(",", ":"))


def _person_dict_from_checkpoint_row(
    row: sqlite3.Row,
    trait_slots: tuple[str, ...],
    *,
    region_ids_by_key: dict[int, str] | None = None,
    settlement_ids_by_key: dict[int, str] | None = None,
) -> dict[str, object]:
    d: dict[str, object] = {}
    raw_payload = row["person_json"] if "person_json" in row.keys() else None
    if raw_payload:
        loaded = json.loads(raw_payload)
        if isinstance(loaded, dict):
            d.update(loaded)
            row_slots = tuple(str(x) for x in loaded.get("ts", []) if str(x).strip())
            slots = row_slots or trait_slots
            if "g" in loaded and "genome" not in loaded:
                d["genome"] = _decode_trait_array(loaded.get("g"), slots)
            if "mb" in loaded and "mind_body" not in loaded:
                d["mind_body"] = _decode_trait_array(loaded.get("mb"), slots)
    for key in _PERSON_CHECKPOINT_COLUMNS:
        if key in row.keys():
            d[key] = row[key]
    region_map = region_ids_by_key or {}
    settlement_map = settlement_ids_by_key or {}
    if "birthplace_region_key" in d:
        key = d.pop("birthplace_region_key")
        d["birthplace_region_id"] = (
            region_map.get(int(key)) if key is not None else None
        )
    if "birthplace_settlement_key" in d:
        key = d.pop("birthplace_settlement_key")
        d["birthplace_settlement_id"] = (
            settlement_map.get(int(key)) if key is not None else None
        )
    if "current_settlement_key" in d:
        key = d.pop("current_settlement_key")
        d["current_settlement_id"] = (
            settlement_map.get(int(key)) if key is not None else None
        )
    return d


def _person_from_dict(d: dict) -> Person:
    genome_raw = d.get("genome") or {}
    genome = {str(k): float(v) for k, v in genome_raw.items()} if genome_raw else {}
    mb_raw = d.get("mind_body") or {}
    mind_body = (
        {str(k): float(v) for k, v in mb_raw.items()} if mb_raw else {}
    )
    if not mind_body and genome:
        mind_body = mind_body_from_genome(genome)
    att_01 = (
        float(d["attractiveness_01"])
        if d.get("attractiveness_01") is not None
        else None
    )
    p = Person(
        first_name=str(d.get("first_name") or ""),
        last_name=str(d.get("last_name") or ""),
        gender=str(d.get("gender") or ""),
        ethnic=str(d.get("ethnic") or ""),
        species=str(d.get("species") or ""),
        birthyear=int(d.get("birthyear") or 0),
        deathyear=(
            None
            if d.get("deathyear") is None or str(d.get("deathyear")).strip() == ""
            else int(d["deathyear"])
        ),
        birthplace=str(d.get("birthplace") or "Placeholder"),
        birthplace_region_id=(
            str(d["birthplace_region_id"])
            if d.get("birthplace_region_id") is not None
            else None
        ),
        birthplace_settlement_id=(
            str(d["birthplace_settlement_id"])
            if d.get("birthplace_settlement_id") is not None
            else None
        ),
        life_stage=(
            str(d["life_stage"]) if d.get("life_stage") is not None else None
        ),
        maturity_height_cm=(
            float(d["maturity_height_cm"])
            if d.get("maturity_height_cm") is not None
            else None
        ),
        maturity_weight_kg=(
            float(d["maturity_weight_kg"])
            if d.get("maturity_weight_kg") is not None
            else None
        ),
        skin_tone=str(d["skin_tone"]) if d.get("skin_tone") is not None else None,
        hair=str(d["hair"]) if d.get("hair") is not None else None,
        eyes=str(d["eyes"]) if d.get("eyes") is not None else None,
        min_fertility_age=(
            int(d["min_fertility_age"])
            if d.get("min_fertility_age") is not None
            else None
        ),
        max_fertility_age=(
            int(d["max_fertility_age"])
            if d.get("max_fertility_age") is not None
            else None
        ),
        genome=genome,
        mind_body=mind_body,
        attractiveness_01=att_01,
        sexual_nature=(
            str(d["sexual_nature"]).lower()
            if d.get("sexual_nature") is not None
            else None
        ),
        gender_mind=(
            str(d["gender_mind"]).lower()
            if d.get("gender_mind") is not None
            else None
        ),
        father_name=(
            str(d["father_name"]) if d.get("father_name") is not None else None
        ),
        mother_name=(
            str(d["mother_name"]) if d.get("mother_name") is not None else None
        ),
        current_settlement_id=(
            str(d["current_settlement_id"])
            if d.get("current_settlement_id") is not None
            else None
        ),
        partner_person_id=(
            int(d["partner_person_id"])
            if d.get("partner_person_id") is not None
            else None
        ),
        paramour_person_id=(
            int(d["paramour_person_id"])
            if d.get("paramour_person_id") is not None
            else None
        ),
        paramour_person_ids=_parse_person_id_tuple(d.get("paramour_person_ids")),
        last_birth_event_year=(
            int(d["last_birth_event_year"])
            if d.get("last_birth_event_year") is not None
            else None
        ),
        birth_relationship_type=(
            str(d["birth_relationship_type"])
            if d.get("birth_relationship_type") is not None
            else None
        ),
        born_out_of_wedlock=(
            _coerce_event_bool(d["born_out_of_wedlock"])
            if d.get("born_out_of_wedlock") is not None
            else None
        ),
        legitimacy_status=(
            str(d["legitimacy_status"])
            if d.get("legitimacy_status") is not None
            else None
        ),
        job=str(d["job"]) if d.get("job") is not None else None,
        job_assigned_year=(
            int(d["job_assigned_year"])
            if d.get("job_assigned_year") is not None
            else None
        ),
        job_era=str(d["job_era"]) if d.get("job_era") is not None else None,
        job_tier=str(d["job_tier"]) if d.get("job_tier") is not None else None,
        job_market_type=(
            str(d["job_market_type"])
            if d.get("job_market_type") is not None
            else None
        ),
        housing_status=(
            str(d["housing_status"])
            if d.get("housing_status") is not None
            else None
        ),
        household_role=(
            str(d["household_role"])
            if d.get("household_role") is not None
            else None
        ),
        host_person_id=(
            int(d["host_person_id"])
            if d.get("host_person_id") is not None
            else None
        ),
        employer_person_id=(
            int(d["employer_person_id"])
            if d.get("employer_person_id") is not None
            else None
        ),
        social_class_band=(
            str(d["social_class_band"])
            if d.get("social_class_band") is not None
            else None
        ),
        social_standing_01=(
            float(d["social_standing_01"])
            if d.get("social_standing_01") is not None
            else None
        ),
        societal_impact_01=(
            float(d["societal_impact_01"])
            if d.get("societal_impact_01") is not None
            else None
        ),
        perceived_worth_01=(
            float(d["perceived_worth_01"])
            if d.get("perceived_worth_01") is not None
            else None
        ),
        status_tendency=(
            str(d["status_tendency"])
            if d.get("status_tendency") is not None
            else None
        ),
        leader_quality=(
            str(d["leader_quality"])
            if d.get("leader_quality") is not None
            else None
        ),
        leader_tendency=(
            str(d["leader_tendency"])
            if d.get("leader_tendency") is not None
            else None
        ),
        outlaw_status=(
            str(d["outlaw_status"])
            if d.get("outlaw_status") is not None
            else None
        ),
        outlaw_case_key=(
            str(d["outlaw_case_key"])
            if d.get("outlaw_case_key") is not None
            else None
        ),
        outlaw_refuge_id=(
            str(d["outlaw_refuge_id"])
            if d.get("outlaw_refuge_id") is not None
            else None
        ),
        outlaw_since_year=(
            int(d["outlaw_since_year"])
            if d.get("outlaw_since_year") is not None
            else None
        ),
        last_free_settlement_id=(
            str(d["last_free_settlement_id"])
            if d.get("last_free_settlement_id") is not None
            else None
        ),
        outlaw_custody_id=(
            str(d["outlaw_custody_id"])
            if d.get("outlaw_custody_id") is not None
            else None
        ),
        outlaw_custody_status=(
            str(d["outlaw_custody_status"])
            if d.get("outlaw_custody_status") is not None
            else None
        ),
        outlaw_custody_start_year=(
            int(d["outlaw_custody_start_year"])
            if d.get("outlaw_custody_start_year") is not None
            else None
        ),
        outlaw_custody_expected_release_year=(
            int(d["outlaw_custody_expected_release_year"])
            if d.get("outlaw_custody_expected_release_year") is not None
            else None
        ),
        outlaw_custody_release_year=(
            int(d["outlaw_custody_release_year"])
            if d.get("outlaw_custody_release_year") is not None
            else None
        ),
        outlaw_custody_site_settlement_id=(
            str(d["outlaw_custody_site_settlement_id"])
            if d.get("outlaw_custody_site_settlement_id") is not None
            else None
        ),
        employment_status=(
            str(d["employment_status"])
            if d.get("employment_status") is not None
            else None
        ),
        job_lost_year=(
            int(d["job_lost_year"])
            if d.get("job_lost_year") is not None
            else None
        ),
        unemployment_started_year=(
            int(d["unemployment_started_year"])
            if d.get("unemployment_started_year") is not None
            else None
        ),
        last_job=str(d["last_job"]) if d.get("last_job") is not None else None,
        career_fitness_score=(
            float(d["career_fitness_score"])
            if d.get("career_fitness_score") is not None
            else None
        ),
        job_prosperity_01=(
            float(d["job_prosperity_01"])
            if d.get("job_prosperity_01") is not None
            else None
        ),
        household_prosperity=(
            float(d["household_prosperity"])
            if d.get("household_prosperity") is not None
            else None
        ),
        household_purseholder_person_id=(
            int(d["household_purseholder_person_id"])
            if d.get("household_purseholder_person_id") is not None
            else None
        ),
        genome_composite_names=_parse_genome_composite_names(
            d.get("genome_composite_names")
            if d.get("genome_composite_names") is not None
            else d.get("genome_composite_descriptions")
        ),
        genome_composite_scores=_parse_genome_composite_scores(
            d.get("genome_composite_scores")
        ),
        genome_trait_phrases=_parse_genome_trait_phrases(
            d.get("genome_trait_phrases")
        ),
        birth_litter_size=max(1, int(d.get("birth_litter_size") or 1)),
    )
    if (
        p.current_settlement_id is None
        and p.birthplace_settlement_id
        and str(p.outlaw_status or "").strip().lower() != "fugitive"
    ):
        p = replace(p, current_settlement_id=p.birthplace_settlement_id)
    return p


def _passive_person_values(
    conn: sqlite3.Connection,
    rec: PassivePersonRecord,
) -> tuple[object, ...]:
    p = rec.person
    birthplace_region_key = _lookup_or_insert_region_key(conn, p.birthplace_region_id)
    birthplace_settlement_key = _lookup_or_insert_settlement_key(
        conn, p.birthplace_settlement_id, p.birthplace_region_id
    )
    current_settlement_key = _lookup_or_insert_settlement_key(
        conn, p.current_settlement_id, p.birthplace_region_id
    )
    return (
        int(rec.person_id),
        p.name,
        int(p.birthyear),
        p.deathyear,
        1 if p.deathyear is None else 0,
        p.gender,
        p.species,
        p.ethnic,
        birthplace_region_key,
        birthplace_settlement_key,
        current_settlement_key,
        p.job_family,
        p.partner_person_id,
        p.partner_name,
        p.partner_birthyear,
        p.partner_deathyear,
        p.partnership_start_year,
        p.partnership_end_year,
        p.father_id,
        p.mother_id,
        int(p.child_count),
        _json_int_tuple(p.child_person_ids),
        _json_int_tuple(p.child_birthyears),
        p.status_bucket,
        p.prosperity_bucket,
    )


def _json_int_tuple(values: tuple[int, ...]) -> str:
    return json.dumps([int(v) for v in values], separators=(",", ":"))


def _row_optional_int(row: sqlite3.Row, key: str) -> int | None:
    if key not in row.keys() or row[key] is None:
        return None
    return int(row[key])


def _row_optional_str(row: sqlite3.Row, key: str) -> str | None:
    if key not in row.keys() or row[key] is None:
        return None
    value = str(row[key]).strip()
    return value or None


def _parse_json_int_tuple(row: sqlite3.Row, key: str) -> tuple[int, ...]:
    if key not in row.keys() or row[key] is None:
        return ()
    try:
        data = json.loads(str(row[key] or "[]"))
    except json.JSONDecodeError:
        return ()
    if not isinstance(data, list):
        return ()
    out: list[int] = []
    for item in data:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return tuple(out)


def _passive_person_from_checkpoint_row(
    row: sqlite3.Row,
    *,
    region_ids_by_key: dict[int, str],
    settlement_ids_by_key: dict[int, str],
) -> PassivePerson:
    def region_id(key: object) -> str | None:
        return region_ids_by_key.get(int(key)) if key is not None else None

    def settlement_id(key: object) -> str | None:
        return settlement_ids_by_key.get(int(key)) if key is not None else None

    return PassivePerson(
        name=str(row["name"] or ""),
        birthyear=int(row["birthyear"] or 0),
        deathyear=int(row["deathyear"]) if row["deathyear"] is not None else None,
        gender=str(row["gender"] or ""),
        species=str(row["species"]) if "species" in row.keys() and row["species"] is not None else None,
        ethnic=str(row["ethnic"]) if "ethnic" in row.keys() and row["ethnic"] is not None else None,
        birthplace_region_id=region_id(row["birthplace_region_key"]),
        birthplace_settlement_id=settlement_id(row["birthplace_settlement_key"]),
        current_settlement_id=settlement_id(row["current_settlement_key"]),
        job_family=str(row["job_family"]) if row["job_family"] is not None else None,
        partner_person_id=(
            int(row["partner_person_id"])
            if row["partner_person_id"] is not None
            else None
        ),
        partner_name=_row_optional_str(row, "partner_name"),
        partner_birthyear=_row_optional_int(row, "partner_birthyear"),
        partner_deathyear=_row_optional_int(row, "partner_deathyear"),
        partnership_start_year=_row_optional_int(row, "partnership_start_year"),
        partnership_end_year=_row_optional_int(row, "partnership_end_year"),
        father_id=int(row["father_id"]) if row["father_id"] is not None else None,
        mother_id=int(row["mother_id"]) if row["mother_id"] is not None else None,
        child_count=int(row["child_count"] or 0),
        child_person_ids=_parse_json_int_tuple(row, "child_person_ids_json"),
        child_birthyears=_parse_json_int_tuple(row, "child_birthyears_json"),
        status_bucket=(
            str(row["status_bucket"]) if row["status_bucket"] is not None else None
        ),
        prosperity_bucket=(
            str(row["prosperity_bucket"])
            if row["prosperity_bucket"] is not None
            else None
        ),
    )


def _passive_cohort_values(
    conn: sqlite3.Connection,
    cohort: PassiveCohort,
) -> tuple[object, ...]:
    region_key = _lookup_or_insert_region_key(conn, cohort.region_id)
    settlement_key = _lookup_or_insert_settlement_key(
        conn, cohort.settlement_id, cohort.region_id
    )
    return (
        int(cohort.sim_year),
        region_key,
        settlement_key,
        cohort.age_band,
        cohort.gender,
        cohort.species,
        cohort.culture,
        cohort.job_family,
        cohort.status_bucket,
        int(cohort.population_count),
        int(cohort.birth_count),
        int(cohort.death_count),
    )


def _passive_cohort_from_checkpoint_row(
    row: sqlite3.Row,
    *,
    region_ids_by_key: dict[int, str],
    settlement_ids_by_key: dict[int, str],
) -> PassiveCohort:
    rkey = row["region_key"]
    skey = row["settlement_key"]
    return PassiveCohort(
        sim_year=int(row["sim_year"]),
        region_id=region_ids_by_key.get(int(rkey)) if rkey is not None else None,
        settlement_id=settlement_ids_by_key.get(int(skey)) if skey is not None else None,
        age_band=str(row["age_band"] or ""),
        gender=str(row["gender"] or ""),
        species=str(row["species"] or ""),
        culture=str(row["culture"] or ""),
        job_family=str(row["job_family"] or ""),
        status_bucket=str(row["status_bucket"] or ""),
        population_count=int(row["population_count"] or 0),
        birth_count=int(row["birth_count"] or 0),
        death_count=int(row["death_count"] or 0),
    )


def _flush_passive_promotion_log_entries(
    conn: sqlite3.Connection,
    entries: object,
) -> None:
    ts = _utc_now_iso()
    for entry in entries or ():
        try:
            person_id = int(getattr(entry, "person_id"))
        except (TypeError, ValueError):
            continue
        try:
            sim_year_raw = getattr(entry, "sim_year")
            sim_year = int(sim_year_raw) if sim_year_raw is not None else None
        except (TypeError, ValueError):
            sim_year = None
        reason = str(getattr(entry, "reason", "") or "").strip()
        if person_id <= 0 or not reason:
            continue
        try:
            source_event_raw = getattr(entry, "source_event_id", None)
            source_event_id = (
                int(source_event_raw) if source_event_raw is not None else None
            )
        except (TypeError, ValueError):
            source_event_id = None
        synthesized = getattr(entry, "synthesized", {})
        if not isinstance(synthesized, dict):
            synthesized = {}
        synthesized_json = json.dumps(
            synthesized,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        )
        conn.execute(
            """
            INSERT INTO simulation_promotion_log (
                person_id, sim_year, reason, source_event_id, synthesized_json, created_at
            )
            SELECT ?, ?, ?, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1
                FROM simulation_promotion_log
                WHERE person_id = ?
                  AND reason = ?
                  AND ((sim_year IS NULL AND ? IS NULL) OR sim_year = ?)
            )
            """,
            (
                person_id,
                sim_year,
                reason,
                source_event_id,
                synthesized_json,
                ts,
                person_id,
                reason,
                sim_year,
                sim_year,
            ),
        )


def checkpoint_simulation_snapshot(
    ctx: "SimulationContext",
    *,
    refresh_person_almanack: bool = True,
) -> None:
    """Upsert archival rows for people and settlements; rewrite derived snapshot slices.

    ``simulation_people`` / ``simulation_settlements`` keep historical rows (``INSERT OR
    REPLACE`` only). Regions, couples, and paramours are still replaced from current ctx.
    After a successful commit, long-dead persons are dropped from RAM (see
    :func:`prune_ancient_dead_from_ram`).
    """
    year = int(ctx.current_year if ctx.current_year is not None else ctx.simulation_start_year)
    simulation_timing.record_gauge(year, "checkpoint", "snapshot_people_rows", len(ctx.people))
    simulation_timing.record_gauge(
        year,
        "checkpoint",
        "snapshot_passive_people_rows",
        len(getattr(ctx, "passive_people", {})),
    )
    simulation_timing.record_gauge(
        year,
        "checkpoint",
        "snapshot_passive_cohort_rows",
        len(getattr(ctx, "passive_cohorts", [])),
    )
    t0 = _profile_t0(year)
    with _open_save(ctx.save_db_path) as conn:
        ensure_checkpoint_schema(conn)
        cur = conn.cursor()
        cur.execute("DELETE FROM simulation_regions")
        cur.execute("DELETE FROM simulation_couples")
        cur.execute("DELETE FROM simulation_paramours")
        t0 = _profile_accumulate("checkpoint.snapshot_prepare", t0)

        alive = ctx.current_people_ids
        trait_slots = _trait_slots_for_checkpoint(ctx)
        include_trait_slots = not bool(_trait_slots_from_config(ctx.db_path))
        for st in ctx.settlements_by_id.values():
            _lookup_or_insert_settlement_key(conn, st.settlement_id, st.region_id)
        person_column_names = (
            "person_id",
            "is_founder",
            "father_id",
            "mother_id",
            "is_alive",
            *_PERSON_CHECKPOINT_COLUMNS,
            "person_json",
        )
        person_cols_sql = ", ".join(person_column_names)
        person_placeholders = ", ".join("?" for _ in person_column_names)
        person_rows: list[tuple[object, ...]] = []
        for rec in ctx.people:
            person_cols, payload = _person_checkpoint_payload(
                rec.person,
                trait_slots,
                include_trait_slots=include_trait_slots,
                conn=conn,
            )
            person_rows.append(
                (
                    rec.person_id,
                    1 if rec.is_founder else 0,
                    rec.father_id,
                    rec.mother_id,
                    1 if rec.person_id in alive else 0,
                    *(person_cols[c] for c in _PERSON_CHECKPOINT_COLUMNS),
                    payload,
                )
            )
        cur.executemany(
            f"""
            INSERT OR REPLACE INTO simulation_people ({person_cols_sql})
            VALUES ({person_placeholders})
            """,
            person_rows,
        )
        t0 = _profile_accumulate("checkpoint.snapshot_people", t0)

        _sync_household_service_contracts(conn, ctx)
        t0 = _profile_accumulate("checkpoint.snapshot_service_contracts", t0)

        _sync_patronage_ties(conn, ctx)
        t0 = _profile_accumulate("checkpoint.snapshot_patronage_ties", t0)

        _sync_outlaw_state(conn, ctx)
        t0 = _profile_accumulate("checkpoint.snapshot_outlaws", t0)

        _sync_serial_predation_candidates(conn, ctx)
        t0 = _profile_accumulate("checkpoint.snapshot_serial_predation_candidates", t0)

        passive_column_names = (
            "person_id",
            "name",
            "birthyear",
            "deathyear",
            "is_alive",
            "gender",
            "species",
            "ethnic",
            "birthplace_region_key",
            "birthplace_settlement_key",
            "current_settlement_key",
            "job_family",
            "partner_person_id",
            "partner_name",
            "partner_birthyear",
            "partner_deathyear",
            "partnership_start_year",
            "partnership_end_year",
            "father_id",
            "mother_id",
            "child_count",
            "child_person_ids_json",
            "child_birthyears_json",
            "status_bucket",
            "prosperity_bucket",
        )
        passive_cols_sql = ", ".join(passive_column_names)
        passive_placeholders = ", ".join("?" for _ in passive_column_names)
        passive_rows = [
            _passive_person_values(conn, rec)
            for rec in getattr(ctx, "passive_people", {}).values()
        ]
        cur.executemany(
            f"""
            INSERT OR REPLACE INTO simulation_people_light ({passive_cols_sql})
            VALUES ({passive_placeholders})
            """,
            passive_rows,
        )
        t0 = _profile_accumulate("checkpoint.snapshot_passive_people", t0)

        cohort_column_names = (
            "sim_year",
            "region_key",
            "settlement_key",
            "age_band",
            "gender",
            "species",
            "culture",
            "job_family",
            "status_bucket",
            "population_count",
            "birth_count",
            "death_count",
        )
        cohort_cols_sql = ", ".join(cohort_column_names)
        cohort_placeholders = ", ".join("?" for _ in cohort_column_names)
        cohort_rows = [
            _passive_cohort_values(conn, cohort)
            for cohort in getattr(ctx, "passive_cohorts", [])
        ]
        cur.executemany(
            f"""
            INSERT OR REPLACE INTO simulation_cohorts ({cohort_cols_sql})
            VALUES ({cohort_placeholders})
            """,
            cohort_rows,
        )
        t0 = _profile_accumulate("checkpoint.snapshot_passive_cohorts", t0)

        _flush_passive_promotion_log_entries(
            conn, getattr(ctx, "passive_promotion_log", ())
        )
        t0 = _profile_accumulate("checkpoint.snapshot_promotion_log", t0)

        by_region: dict[str, list[SettlementState]] = defaultdict(list)
        settlement_rows: list[tuple[object, ...]] = []
        for settlement_id, st in ctx.settlements_by_id.items():
            by_region[st.region_id].append(st)
            settlement_key = _lookup_or_insert_settlement_key(
                conn, st.settlement_id, st.region_id
            )
            region_key = _lookup_or_insert_region_key(conn, st.region_id)
            if settlement_key is None or region_key is None:
                continue
            settlement_rows.append(
                (
                    settlement_key,
                    region_key,
                    st.level,
                    st.resident_count,
                    st.household_cap,
                    st.food_pressure,
                    st.prosperity_pool,
                    st.stability,
                    st.market_pull,
                    st.display_name,
                    st.etymology,
                    st.name_category_primary,
                    st.name_category_secondary,
                    st.name_culture_primary,
                    st.name_culture_secondary,
                    st.local_geography_json,
                    st.founded_sim_year,
                    st.abandoned_sim_year,
                    st.status,
                    st.consecutive_empty_years,
                    st.site_slot,
                    st.founding_reason,
                    st.mother_settlement_id,
                    st.trade_network_id or st.settlement_id,
                    st.autonomy_level,
                ),
            )
        cur.executemany(
            """
            INSERT OR REPLACE INTO simulation_settlements (
                settlement_key, region_key, level, population_cap, household_cap,
                food_pressure, prosperity_pool, stability, market_pull,
                display_name, etymology,
                name_category_primary, name_category_secondary,
                name_culture_primary, name_culture_secondary,
                local_geography_json,
                founded_sim_year, abandoned_sim_year, status,
                consecutive_empty_years, site_slot,
                founding_reason, mother_settlement_id, trade_network_id,
                autonomy_level
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            settlement_rows,
        )

        region_rows: list[tuple[object, ...]] = []
        for region_id, bucket in by_region.items():
            tot_pop, tot_hh, fp, stb, mp = _aggregate_region_metrics(bucket)
            r_label = _resolve_region_display_name_for_checkpoint(ctx, region_id, bucket[0])
            region_key = _lookup_or_insert_region_key(conn, region_id)
            if region_key is None:
                continue
            region_rows.append(
                (
                    region_key,
                    r_label,
                    tot_pop,
                    tot_hh,
                    fp,
                    stb,
                    mp,
                    float(
                        getattr(ctx, "region_prosperity_pool", {}).get(region_id, 1.0)
                    ),
                    float(
                        getattr(ctx, "region_treasury_balance", {}).get(region_id, 0.0)
                    ),
                ),
            )
        cur.executemany(
            """
            INSERT INTO simulation_regions (
                region_key, region_display_name,
                total_population_cap, total_household_cap,
                food_pressure, stability, market_pull,
                prosperity_pool, treasury_balance
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            region_rows,
        )
        t0 = _profile_accumulate("checkpoint.snapshot_settlements_regions", t0)

        surname_conventions = getattr(ctx, "surname_conventions_by_pair", {})
        couple_rows = [
            (
                i,
                a_id,
                b_id,
                surname_conventions.get(tuple(sorted((int(a_id), int(b_id))))),
            )
            for i, (a_id, b_id) in enumerate(ctx.couples)
        ]
        cur.executemany(
            """
            INSERT INTO simulation_couples (
                sort_order, person_a_id, person_b_id, surname_convention
            )
            VALUES (?, ?, ?, ?)
            """,
            couple_rows,
        )

        paramour_rows = [
            (
                i,
                a_id,
                b_id,
                surname_conventions.get(tuple(sorted((int(a_id), int(b_id))))),
            )
            for i, (a_id, b_id) in enumerate(ctx.paramours)
        ]
        cur.executemany(
            """
            INSERT INTO simulation_paramours (
                sort_order, person_a_id, person_b_id, surname_convention
            )
            VALUES (?, ?, ?, ?)
            """,
            paramour_rows,
        )
        t0 = _profile_accumulate("checkpoint.snapshot_relationships", t0)

        from library.government_checkpoint import checkpoint_government as _checkpoint_gov

        _checkpoint_gov(ctx, cur)
        t0 = _profile_accumulate("checkpoint.snapshot_government", t0)

        from library.person_archive_scores import refresh_person_archive_scores

        archive_score_rows = refresh_person_archive_scores(
            conn,
            person_ids=(int(rec.person_id) for rec in ctx.people),
            simulation_year=year,
        )
        simulation_timing.record_gauge(
            year, "checkpoint", "person_archive_score_rows", archive_score_rows
        )
        t0 = _profile_accumulate("checkpoint.snapshot_person_archive_scores", t0)

        if refresh_person_almanack:
            from library.person_almanack import refresh_person_almanack

            almanack_rows = refresh_person_almanack(conn, simulation_year=year)
            simulation_timing.record_gauge(
                year, "checkpoint", "person_almanack_metric_rows", almanack_rows
            )
            t0 = _profile_accumulate("checkpoint.snapshot_person_almanack", t0)
        else:
            t0 = _profile_accumulate("checkpoint.snapshot_person_almanack.skipped", t0)

        cur.execute(
            """
            INSERT OR REPLACE INTO simulation_meta (meta_key, meta_value)
            VALUES (?, ?)
            """,
            ("next_person_id", str(ctx.next_person_id)),
        )
        cur.execute(
            """
            INSERT OR REPLACE INTO simulation_meta (meta_key, meta_value)
            VALUES (?, ?)
            """,
            (
                REGION_EFFECTIVE_CAP_MULTIPLIER_META_KEY,
                serialize_region_effective_cap_multipliers(ctx),
            ),
        )
        cur.execute(
            """
            INSERT OR REPLACE INTO simulation_meta (meta_key, meta_value)
            VALUES (?, ?)
            """,
            (
                REGION_DISPLAY_LABEL_OVERRIDES_META_KEY,
                serialize_region_display_label_overrides(ctx),
            ),
        )
        cur.execute(
            """
            INSERT OR REPLACE INTO simulation_meta (meta_key, meta_value)
            VALUES (?, ?)
            """,
            (REGION_NAMING_AUX_META_KEY, serialize_region_naming_aux(ctx)),
        )
        cur.execute(
            """
            INSERT OR REPLACE INTO simulation_meta (meta_key, meta_value)
            VALUES (?, ?)
            """,
            (SETTLEMENT_SPINOFF_STATE_META_KEY, serialize_settlement_spinoff_state(ctx)),
        )
        cur.execute(
            """
            INSERT OR REPLACE INTO simulation_meta (meta_key, meta_value)
            VALUES (?, ?)
            """,
            (
                PENDING_SETTLEMENT_MOVES_META_KEY,
                serialize_pending_settlement_moves(ctx),
            ),
        )
        cur.execute(
            """
            INSERT OR REPLACE INTO simulation_meta (meta_key, meta_value)
            VALUES (?, ?)
            """,
            (WORLD_MAP_SEED_META_KEY, _ctx_world_map_seed(ctx)),
        )
        t0 = _profile_accumulate("checkpoint.snapshot_meta", t0)
        conn.commit()
        t0 = _profile_accumulate("checkpoint.commit", t0)
    t0 = _profile_t0(year)
    prune_ancient_dead_from_ram(ctx)
    _profile_accumulate("checkpoint.prune_dead_ram", t0)


def checkpoint_simulation_to_save(
    ctx: "SimulationContext",
    *,
    full_snapshot: bool = True,
    refresh_person_almanack: bool = True,
) -> None:
    """Flush pending events; optionally write full snapshot tables to ``save.sqlite``.

    When ``full_snapshot`` is false, still writes ``simulation_meta`` for
    ``next_person_id``, cap multipliers, region display label overrides, naming
    aux state, and settlement spinoff accrual so resume stays aligned with RAM between full snapshots.
    ``refresh_person_almanack`` controls the expensive derived Almanack ranking
    cache only; the Gradio browser can still rebuild it on demand.
    """
    flush_pending_simulation_events(ctx)
    if full_snapshot:
        checkpoint_simulation_snapshot(
            ctx,
            refresh_person_almanack=bool(refresh_person_almanack),
        )
    else:
        flush_simulation_meta_checkpoint(ctx)


def try_load_simulation_checkpoint(ctx: "SimulationContext") -> bool:
    """If a checkpoint exists, load working-set state into ``ctx``.

    All ``simulation_settlements`` rows load into RAM; only alive + recent dead people
    (see :func:`person_belongs_in_working_ram`) load so long runs stay bounded.
    """
    from library.simulation_context import SimulationPersonRecord

    cap_multipliers: dict[str, float] = {}
    spin_pending: dict[str, int] = {}
    spin_last: dict[str, int] = {}
    with _open_save(ctx.save_db_path) as conn:
        ensure_checkpoint_schema(conn)
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM simulation_people",
        ).fetchone()
        light_row = conn.execute(
            "SELECT COUNT(*) AS c FROM simulation_people_light",
        ).fetchone()
        detailed_count = int(row["c"] or 0) if row is not None else 0
        passive_count = int(light_row["c"] or 0) if light_row is not None else 0
        if detailed_count == 0 and passive_count == 0:
            return False

        world_state_cols = _table_columns(conn, "world_state") if _table_exists(conn, "world_state") else []
        if "id" in world_state_cols:
            ref_row = conn.execute(
                "SELECT current_year FROM world_state WHERE id = 1",
            ).fetchone()
        elif "world" in world_state_cols:
            ref_row = conn.execute(
                "SELECT current_year FROM world_state WHERE world = ?",
                (ctx.world,),
            ).fetchone()
        else:
            ref_row = None
        reference_year = int(ctx.simulation_start_year)
        if ref_row is not None and ref_row["current_year"] is not None:
            reference_year = int(ref_row["current_year"])
        elif ctx.current_year is not None:
            reference_year = int(ctx.current_year)
        retention = int(
            getattr(ctx, "working_set_dead_retention_years", _DEFAULT_WORKING_SET_DEAD_RETENTION)
        )

        people_rows = conn.execute(
            """
            SELECT *
            FROM simulation_people
            WHERE is_alive = 1
               OR deathyear IS NULL
               OR deathyear >= ?
            ORDER BY person_id
            """,
            (int(reference_year) - int(retention),),
        ).fetchall()
        region_ids_by_key = {
            int(r["region_key"]): str(r["region_id"])
            for r in conn.execute(
                "SELECT region_key, region_id FROM simulation_region_lookup"
            ).fetchall()
        }
        settlement_ids_by_key = {
            int(r["settlement_key"]): str(r["settlement_id"])
            for r in conn.execute(
                "SELECT settlement_key, settlement_id FROM simulation_settlement_lookup"
            ).fetchall()
        }
        trait_slots = _trait_slots_from_config(ctx.db_path)
        loaded: list[SimulationPersonRecord] = []
        alive: set[int] = set()
        id_to: dict[int, SimulationPersonRecord] = {}
        passive_people: dict[int, PassivePersonRecord] = {}
        passive_cohorts: list[PassiveCohort] = []
        for row in people_rows:
            pid = int(row["person_id"])
            person = _person_from_dict(
                _person_dict_from_checkpoint_row(
                    row,
                    trait_slots,
                    region_ids_by_key=region_ids_by_key,
                    settlement_ids_by_key=settlement_ids_by_key,
                )
            )
            if person.genome_composite_scores:
                from library.genome_composites import refresh_genome_composite_scores

                person = refresh_genome_composite_scores(
                    person,
                    ctx.db_path,
                    current_year=reference_year,
                )
            else:
                from library.genome_composites import refresh_genome_composite_profile

                person = refresh_genome_composite_profile(
                    person,
                    ctx.db_path,
                    current_year=reference_year,
                )
            if not person_belongs_in_working_ram(
                person,
                reference_year=reference_year,
                retention_years=retention,
            ):
                continue
            rec = SimulationPersonRecord(
                person_id=pid,
                person=person,
                is_founder=bool(int(row["is_founder"])),
                father_id=(
                    int(row["father_id"])
                    if row["father_id"] is not None
                    else None
                ),
                mother_id=(
                    int(row["mother_id"])
                    if row["mother_id"] is not None
                    else None
                ),
            )
            loaded.append(rec)
            id_to[pid] = rec
            if int(row["is_alive"]):
                alive.add(pid)
        simulation_timing.record_gauge(
            reference_year,
            "checkpoint_load",
            "selected_detailed_people_rows",
            len(people_rows),
        )

        for row in conn.execute(
            "SELECT * FROM simulation_people_light ORDER BY person_id"
        ).fetchall():
            pid = int(row["person_id"])
            passive_people[pid] = PassivePersonRecord(
                person_id=pid,
                person=_passive_person_from_checkpoint_row(
                    row,
                    region_ids_by_key=region_ids_by_key,
                    settlement_ids_by_key=settlement_ids_by_key,
                ),
            )

        latest_cohort_row = conn.execute(
            "SELECT MAX(sim_year) AS sim_year FROM simulation_cohorts",
        ).fetchone()
        latest_cohort_year = (
            int(latest_cohort_row["sim_year"])
            if latest_cohort_row is not None and latest_cohort_row["sim_year"] is not None
            else None
        )
        if latest_cohort_year is not None:
            for row in conn.execute(
                """
                SELECT *
                FROM simulation_cohorts
                WHERE sim_year = ?
                ORDER BY region_key, settlement_key, age_band, gender
                """,
                (latest_cohort_year,),
            ).fetchall():
                passive_cohorts.append(
                    _passive_cohort_from_checkpoint_row(
                        row,
                        region_ids_by_key=region_ids_by_key,
                        settlement_ids_by_key=settlement_ids_by_key,
                    )
                )

        settle_rows = conn.execute(
            "SELECT * FROM simulation_settlements_readable",
        ).fetchall()
        settlements_by_id: dict[str, SettlementState] = {}
        settlement_ids_by_region: dict[str, list[str]] = defaultdict(list)
        for row in settle_rows:
            st = _settlement_state_from_db_row(row)
            settlements_by_id[st.settlement_id] = st
            settlement_ids_by_region[st.region_id].append(st.settlement_id)
        for rid in settlement_ids_by_region:
            settlement_ids_by_region[rid].sort()

        region_labels: dict[str, str] = {}
        region_prosperity_loaded: dict[str, float] = {}
        region_treasury_loaded: dict[str, float] = {}
        region_rows = conn.execute(
            "SELECT * FROM simulation_regions_readable",
        ).fetchall()
        for r in region_rows:
            rid = str(r["region_id"] or "").strip()
            if not rid:
                continue
            region_labels[rid] = str(r["region_display_name"] or "").strip()
            if "prosperity_pool" in r.keys() and r["prosperity_pool"] is not None:
                try:
                    region_prosperity_loaded[rid] = float(r["prosperity_pool"])
                except (TypeError, ValueError):
                    pass
            if "treasury_balance" in r.keys() and r["treasury_balance"] is not None:
                try:
                    region_treasury_loaded[rid] = float(r["treasury_balance"])
                except (TypeError, ValueError):
                    pass

        couple_rows = conn.execute(
            """
            SELECT person_a_id, person_b_id, surname_convention
            FROM simulation_couples
            ORDER BY sort_order
            """,
        ).fetchall()
        couple_ids: list[tuple[int, int]] = [
            (int(r["person_a_id"]), int(r["person_b_id"])) for r in couple_rows
        ]
        couples = [(a, b) for (a, b) in couple_ids if a in id_to and b in id_to]
        surname_conventions_by_pair: dict[tuple[int, int], str] = {}
        for r in couple_rows:
            a = int(r["person_a_id"])
            b = int(r["person_b_id"])
            convention = str(r["surname_convention"] or "").strip()
            if convention and a in id_to and b in id_to:
                surname_conventions_by_pair[tuple(sorted((a, b)))] = convention

        try:
            paramour_rows = conn.execute(
                """
                SELECT person_a_id, person_b_id, surname_convention
                FROM simulation_paramours
                ORDER BY sort_order
                """,
            ).fetchall()
        except sqlite3.OperationalError:
            paramour_rows = []
        paramour_ids: list[tuple[int, int]] = [
            (int(r["person_a_id"]), int(r["person_b_id"])) for r in paramour_rows
        ]
        paramours = [(a, b) for (a, b) in paramour_ids if a in id_to and b in id_to]
        for r in paramour_rows:
            a = int(r["person_a_id"])
            b = int(r["person_b_id"])
            convention = str(r["surname_convention"] or "").strip()
            if convention and a in id_to and b in id_to:
                surname_conventions_by_pair[tuple(sorted((a, b)))] = convention

        from library.simulation_context import SimulationPatronageTie

        patronage_ties: dict[tuple[int, int, str], SimulationPatronageTie] = {}
        if _table_exists(conn, "simulation_patronage_ties"):
            for r in conn.execute(
                """
                SELECT patron_person_id, client_person_id, tie_kind, strength_01,
                       status, start_year, end_year, settlement_id, polity_id,
                       updated_year
                FROM simulation_patronage_ties
                WHERE status = 'active'
                ORDER BY patron_person_id, client_person_id, tie_kind, start_year
                """
            ).fetchall():
                patron_id = int(r["patron_person_id"])
                client_id = int(r["client_person_id"])
                if patron_id not in id_to or client_id not in id_to:
                    continue
                kind = str(r["tie_kind"] or "patronage").strip() or "patronage"
                patronage_ties[(patron_id, client_id, kind)] = SimulationPatronageTie(
                    patron_person_id=patron_id,
                    client_person_id=client_id,
                    tie_kind=kind,
                    strength_01=float(r["strength_01"] or 0.0),
                    status=str(r["status"] or "active"),
                    start_year=(
                        int(r["start_year"]) if r["start_year"] is not None else None
                    ),
                    end_year=(
                        int(r["end_year"]) if r["end_year"] is not None else None
                    ),
                    settlement_id=(
                        str(r["settlement_id"]) if r["settlement_id"] is not None else None
                    ),
                    polity_id=(
                        int(r["polity_id"]) if r["polity_id"] is not None else None
                    ),
                    updated_year=(
                        int(r["updated_year"]) if r["updated_year"] is not None else None
                    ),
                )

        from library.simulation_outlaws import (
            SimulationOutlawCase,
            SimulationOutlawCustody,
            SimulationOutlawRefuge,
            outlaw_refuge_display_name,
        )

        outlaw_refuges: dict[str, SimulationOutlawRefuge] = {}
        if _table_exists(conn, "simulation_outlaw_refuges"):
            for r in conn.execute(
                """
                SELECT *
                FROM simulation_outlaw_refuges
                WHERE status = 'active' OR abandoned_year IS NULL
                ORDER BY refuge_id
                """
            ).fetchall():
                details: dict[str, object] = {}
                raw_details = r["details_json"] if "details_json" in r.keys() else None
                if raw_details:
                    try:
                        parsed = json.loads(str(raw_details))
                        if isinstance(parsed, dict):
                            details = parsed
                    except json.JSONDecodeError:
                        details = {}
                rkey = r["region_key"] if r["region_key"] is not None else None
                skey = (
                    r["near_settlement_key"]
                    if r["near_settlement_key"] is not None
                    else None
                )
                refuge = SimulationOutlawRefuge(
                    refuge_id=str(r["refuge_id"] or ""),
                    region_id=region_ids_by_key.get(int(rkey), "unknown")
                    if rkey is not None
                    else "unknown",
                    display_name=(
                        str(r["display_name"] or "").strip()
                        if "display_name" in r.keys()
                        else ""
                    )
                    or outlaw_refuge_display_name(
                        str(r["refuge_id"] or ""),
                        region_id=(
                            region_ids_by_key.get(int(rkey), "unknown")
                            if rkey is not None
                            else "unknown"
                        ),
                        near_place_label=(
                            settlement_ids_by_key.get(int(skey))
                            if skey is not None
                            else ""
                        ),
                        year=(r["founded_year"] if r["founded_year"] is not None else ""),
                    ),
                    near_settlement_id=(
                        settlement_ids_by_key.get(int(skey))
                        if skey is not None
                        else None
                    ),
                    status=str(r["status"] or "active"),
                    founded_year=(
                        int(r["founded_year"]) if r["founded_year"] is not None else None
                    ),
                    discovered_year=(
                        int(r["discovered_year"])
                        if r["discovered_year"] is not None
                        else None
                    ),
                    abandoned_year=(
                        int(r["abandoned_year"])
                        if r["abandoned_year"] is not None
                        else None
                    ),
                    band_size=int(r["band_size"] or 0),
                    concealment_01=float(r["concealment_01"] or 0.0),
                    support_01=float(r["support_01"] or 0.0),
                    last_activity_year=(
                        int(r["last_activity_year"])
                        if r["last_activity_year"] is not None
                        else None
                    ),
                    details=details,
                )
                if refuge.refuge_id:
                    outlaw_refuges[refuge.refuge_id] = refuge

        outlaw_custodies: dict[str, SimulationOutlawCustody] = {}
        if _table_exists(conn, "simulation_outlaw_custodies"):
            for r in conn.execute(
                """
                SELECT *
                FROM simulation_outlaw_custodies
                WHERE status = 'active' OR release_year IS NULL OR release_year >= ?
                ORDER BY start_year, custody_id
                """,
                (int(reference_year) - int(retention),),
            ).fetchall():
                person_id = int(r["person_id"] or 0)
                if person_id not in id_to:
                    continue
                details: dict[str, object] = {}
                raw_details = r["details_json"] if "details_json" in r.keys() else None
                if raw_details:
                    try:
                        parsed = json.loads(str(raw_details))
                        if isinstance(parsed, dict):
                            details = parsed
                    except json.JSONDecodeError:
                        details = {}
                rkey = r["region_key"] if r["region_key"] is not None else None
                skey = r["site_settlement_key"] if r["site_settlement_key"] is not None else None
                custody = SimulationOutlawCustody(
                    custody_id=str(r["custody_id"] or ""),
                    case_key=str(r["case_key"] or ""),
                    person_id=person_id,
                    custody_type=str(r["custody_type"] or "imprisonment"),
                    status=str(r["status"] or "active"),
                    site_settlement_id=(
                        settlement_ids_by_key.get(int(skey))
                        if skey is not None
                        else None
                    ),
                    region_id=(
                        region_ids_by_key.get(int(rkey)) if rkey is not None else None
                    ),
                    start_year=(
                        int(r["start_year"]) if r["start_year"] is not None else None
                    ),
                    expected_release_year=(
                        int(r["expected_release_year"])
                        if r["expected_release_year"] is not None
                        else None
                    ),
                    release_year=(
                        int(r["release_year"]) if r["release_year"] is not None else None
                    ),
                    severity_01=float(r["severity_01"] or 0.0),
                    details=details,
                )
                if custody.custody_id:
                    outlaw_custodies[custody.custody_id] = custody

        outlaw_cases: dict[str, SimulationOutlawCase] = {}
        if _table_exists(conn, "simulation_outlaw_cases"):
            for r in conn.execute(
                """
                SELECT *
                FROM simulation_outlaw_cases
                WHERE accused_person_id IN (
                    SELECT person_id
                    FROM simulation_people
                    WHERE is_alive = 1 OR deathyear IS NULL OR deathyear >= ?
                )
                ORDER BY start_year, case_key
                """,
                (int(reference_year) - int(retention),),
            ).fetchall():
                accused_id = int(r["accused_person_id"] or 0)
                if accused_id not in id_to:
                    continue
                details: dict[str, object] = {}
                raw_details = r["details_json"] if "details_json" in r.keys() else None
                if raw_details:
                    try:
                        parsed = json.loads(str(raw_details))
                        if isinstance(parsed, dict):
                            details = parsed
                    except json.JSONDecodeError:
                        details = {}
                rkey = r["region_key"] if r["region_key"] is not None else None
                skey = r["settlement_key"] if r["settlement_key"] is not None else None
                case = SimulationOutlawCase(
                    case_key=str(r["case_key"] or ""),
                    accused_person_id=accused_id,
                    offense_type=str(r["offense_type"] or ""),
                    offense_kind=str(r["offense_kind"] or ""),
                    status=str(r["status"] or "active"),
                    source_event_id=(
                        int(r["source_event_id"])
                        if r["source_event_id"] is not None
                        else None
                    ),
                    source_event_key=str(r["source_event_key"] or ""),
                    victim_person_id=(
                        int(r["victim_person_id"])
                        if r["victim_person_id"] is not None
                        else None
                    ),
                    target_person_id=(
                        int(r["target_person_id"])
                        if r["target_person_id"] is not None
                        else None
                    ),
                    severity_01=float(r["severity_01"] or 0.0),
                    knownness_01=float(r["knownness_01"] or 0.0),
                    pursuit_pressure_01=float(r["pursuit_pressure_01"] or 0.0),
                    buyoff_power_01=float(r["buyoff_power_01"] or 0.0),
                    start_year=(
                        int(r["start_year"]) if r["start_year"] is not None else None
                    ),
                    last_seen_year=(
                        int(r["last_seen_year"])
                        if r["last_seen_year"] is not None
                        else None
                    ),
                    expected_forget_year=(
                        int(r["expected_forget_year"])
                        if r["expected_forget_year"] is not None
                        else None
                    ),
                    resolved_year=(
                        int(r["resolved_year"])
                        if r["resolved_year"] is not None
                        else None
                    ),
                    resolution=(
                        str(r["resolution"]) if r["resolution"] is not None else None
                    ),
                    region_id=(
                        region_ids_by_key.get(int(rkey)) if rkey is not None else None
                    ),
                    settlement_id=(
                        settlement_ids_by_key.get(int(skey)) if skey is not None else None
                    ),
                    refuge_id=(
                        str(r["refuge_id"]) if r["refuge_id"] is not None else None
                    ),
                    custody_id=(
                        str(r["custody_id"])
                        if "custody_id" in r.keys() and r["custody_id"] is not None
                        else None
                    ),
                    details=details,
                )
                if case.case_key:
                    outlaw_cases[case.case_key] = case

        serial_predation_candidates: dict[int, dict[str, object]] = {}
        if _table_exists(conn, "simulation_serial_predation_candidates"):
            for r in conn.execute(
                """
                SELECT *
                FROM simulation_serial_predation_candidates
                WHERE person_id IN (
                    SELECT person_id
                    FROM simulation_people
                    WHERE is_alive = 1 OR deathyear IS NULL OR deathyear >= ?
                )
                ORDER BY person_id
                """,
                (int(reference_year) - int(retention),),
            ).fetchall():
                pid = int(r["person_id"] or 0)
                if pid not in id_to:
                    continue
                rejection_reasons: list[str] = []
                raw_reasons = r["rejection_reasons_json"] if "rejection_reasons_json" in r.keys() else None
                if raw_reasons:
                    try:
                        parsed_reasons = json.loads(str(raw_reasons))
                        if isinstance(parsed_reasons, list):
                            rejection_reasons = [str(v) for v in parsed_reasons]
                    except json.JSONDecodeError:
                        rejection_reasons = []
                details: dict[str, object] = {}
                raw_details = r["details_json"] if "details_json" in r.keys() else None
                if raw_details:
                    try:
                        parsed_details = json.loads(str(raw_details))
                        if isinstance(parsed_details, dict):
                            details = parsed_details
                    except json.JSONDecodeError:
                        details = {}
                serial_predation_candidates[pid] = {
                    "person_id": pid,
                    "risk_lane": str(r["risk_lane"] or ""),
                    "status": str(r["status"] or "dormant"),
                    "risk_score": float(r["risk_score"] or 0.0),
                    "harm_drive": float(r["harm_drive"] or 0.0),
                    "inhibition": float(r["inhibition"] or 0.0),
                    "control": float(r["control"] or 0.0),
                    "exposure_noise": float(r["exposure_noise"] or 0.0),
                    "organized_serial_risk": float(r["organized_serial_risk"] or 0.0),
                    "disorganized_serial_risk": float(r["disorganized_serial_risk"] or 0.0),
                    "next_check_year": (
                        int(r["next_check_year"])
                        if r["next_check_year"] is not None
                        else None
                    ),
                    "last_checked_year": (
                        int(r["last_checked_year"])
                        if r["last_checked_year"] is not None
                        else None
                    ),
                    "last_serious_crime_year": (
                        int(r["last_serious_crime_year"])
                        if r["last_serious_crime_year"] is not None
                        else None
                    ),
                    "hidden_linked_kill_count": int(r["hidden_linked_kill_count"] or 0),
                    "suspected_linked_kill_count": int(r["suspected_linked_kill_count"] or 0),
                    "public_suspicion_score": float(r["public_suspicion_score"] or 0.0),
                    "pattern_recognized": bool(int(r["pattern_recognized"] or 0)),
                    "offender_identity_confidence": float(r["offender_identity_confidence"] or 0.0),
                    "rejection_reasons": rejection_reasons,
                    "details": details,
                }

        meta_row = conn.execute(
            "SELECT meta_value FROM simulation_meta WHERE meta_key = ?",
            ("next_person_id",),
        ).fetchone()
        max_loaded = max((r.person_id for r in loaded), default=0)
        mx_row = conn.execute(
            "SELECT MAX(person_id) AS m FROM simulation_people",
        ).fetchone()
        max_any = max_loaded
        if mx_row is not None and mx_row["m"] is not None:
            max_any = max(max_any, int(mx_row["m"]))
        max_passive = max(passive_people.keys(), default=0)
        max_any = max(max_any, max_passive)
        nd_row = conn.execute(
            "SELECT MAX(person_id) AS m FROM simulation_people_nondetailed",
        ).fetchone()
        if nd_row is not None and nd_row["m"] is not None:
            max_any = max(max_any, int(nd_row["m"]))
        next_id = max_any + 1
        if meta_row is not None and str(meta_row["meta_value"] or "").strip():
            next_id = max(next_id, int(meta_row["meta_value"]))

        cap_m_row = conn.execute(
            """
            SELECT meta_value FROM simulation_meta
            WHERE meta_key = ?
            """,
            (REGION_EFFECTIVE_CAP_MULTIPLIER_META_KEY,),
        ).fetchone()
        cap_raw = cap_m_row["meta_value"] if cap_m_row is not None else None
        cap_multipliers = parse_region_effective_cap_multipliers(
            str(cap_raw) if cap_raw is not None else None
        )

        lbl_m_row = conn.execute(
            """
            SELECT meta_value FROM simulation_meta
            WHERE meta_key = ?
            """,
            (REGION_DISPLAY_LABEL_OVERRIDES_META_KEY,),
        ).fetchone()
        lbl_raw = lbl_m_row["meta_value"] if lbl_m_row is not None else None
        display_overrides = parse_region_display_label_overrides(
            str(lbl_raw) if lbl_raw is not None else None
        )

        aux_m_row = conn.execute(
            """
            SELECT meta_value FROM simulation_meta
            WHERE meta_key = ?
            """,
            (REGION_NAMING_AUX_META_KEY,),
        ).fetchone()
        aux_raw = aux_m_row["meta_value"] if aux_m_row is not None else None
        label_src, miss_streak = parse_region_naming_aux(
            str(aux_raw) if aux_raw is not None else None
        )

        spin_m_row = conn.execute(
            """
            SELECT meta_value FROM simulation_meta
            WHERE meta_key = ?
            """,
            (SETTLEMENT_SPINOFF_STATE_META_KEY,),
        ).fetchone()
        spin_raw = spin_m_row["meta_value"] if spin_m_row is not None else None
        spin_pending, spin_last = parse_settlement_spinoff_state(
            str(spin_raw) if spin_raw is not None else None
        )

        moves_m_row = conn.execute(
            """
            SELECT meta_value FROM simulation_meta
            WHERE meta_key = ?
            """,
            (PENDING_SETTLEMENT_MOVES_META_KEY,),
        ).fetchone()
        moves_raw = moves_m_row["meta_value"] if moves_m_row is not None else None
        pending_moves = parse_pending_settlement_moves(
            str(moves_raw) if moves_raw is not None else None
        )

        map_seed_m_row = conn.execute(
            """
            SELECT meta_value FROM simulation_meta
            WHERE meta_key = ?
            """,
            (WORLD_MAP_SEED_META_KEY,),
        ).fetchone()
        map_seed_raw = (
            str(map_seed_m_row["meta_value"]).strip()
            if map_seed_m_row is not None and map_seed_m_row["meta_value"] is not None
            else ""
        )

        merged_region_labels = {**region_labels}
        for rk, rv in display_overrides.items():
            merged_region_labels[rk] = rv

        for rec in loaded:
            p = rec.person
            np = p
            if p.partner_person_id is not None and p.partner_person_id not in id_to:
                np = replace(np, partner_person_id=None)
            if p.paramour_person_id is not None and p.paramour_person_id not in id_to:
                ids = tuple(pid for pid in p.paramour_person_ids if pid in id_to)
                np = replace(
                    np,
                    paramour_person_id=(ids[0] if ids else None),
                    paramour_person_ids=ids,
                )
            if np is not p:
                rec.person = np

    ctx.people = loaded
    ctx.passive_people = passive_people
    ctx.passive_cohorts = passive_cohorts
    ctx.id_to_record = id_to
    ctx.current_people_ids = alive
    if hasattr(ctx, "invalidate_alive_census_cache"):
        ctx.invalidate_alive_census_cache()
    ctx.couples = couples
    ctx.paramours = paramours
    ctx.surname_conventions_by_pair = surname_conventions_by_pair
    ctx.patronage_ties = patronage_ties
    ctx.outlaw_refuges = outlaw_refuges
    ctx.outlaw_cases = outlaw_cases
    ctx.outlaw_custodies = outlaw_custodies
    ctx.serial_predation_candidates = serial_predation_candidates
    for a_id, b_id in couples:
        ra = id_to.get(a_id)
        rb = id_to.get(b_id)
        if ra is not None:
            ra.person = replace(ra.person, partner_person_id=b_id)
        if rb is not None:
            rb.person = replace(rb.person, partner_person_id=a_id)
    if hasattr(ctx, "sync_all_paramour_fields"):
        ctx.sync_all_paramour_fields(include_legacy_scalars=True)
    ctx.settlements_by_id = _enrich_settlements_region_display_names(
        ctx, settlements_by_id, merged_region_labels
    )
    ctx.settlement_ids_by_region = {
        rid: [sid for sid in settlement_ids_by_region[rid]]
        for rid in settlement_ids_by_region
    }
    ctx.next_person_id = next_id
    ctx.region_effective_cap_multiplier = cap_multipliers
    ctx.region_display_label_overrides = display_overrides
    ctx.region_label_source = label_src
    ctx.region_city_rename_miss_streak = miss_streak
    ctx.region_prosperity_pool = region_prosperity_loaded
    ctx.region_treasury_balance = region_treasury_loaded
    ctx.spinoff_pending_families_by_region = spin_pending
    ctx.last_spinoff_sim_year_by_region = spin_last
    ctx.pending_settlement_moves = pending_moves
    if map_seed_raw:
        ctx.world_map_seed = map_seed_raw
    try:
        from library.government_checkpoint import load_government as _load_gov
        from library.world_save import _open_save as _open_save_gov

        with _open_save_gov(ctx.save_db_path) as conn_gov:
            _load_gov(
                ctx,
                conn_gov,
                valid_person_ids=frozenset(ctx.id_to_record.keys()),
            )
    except sqlite3.OperationalError:
        pass
    return True
