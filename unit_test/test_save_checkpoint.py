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
    checkpoint_simulation_to_save,
    clear_world_checkpoint,
    ensure_checkpoint_schema,
    ensure_checkpoint_schema_for_file,
    parse_region_effective_cap_multipliers,
    rebuild_save_sqlite_for_schema_upgrade,
    save_schema_version,
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
            with sqlite3.connect(sav) as conn:
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

            with sqlite3.connect(rebuilt) as conn:
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
            with sqlite3.connect(sav) as conn:
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
                with sqlite3.connect(sav) as conn:
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
            with sqlite3.connect(sav) as conn:
                ev = conn.execute(
                    "SELECT COUNT(*) FROM simulation_events",
                ).fetchone()[0]
                pe = conn.execute(
                    "SELECT COUNT(*) FROM simulation_people",
                ).fetchone()[0]
            self.assertGreaterEqual(int(ev), 1)
            self.assertEqual(int(pe), 0)
            with sqlite3.connect(sav) as conn:
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

            with sqlite3.connect(compact_sav) as conn:
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
            with sqlite3.connect(verbose_sav) as conn:
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

            with sqlite3.connect(sav) as conn:
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

            with sqlite3.connect(sav) as conn:
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
            with sqlite3.connect(sav) as conn:
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
                with sqlite3.connect(sav) as conn:
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
