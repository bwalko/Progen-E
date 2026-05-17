"""Checkpoint simulation state (people, settlements, couples) into ``save.sqlite``.

Coexists with ``world_state`` in the same file. Uses JSON for ``Person`` payloads.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from library.mind_body import mind_body_from_genome
from library.person import Person
from library.geography import get_region
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

SAVE_SCHEMA_VERSION = 3
SAVE_SCHEMA_VERSION_META_KEY = "save_schema_version"

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
    "simulation_people",
    "simulation_couples",
    "simulation_paramours",
    "simulation_events",
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
    "birthplace_region_id",
    "birthplace_settlement_id",
    "current_settlement_id",
    "partner_person_id",
    "paramour_person_id",
    "last_birth_event_year",
    "job",
    "job_assigned_year",
    "job_era",
    "job_tier",
    "status_tendency",
    "leader_quality",
    "leader_tendency",
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

_PERSON_EXTENSION_KEYS: tuple[str, ...] = (
    "genome_composite_names",
    "genome_trait_phrases",
)


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
            np = replace(np, paramour_person_id=None)
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
    if version not in (0, SAVE_SCHEMA_VERSION):
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
            birthplace_region_id TEXT,
            birthplace_settlement_id TEXT,
            current_settlement_id TEXT,
            partner_person_id INTEGER,
            paramour_person_id INTEGER,
            last_birth_event_year INTEGER,
            job TEXT,
            job_assigned_year INTEGER,
            job_era TEXT,
            job_tier TEXT,
            status_tendency TEXT,
            leader_quality TEXT,
            leader_tendency TEXT,
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
        CREATE INDEX IF NOT EXISTS idx_simulation_people_birthyear
        ON simulation_people (birthyear);
        CREATE INDEX IF NOT EXISTS idx_simulation_people_current_settlement
        ON simulation_people (current_settlement_id);
        """
    )
    cols = set(_table_columns(conn, "simulation_people"))
    missing = [c for c in _PERSON_CHECKPOINT_COLUMNS if c not in cols]
    if missing:
        raise RuntimeError(
            "simulation_people uses a pre-v3 schema. Delete or rebuild save.sqlite "
            "before opening it with the compact people checkpoint schema."
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
            for table in _SAVE_REBUILD_TABLES:
                if (
                    table == "simulation_people"
                    and _table_exists(conn, table, schema=_SAVE_REBUILD_SOURCE_SCHEMA)
                    and "first_name"
                    not in _table_columns(conn, table, schema=_SAVE_REBUILD_SOURCE_SCHEMA)
                ):
                    raise RuntimeError(
                        "source simulation_people is older than compact schema v3; "
                        "delete and regenerate the pre-alpha save.sqlite"
                    )
                _copy_common_table_columns_from_attached_source(conn, table)
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
        region_id TEXT PRIMARY KEY,
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
        settlement_id TEXT PRIMARY KEY,
        region_id TEXT NOT NULL,
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
    ON simulation_settlements (region_id);
"""


def ensure_checkpoint_schema(conn: sqlite3.Connection) -> None:
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
        CREATE TABLE IF NOT EXISTS simulation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sim_year INTEGER,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_simulation_events_year
        ON simulation_events (sim_year);
        """
    )
    _ensure_simulation_people_table(conn)
    conn.executescript(_CREATE_SIMULATION_REGIONS)
    _ensure_simulation_settlements_table(conn)
    _migrate_cap_named_columns(conn)
    _migrate_simulation_regions_region_display_name(conn)
    _migrate_simulation_settlements_lifecycle_columns(conn)
    _migrate_simulation_settlements_empty_site_columns(conn)
    _migrate_simulation_settlements_prosperity_pool(conn)
    _migrate_simulation_regions_economy_columns(conn)
    _migrate_relationship_surname_convention_columns(conn)
    from library import government_checkpoint as _gov_ckpt

    _gov_ckpt.ensure_government_schema(conn)
    _ensure_supported_save_schema(conn)
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
    if "settlement_id" not in cols:
        raise RuntimeError(
            "simulation_settlements uses a pre-v2 schema. Delete or rebuild save.sqlite "
            "before opening it with the single-world schema."
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
    )


def ensure_checkpoint_schema_for_file(save_db_path: Path | str) -> None:
    with _open_save(save_db_path) as conn:
        ensure_checkpoint_schema(conn)


def clear_world_checkpoint(save_db_path: Path | str, *, world: str) -> None:
    """Remove checkpoint rows from this single-world save (not ``world_state``)."""
    with _open_save(save_db_path) as conn:
        ensure_checkpoint_schema(conn)
        conn.execute("DELETE FROM simulation_people")
        conn.execute("DELETE FROM simulation_settlements")
        conn.execute("DELETE FROM simulation_regions")
        conn.execute("DELETE FROM simulation_couples")
        conn.execute("DELETE FROM simulation_paramours")
        conn.execute("DELETE FROM simulation_meta")
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
) -> None:
    """Insert append-only simulation event rows. Each row is (sim_year, event_type, payload_dict)."""
    ts = created_at or _utc_now_iso()
    cur = conn.cursor()
    for sim_year, event_type, payload in rows:
        cur.execute(
            """
            INSERT INTO simulation_events (sim_year, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (sim_year, event_type, json.dumps(payload, separators=(",", ":")), ts),
        )


def flush_pending_simulation_events(ctx: "SimulationContext") -> None:
    """Persist buffered domain events from ``ctx`` to ``save.sqlite``."""
    pending = ctx._pending_simulation_events
    if not pending:
        return
    with _open_save(ctx.save_db_path) as conn:
        ensure_checkpoint_schema(conn)
        append_simulation_event_rows(conn, ctx.world.strip(), pending)
        conn.commit()
    pending.clear()


def flush_simulation_meta_checkpoint(ctx: "SimulationContext") -> None:
    """Persist ``next_person_id``, cap-multiplier JSON, spinoff state, etc. without full snapshot.

    Used when ``checkpoint_simulation_to_save(..., full_snapshot=False)`` so runs that
    flush events between sparse full snapshots still save resume-relevant meta.
    """
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
        conn.commit()


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
        with sqlite3.connect(path) as conn:
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
) -> tuple[dict[str, object], str]:
    """Split a person into scalar columns plus compact extension JSON."""
    raw = asdict(person)
    cols = {k: raw.get(k) for k in _PERSON_CHECKPOINT_COLUMNS}
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
        last_birth_event_year=(
            int(d["last_birth_event_year"])
            if d.get("last_birth_event_year") is not None
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
        genome_trait_phrases=_parse_genome_trait_phrases(
            d.get("genome_trait_phrases")
        ),
        birth_litter_size=max(1, int(d.get("birth_litter_size") or 1)),
    )
    if p.current_settlement_id is None and p.birthplace_settlement_id:
        p = replace(p, current_settlement_id=p.birthplace_settlement_id)
    return p


def checkpoint_simulation_snapshot(ctx: "SimulationContext") -> None:
    """Upsert archival rows for people and settlements; rewrite derived snapshot slices.

    ``simulation_people`` / ``simulation_settlements`` keep historical rows (``INSERT OR
    REPLACE`` only). Regions, couples, and paramours are still replaced from current ctx.
    After a successful commit, long-dead persons are dropped from RAM (see
    :func:`prune_ancient_dead_from_ram`).
    """
    with _open_save(ctx.save_db_path) as conn:
        ensure_checkpoint_schema(conn)
        cur = conn.cursor()
        cur.execute("DELETE FROM simulation_regions")
        cur.execute("DELETE FROM simulation_couples")
        cur.execute("DELETE FROM simulation_paramours")

        alive = ctx.current_people_ids
        trait_slots = _trait_slots_for_checkpoint(ctx)
        include_trait_slots = not bool(_trait_slots_from_config(ctx.db_path))
        for rec in ctx.people:
            person_cols, payload = _person_checkpoint_payload(
                rec.person,
                trait_slots,
                include_trait_slots=include_trait_slots,
            )
            column_names = (
                "person_id",
                "is_founder",
                "father_id",
                "mother_id",
                "is_alive",
                *_PERSON_CHECKPOINT_COLUMNS,
                "person_json",
            )
            values = (
                rec.person_id,
                1 if rec.is_founder else 0,
                rec.father_id,
                rec.mother_id,
                1 if rec.person_id in alive else 0,
                *(person_cols[c] for c in _PERSON_CHECKPOINT_COLUMNS),
                payload,
            )
            cols_sql = ", ".join(column_names)
            placeholders = ", ".join("?" for _ in column_names)
            cur.execute(
                f"""
                INSERT OR REPLACE INTO simulation_people ({cols_sql})
                VALUES ({placeholders})
                """,
                values,
            )

        by_region: dict[str, list[SettlementState]] = defaultdict(list)
        for settlement_id, st in ctx.settlements_by_id.items():
            by_region[st.region_id].append(st)
            cur.execute(
                """
                INSERT OR REPLACE INTO simulation_settlements (
                    settlement_id, region_id, level, population_cap, household_cap,
                    food_pressure, prosperity_pool, stability, market_pull,
                    display_name, etymology,
                    name_category_primary, name_category_secondary,
                    name_culture_primary, name_culture_secondary,
                    local_geography_json,
                    founded_sim_year, abandoned_sim_year, status,
                    consecutive_empty_years, site_slot
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    st.settlement_id,
                    st.region_id,
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
                ),
            )

        for region_id, bucket in by_region.items():
            tot_pop, tot_hh, fp, stb, mp = _aggregate_region_metrics(bucket)
            r_label = _resolve_region_display_name_for_checkpoint(ctx, region_id, bucket[0])
            cur.execute(
                """
                INSERT INTO simulation_regions (
                    region_id, region_display_name,
                    total_population_cap, total_household_cap,
                    food_pressure, stability, market_pull,
                    prosperity_pool, treasury_balance
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    region_id,
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

        for i, (a_id, b_id) in enumerate(ctx.couples):
            convention = getattr(ctx, "surname_conventions_by_pair", {}).get(
                tuple(sorted((int(a_id), int(b_id))))
            )
            cur.execute(
                """
                INSERT INTO simulation_couples (
                    sort_order, person_a_id, person_b_id, surname_convention
                )
                VALUES (?, ?, ?, ?)
                """,
                (i, a_id, b_id, convention),
            )

        for i, (a_id, b_id) in enumerate(ctx.paramours):
            convention = getattr(ctx, "surname_conventions_by_pair", {}).get(
                tuple(sorted((int(a_id), int(b_id))))
            )
            cur.execute(
                """
                INSERT INTO simulation_paramours (
                    sort_order, person_a_id, person_b_id, surname_convention
                )
                VALUES (?, ?, ?, ?)
                """,
                (i, a_id, b_id, convention),
            )

        from library.government_checkpoint import checkpoint_government as _checkpoint_gov

        _checkpoint_gov(ctx, cur)

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
        conn.commit()
    prune_ancient_dead_from_ram(ctx)


def checkpoint_simulation_to_save(
    ctx: "SimulationContext", *, full_snapshot: bool = True
) -> None:
    """Flush pending events; optionally write full snapshot tables to ``save.sqlite``.

    When ``full_snapshot`` is false, still writes ``simulation_meta`` for
    ``next_person_id``, cap multipliers, region display label overrides, naming
    aux state, and settlement spinoff accrual so resume stays aligned with RAM between full snapshots.
    """
    flush_pending_simulation_events(ctx)
    if full_snapshot:
        checkpoint_simulation_snapshot(ctx)
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
        if row is None or int(row["c"] or 0) == 0:
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
            "SELECT * FROM simulation_people ORDER BY person_id",
        ).fetchall()
        trait_slots = _trait_slots_from_config(ctx.db_path)
        loaded: list[SimulationPersonRecord] = []
        alive: set[int] = set()
        id_to: dict[int, SimulationPersonRecord] = {}
        for row in people_rows:
            pid = int(row["person_id"])
            person = _person_from_dict(_person_dict_from_checkpoint_row(row, trait_slots))
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

        settle_rows = conn.execute(
            "SELECT * FROM simulation_settlements",
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
            "SELECT * FROM simulation_regions",
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

        merged_region_labels = {**region_labels}
        for rk, rv in display_overrides.items():
            merged_region_labels[rk] = rv

        for rec in loaded:
            p = rec.person
            np = p
            if p.partner_person_id is not None and p.partner_person_id not in id_to:
                np = replace(np, partner_person_id=None)
            if p.paramour_person_id is not None and p.paramour_person_id not in id_to:
                np = replace(np, paramour_person_id=None)
            if np is not p:
                rec.person = np

    ctx.people = loaded
    ctx.id_to_record = id_to
    ctx.current_people_ids = alive
    if hasattr(ctx, "invalidate_alive_census_cache"):
        ctx.invalidate_alive_census_cache()
    ctx.couples = couples
    ctx.paramours = paramours
    ctx.surname_conventions_by_pair = surname_conventions_by_pair
    for a_id, b_id in couples:
        ra = id_to.get(a_id)
        rb = id_to.get(b_id)
        if ra is not None:
            ra.person = replace(ra.person, partner_person_id=b_id)
        if rb is not None:
            rb.person = replace(rb.person, partner_person_id=a_id)
    for a_id, b_id in paramours:
        ra = id_to.get(a_id)
        rb = id_to.get(b_id)
        if ra is not None:
            ra.person = replace(ra.person, paramour_person_id=b_id)
        if rb is not None:
            rb.person = replace(rb.person, paramour_person_id=a_id)
    ctx.settlements_by_id = _enrich_settlements_region_display_names(
        ctx, settlements_by_id, merged_region_labels
    )
    ctx.settlement_ids_by_region = {
        rid: [sid for sid in settlement_ids_by_region[rid]]
        for rid in settlement_ids_by_region
    }
    if hasattr(ctx, "refresh_all_region_local_geographies"):
        ctx.refresh_all_region_local_geographies()
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
