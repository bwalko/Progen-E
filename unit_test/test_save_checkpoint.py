"""Tests for persisting simulation state to save.sqlite checkpoints."""

from __future__ import annotations

import json
import random
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from library.config_import import load_all_csvs_into_sqlite
from library.generator import generate_person_random
from library.geography import _population_scale_cache, get_region
from library.passive_population import PassiveCohort, PassivePerson
from library.simulation_context import SimulationContext
from library.world_save import (
    SAVE_SCHEMA_VERSION,
    REGION_EFFECTIVE_CAP_MULTIPLIER_META_KEY,
    append_simulation_event_rows,
    checkpoint_simulation_to_save,
    clear_world_checkpoint,
    ensure_checkpoint_schema,
    ensure_checkpoint_schema_for_file,
    event_consequence_annual_tick_for_save,
    mark_event_record_lost,
    parse_region_effective_cap_multipliers,
    rebuild_save_sqlite_for_schema_upgrade,
    rediscover_event_record,
    save_schema_version,
    seal_event_record,
    mark_event_record_rumored,
    try_load_simulation_checkpoint,
)


def _force_population_scale(cfg_path: Path, scale: float) -> None:
    with closing(sqlite3.connect(cfg_path)) as conn:
        conn.execute("UPDATE world_start SET population_scale = ?", (str(scale),))
        conn.commit()
    _population_scale_cache.clear()


class TestSaveCheckpoint(unittest.TestCase):
    def test_save_schema_version_and_rebuild_copy(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            sav = root / "save.sqlite"
            rebuilt = root / "rebuilt.sqlite"

            ensure_checkpoint_schema_for_file(sav)
            person_payload = {
                "first_name": "Test",
                "last_name": "Person",
                "gender": "female",
                "ethnic": "human",
                "species": "human",
                "birthyear": 1000,
                "genome": {},
                "mind_body": {},
            }
            with closing(sqlite3.connect(sav)) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS world_state (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        start_year INTEGER NOT NULL,
                        current_year INTEGER NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO world_state (id, start_year, current_year)
                    VALUES (1, ?, ?)
                    """,
                    (1000, 1001),
                )
                conn.execute(
                    """
                    INSERT INTO simulation_people (
                        person_id, is_founder, father_id, mother_id,
                        is_alive, first_name, last_name, gender, ethnic,
                        species, birthyear, person_json
                    )
                    VALUES (?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        1,
                        1,
                        "Test",
                        "Person",
                        "female",
                        "human",
                        "human",
                        1000,
                        json.dumps(person_payload, separators=(",", ":")),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO simulation_events (
                        sim_year, event_type, payload_json, created_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        1000,
                        "founder_created",
                        json.dumps({"person_id": 1}, separators=(",", ":")),
                        "2026-01-01T00:00:00+00:00",
                    ),
                )
                self.assertEqual(save_schema_version(conn), SAVE_SCHEMA_VERSION)
                self.assertEqual(
                    conn.execute("PRAGMA user_version").fetchone()[0],
                    SAVE_SCHEMA_VERSION,
                )
                conn.commit()

            out = rebuild_save_sqlite_for_schema_upgrade(sav, output_path=rebuilt)
            self.assertEqual(out, rebuilt)

            with closing(sqlite3.connect(rebuilt)) as conn:
                self.assertEqual(save_schema_version(conn), SAVE_SCHEMA_VERSION)
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM world_state").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM simulation_people").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM simulation_events").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute(
                        """
                        SELECT COUNT(*) FROM simulation_event_people
                        WHERE person_id = 1 AND role = 'subject'
                        """
                    ).fetchone()[0],
                    1,
                )
                event_row = conn.execute(
                    """
                    SELECT primary_person_id
                    FROM simulation_events
                    WHERE event_type = 'founder_created'
                    """
                ).fetchone()
                self.assertEqual(int(event_row[0]), 1)
                row = conn.execute(
                    "SELECT first_name, person_json FROM simulation_people WHERE person_id = 1"
                ).fetchone()
                self.assertEqual(row[0], "Test")
                self.assertEqual(json.loads(row[1])["first_name"], "Test")

    def test_v3_events_backfill_to_normalized_event_people(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            sav = Path(td) / "save.sqlite"
            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute(
                    """
                    CREATE TABLE save_metadata (
                        meta_key TEXT PRIMARY KEY,
                        meta_value TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO save_metadata (meta_key, meta_value)
                    VALUES ('save_schema_version', '3')
                    """
                )
                conn.execute("PRAGMA user_version = 3")
                conn.execute(
                    """
                    CREATE TABLE simulation_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        sim_year INTEGER,
                        event_type TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO simulation_events (
                        sim_year, event_type, payload_json, created_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        1001,
                        "household_childcare_shortfall",
                        json.dumps(
                            {
                                "household_member_ids": [1],
                                "dependent_minor_ids": [2],
                                "victim_person_id": 2,
                                "settlement_id": "aeria_north:settlement:1",
                            },
                            separators=(",", ":"),
                        ),
                        "2026-01-01T00:00:00+00:00",
                    ),
                )
                ensure_checkpoint_schema(conn)

                self.assertEqual(save_schema_version(conn), SAVE_SCHEMA_VERSION)
                links = {
                    (int(r["person_id"]), str(r["role"]))
                    for r in conn.execute(
                        """
                        SELECT person_id, role
                        FROM simulation_event_people
                        ORDER BY person_id, role
                        """
                    )
                }
                self.assertIn((1, "household_member"), links)
                self.assertIn((2, "dependent_minor"), links)
                self.assertIn((2, "victim"), links)
                event = conn.execute(
                    """
                    SELECT primary_person_id, secondary_person_id, settlement_id
                    FROM simulation_events_readable
                    WHERE event_type = 'household_childcare_shortfall'
                    """
                ).fetchone()
                self.assertEqual(int(event["primary_person_id"]), 2)
                self.assertEqual(int(event["secondary_person_id"]), 1)
                self.assertEqual(
                    str(event["settlement_id"]), "aeria_north:settlement:1"
                )

    def test_appended_events_create_default_memory_records(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            sav = Path(td) / "save.sqlite"
            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                append_simulation_event_rows(
                    conn,
                    "default",
                    [
                        (
                            1000,
                            "paramour_formed",
                            {
                                "person_a_id": 1,
                                "person_b_id": 2,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                        (
                            1001,
                            "office_succession",
                            {
                                "holder_person_id": 3,
                                "previous_holder_id": 4,
                                "seat_id": 7,
                            },
                        ),
                        (
                            1002,
                            "career_fitness_updated",
                            {"person_id": 5, "fitness_score": 0.71},
                        ),
                        (
                            1003,
                            "settlement_commercial_outpost_founded",
                            {
                                "settlement_id": "boreas_port:settlement:2",
                                "region_id": "boreas_port",
                                "mother_settlement_id": "aeria_port:settlement:1",
                                "trade_network_id": "aeria_port:settlement:1",
                                "founder_person_ids": [6, 7],
                            },
                        ),
                    ],
                    created_at="2026-01-01T00:00:00+00:00",
                )

                rows = {
                    str(r["event_type"]): r
                    for r in conn.execute(
                        """
                        SELECT event_type, record_type, visibility_state,
                               confidence, preserving_settlement_id,
                               preserving_region_id, public_actor_person_id,
                               prose_variant_key
                        FROM simulation_event_records_readable
                        ORDER BY event_id
                        """
                    )
                }

            self.assertEqual(
                str(rows["paramour_formed"]["record_type"]), "household_secret"
            )
            self.assertEqual(
                str(rows["paramour_formed"]["visibility_state"]), "private_known"
            )
            self.assertEqual(
                str(rows["paramour_formed"]["preserving_settlement_id"]),
                "aeria_north:settlement:1",
            )
            self.assertEqual(
                str(rows["paramour_formed"]["preserving_region_id"]), "aeria_north"
            )
            self.assertEqual(int(rows["paramour_formed"]["public_actor_person_id"]), 1)
            self.assertEqual(
                str(rows["paramour_formed"]["prose_variant_key"]),
                "household_secret.private_known.default",
            )
            self.assertEqual(
                str(rows["office_succession"]["record_type"]), "court_chronicle"
            )
            self.assertEqual(
                str(rows["office_succession"]["visibility_state"]), "public_known"
            )
            self.assertEqual(
                str(rows["career_fitness_updated"]["record_type"]), "admin_note"
            )
            self.assertEqual(
                str(rows["career_fitness_updated"]["visibility_state"]), "admin_known"
            )
            self.assertEqual(float(rows["career_fitness_updated"]["confidence"]), 1.0)
            self.assertEqual(
                str(rows["settlement_commercial_outpost_founded"]["record_type"]),
                "settlement_chronicle",
            )
            self.assertEqual(
                str(rows["settlement_commercial_outpost_founded"]["visibility_state"]),
                "public_known",
            )
            self.assertEqual(
                str(
                    rows["settlement_commercial_outpost_founded"][
                        "preserving_region_id"
                    ]
                ),
                "boreas_port",
            )

    def test_v8_knowledge_events_backfill_domain_state(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            sav = Path(td) / "save.sqlite"
            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                conn.executescript(
                    """
                    CREATE TABLE save_metadata (
                        meta_key TEXT PRIMARY KEY,
                        meta_value TEXT NOT NULL
                    );
                    INSERT INTO save_metadata (meta_key, meta_value)
                    VALUES ('save_schema_version', '8');
                    PRAGMA user_version = 8;

                    CREATE TABLE simulation_region_lookup (
                        region_key INTEGER PRIMARY KEY AUTOINCREMENT,
                        region_id TEXT NOT NULL UNIQUE
                    );
                    CREATE TABLE simulation_settlement_lookup (
                        settlement_key INTEGER PRIMARY KEY AUTOINCREMENT,
                        settlement_id TEXT NOT NULL UNIQUE,
                        region_key INTEGER NOT NULL
                    );
                    INSERT INTO simulation_region_lookup (region_key, region_id)
                    VALUES (1, 'aeria_north');
                    INSERT INTO simulation_settlement_lookup (
                        settlement_key, settlement_id, region_key
                    )
                    VALUES (1, 'aeria_north:settlement:1', 1);

                    CREATE TABLE simulation_events (
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
                    """
                )
                conn.execute(
                    """
                    INSERT INTO simulation_events (
                        id, sim_year, event_type, primary_person_id,
                        settlement_key, region_key, payload_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        7,
                        1002,
                        "knowledge_culture",
                        41,
                        1,
                        1,
                        json.dumps(
                            {
                                "creator_person_id": 41,
                                "incident_kind": "improved_plow",
                                "knowledge_domain": "toolmaking",
                                "novelty_value": 0.2,
                                "consequences": {
                                    "knowledge_state": {
                                        "domain": "toolmaking",
                                        "state_delta": 0.07,
                                    }
                                },
                            },
                            separators=(",", ":"),
                        ),
                        "2026-01-01T00:00:00+00:00",
                    ),
                )

                ensure_checkpoint_schema(conn)

                self.assertEqual(save_schema_version(conn), SAVE_SCHEMA_VERSION)
                row = conn.execute(
                    """
                    SELECT region_id, domain, domain_score, breakthrough_count,
                           first_event_year, latest_event_year, first_event_id,
                           latest_event_id, latest_incident_kind,
                           latest_creator_person_id, latest_settlement_id
                    FROM simulation_domain_states_readable
                    """
                ).fetchone()
                processed = conn.execute(
                    """
                    SELECT meta_value
                    FROM save_metadata
                    WHERE meta_key = 'simulation_domain_states_backfilled_event_id'
                    """
                ).fetchone()

            self.assertIsNotNone(row)
            self.assertEqual(str(row["region_id"]), "aeria_north")
            self.assertEqual(str(row["domain"]), "toolmaking")
            self.assertAlmostEqual(float(row["domain_score"]), 0.07)
            self.assertEqual(int(row["breakthrough_count"]), 1)
            self.assertEqual(int(row["first_event_year"]), 1002)
            self.assertEqual(int(row["latest_event_year"]), 1002)
            self.assertEqual(int(row["first_event_id"]), 7)
            self.assertEqual(int(row["latest_event_id"]), 7)
            self.assertEqual(str(row["latest_incident_kind"]), "improved_plow")
            self.assertEqual(int(row["latest_creator_person_id"]), 41)
            self.assertEqual(
                str(row["latest_settlement_id"]), "aeria_north:settlement:1"
            )
            self.assertIsNotNone(processed)
            self.assertEqual(str(processed["meta_value"]), "7")

    def test_v9_public_virtue_events_backfill_obligations(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            sav = Path(td) / "save.sqlite"
            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                conn.executescript(
                    """
                    CREATE TABLE save_metadata (
                        meta_key TEXT PRIMARY KEY,
                        meta_value TEXT NOT NULL
                    );
                    INSERT INTO save_metadata (meta_key, meta_value)
                    VALUES ('save_schema_version', '9');
                    PRAGMA user_version = 9;

                    CREATE TABLE simulation_region_lookup (
                        region_key INTEGER PRIMARY KEY AUTOINCREMENT,
                        region_id TEXT NOT NULL UNIQUE
                    );
                    CREATE TABLE simulation_settlement_lookup (
                        settlement_key INTEGER PRIMARY KEY AUTOINCREMENT,
                        settlement_id TEXT NOT NULL UNIQUE,
                        region_key INTEGER NOT NULL
                    );
                    INSERT INTO simulation_region_lookup (region_key, region_id)
                    VALUES (1, 'aeria_north');
                    INSERT INTO simulation_settlement_lookup (
                        settlement_key, settlement_id, region_key
                    )
                    VALUES (1, 'aeria_north:settlement:1', 1);

                    CREATE TABLE simulation_events (
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
                    """
                )
                conn.execute(
                    """
                    INSERT INTO simulation_events (
                        id, sim_year, event_type, primary_person_id,
                        secondary_person_id, settlement_key, region_key,
                        payload_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        8,
                        1003,
                        "public_virtue",
                        51,
                        52,
                        1,
                        1,
                        json.dumps(
                            {
                                "benefactor_person_id": 51,
                                "beneficiary_person_id": 52,
                                "incident_kind": "famine_mercy",
                                "relief_value": 0.18,
                                "consequences": {
                                    "relief": {
                                        "beneficiary": {"prosperity_delta": 0.1},
                                        "benefactor": {"prosperity_delta": -0.04},
                                    }
                                },
                            },
                            separators=(",", ":"),
                        ),
                        "2026-01-01T00:00:00+00:00",
                    ),
                )

                ensure_checkpoint_schema(conn)

                self.assertEqual(save_schema_version(conn), SAVE_SCHEMA_VERSION)
                row = conn.execute(
                    """
                    SELECT source_event_id, source_event_year, source_event_type,
                           obligation_key, obligation_type, status,
                           owed_by_person_id, owed_to_person_id, region_id,
                           settlement_id, strength, start_year, expected_end_year,
                           details_json
                    FROM simulation_obligations_readable
                    """
                ).fetchone()
                processed = conn.execute(
                    """
                    SELECT meta_value
                    FROM save_metadata
                    WHERE meta_key = 'simulation_obligations_backfilled_event_id'
                    """
                ).fetchone()

            self.assertIsNotNone(row)
            self.assertEqual(int(row["source_event_id"]), 8)
            self.assertEqual(int(row["source_event_year"]), 1003)
            self.assertEqual(str(row["source_event_type"]), "public_virtue")
            self.assertEqual(str(row["obligation_key"]), "beneficiary_to_benefactor")
            self.assertEqual(str(row["obligation_type"]), "relief_debt")
            self.assertEqual(str(row["status"]), "active")
            self.assertEqual(int(row["owed_by_person_id"]), 52)
            self.assertEqual(int(row["owed_to_person_id"]), 51)
            self.assertEqual(str(row["region_id"]), "aeria_north")
            self.assertEqual(
                str(row["settlement_id"]), "aeria_north:settlement:1"
            )
            self.assertGreater(float(row["strength"]), 0.0)
            self.assertEqual(int(row["start_year"]), 1003)
            self.assertEqual(int(row["expected_end_year"]), 1015)
            self.assertEqual(
                json.loads(str(row["details_json"])),
                {"source_role": "public_virtue_relief"},
            )
            self.assertIsNotNone(processed)
            self.assertEqual(str(processed["meta_value"]), "8")

    def test_v10_public_virtue_events_backfill_reputation_marks(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            sav = Path(td) / "save.sqlite"
            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                conn.executescript(
                    """
                    CREATE TABLE save_metadata (
                        meta_key TEXT PRIMARY KEY,
                        meta_value TEXT NOT NULL
                    );
                    INSERT INTO save_metadata (meta_key, meta_value)
                    VALUES ('save_schema_version', '10');
                    PRAGMA user_version = 10;

                    CREATE TABLE simulation_region_lookup (
                        region_key INTEGER PRIMARY KEY AUTOINCREMENT,
                        region_id TEXT NOT NULL UNIQUE
                    );
                    CREATE TABLE simulation_settlement_lookup (
                        settlement_key INTEGER PRIMARY KEY AUTOINCREMENT,
                        settlement_id TEXT NOT NULL UNIQUE,
                        region_key INTEGER NOT NULL
                    );
                    INSERT INTO simulation_region_lookup (region_key, region_id)
                    VALUES (1, 'aeria_north');
                    INSERT INTO simulation_settlement_lookup (
                        settlement_key, settlement_id, region_key
                    )
                    VALUES (1, 'aeria_north:settlement:1', 1);

                    CREATE TABLE simulation_events (
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
                    """
                )
                conn.execute(
                    """
                    INSERT INTO simulation_events (
                        id, sim_year, event_type, primary_person_id,
                        secondary_person_id, settlement_key, region_key,
                        payload_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        9,
                        1004,
                        "public_virtue",
                        61,
                        62,
                        1,
                        1,
                        json.dumps(
                            {
                                "benefactor_person_id": 61,
                                "beneficiary_person_id": 62,
                                "incident_kind": "heroic_rescue",
                                "consequences": {
                                    "public_reputation": {
                                        "person_id": 61,
                                        "leader_tendency_before": "low",
                                        "leader_tendency_after": "medium",
                                    }
                                },
                            },
                            separators=(",", ":"),
                        ),
                        "2026-01-01T00:00:00+00:00",
                    ),
                )

                ensure_checkpoint_schema(conn)

                self.assertEqual(save_schema_version(conn), SAVE_SCHEMA_VERSION)
                row = conn.execute(
                    """
                    SELECT source_event_id, source_event_year, source_event_type,
                           mark_key, person_id, reputation_axis,
                           reputation_before, reputation_after, direction,
                           mark_strength, region_id, settlement_id, mark_year,
                           details_json
                    FROM simulation_reputation_marks_readable
                    """
                ).fetchone()
                processed = conn.execute(
                    """
                    SELECT meta_value
                    FROM save_metadata
                    WHERE meta_key = 'simulation_reputation_marks_backfilled_event_id'
                    """
                ).fetchone()

            self.assertIsNotNone(row)
            self.assertEqual(int(row["source_event_id"]), 9)
            self.assertEqual(int(row["source_event_year"]), 1004)
            self.assertEqual(str(row["source_event_type"]), "public_virtue")
            self.assertEqual(str(row["mark_key"]), "leadership:61")
            self.assertEqual(int(row["person_id"]), 61)
            self.assertEqual(str(row["reputation_axis"]), "leadership")
            self.assertEqual(str(row["reputation_before"]), "low")
            self.assertEqual(str(row["reputation_after"]), "medium")
            self.assertEqual(str(row["direction"]), "positive")
            self.assertGreater(float(row["mark_strength"]), 0.0)
            self.assertEqual(str(row["region_id"]), "aeria_north")
            self.assertEqual(
                str(row["settlement_id"]), "aeria_north:settlement:1"
            )
            self.assertEqual(int(row["mark_year"]), 1004)
            self.assertEqual(
                json.loads(str(row["details_json"])),
                {"source_role": "public_virtue_reputation"},
            )
            self.assertIsNotNone(processed)
            self.assertEqual(str(processed["meta_value"]), "9")

    def test_v11_affair_scandal_events_backfill_legal_fallout(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            sav = Path(td) / "save.sqlite"
            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                conn.executescript(
                    """
                    CREATE TABLE save_metadata (
                        meta_key TEXT PRIMARY KEY,
                        meta_value TEXT NOT NULL
                    );
                    INSERT INTO save_metadata (meta_key, meta_value)
                    VALUES ('save_schema_version', '11');
                    PRAGMA user_version = 11;

                    CREATE TABLE simulation_region_lookup (
                        region_key INTEGER PRIMARY KEY AUTOINCREMENT,
                        region_id TEXT NOT NULL UNIQUE
                    );
                    CREATE TABLE simulation_settlement_lookup (
                        settlement_key INTEGER PRIMARY KEY AUTOINCREMENT,
                        settlement_id TEXT NOT NULL UNIQUE,
                        region_key INTEGER NOT NULL
                    );
                    INSERT INTO simulation_region_lookup (region_key, region_id)
                    VALUES (1, 'aeria_north');
                    INSERT INTO simulation_settlement_lookup (
                        settlement_key, settlement_id, region_key
                    )
                    VALUES (1, 'aeria_north:settlement:1', 1);

                    CREATE TABLE simulation_events (
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
                    """
                )
                conn.execute(
                    """
                    INSERT INTO simulation_events (
                        id, sim_year, event_type, primary_person_id,
                        secondary_person_id, settlement_key, region_key,
                        payload_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        10,
                        1005,
                        "affair_scandal",
                        71,
                        72,
                        1,
                        1,
                        json.dumps(
                            {
                                "accused_person_id": 71,
                                "betrayed_partner_person_id": 72,
                                "paramour_person_id": 73,
                                "betrayed_partner_person_ids": [72],
                                "incident_kind": "inheritance_scandal",
                                "historical_importance": 0.42,
                                "consequences": {
                                    "ended_paramour": True,
                                },
                            },
                            separators=(",", ":"),
                        ),
                        "2026-01-01T00:00:00+00:00",
                    ),
                )

                ensure_checkpoint_schema(conn)

                self.assertEqual(save_schema_version(conn), SAVE_SCHEMA_VERSION)
                row = conn.execute(
                    """
                    SELECT source_event_id, source_event_year, source_event_type,
                           fallout_key, fallout_type, status,
                           principal_person_id, opposing_person_id,
                           related_person_id, region_id, settlement_id, severity,
                           start_year, expected_resolution_year, details_json
                    FROM simulation_legal_fallout_readable
                    """
                ).fetchone()
                processed = conn.execute(
                    """
                    SELECT meta_value
                    FROM save_metadata
                    WHERE meta_key = 'simulation_legal_fallout_backfilled_event_id'
                    """
                ).fetchone()

            self.assertIsNotNone(row)
            self.assertEqual(int(row["source_event_id"]), 10)
            self.assertEqual(int(row["source_event_year"]), 1005)
            self.assertEqual(str(row["source_event_type"]), "affair_scandal")
            self.assertEqual(str(row["fallout_key"]), "inheritance:71:72:73")
            self.assertEqual(str(row["fallout_type"]), "inheritance_dispute")
            self.assertEqual(str(row["status"]), "active")
            self.assertEqual(int(row["principal_person_id"]), 71)
            self.assertEqual(int(row["opposing_person_id"]), 72)
            self.assertEqual(int(row["related_person_id"]), 73)
            self.assertEqual(str(row["region_id"]), "aeria_north")
            self.assertEqual(str(row["settlement_id"]), "aeria_north:settlement:1")
            self.assertGreater(float(row["severity"]), 0.0)
            self.assertEqual(int(row["start_year"]), 1005)
            self.assertEqual(int(row["expected_resolution_year"]), 1013)
            self.assertEqual(
                json.loads(str(row["details_json"])),
                {
                    "source_role": "affair_scandal_legal_fallout",
                    "incident_kind": "inheritance_scandal",
                    "betrayed_partner_person_ids": [72],
                },
            )
            self.assertIsNotNone(processed)
            self.assertEqual(str(processed["meta_value"]), "10")

    def test_deep_consequence_payloads_persist_factions_and_institutions(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            sav = Path(td) / "save.sqlite"
            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                murder_id, knowledge_id = append_simulation_event_rows(
                    conn,
                    "default",
                    [
                        (
                            1001,
                            "murder",
                            {
                                "killer_person_id": 1,
                                "victim_person_id": 2,
                                "incident_kind": "feud_killing",
                                "historical_importance": 0.7,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                        (
                            1002,
                            "knowledge_culture",
                            {
                                "creator_person_id": 3,
                                "patron_person_id": 4,
                                "incident_kind": "calendar_reform",
                                "knowledge_domain": "calendar",
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                                "consequences": {
                                    "knowledge_state": {
                                        "domain": "calendar",
                                        "state_delta": 0.12,
                                    },
                                    "institutions": [
                                        {
                                            "institution_key": "aeria_north:school:calendar",
                                            "institution_type": "school",
                                            "focus_domain": "calendar",
                                            "strength_delta": 0.04,
                                        },
                                        {
                                            "institution_key": "aeria_north:guild:calendar",
                                            "institution_type": "guild",
                                            "focus_domain": "calendar",
                                            "strength_delta": 0.03,
                                        },
                                        {
                                            "institution_key": "aeria_north:doctrine:calendar",
                                            "institution_type": "doctrine",
                                            "focus_domain": "calendar",
                                            "strength_delta": 0.05,
                                        },
                                        {
                                            "institution_key": "aeria_north:craft_institution:calendar",
                                            "institution_type": "craft_institution",
                                            "focus_domain": "calendar",
                                            "strength_delta": 0.02,
                                        },
                                    ],
                                },
                            },
                        ),
                    ],
                    created_at="2026-01-01T00:00:00+00:00",
                )
                faction = conn.execute(
                    """
                    SELECT source_event_id, memory_type, polarity, strength,
                           principal_person_id, opposing_person_id, region_id
                    FROM simulation_faction_memory_readable
                    WHERE source_event_id = ?
                    """,
                    (murder_id,),
                ).fetchone()
                institutions = conn.execute(
                    """
                    SELECT institution_type, focus_domain, founding_event_id,
                           latest_event_id, strength, influence_score, region_id
                    FROM simulation_institutions_readable
                    WHERE latest_event_id = ?
                    ORDER BY institution_type
                    """,
                    (knowledge_id,),
                ).fetchall()

            self.assertIsNotNone(faction)
            self.assertEqual(str(faction["memory_type"]), "violent_grievance")
            self.assertEqual(str(faction["polarity"]), "negative")
            self.assertEqual(int(faction["principal_person_id"]), 2)
            self.assertEqual(int(faction["opposing_person_id"]), 1)
            self.assertEqual(str(faction["region_id"]), "aeria_north")
            self.assertGreater(float(faction["strength"]), 0.0)
            institution_types = {str(row["institution_type"]) for row in institutions}
            self.assertEqual(
                institution_types,
                {"school", "guild", "doctrine", "craft_institution"},
            )
            self.assertTrue(all(str(row["focus_domain"]) == "calendar" for row in institutions))
            self.assertTrue(all(int(row["founding_event_id"]) == knowledge_id for row in institutions))
            self.assertTrue(all(float(row["strength"]) > 0.0 for row in institutions))

    def test_annual_consequence_tick_resolves_legal_fallout_and_diffuses_domains(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                append_simulation_event_rows(
                    conn,
                    "default",
                    [
                        (
                            1001,
                            "affair_scandal",
                            {
                                "accused_person_id": 11,
                                "betrayed_partner_person_id": 12,
                                "paramour_person_id": 13,
                                "incident_kind": "inheritance_scandal",
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                                "consequences": {
                                    "legal_fallout": [
                                        {
                                            "fallout_key": "inheritance:11:12:13",
                                            "fallout_type": "inheritance_dispute",
                                            "status": "active",
                                            "principal_person_id": 11,
                                            "opposing_person_id": 12,
                                            "related_person_id": 13,
                                            "severity": 0.72,
                                            "start_year": 1001,
                                            "expected_resolution_year": 1002,
                                            "settlement_id": "aeria_north:settlement:1",
                                            "region_id": "aeria_north",
                                        }
                                    ]
                                },
                            },
                        ),
                        (
                            1001,
                            "knowledge_culture",
                            {
                                "creator_person_id": 21,
                                "incident_kind": "shipbuilding_advance",
                                "knowledge_domain": "shipbuilding",
                                "settlement_id": "aeria_port:settlement:1",
                                "region_id": "aeria_port",
                                "consequences": {
                                    "knowledge_state": {
                                        "domain": "shipbuilding",
                                        "state_delta": 0.2,
                                    }
                                },
                            },
                        ),
                    ],
                    created_at="2026-01-01T00:00:00+00:00",
                )
                conn.commit()

            summary = event_consequence_annual_tick_for_save(
                sav,
                config_db_path=cfg,
                year=1002,
                world="default",
            )

            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                fallout = conn.execute(
                    """
                    SELECT status, resolved_year
                    FROM simulation_legal_fallout_readable
                    WHERE fallout_key = 'inheritance:11:12:13'
                    """
                ).fetchone()
                adjudication = conn.execute(
                    """
                    SELECT adjudication_type, outcome, principal_result,
                           opposing_result, adjudication_year
                    FROM simulation_legal_adjudications_readable
                    WHERE fallout_id = (
                        SELECT fallout_id
                        FROM simulation_legal_fallout_readable
                        WHERE fallout_key = 'inheritance:11:12:13'
                    )
                    """
                ).fetchone()
                diffusion = conn.execute(
                    """
                    SELECT source_region_id, target_region_id, domain, state_delta,
                           target_domain_score_before, target_domain_score_after
                    FROM simulation_domain_diffusion_readable
                    WHERE domain = 'shipbuilding'
                    ORDER BY diffusion_id
                    """
                ).fetchone()

            self.assertEqual(summary["legal_adjudications"], 1)
            self.assertGreaterEqual(summary["domain_diffusions"], 1)
            self.assertEqual(str(fallout["status"]), "resolved")
            self.assertEqual(int(fallout["resolved_year"]), 1002)
            self.assertIsNotNone(adjudication)
            self.assertEqual(
                str(adjudication["adjudication_type"]),
                "inheritance_dispute_resolution",
            )
            self.assertEqual(str(adjudication["outcome"]), "inheritance_split")
            self.assertEqual(int(adjudication["adjudication_year"]), 1002)
            self.assertIsNotNone(diffusion)
            self.assertEqual(str(diffusion["source_region_id"]), "aeria_port")
            self.assertEqual(str(diffusion["domain"]), "shipbuilding")
            self.assertGreater(float(diffusion["state_delta"]), 0.0)
            self.assertGreater(
                float(diffusion["target_domain_score_after"]),
                float(diffusion["target_domain_score_before"]),
            )

    def test_event_record_visibility_state_transitions(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            sav = Path(td) / "save.sqlite"
            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                event_id = append_simulation_event_rows(
                    conn,
                    "default",
                    [(1000, "death", {"person_id": 11})],
                    created_at="2026-01-01T00:00:00+00:00",
                )[0]

                mark_event_record_lost(conn, event_id, lost_year=1010)
                lost = conn.execute(
                    """
                    SELECT visibility_state, lost_year, prose_variant_key
                    FROM simulation_event_records_readable
                    WHERE event_id = ?
                    """,
                    (event_id,),
                ).fetchone()
                self.assertEqual(str(lost["visibility_state"]), "lost")
                self.assertEqual(int(lost["lost_year"]), 1010)
                self.assertEqual(
                    str(lost["prose_variant_key"]),
                    "mortuary_memory.lost.default",
                )

                seal_event_record(
                    conn,
                    event_id,
                    sealed_year=1011,
                    source_institution_id="ducal_archive",
                )
                sealed = conn.execute(
                    """
                    SELECT visibility_state, lost_year, source_institution_id,
                           prose_variant_key
                    FROM simulation_event_records_readable
                    WHERE event_id = ?
                    """,
                    (event_id,),
                ).fetchone()
                self.assertEqual(str(sealed["visibility_state"]), "sealed")
                self.assertEqual(int(sealed["lost_year"]), 1010)
                self.assertEqual(str(sealed["source_institution_id"]), "ducal_archive")
                self.assertEqual(
                    str(sealed["prose_variant_key"]),
                    "mortuary_memory.sealed.default",
                )

                mark_event_record_rumored(
                    conn,
                    event_id,
                    rumor_year=1012,
                    confidence=0.37,
                    source_person_id=21,
                    distortion={"public_cause": "wolf attack", "true_cause_hidden": True},
                )
                rumored = conn.execute(
                    """
                    SELECT visibility_state, known_since_year, confidence,
                           source_person_id, distortion_json, prose_variant_key
                    FROM simulation_event_records_readable
                    WHERE event_id = ?
                    """,
                    (event_id,),
                ).fetchone()
            self.assertEqual(str(rumored["visibility_state"]), "rumored")
            self.assertEqual(int(rumored["known_since_year"]), 1000)
            self.assertAlmostEqual(float(rumored["confidence"]), 0.37)
            self.assertEqual(int(rumored["source_person_id"]), 21)
            self.assertEqual(
                json.loads(str(rumored["distortion_json"])),
                {"public_cause": "wolf attack", "true_cause_hidden": True},
            )
            self.assertEqual(
                str(rumored["prose_variant_key"]), "mortuary_memory.rumored.default"
            )

    def test_rediscover_event_record_updates_memory_and_logs_event(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            sav = Path(td) / "save.sqlite"
            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                original_event_id = append_simulation_event_rows(
                    conn,
                    "default",
                    [
                        (
                            1000,
                            "birth",
                            {
                                "person_id": 31,
                                "settlement_id": "aeria_north:settlement:1",
                            },
                        )
                    ],
                    created_at="2026-01-01T00:00:00+00:00",
                )[0]
                mark_event_record_lost(conn, original_event_id, lost_year=1040)

                rediscovery_event_id = rediscover_event_record(
                    conn,
                    original_event_id,
                    rediscovered_year=1100,
                    source_person_id=41,
                    source_institution_id="temple_ledger",
                    preserving_settlement_id="aeria_north:settlement:1",
                    confidence=0.82,
                    distortion={"date_uncertain": True},
                )

                original = conn.execute(
                    """
                    SELECT visibility_state, lost_year, rediscovered_year,
                           confidence, source_person_id, source_institution_id,
                           preserving_settlement_id, preserving_region_id,
                           distortion_json, prose_variant_key
                    FROM simulation_event_records_readable
                    WHERE event_id = ?
                    """,
                    (original_event_id,),
                ).fetchone()
                rediscovery = conn.execute(
                    """
                    SELECT e.sim_year, e.event_type, e.primary_person_id,
                           er.settlement_id, er.region_id, e.payload_json
                    FROM simulation_events e
                    JOIN simulation_events_readable er ON er.id = e.id
                    WHERE e.id = ?
                    """,
                    (rediscovery_event_id,),
                ).fetchone()
                rediscovery_record = conn.execute(
                    """
                    SELECT record_type, visibility_state, prose_variant_key
                    FROM simulation_event_records_readable
                    WHERE event_id = ?
                    """,
                    (rediscovery_event_id,),
                ).fetchone()

            self.assertIsNotNone(rediscovery_event_id)
            self.assertEqual(str(original["visibility_state"]), "rediscovered")
            self.assertEqual(int(original["lost_year"]), 1040)
            self.assertEqual(int(original["rediscovered_year"]), 1100)
            self.assertAlmostEqual(float(original["confidence"]), 0.82)
            self.assertEqual(int(original["source_person_id"]), 41)
            self.assertEqual(str(original["source_institution_id"]), "temple_ledger")
            self.assertEqual(
                str(original["preserving_settlement_id"]), "aeria_north:settlement:1"
            )
            self.assertEqual(str(original["preserving_region_id"]), "aeria_north")
            self.assertEqual(
                json.loads(str(original["distortion_json"])),
                {"date_uncertain": True},
            )
            self.assertEqual(
                str(original["prose_variant_key"]),
                "lineage_memory.rediscovered.default",
            )
            self.assertEqual(int(rediscovery["sim_year"]), 1100)
            self.assertEqual(str(rediscovery["event_type"]), "event_rediscovered")
            self.assertEqual(int(rediscovery["primary_person_id"]), 41)
            self.assertEqual(
                str(rediscovery["settlement_id"]), "aeria_north:settlement:1"
            )
            self.assertEqual(str(rediscovery["region_id"]), "aeria_north")
            payload = json.loads(str(rediscovery["payload_json"]))
            self.assertEqual(int(payload["original_event_id"]), original_event_id)
            self.assertEqual(str(payload["original_record_key"]), "default")
            self.assertEqual(str(rediscovery_record["record_type"]), "rediscovery_record")
            self.assertEqual(
                str(rediscovery_record["visibility_state"]), "public_known"
            )
            self.assertEqual(
                str(rediscovery_record["prose_variant_key"]),
                "rediscovery_record.public_known.default",
            )

    def test_v7_events_backfill_to_default_memory_records(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            sav = Path(td) / "save.sqlite"
            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute(
                    """
                    CREATE TABLE save_metadata (
                        meta_key TEXT PRIMARY KEY,
                        meta_value TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO save_metadata (meta_key, meta_value)
                    VALUES ('save_schema_version', '7')
                    """
                )
                conn.execute("PRAGMA user_version = 7")
                conn.execute(
                    """
                    CREATE TABLE simulation_events (
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
                    )
                    """
                )
                events = [
                    (
                        1000,
                        "birth",
                        {
                            "person_id": 1,
                            "settlement_id": "aeria_north:settlement:1",
                            "region_id": "aeria_north",
                        },
                    ),
                    (1001, "death", {"person_id": 2}),
                    (1002, "career_fitness_updated", {"person_id": 3}),
                ]
                for sim_year, event_type, payload in events:
                    conn.execute(
                        """
                        INSERT INTO simulation_events (
                            sim_year, event_type, payload_json, created_at
                        )
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            sim_year,
                            event_type,
                            json.dumps(payload, separators=(",", ":")),
                            "2026-01-01T00:00:00+00:00",
                        ),
                    )

                ensure_checkpoint_schema(conn)

                self.assertEqual(save_schema_version(conn), SAVE_SCHEMA_VERSION)
                rows = {
                    str(r["event_type"]): r
                    for r in conn.execute(
                        """
                        SELECT event_type, record_type, visibility_state,
                               preserving_settlement_id, preserving_region_id,
                               public_actor_person_id, public_victim_person_id
                        FROM simulation_event_records_readable
                        ORDER BY event_id
                        """
                    )
                }

            self.assertEqual(str(rows["birth"]["record_type"]), "lineage_memory")
            self.assertEqual(str(rows["birth"]["visibility_state"]), "private_known")
            self.assertEqual(
                str(rows["birth"]["preserving_settlement_id"]),
                "aeria_north:settlement:1",
            )
            self.assertEqual(str(rows["birth"]["preserving_region_id"]), "aeria_north")
            self.assertEqual(int(rows["birth"]["public_actor_person_id"]), 1)
            self.assertEqual(str(rows["death"]["record_type"]), "mortuary_memory")
            self.assertEqual(str(rows["death"]["visibility_state"]), "public_known")
            self.assertIsNone(rows["death"]["public_actor_person_id"])
            self.assertEqual(int(rows["death"]["public_victim_person_id"]), 2)
            self.assertEqual(
                str(rows["career_fitness_updated"]["record_type"]), "admin_note"
            )
            self.assertEqual(
                str(rows["career_fitness_updated"]["visibility_state"]), "admin_known"
            )

    def test_checkpoint_roundtrip_one_person(self) -> None:
        random.seed(42)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)

            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="isolate",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            ) as ctx:
                p = generate_person_random(simulation_context=ctx, simulation_year=1000)
                ctx.add_person(person=p, is_founder=True)
                checkpoint_simulation_to_save(ctx)
                with closing(sqlite3.connect(sav)) as conn:
                    person_row = conn.execute(
                        """
                        SELECT first_name, last_name, birthyear, person_json
                        FROM simulation_people
                        WHERE person_id = 1
                        """
                    ).fetchone()
                    n = conn.execute(
                        "SELECT COUNT(*) FROM simulation_events",
                    ).fetchone()[0]
                    n_regions = conn.execute(
                        "SELECT COUNT(*) FROM simulation_regions",
                    ).fetchone()[0]
                    n_settle = conn.execute(
                        "SELECT COUNT(*) FROM simulation_settlements",
                    ).fetchone()[0]
                    birth_rid = (p.birthplace_region_id or "").strip()
                    self.assertTrue(birth_rid)
                    sample_rn = conn.execute(
                        """
                        SELECT region_display_name FROM simulation_regions_readable
                        WHERE region_id = ?
                        """,
                        (birth_rid,),
                    ).fetchone()
                    expected_label = (
                        get_region(birth_rid, world="default", db_path=cfg).region_name or ""
                    ).strip()
                self.assertGreaterEqual(int(n), 1)
                self.assertEqual(person_row[0], p.first_name)
                self.assertEqual(person_row[1], p.last_name)
                self.assertEqual(int(person_row[2]), p.birthyear)
                person_ext = json.loads(person_row[3])
                self.assertNotIn("first_name", person_ext)
                self.assertNotIn("genome", person_ext)
                self.assertNotIn("ts", person_ext)
                self.assertIn("g", person_ext)
                self.assertIsInstance(person_ext["g"], list)
                self.assertEqual(int(n_regions), len(ctx.settlement_ids_by_region))
                self.assertEqual(int(n_settle), len(ctx.settlements_by_id))
                self.assertIsNotNone(sample_rn)
                self.assertEqual(str(sample_rn[0]).strip(), expected_label)

            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="isolate2",
                world="default",
                start_year=None,
                refresh_config=False,
                flush_run_store=False,
            ) as ctx2:
                self.assertEqual(len(ctx2.people), 1)
                self.assertEqual(ctx2.people[0].person_id, 1)
                self.assertEqual(ctx2.people[0].person.full_name, p.full_name)
                self.assertEqual(ctx2.people[0].person.genome, p.genome)
                self.assertTrue(ctx2.settlements_by_id)

    def test_start_year_clears_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)

            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="a",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            ) as ctx:
                ctx.add_person(
                    person=generate_person_random(
                        simulation_context=ctx, simulation_year=1000
                    ),
                    is_founder=True,
                )
                checkpoint_simulation_to_save(ctx)
                self.assertEqual(len(ctx.people), 1)

            ctx3 = SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="b",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            )
            self.assertEqual(len(ctx3.people), 0)

    def test_clear_world_checkpoint_explicit(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            # No ``with``: exiting a context manager would finalize_run() and refill the DB
            # after clear_world_checkpoint.
            ctx = SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="c",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            )
            ctx.add_person(
                person=generate_person_random(simulation_context=ctx, simulation_year=1000),
                is_founder=True,
            )
            checkpoint_simulation_to_save(ctx)
            clear_world_checkpoint(sav, world="default")
            shell = SimulationContext(
                db_path=cfg,
                save_db_path=sav,
                world="default",
                simulation_start_year=1000,
                history_equivalent_start_year=1000,
                current_year=1000,
                settlements_by_id={},
                settlement_ids_by_region={},
                paramours=[],
            )
            self.assertFalse(try_load_simulation_checkpoint(shell))

    def test_couple_surname_convention_roundtrip_on_resume(self) -> None:
        random.seed(5)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="surname",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            )
            male = ctx.add_person(
                person=generate_person_random(
                    gender="Male",
                    age=22,
                    simulation_context=ctx,
                    simulation_year=1000,
                ),
                is_founder=True,
            )
            female = ctx.add_person(
                person=generate_person_random(
                    gender="Female",
                    age=22,
                    simulation_context=ctx,
                    simulation_year=1000,
                ),
                is_founder=True,
            )
            ctx.add_couple(male.person_id, female.person_id)
            key = ctx._relationship_pair_key(male.person_id, female.person_id)
            ctx.surname_conventions_by_pair[key] = "kin"
            checkpoint_simulation_to_save(ctx)

            shell = SimulationContext(
                db_path=cfg,
                save_db_path=sav,
                world="default",
                simulation_start_year=1000,
                history_equivalent_start_year=1000,
                current_year=1000,
            )

            self.assertTrue(try_load_simulation_checkpoint(shell))
            self.assertEqual(shell.surname_conventions_by_pair.get(key), "kin")

    def test_events_flush_without_full_snapshot(self) -> None:
        # Partial checkpoint on purpose; do not use ``with SimulationContext.create`` because
        # __exit__ would call finalize_run() and write simulation_people, breaking assertions.
        random.seed(1)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="ev",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            )
            ctx.add_person(
                person=generate_person_random(simulation_context=ctx, simulation_year=1000),
                is_founder=True,
            )
            checkpoint_simulation_to_save(ctx, full_snapshot=False)
            with closing(sqlite3.connect(sav)) as conn:
                ev = conn.execute(
                    "SELECT COUNT(*) FROM simulation_events",
                ).fetchone()[0]
                pe = conn.execute(
                    "SELECT COUNT(*) FROM simulation_people",
                ).fetchone()[0]
            self.assertGreaterEqual(int(ev), 1)
            self.assertEqual(int(pe), 0)
            with closing(sqlite3.connect(sav)) as conn:
                link_count = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM simulation_event_people
                    WHERE person_id = 1
                    """
                ).fetchone()[0]
                common = conn.execute(
                    """
                    SELECT primary_person_id
                    FROM simulation_events_readable
                    WHERE event_type = 'founder_created'
                    ORDER BY id
                    LIMIT 1
                    """
                ).fetchone()
            self.assertGreaterEqual(int(link_count), 1)
            self.assertEqual(int(common[0]), 1)

    def test_place_ids_are_normalized_and_verbose_events_are_opt_in(self) -> None:
        random.seed(11)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            compact_sav = root / "compact.sqlite"
            verbose_sav = root / "verbose.sqlite"
            load_all_csvs_into_sqlite(cfg)

            compact = SimulationContext.create(
                db_path=cfg,
                save_db_path=compact_sav,
                world_id="compact",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            )
            rec = compact.add_person(
                person=generate_person_random(
                    simulation_context=compact, simulation_year=1000
                ),
                is_founder=True,
            )
            rid = str(rec.person.birthplace_region_id or "")
            sid = str(rec.person.current_settlement_id or rec.person.birthplace_settlement_id or "")
            compact._record_simulation_event(
                1000,
                "place_debug_probe",
                {
                    "person_id": rec.person_id,
                    "region_id": rid,
                    "settlement_id": sid,
                    "note": "compact",
                },
            )
            checkpoint_simulation_to_save(compact, full_snapshot=True)

            with closing(sqlite3.connect(compact_sav)) as conn:
                conn.row_factory = sqlite3.Row
                people_cols = {r["name"] for r in conn.execute("PRAGMA table_info(simulation_people)")}
                event = conn.execute(
                    """
                    SELECT e.region_key, e.settlement_key, e.payload_json,
                           er.region_id, er.settlement_id, er.event_origin
                    FROM simulation_events e
                    JOIN simulation_events_readable er ON er.id = e.id
                    WHERE e.event_type = 'place_debug_probe'
                    """
                ).fetchone()
                person_place = conn.execute(
                    """
                    SELECT birthplace_region_key, current_settlement_key
                    FROM simulation_people
                    WHERE person_id = ?
                    """,
                    (rec.person_id,),
                ).fetchone()
            selfIn = self.assertIn
            selfNotIn = self.assertNotIn
            selfIn("birthplace_region_key", people_cols)
            selfNotIn("birthplace_region_id", people_cols)
            self.assertIsNotNone(event["region_key"])
            self.assertIsNotNone(event["settlement_key"])
            self.assertEqual(str(event["region_id"]), rid)
            self.assertEqual(str(event["settlement_id"]), sid)
            self.assertEqual(str(event["event_origin"]), "generated")
            payload = json.loads(str(event["payload_json"]))
            selfNotIn("region_id", payload)
            selfNotIn("settlement_id", payload)
            selfNotIn("event_origin", payload)
            self.assertIsNotNone(person_place["birthplace_region_key"])
            self.assertIsNotNone(person_place["current_settlement_key"])

            verbose = SimulationContext.create(
                db_path=cfg,
                save_db_path=verbose_sav,
                world_id="verbose",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
                verbose_event_logging=True,
            )
            rec2 = verbose.add_person(
                person=generate_person_random(
                    simulation_context=verbose, simulation_year=1000
                ),
                is_founder=True,
            )
            rid2 = str(rec2.person.birthplace_region_id or "")
            sid2 = str(rec2.person.current_settlement_id or rec2.person.birthplace_settlement_id or "")
            verbose._record_simulation_event(
                1000,
                "place_debug_probe",
                {
                    "person_id": rec2.person_id,
                    "region_id": rid2,
                    "settlement_id": sid2,
                    "note": "verbose",
                },
            )
            checkpoint_simulation_to_save(verbose, full_snapshot=False)
            with closing(sqlite3.connect(verbose_sav)) as conn:
                payload_json = conn.execute(
                    """
                    SELECT payload_json
                    FROM simulation_events
                    WHERE event_type = 'place_debug_probe'
                    """
                ).fetchone()[0]
            verbose_payload = json.loads(str(payload_json))
            self.assertEqual(verbose_payload["region_id"], rid2)
            self.assertEqual(verbose_payload["settlement_id"], sid2)

    def test_settlement_move_details_are_normalized_and_compacted(self) -> None:
        random.seed(17)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)

            ctx = SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="moves",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            )
            rec = ctx.add_person(
                person=generate_person_random(
                    simulation_context=ctx, simulation_year=1000
                ),
                is_founder=True,
            )
            from_sid = str(
                rec.person.current_settlement_id or rec.person.birthplace_settlement_id or ""
            )
            from_rid = str(rec.person.birthplace_region_id or "")
            to_rid = "aeria_granite_range" if from_rid != "aeria_granite_range" else "aeria_north"
            target = ctx.ensure_active_settlement_for_region(to_rid)

            ctx.move_person_to_settlement(
                rec.person_id,
                target.settlement_id,
                move_reason="storage_probe",
                requested_year=1000,
                planned_apply_year=1001,
                source_event="save_test",
                group_id="move:1",
            )
            checkpoint_simulation_to_save(ctx, full_snapshot=False)

            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                self.assertEqual(save_schema_version(conn), SAVE_SCHEMA_VERSION)
                row = conn.execute(
                    """
                    SELECT e.primary_person_id, er.settlement_id AS event_settlement_id,
                           er.region_id AS event_region_id, e.payload_json,
                           m.moved_person_id, m.from_settlement_id, m.to_settlement_id,
                           m.from_region_id, m.to_region_id, m.cross_region,
                           m.move_reason, m.requested_year, m.planned_apply_year,
                           m.source_event, m.group_id
                    FROM simulation_events e
                    JOIN simulation_events_readable er ON er.id = e.id
                    JOIN simulation_event_moves_readable m ON m.event_id = e.id
                    WHERE e.event_type = 'settlement_moved'
                    ORDER BY e.id DESC
                    LIMIT 1
                    """
                ).fetchone()
                link_count = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM simulation_event_people
                    WHERE event_id = (
                        SELECT id FROM simulation_events
                        WHERE event_type = 'settlement_moved'
                        ORDER BY id DESC
                        LIMIT 1
                    )
                      AND person_id = ?
                    """,
                    (rec.person_id,),
                ).fetchone()[0]

            self.assertIsNotNone(row)
            self.assertEqual(int(row["primary_person_id"]), rec.person_id)
            self.assertEqual(int(row["moved_person_id"]), rec.person_id)
            self.assertEqual(str(row["event_settlement_id"]), target.settlement_id)
            self.assertEqual(str(row["event_region_id"]), to_rid)
            self.assertEqual(str(row["from_settlement_id"]), from_sid)
            self.assertEqual(str(row["to_settlement_id"]), target.settlement_id)
            self.assertEqual(str(row["from_region_id"]), from_rid)
            self.assertEqual(str(row["to_region_id"]), to_rid)
            self.assertEqual(int(row["cross_region"]), 1)
            self.assertEqual(str(row["move_reason"]), "storage_probe")
            self.assertEqual(int(row["requested_year"]), 1000)
            self.assertEqual(int(row["planned_apply_year"]), 1001)
            self.assertEqual(str(row["source_event"]), "save_test")
            self.assertEqual(str(row["group_id"]), "move:1")
            self.assertEqual(json.loads(str(row["payload_json"])), {})
            self.assertGreaterEqual(int(link_count), 1)

    def test_v6_settlement_move_payload_backfills_to_event_moves(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            sav = Path(td) / "save.sqlite"
            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute(
                    """
                    CREATE TABLE save_metadata (
                        meta_key TEXT PRIMARY KEY,
                        meta_value TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO save_metadata (meta_key, meta_value)
                    VALUES ('save_schema_version', '6')
                    """
                )
                conn.execute("PRAGMA user_version = 6")
                conn.execute(
                    """
                    CREATE TABLE simulation_events (
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
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO simulation_events (
                        sim_year, event_type, payload_json, created_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        1002,
                        "settlement_moved",
                        json.dumps(
                            {
                                "person_id": 42,
                                "from_settlement_id": "aeria_north:settlement:1",
                                "to_settlement_id": "aeria_granite_range:settlement:1",
                                "from_region_id": "aeria_north",
                                "to_region_id": "aeria_granite_range",
                                "cross_region": True,
                                "move_reason": "resource_pressure_migration",
                            },
                            separators=(",", ":"),
                        ),
                        "2026-01-01T00:00:00+00:00",
                    ),
                )
                ensure_checkpoint_schema(conn)

                self.assertEqual(save_schema_version(conn), SAVE_SCHEMA_VERSION)
                row = conn.execute(
                    """
                    SELECT moved_person_id, from_settlement_id, to_settlement_id,
                           from_region_id, to_region_id, cross_region, move_reason
                    FROM simulation_event_moves_readable
                    WHERE event_type = 'settlement_moved'
                    """
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(int(row["moved_person_id"]), 42)
            self.assertEqual(str(row["from_settlement_id"]), "aeria_north:settlement:1")
            self.assertEqual(
                str(row["to_settlement_id"]),
                "aeria_granite_range:settlement:1",
            )
            self.assertEqual(str(row["from_region_id"]), "aeria_north")
            self.assertEqual(str(row["to_region_id"]), "aeria_granite_range")
            self.assertEqual(int(row["cross_region"]), 1)
            self.assertEqual(str(row["move_reason"]), "resource_pressure_migration")

    def test_inferred_event_origin_is_stored_outside_compact_payload(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)

            ctx = SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="inferred",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            )
            ctx._record_inferred_simulation_event(
                990,
                "promotion_backfill_birth",
                {"person_id": 123, "event_origin": "inferred", "details": "anchor"},
            )
            checkpoint_simulation_to_save(ctx, full_snapshot=False)

            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT event_origin, payload_json
                    FROM simulation_events
                    WHERE event_type = 'promotion_backfill_birth'
                    """
                ).fetchone()
            self.assertEqual(row["event_origin"], "inferred")
            payload = json.loads(str(row["payload_json"]))
            self.assertNotIn("event_origin", payload)
            self.assertEqual(payload["person_id"], 123)

    def test_passive_people_checkpoint_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)

            ctx = SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="passive",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            )
            detailed = ctx.add_person(
                person=generate_person_random(
                    simulation_context=ctx, simulation_year=1000
                ),
                is_founder=True,
            )
            passive = ctx.add_passive_person(
                PassivePerson(
                    name="Mira Lowdetail",
                    birthyear=1001,
                    gender="female",
                    species=detailed.person.species,
                    ethnic=detailed.person.ethnic,
                    birthplace_region_id=detailed.person.birthplace_region_id,
                    birthplace_settlement_id=detailed.person.birthplace_settlement_id,
                    current_settlement_id=detailed.person.current_settlement_id,
                    job_family="farm",
                    partner_name="Toma Lowdetail",
                    partner_birthyear=999,
                    partnership_start_year=1019,
                    father_id=detailed.person_id,
                    child_count=2,
                    child_birthyears=(1020, 1023),
                    child_person_ids=(9001, 9002),
                    status_bucket="common",
                    prosperity_bucket="modest",
                )
            )
            ctx.add_passive_cohort(
                PassiveCohort(
                    sim_year=1000,
                    region_id=detailed.person.birthplace_region_id,
                    settlement_id=detailed.person.current_settlement_id,
                    age_band="0-4",
                    gender="female",
                    species=detailed.person.species,
                    culture=detailed.person.ethnic,
                    job_family="farm",
                    status_bucket="common",
                    population_count=70,
                    birth_count=8,
                    death_count=0,
                )
            )
            ctx.add_passive_cohort(
                PassiveCohort(
                    sim_year=1001,
                    region_id=detailed.person.birthplace_region_id,
                    settlement_id=detailed.person.current_settlement_id,
                    age_band="0-4",
                    gender="female",
                    species=detailed.person.species,
                    culture=detailed.person.ethnic,
                    job_family="farm",
                    status_bucket="common",
                    population_count=80,
                    birth_count=9,
                    death_count=1,
                )
            )
            self.assertNotIn(passive.person_id, ctx.current_people_ids)
            self.assertEqual(
                ctx.count_alive_in_settlement(str(detailed.person.current_settlement_id)),
                1,
            )
            checkpoint_simulation_to_save(ctx, full_snapshot=True)

            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT *
                    FROM simulation_people_light_readable
                    WHERE person_id = ?
                    """,
                    (passive.person_id,),
                ).fetchone()
                tables = {
                    r["name"]
                    for r in conn.execute(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE type IN ('table', 'view')
                        """
                    )
                }
                cohort = conn.execute(
                    """
                    SELECT *
                    FROM simulation_cohorts_readable
                    WHERE sim_year = 1001 AND age_band = '0-4'
                    """
                ).fetchone()
                cohort_year_count = conn.execute(
                    """
                    SELECT COUNT(DISTINCT sim_year)
                    FROM simulation_cohorts
                    """
                ).fetchone()[0]
            self.assertIn("simulation_people_light", tables)
            self.assertIn("simulation_cohorts", tables)
            self.assertIn("simulation_promotion_log", tables)
            self.assertEqual(row["name"], "Mira Lowdetail")
            self.assertEqual(row["species"], detailed.person.species)
            self.assertEqual(row["ethnic"], detailed.person.ethnic)
            self.assertEqual(row["job_family"], "farm")
            self.assertEqual(row["partner_name"], "Toma Lowdetail")
            self.assertEqual(row["partner_birthyear"], 999)
            self.assertEqual(row["partnership_start_year"], 1019)
            self.assertEqual(row["father_id"], detailed.person_id)
            self.assertEqual(row["child_count"], 2)
            self.assertEqual(json.loads(row["child_birthyears_json"]), [1020, 1023])
            self.assertEqual(json.loads(row["child_person_ids_json"]), [9001, 9002])
            self.assertEqual(
                row["current_settlement_id"], detailed.person.current_settlement_id
            )
            self.assertEqual(cohort["population_count"], 80)
            self.assertEqual(cohort["birth_count"], 9)
            self.assertEqual(cohort["death_count"], 1)
            self.assertEqual(cohort_year_count, 2)

            loaded = SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="passive",
                world="default",
                start_year=None,
                refresh_config=False,
                flush_run_store=False,
            )
            loaded_passive = loaded.passive_people[passive.person_id].person
            self.assertEqual(loaded_passive.name, "Mira Lowdetail")
            self.assertEqual(loaded_passive.species, detailed.person.species)
            self.assertEqual(loaded_passive.ethnic, detailed.person.ethnic)
            self.assertEqual(loaded_passive.job_family, "farm")
            self.assertEqual(loaded_passive.partner_name, "Toma Lowdetail")
            self.assertEqual(loaded_passive.partner_birthyear, 999)
            self.assertEqual(loaded_passive.partnership_start_year, 1019)
            self.assertEqual(loaded_passive.father_id, detailed.person_id)
            self.assertEqual(loaded_passive.child_birthyears, (1020, 1023))
            self.assertEqual(loaded_passive.child_person_ids, (9001, 9002))
            self.assertNotIn(passive.person_id, loaded.current_people_ids)
            self.assertEqual(len(loaded.passive_cohorts), 1)
            self.assertEqual(loaded.passive_cohorts[0].sim_year, 1001)
            self.assertEqual(loaded.passive_cohorts[0].population_count, 80)
            self.assertGreaterEqual(loaded.next_person_id, passive.person_id + 1)

    def test_partial_checkpoint_writes_simulation_meta(self) -> None:
        random.seed(2)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="meta",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            )
            ctx.add_person(
                person=generate_person_random(simulation_context=ctx, simulation_year=1000),
                is_founder=True,
            )
            checkpoint_simulation_to_save(ctx, full_snapshot=True)
            ctx.region_effective_cap_multiplier["aeria_north"] = 0.33
            expected_next = str(ctx.next_person_id)
            checkpoint_simulation_to_save(ctx, full_snapshot=False)
            with closing(sqlite3.connect(sav)) as conn:
                row = conn.execute(
                    """
                    SELECT meta_value FROM simulation_meta
                    WHERE meta_key = ?
                    """,
                    (REGION_EFFECTIVE_CAP_MULTIPLIER_META_KEY,),
                ).fetchone()
                nid = conn.execute(
                    """
                    SELECT meta_value FROM simulation_meta
                    WHERE meta_key = ?
                    """,
                    ("next_person_id",),
                ).fetchone()
            self.assertIsNotNone(row)
            caps = parse_region_effective_cap_multipliers(str(row[0]))
            self.assertAlmostEqual(caps.get("aeria_north", 0.0), 0.33, places=5)
            self.assertIsNotNone(nid)
            self.assertEqual(str(nid[0]).strip(), expected_next)

    def test_promote_passive_person_backfills_family_events(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="promofam",
                world="default",
                start_year=1050,
                refresh_config=False,
                flush_run_store=False,
            )
            st = ctx.ensure_active_settlement_for_region("aeria_north")
            passive = ctx.add_passive_person(
                PassivePerson(
                    name="Mira Lowdetail",
                    birthyear=1020,
                    gender="Female",
                    species="Human",
                    ethnic="Gaulish",
                    birthplace_region_id=st.region_id,
                    birthplace_settlement_id=st.settlement_id,
                    current_settlement_id=st.settlement_id,
                    partner_name="Toma Lowdetail",
                    partner_birthyear=1018,
                    partnership_start_year=1040,
                    child_count=2,
                    child_birthyears=(1041, 1044),
                )
            )

            promoted = ctx.promote_passive_person(
                passive.person_id,
                year=1050,
                reason="unit_test_family",
            )

            self.assertEqual(promoted.person_id, passive.person_id)
            event_types = [event_type for _, event_type, _ in ctx._pending_simulation_events]
            self.assertIn("promotion_backfill_partnership", event_types)
            self.assertIn("promotion_backfill_children", event_types)
            family_payloads = {
                event_type: payload
                for _, event_type, payload in ctx._pending_simulation_events
                if event_type.startswith("promotion_backfill_")
            }
            self.assertEqual(
                family_payloads["promotion_backfill_partnership"]["partner_name"],
                "Toma Lowdetail",
            )
            self.assertEqual(
                family_payloads["promotion_backfill_children"]["child_birthyears"],
                [1041, 1044],
            )

    def test_region_effective_cap_multiplier_roundtrip_on_resume(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="caprt",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            ) as ctx:
                ctx.add_person(
                    person=generate_person_random(
                        simulation_context=ctx, simulation_year=1000
                    ),
                    is_founder=True,
                )
                ctx.region_effective_cap_multiplier["aeria_north"] = 0.8125
                checkpoint_simulation_to_save(ctx, full_snapshot=True)

            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="caprt2",
                world="default",
                start_year=None,
                refresh_config=False,
                flush_run_store=False,
            ) as ctx2:
                self.assertAlmostEqual(
                    ctx2.region_effective_cap_multiplier.get("aeria_north", 0.0),
                    0.8125,
                    places=6,
                )

    def test_government_checkpoint_roundtrip(self) -> None:
        random.seed(7)
        from library.simulation_government import simulation_government_annual_tick

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            _force_population_scale(cfg, 0.0002)

            polity_ids_first: set[int] = set()
            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="govrt",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            ) as ctx:
                ctx.add_person(
                    person=generate_person_random(
                        simulation_context=ctx, simulation_year=1000
                    ),
                    is_founder=True,
                )
                simulation_government_annual_tick(ctx, 1000)
                self.assertGreater(len(ctx.gov_polities), 0)
                polity_ids_first = set(ctx.gov_polities.keys())
                checkpoint_simulation_to_save(ctx, full_snapshot=True)
                with closing(sqlite3.connect(sav)) as conn:
                    n = conn.execute(
                        "SELECT COUNT(*) FROM simulation_polities",
                    ).fetchone()[0]
                self.assertGreater(int(n), 0)

            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="govrt2",
                world="default",
                start_year=None,
                refresh_config=False,
                flush_run_store=False,
            ) as ctx2:
                self.assertEqual(set(ctx2.gov_polities.keys()), polity_ids_first)
                self.assertGreaterEqual(len(ctx2.gov_territory_rows), 1)


if __name__ == "__main__":
    unittest.main()
