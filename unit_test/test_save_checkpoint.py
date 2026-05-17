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
from library.simulation_context import SimulationContext
from library.world_save import (
    SAVE_SCHEMA_VERSION,
    REGION_EFFECTIVE_CAP_MULTIPLIER_META_KEY,
    checkpoint_simulation_to_save,
    clear_world_checkpoint,
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
                row = conn.execute(
                    "SELECT first_name, person_json FROM simulation_people WHERE person_id = 1"
                ).fetchone()
                self.assertEqual(row[0], "Test")
                self.assertEqual(json.loads(row[1])["first_name"], "Test")

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
                        SELECT region_display_name FROM simulation_regions
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
