import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from library.config_import import load_all_csvs_into_sqlite
from library.nondetailed_population import (
    NondetailedPersonSeed,
    add_nondetailed_person,
    apply_nondetailed_job_family_economy_effects,
    nondetailed_alive_count,
    nondetailed_job_counts_by_settlement,
    run_nondetailed_sql_migration,
    run_nondetailed_sql_annual_tick,
)
from library.settlements import SettlementState
from library.simulation_context import SimulationContext
from library.passive_population import (
    promote_passive_candidate_for_marriage,
    promote_passive_candidate_for_office,
    promote_passive_person_for_focus,
)
from library.world_save import ensure_checkpoint_schema


class TestNondetailedPopulation(unittest.TestCase):
    def test_schema_roundtrip_counts_and_job_groups(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            save = Path(td) / "save.sqlite"
            with closing(sqlite3.connect(save)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                add_nondetailed_person(
                    conn,
                    NondetailedPersonSeed(
                        birthyear=980,
                        gender="Female",
                        region_id="r1",
                        settlement_id="r1:s1",
                        species="Human",
                        culture="Test",
                        job_family="farm",
                        is_partnered=True,
                    ),
                    person_id=10,
                )
                add_nondetailed_person(
                    conn,
                    NondetailedPersonSeed(
                        birthyear=990,
                        gender="Male",
                        region_id="r1",
                        settlement_id="r1:s1",
                        job_family="military",
                    ),
                    person_id=11,
                )
                conn.commit()
                row = conn.execute(
                    """
                    SELECT *
                    FROM simulation_people_nondetailed_readable
                    WHERE person_id = 10
                    """
                ).fetchone()
                jobs = nondetailed_job_counts_by_settlement(conn)

            self.assertEqual(row["current_settlement_id"], "r1:s1")
            self.assertEqual(row["job_family"], "food")
            self.assertEqual(row["is_partnered"], 1)
            self.assertEqual(jobs["r1:s1"]["food"], 1)
            self.assertEqual(jobs["r1:s1"]["military"], 1)

    def test_sql_tick_updates_directory_sets(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            save = Path(td) / "save.sqlite"
            with closing(sqlite3.connect(save)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                for i in range(400):
                    add_nondetailed_person(
                        conn,
                        NondetailedPersonSeed(
                            birthyear=970 + (i % 20),
                            gender="Female" if i % 2 else "Male",
                            region_id="r1",
                            settlement_id="r1:s1",
                            job_family="other",
                            is_partnered=bool(i % 3 == 0),
                        ),
                        person_id=i + 1,
                    )
                for i in range(40):
                    add_nondetailed_person(
                        conn,
                        NondetailedPersonSeed(
                            birthyear=900,
                            gender="Male",
                            region_id="r1",
                            settlement_id="r1:s1",
                            job_family="other",
                        ),
                        person_id=1000 + i,
                    )
                conn.commit()
                before = nondetailed_alive_count(conn)
                result = run_nondetailed_sql_annual_tick(conn, year=1000)
                conn.commit()
                after = nondetailed_alive_count(conn)
                food_workers = conn.execute(
                    """
                    SELECT COUNT(*) AS c
                    FROM simulation_people_nondetailed
                    WHERE is_alive = 1 AND job_family = 'food'
                    """
                ).fetchone()["c"]

            self.assertEqual(result.alive_after, after)
            self.assertLess(after, before + result.births)
            self.assertGreater(result.deaths, 0)
            self.assertGreater(result.job_updates, 0)
            self.assertGreater(result.births, 0)
            self.assertGreater(food_workers, 0)

    def test_sql_tick_extreme_old_age_mortality_is_probabilistic_but_steep(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            save = Path(td) / "save.sqlite"
            with closing(sqlite3.connect(save)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                for i in range(1000):
                    add_nondetailed_person(
                        conn,
                        NondetailedPersonSeed(
                            birthyear=883,
                            gender="Female" if i % 2 else "Male",
                            region_id="r1",
                            settlement_id="r1:s1",
                            species="Human",
                            culture="Test",
                            job_family="other",
                            is_partnered=False,
                        ),
                        person_id=10000 + i,
                    )
                conn.commit()
                result = run_nondetailed_sql_annual_tick(conn, year=1000)
                conn.commit()
                alive_after = nondetailed_alive_count(conn)

            self.assertEqual(result.alive_after, alive_after)
            self.assertGreater(result.deaths, 850)
            self.assertGreater(alive_after, 0)
            self.assertLess(alive_after, 150)

    def test_context_counts_and_promotion_use_nondetailed_directory(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            save = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext.create(
                db_path=cfg,
                save_db_path=save,
                world_id="nondetailed",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            )
            ctx.settlements_by_id["r1:s1"] = SettlementState(
                settlement_id="r1:s1",
                region_id="r1",
                level="settlement",
                resident_count=0,
                household_cap=0,
                food_pressure=0.0,
                stability=0.5,
                market_pull=0.5,
            )
            with closing(sqlite3.connect(save)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                add_nondetailed_person(
                    conn,
                    NondetailedPersonSeed(
                        birthyear=980,
                        gender="Female",
                        region_id="r1",
                        settlement_id="r1:s1",
                        species="Human",
                        culture=None,
                        job_family="care",
                        is_partnered=True,
                    ),
                    person_id=50,
                )
                conn.commit()

            self.assertEqual(ctx.mixed_population_count_in_settlement("r1:s1"), 1)
            promoted = ctx.promote_nondetailed_person(
                50,
                year=1000,
                reason="user_inspection",
            )
            self.assertIsNotNone(promoted)
            self.assertEqual(promoted.person_id, 50)
            self.assertEqual(promoted.person.birthyear, 980)
            self.assertEqual(promoted.person.current_settlement_id, "r1:s1")
            self.assertEqual(promoted.person.job, "care worker")
            self.assertIn(50, ctx.current_people_ids)
            self.assertEqual(ctx.nondetailed_population_count(), 0)
            job_payload = next(
                payload
                for _year, event_type, payload in ctx._pending_simulation_events
                if event_type == "job_assigned" and payload.get("person_id") == 50
            )
            self.assertEqual(job_payload["job"], "care worker")
            self.assertEqual(job_payload["job_family"], "care")

    def test_automatic_nondetailed_promotion_skips_non_prime_elder_rows(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            save = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext.create(
                db_path=cfg,
                save_db_path=save,
                world_id="nondetailed_age_window",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            )
            sid = "r1:s1"
            ctx.settlements_by_id[sid] = SettlementState(
                settlement_id=sid,
                region_id="r1",
                resident_count=10,
                household_cap=3,
            )
            with closing(sqlite3.connect(save)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                add_nondetailed_person(
                    conn,
                    NondetailedPersonSeed(
                        birthyear=885,
                        gender="Female",
                        region_id="r1",
                        settlement_id=sid,
                        job_family="care",
                    ),
                    person_id=1,
                )
                add_nondetailed_person(
                    conn,
                    NondetailedPersonSeed(
                        birthyear=970,
                        gender="Female",
                        region_id="r1",
                        settlement_id=sid,
                        job_family="care",
                    ),
                    person_id=2,
                )
                conn.commit()

            promoted = ctx.promote_nondetailed_people(
                year=1000,
                reason="settlement_detail_floor",
                settlement_id=sid,
                limit=1,
            )

            self.assertEqual([rec.person_id for rec in promoted], [2])
            self.assertEqual(promoted[0].person.birthyear, 970)
            event_payload = next(
                payload
                for _year, event_type, payload in ctx._pending_simulation_events
                if event_type == "nondetailed_person_promoted"
            )
            self.assertEqual(event_payload["source"]["selector_max_age"], 70)
            self.assertEqual(event_payload["source"]["selector_preferred_min_age"], 22)
            self.assertEqual(event_payload["source"]["selector_preferred_max_age"], 55)

    def test_marriage_nondetailed_promotion_uses_tighter_adult_window(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            save = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext.create(
                db_path=cfg,
                save_db_path=save,
                world_id="nondetailed_marriage_age_window",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            )
            sid = "r1:s1"
            ctx.settlements_by_id[sid] = SettlementState(
                settlement_id=sid,
                region_id="r1",
                resident_count=10,
                household_cap=3,
            )
            with closing(sqlite3.connect(save)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                add_nondetailed_person(
                    conn,
                    NondetailedPersonSeed(
                        birthyear=885,
                        gender="Male",
                        region_id="r1",
                        settlement_id=sid,
                        job_family="admin",
                        is_partnered=False,
                    ),
                    person_id=1,
                )
                add_nondetailed_person(
                    conn,
                    NondetailedPersonSeed(
                        birthyear=965,
                        gender="Male",
                        region_id="r1",
                        settlement_id=sid,
                        job_family="craft",
                        is_partnered=False,
                    ),
                    person_id=2,
                )
                conn.commit()

            spouse = promote_passive_candidate_for_marriage(
                ctx,
                year=1000,
                gender="Male",
                settlement_id=sid,
                region_id="r1",
                min_age=18,
            )

            self.assertIsNotNone(spouse)
            self.assertEqual(spouse.person_id, 2)
            self.assertEqual(1000 - spouse.person.birthyear, 35)

    def test_context_promotes_nondetailed_people_by_selector(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            save = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext.create(
                db_path=cfg,
                save_db_path=save,
                world_id="nondetailed_selector",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            )
            ctx.settlements_by_id["r1:s1"] = SettlementState(
                settlement_id="r1:s1",
                region_id="r1",
                resident_count=10,
                household_cap=3,
            )
            ctx.settlements_by_id["r1:s2"] = SettlementState(
                settlement_id="r1:s2",
                region_id="r1",
                resident_count=10,
                household_cap=3,
            )
            with closing(sqlite3.connect(save)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                add_nondetailed_person(
                    conn,
                    NondetailedPersonSeed(
                        birthyear=980,
                        gender="Female",
                        region_id="r1",
                        settlement_id="r1:s1",
                        job_family="care",
                    ),
                    person_id=60,
                )
                add_nondetailed_person(
                    conn,
                    NondetailedPersonSeed(
                        birthyear=979,
                        gender="Male",
                        region_id="r1",
                        settlement_id="r1:s1",
                        job_family="trade",
                    ),
                    person_id=61,
                )
                add_nondetailed_person(
                    conn,
                    NondetailedPersonSeed(
                        birthyear=978,
                        gender="Female",
                        region_id="r1",
                        settlement_id="r1:s2",
                        job_family="care",
                    ),
                    person_id=62,
                )
                conn.commit()

            promoted = ctx.promote_nondetailed_people(
                year=1000,
                reason="user_inspection",
                settlement_id="r1:s1",
                job_family="care",
                limit=5,
            )
            region_promoted = ctx.promote_nondetailed_people(
                year=1000,
                reason="regional_spotlight",
                region_id="r1",
                limit=1,
            )

            self.assertEqual([rec.person_id for rec in promoted], [60])
            self.assertEqual([rec.person_id for rec in region_promoted], [62])
            self.assertIn(60, ctx.current_people_ids)
            self.assertIn(62, ctx.current_people_ids)
            self.assertEqual(ctx.nondetailed_population_count(), 1)
            event_payload = next(
                payload
                for _year, event_type, payload in ctx._pending_simulation_events
                if event_type == "nondetailed_person_promoted"
                and payload.get("person_id") == 60
            )
            self.assertEqual(event_payload["source"]["selector_settlement_id"], "r1:s1")
            self.assertEqual(event_payload["source"]["selector_job_family"], "care")

    def test_context_nondetailed_selector_requires_filter(self) -> None:
        ctx = SimulationContext(
            db_path=Path("unused-config.sqlite"),
            save_db_path=Path("unused-save.sqlite"),
            world="default",
            simulation_start_year=1000,
            current_year=1000,
        )

        with self.assertRaises(ValueError):
            ctx.promote_nondetailed_people(year=1000, reason="missing_selector")

    def test_shared_promotion_helpers_prefer_nondetailed_directory(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            save = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext.create(
                db_path=cfg,
                save_db_path=save,
                world_id="nondetailed_shared_promotions",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            )
            sid = "r1:s1"
            ctx.settlements_by_id[sid] = SettlementState(
                settlement_id=sid,
                region_id="r1",
                resident_count=30,
                household_cap=8,
            )
            with closing(sqlite3.connect(save)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                add_nondetailed_person(
                    conn,
                    NondetailedPersonSeed(
                        birthyear=960,
                        gender="Male",
                        region_id="r1",
                        settlement_id=sid,
                        job_family="admin",
                    ),
                    person_id=70,
                )
                add_nondetailed_person(
                    conn,
                    NondetailedPersonSeed(
                        birthyear=970,
                        gender="Female",
                        region_id="r1",
                        settlement_id=sid,
                        job_family="craft",
                        is_partnered=True,
                        partner_person_id=700,
                        father_id=10,
                        mother_id=11,
                        child_count=2,
                    ),
                    person_id=71,
                )
                add_nondetailed_person(
                    conn,
                    NondetailedPersonSeed(
                        birthyear=975,
                        gender="Female",
                        region_id="r1",
                        settlement_id=sid,
                        job_family="care",
                        is_partnered=False,
                    ),
                    person_id=72,
                )
                conn.commit()

            office = promote_passive_candidate_for_office(
                ctx,
                year=1000,
                settlement_id=sid,
                min_age=16,
                reason="office_selection",
            )
            focus = promote_passive_person_for_focus(
                ctx,
                year=1000,
                focus="inspection",
                settlement_id=sid,
            )
            spouse = promote_passive_candidate_for_marriage(
                ctx,
                year=1000,
                gender="Female",
                settlement_id=sid,
                region_id="r1",
                min_age=16,
            )

            self.assertEqual([office.person_id, focus.person_id, spouse.person_id], [70, 71, 72])
            self.assertEqual(ctx.nondetailed_population_count(), 0)
            event_types = [event_type for _year, event_type, _payload in ctx._pending_simulation_events]
            self.assertEqual(event_types.count("nondetailed_person_promoted"), 3)
            self.assertEqual(event_types.count("promotion_backfill_birth"), 3)
            self.assertIn("promotion_backfill_partnership", event_types)
            self.assertIn("promotion_backfill_children", event_types)
            promoted_sources = [
                entry.synthesized["source"]["source_kind"]
                for entry in ctx.passive_promotion_log
            ]
            self.assertEqual(promoted_sources, ["nondetailed_directory"] * 3)

    def test_job_family_counts_affect_settlement_economy(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            save = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext.create(
                db_path=cfg,
                save_db_path=save,
                world_id="nondetailed_economy",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            )
            sid = "r1:s1"
            ctx.settlements_by_id[sid] = SettlementState(
                settlement_id=sid,
                region_id="r1",
                level="settlement",
                resident_count=0,
                household_cap=0,
                food_pressure=0.2,
                prosperity_pool=1.0,
                stability=0.5,
                market_pull=0.2,
            )
            with closing(sqlite3.connect(save)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                for i in range(120):
                    add_nondetailed_person(
                        conn,
                        NondetailedPersonSeed(
                            birthyear=970,
                            gender="Male" if i % 2 else "Female",
                            region_id="r1",
                            settlement_id=sid,
                            job_family="military" if i < 90 else "craft",
                        ),
                        person_id=1000 + i,
                    )
                conn.commit()
                result = apply_nondetailed_job_family_economy_effects(
                    conn, ctx, year=1000
                )

            updated = ctx.settlements_by_id[sid]
            self.assertEqual(result.affected_settlements, 1)
            self.assertGreater(updated.food_pressure, 0.2)
            self.assertGreater(updated.market_pull, 0.2)
            payload = next(
                payload
                for _year, event_type, payload in ctx._pending_simulation_events
                if event_type == "nondetailed_job_family_economy_effect"
            )
            self.assertLessEqual(abs(float(payload["food_pressure_delta"])), 0.08)
            self.assertLessEqual(abs(float(payload["prosperity_pool_delta"])), 0.04)
            self.assertLessEqual(abs(float(payload["market_pull_delta"])), 0.03)
            self.assertLessEqual(abs(float(payload["stability_delta"])), 0.04)

    def test_set_based_nondetailed_migration_moves_to_attractive_existing_settlement(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            save = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext.create(
                db_path=cfg,
                save_db_path=save,
                world_id="nondetailed_migration",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            )
            source = "r1:s1"
            dest = "r1:s2"
            ctx.settlements_by_id[source] = SettlementState(
                settlement_id=source,
                region_id="r1",
                level="settlement",
                resident_count=200,
                household_cap=40,
                food_pressure=1.2,
                prosperity_pool=0.4,
                stability=0.35,
                market_pull=0.1,
            )
            ctx.settlements_by_id[dest] = SettlementState(
                settlement_id=dest,
                region_id="r1",
                level="settlement",
                resident_count=10,
                household_cap=3,
                food_pressure=0.1,
                prosperity_pool=1.8,
                stability=0.8,
                market_pull=0.7,
            )
            with closing(sqlite3.connect(save)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                for i in range(200):
                    add_nondetailed_person(
                        conn,
                        NondetailedPersonSeed(
                            birthyear=970 + (i % 20),
                            gender="Male" if i % 2 else "Female",
                            region_id="r1",
                            settlement_id=source,
                            job_family="food",
                        ),
                        person_id=2000 + i,
                    )
                add_nondetailed_person(
                    conn,
                    NondetailedPersonSeed(
                        birthyear=970,
                        gender="Female",
                        region_id="r1",
                        settlement_id=dest,
                        job_family="trade",
                    ),
                    person_id=5000,
                )
                conn.commit()
                result = run_nondetailed_sql_migration(conn, ctx, year=1000)
                conn.commit()
                dest_count = conn.execute(
                    """
                    SELECT COUNT(*) AS c
                    FROM simulation_people_nondetailed_readable
                    WHERE current_settlement_id = ?
                    """,
                    (dest,),
                ).fetchone()["c"]

            self.assertGreater(result.moved, 0)
            self.assertLessEqual(result.moved, 7)
            self.assertGreater(dest_count, 1)


if __name__ == "__main__":
    unittest.main()
