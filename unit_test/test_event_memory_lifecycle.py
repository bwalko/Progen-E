"""Tests for annual event-memory aging, loss, and rediscovery."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from library.config_import import load_all_csvs_into_sqlite
from library.event_memory_lifecycle import event_memory_lifecycle_annual_tick
from library.simulation_context import SimulationContext
from library.world_save import (
    append_simulation_event_rows,
    ensure_checkpoint_schema,
    mark_event_record_lost,
    seal_event_record,
)


class TestEventMemoryLifecycle(unittest.TestCase):
    def test_lifecycle_can_mark_old_in_world_records_lost(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            sav = Path(td) / "save.sqlite"
            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                old_birth_id, crime_id, virtue_id, admin_id, recent_birth_id = (
                    append_simulation_event_rows(
                        conn,
                        "default",
                        [
                            (
                                1000,
                                "birth",
                                {
                                    "person_id": 1,
                                    "settlement_id": "aeria_north:settlement:1",
                                    "region_id": "aeria_north",
                                },
                            ),
                            (
                                1000,
                                "property_crime",
                                {
                                    "perpetrator_person_id": 2,
                                    "target_person_id": 3,
                                    "historical_importance": 0.2,
                                    "settlement_id": "aeria_north:settlement:1",
                                    "region_id": "aeria_north",
                                },
                            ),
                            (
                                1000,
                                "public_virtue",
                                {
                                    "benefactor_person_id": 4,
                                    "historical_importance": 0.2,
                                    "settlement_id": "aeria_north:settlement:1",
                                    "region_id": "aeria_north",
                                },
                            ),
                            (1000, "career_fitness_updated", {"person_id": 5}),
                            (1095, "birth", {"person_id": 6}),
                        ],
                        created_at="2026-01-01T00:00:00+00:00",
                    )
                )

                summary = event_memory_lifecycle_annual_tick(
                    conn,
                    year=1100,
                    shards=1,
                    loss_chance_multiplier=1000.0,
                    rediscovery_chance_multiplier=0.0,
                )
                rows = {
                    int(row["event_id"]): row
                    for row in conn.execute(
                        """
                        SELECT event_id, visibility_state, lost_year
                        FROM simulation_event_records_readable
                        WHERE event_id IN (?, ?, ?, ?, ?)
                        """,
                        (
                            old_birth_id,
                            crime_id,
                            virtue_id,
                            admin_id,
                            recent_birth_id,
                        ),
                    )
                }

            self.assertEqual(summary.records_lost, 3)
            self.assertGreaterEqual(summary.candidates_reviewed, 3)
            self.assertEqual(str(rows[old_birth_id]["visibility_state"]), "lost")
            self.assertEqual(str(rows[crime_id]["visibility_state"]), "lost")
            self.assertEqual(str(rows[virtue_id]["visibility_state"]), "lost")
            self.assertEqual(int(rows[old_birth_id]["lost_year"]), 1100)
            self.assertEqual(str(rows[admin_id]["visibility_state"]), "admin_known")
            self.assertEqual(
                str(rows[recent_birth_id]["visibility_state"]), "private_known"
            )

    def test_high_volume_private_records_decay_later_than_incidents(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            sav = Path(td) / "save.sqlite"
            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                birth_id, job_id, crime_id = append_simulation_event_rows(
                    conn,
                    "default",
                    [
                        (1040, "birth", {"person_id": 1}),
                        (1040, "job_assigned", {"person_id": 1, "job": "miller"}),
                        (
                            1040,
                            "property_crime",
                            {
                                "perpetrator_person_id": 2,
                                "target_person_id": 3,
                                "historical_importance": 0.2,
                            },
                        ),
                    ],
                    created_at="2026-01-01T00:00:00+00:00",
                )

                summary = event_memory_lifecycle_annual_tick(
                    conn,
                    year=1100,
                    shards=1,
                    loss_chance_multiplier=1000.0,
                    rediscovery_chance_multiplier=0.0,
                )
                rows = {
                    int(row["event_id"]): row
                    for row in conn.execute(
                        """
                        SELECT event_id, visibility_state, lost_year
                        FROM simulation_event_records_readable
                        WHERE event_id IN (?, ?, ?)
                        """,
                        (birth_id, job_id, crime_id),
                    )
                }

            self.assertEqual(summary.records_lost, 1)
            self.assertEqual(str(rows[birth_id]["visibility_state"]), "private_known")
            self.assertEqual(str(rows[job_id]["visibility_state"]), "private_known")
            self.assertEqual(str(rows[crime_id]["visibility_state"]), "lost")
            self.assertEqual(int(rows[crime_id]["lost_year"]), 1100)

    def test_lifecycle_rediscovers_old_lost_or_sealed_records(self) -> None:
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
                            1000,
                            "murder",
                            {
                                "killer_person_id": 10,
                                "victim_person_id": 11,
                                "historical_importance": 0.7,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                        (
                            1000,
                            "knowledge_culture",
                            {
                                "creator_person_id": 12,
                                "historical_importance": 0.8,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                    ],
                    created_at="2026-01-01T00:00:00+00:00",
                )
                mark_event_record_lost(conn, murder_id, lost_year=1040)
                seal_event_record(
                    conn,
                    knowledge_id,
                    sealed_year=1045,
                    source_institution_id="locked_library",
                )

                summary = event_memory_lifecycle_annual_tick(
                    conn,
                    year=1080,
                    shards=1,
                    rediscovery_shards=1,
                    loss_chance_multiplier=0.0,
                    rediscovery_chance_multiplier=1000.0,
                )
                restored = {
                    int(row["event_id"]): row
                    for row in conn.execute(
                        """
                        SELECT event_id, visibility_state, rediscovered_year,
                               source_institution_id, confidence, distortion_json
                        FROM simulation_event_records_readable
                        WHERE event_id IN (?, ?)
                        """,
                        (murder_id, knowledge_id),
                    )
                }
                rediscovery_payloads = [
                    json.loads(str(row["payload_json"]))
                    for row in conn.execute(
                        """
                        SELECT payload_json
                        FROM simulation_events_readable
                        WHERE event_type = 'event_rediscovered'
                        ORDER BY id
                        """
                    )
                ]
                rediscovery_record_count = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM simulation_event_records_readable
                    WHERE event_type = 'event_rediscovered'
                      AND record_type = 'rediscovery_record'
                      AND visibility_state = 'public_known'
                    """
                ).fetchone()[0]

            self.assertEqual(summary.records_rediscovered, 2)
            self.assertEqual(str(restored[murder_id]["visibility_state"]), "rediscovered")
            self.assertEqual(
                str(restored[knowledge_id]["visibility_state"]), "rediscovered"
            )
            self.assertEqual(int(restored[murder_id]["rediscovered_year"]), 1080)
            self.assertTrue(str(restored[murder_id]["source_institution_id"]))
            self.assertEqual(
                str(restored[knowledge_id]["source_institution_id"]),
                "locked_library",
            )
            self.assertGreater(float(restored[murder_id]["confidence"]), 0.0)
            self.assertEqual(
                {int(payload["original_event_id"]) for payload in rediscovery_payloads},
                {murder_id, knowledge_id},
            )
            self.assertEqual(int(rediscovery_record_count), 2)

    def test_routine_work_records_rediscover_later_than_incidents(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            sav = Path(td) / "save.sqlite"
            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                job_id, crime_id = append_simulation_event_rows(
                    conn,
                    "default",
                    [
                        (1000, "job_lost", {"person_id": 1, "old_job": "miller"}),
                        (
                            1000,
                            "property_crime",
                            {
                                "perpetrator_person_id": 2,
                                "target_person_id": 3,
                                "historical_importance": 0.2,
                            },
                        ),
                    ],
                    created_at="2026-01-01T00:00:00+00:00",
                )
                mark_event_record_lost(conn, job_id, lost_year=1040)
                mark_event_record_lost(conn, crime_id, lost_year=1040)

                summary = event_memory_lifecycle_annual_tick(
                    conn,
                    year=1080,
                    shards=1,
                    rediscovery_shards=1,
                    loss_chance_multiplier=0.0,
                    rediscovery_chance_multiplier=1000.0,
                )
                rows = {
                    int(row["event_id"]): row
                    for row in conn.execute(
                        """
                        SELECT event_id, visibility_state, rediscovered_year
                        FROM simulation_event_records_readable
                        WHERE event_id IN (?, ?)
                        """,
                        (job_id, crime_id),
                    )
                }

            self.assertEqual(summary.records_rediscovered, 1)
            self.assertEqual(str(rows[job_id]["visibility_state"]), "lost")
            self.assertEqual(str(rows[crime_id]["visibility_state"]), "rediscovered")
            self.assertEqual(int(rows[crime_id]["rediscovered_year"]), 1080)

    def test_record_year_summary_runs_memory_lifecycle_after_save_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="default",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            ) as ctx:
                year = 1000
                rates = {**ctx.get_mortality_rates_for_year(year), "deaths_count": 0.0}
                with patch(
                    "library.event_memory_lifecycle."
                    "event_memory_lifecycle_annual_tick_for_save"
                ) as mock_lifecycle:
                    ctx.record_year_summary(
                        year=year,
                        births_count=0,
                        deaths_count=0,
                        mortality_rates=rates,
                        evolve_settlements_this_tick=False,
                    )

                mock_lifecycle.assert_called_once()
                args, kwargs = mock_lifecycle.call_args
                self.assertEqual(args[0], sav)
                self.assertEqual(kwargs["year"], year)
                self.assertEqual(kwargs["world"], "default")


if __name__ == "__main__":
    unittest.main()
