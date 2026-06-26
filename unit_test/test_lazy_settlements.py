"""Lazy settlement creation, abandonment escalation, and re-establishment."""

from __future__ import annotations

import json
import random
import unittest

from library.config_import import refresh_world_config_from_csv
from library.settlements import (
    ABANDON_DISTRESS_GRACE_YEARS,
    ABANDON_EMPTY_GRACE_YEARS,
    SettlementState,
    directory_mixed_alive,
    evaluate_settlement_abandonment,
    low_resolution_promotion_count,
    roll_abandon_this_year,
    settlement_distress_counts_as_vacancy,
    settlement_economic_distress_this_year,
)


def setUpModule() -> None:
    refresh_world_config_from_csv("default")


class TestLazySettlements(unittest.TestCase):
    def test_roll_abandon_respects_grace(self) -> None:
        rng = random.Random(0)
        self.assertFalse(roll_abandon_this_year(ABANDON_EMPTY_GRACE_YEARS, rng))
        self.assertFalse(roll_abandon_this_year(ABANDON_EMPTY_GRACE_YEARS - 1, rng))

    def test_roll_abandon_saturates_to_certain(self) -> None:
        rng = random.Random(1)
        self.assertTrue(
            roll_abandon_this_year(ABANDON_EMPTY_GRACE_YEARS + 50, rng)
        )

    def test_small_extreme_distress_counts_as_vacancy(self) -> None:
        failing = SettlementState(
            region_id="r1",
            settlement_id="r1:s1",
            resident_count=80,
            food_pressure=1.9,
            stability=0.05,
            prosperity_pool=0.08,
        )
        large = SettlementState(
            region_id="r1",
            settlement_id="r1:s2",
            resident_count=800,
            food_pressure=1.9,
            stability=0.05,
            prosperity_pool=0.08,
        )

        self.assertTrue(settlement_distress_counts_as_vacancy(failing))
        self.assertFalse(settlement_distress_counts_as_vacancy(large))

    def test_mixed_viability_blocks_detailed_only_empty_abandon(self) -> None:
        viable = SettlementState(
            region_id="boreas_west",
            settlement_id="boreas_west:s8",
            food_pressure=0.4,
            stability=0.8,
            prosperity_pool=1.4,
            consecutive_empty_years=ABANDON_EMPTY_GRACE_YEARS + 2,
        )
        decision = evaluate_settlement_abandonment(
            viable,
            detailed_alive=0,
            nondetailed_alive=220,
            rng=random.Random(0),
        )
        self.assertFalse(decision.should_abandon)
        self.assertTrue(decision.should_promote_sample)
        self.assertEqual(low_resolution_promotion_count(220), 3)

    def test_sustained_distress_can_abandon_meaningful_mixed_population(self) -> None:
        distressed = SettlementState(
            region_id="boreas_west",
            settlement_id="boreas_west:s9",
            food_pressure=1.9,
            stability=0.05,
            prosperity_pool=0.08,
            consecutive_empty_years=ABANDON_DISTRESS_GRACE_YEARS + 2,
        )
        self.assertTrue(
            settlement_economic_distress_this_year(
                distressed,
                mixed_alive=directory_mixed_alive(
                    detailed_alive=40,
                    nondetailed_alive=10,
                ),
            )
        )
        decision = evaluate_settlement_abandonment(
            distressed,
            detailed_alive=40,
            nondetailed_alive=10,
            rng=random.Random(1),
        )
        self.assertTrue(decision.should_abandon)
        self.assertEqual(decision.abandon_reason, "economic")

    def test_truly_empty_settlement_can_abandon(self) -> None:
        empty = SettlementState(
            region_id="boreas_west",
            settlement_id="boreas_west:s10",
            consecutive_empty_years=ABANDON_EMPTY_GRACE_YEARS + 20,
        )
        decision = evaluate_settlement_abandonment(
            empty,
            detailed_alive=0,
            nondetailed_alive=0,
            rng=random.Random(2),
        )
        self.assertTrue(decision.should_abandon)
        self.assertEqual(decision.abandon_reason, "empty")

    def test_viable_low_resolution_settlement_promotes_sample(self) -> None:
        from contextlib import closing
        from pathlib import Path
        import sqlite3
        import tempfile

        from library.config_import import load_all_csvs_into_sqlite
        from library.nondetailed_population import (
            NondetailedPersonSeed,
            add_nondetailed_person,
            nondetailed_counts_by_settlement,
        )
        from library.simulation_context import SimulationContext
        from library.world_save import ensure_checkpoint_schema

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            save = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext(
                db_path=cfg,
                save_db_path=save,
                world="default",
                simulation_start_year=1000,
                history_equivalent_start_year=1000,
                current_year=1010,
            )
            try:
                dest = ctx.ensure_active_settlement_for_region("boreas_west")
                low_res = SettlementState(
                    region_id="boreas_west",
                    region_display_name="Boreas West",
                    settlement_id="boreas_west:s8",
                    site_slot=8,
                    status="active",
                    resident_count=0,
                    food_pressure=0.4,
                    stability=0.8,
                    prosperity_pool=1.4,
                    consecutive_empty_years=ABANDON_EMPTY_GRACE_YEARS + 2,
                )
                ctx.settlements_by_id[low_res.settlement_id] = low_res
                ctx.rebuild_settlement_region_index()
                with closing(sqlite3.connect(save)) as conn:
                    conn.row_factory = sqlite3.Row
                    ensure_checkpoint_schema(conn)
                    conn.execute(
                        "INSERT OR IGNORE INTO simulation_settlement_lookup(settlement_id, region_key) "
                        "SELECT ?, region_key FROM simulation_settlement_lookup WHERE settlement_id = ?",
                        (low_res.settlement_id, dest.settlement_id),
                    )
                    for pid in range(601, 821):
                        add_nondetailed_person(
                            conn,
                            NondetailedPersonSeed(
                                birthyear=980,
                                gender="Female" if pid % 2 else "Male",
                                region_id="boreas_west",
                                settlement_id=low_res.settlement_id,
                            ),
                            person_id=pid,
                        )
                    conn.commit()

                ctx.evolve_settlements_one_year()

                kept = ctx.settlements_by_id[low_res.settlement_id]
                self.assertEqual((kept.status or "").strip().lower(), "active")
                detailed_here = ctx.count_alive_in_settlement(low_res.settlement_id)
                self.assertGreaterEqual(detailed_here, 2)
                self.assertLessEqual(detailed_here, 4)
                with closing(sqlite3.connect(save)) as conn:
                    conn.row_factory = sqlite3.Row
                    ensure_checkpoint_schema(conn)
                    counts = nondetailed_counts_by_settlement(conn)
                self.assertGreaterEqual(counts.get(low_res.settlement_id, 0), 216)
            finally:
                ctx.finalize_run()

    def test_reestablish_new_id_same_site_slot(self) -> None:
        from library.simulation_context import SimulationContext
        from pathlib import Path
        import tempfile
        from library.config_import import load_all_csvs_into_sqlite

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext(
                db_path=cfg,
                save_db_path=root / "save.sqlite",
                world="default",
                simulation_start_year=1000,
                history_equivalent_start_year=1000,
                current_year=1005,
            )
            try:
                old = SettlementState(
                    region_id="boreas_west",
                    settlement_id="boreas_west:s1",
                    site_slot=1,
                    status="abandoned",
                    abandoned_sim_year=1002,
                    display_name="Oldtown",
                    region_display_name="Boreas West",
                    local_geography_json="{}",
                )
                ctx.settlements_by_id["boreas_west:s1"] = old
                new = ctx.reestablish_from_abandoned(old)
                self.assertNotEqual(new.settlement_id, old.settlement_id)
                self.assertEqual(new.site_slot, 1)
                self.assertEqual(new.founded_sim_year, 1005)
                self.assertEqual(new.display_name, "Oldtown")
                self.assertEqual(new.status, "active")
            finally:
                ctx.finalize_run()

    def test_reestablish_reuses_existing_active_same_site_slot(self) -> None:
        from library.simulation_context import SimulationContext
        from pathlib import Path
        import tempfile
        from library.config_import import load_all_csvs_into_sqlite

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext(
                db_path=cfg,
                save_db_path=root / "save.sqlite",
                world="default",
                simulation_start_year=1000,
                history_equivalent_start_year=1000,
                current_year=1005,
            )
            try:
                old = SettlementState(
                    region_id="boreas_west",
                    settlement_id="boreas_west:s1",
                    site_slot=1,
                    status="abandoned",
                    abandoned_sim_year=1002,
                    display_name="Oldtown",
                    region_display_name="Boreas West",
                    local_geography_json="{}",
                )
                active = SettlementState(
                    region_id="boreas_west",
                    settlement_id="boreas_west:s2",
                    site_slot=1,
                    status="active",
                    display_name="Oldtown",
                    region_display_name="Boreas West",
                    local_geography_json="{}",
                )
                ctx.settlements_by_id[old.settlement_id] = old
                ctx.settlements_by_id[active.settlement_id] = active
                ctx.rebuild_settlement_region_index()

                reused = ctx.reestablish_from_abandoned(old)

                self.assertEqual(reused.settlement_id, active.settlement_id)
                self.assertEqual(
                    len(ctx.active_settlements_in_region("boreas_west")),
                    1,
                )
            finally:
                ctx.finalize_run()

    def test_additional_settlements_share_multi_slot_region_geography(self) -> None:
        from library.simulation_context import SimulationContext
        from pathlib import Path
        import tempfile
        from library.config_import import load_all_csvs_into_sqlite

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext(
                db_path=cfg,
                save_db_path=root / "save.sqlite",
                world="default",
                simulation_start_year=1000,
                history_equivalent_start_year=1000,
                current_year=1005,
            )
            try:
                first = ctx.ensure_active_settlement_for_region("boreas_fjord_shore")
                second = ctx.create_additional_active_settlement("boreas_fjord_shore")
                third = ctx.create_additional_active_settlement("boreas_fjord_shore")

                self.assertEqual([first.site_slot, second.site_slot, third.site_slot], [1, 2, 3])
                shared_geo = first.local_geography_json
                self.assertEqual(second.local_geography_json, shared_geo)
                self.assertEqual(third.local_geography_json, shared_geo)

                data = json.loads(shared_geo or "{}")
                sites = data.get("settlements", [])
                self.assertEqual(len(sites), 3)
                coords = {(round(float(site["x"]), 6), round(float(site["y"]), 6)) for site in sites}
                self.assertEqual(len(coords), 3)
            finally:
                ctx.finalize_run()

    def test_settlement_creation_falls_back_without_optional_world_geometry(self) -> None:
        from library.simulation_context import SimulationContext
        from pathlib import Path
        import tempfile
        from library.config_import import load_all_csvs_into_sqlite

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext(
                db_path=cfg,
                save_db_path=root / "save.sqlite",
                world="default",
                simulation_start_year=1000,
                history_equivalent_start_year=1000,
                current_year=1005,
            )

            def missing_geometry():
                raise ModuleNotFoundError("optional geometry unavailable")

            try:
                ctx.world_map_geometry_for_settlements = missing_geometry  # type: ignore[method-assign]

                first = ctx.ensure_active_settlement_for_region("boreas_fjord_shore")
                second = ctx.create_additional_active_settlement("boreas_fjord_shore")

                self.assertEqual([first.site_slot, second.site_slot], [1, 2])
                self.assertEqual(first.local_geography_json, second.local_geography_json)
                data = json.loads(first.local_geography_json or "{}")
                self.assertEqual(len(data.get("settlements", [])), 2)
            finally:
                ctx.finalize_run()

    def test_proposed_same_polygon_settlement_reuses_active_settlement(self) -> None:
        from library.simulation_context import SimulationContext
        from pathlib import Path
        import tempfile
        from library.config_import import load_all_csvs_into_sqlite

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext(
                db_path=cfg,
                save_db_path=root / "save.sqlite",
                world="default",
                simulation_start_year=1000,
                history_equivalent_start_year=1000,
                current_year=1005,
            )
            try:
                first = ctx.ensure_active_settlement_for_region("boreas_fjord_shore")
                first_xy = ctx._site_world_xy_from_geo_json(
                    first.local_geography_json,
                    first.site_slot,
                    first.region_id,
                )
                self.assertIsNotNone(first_xy)
                proposed_geo = json.dumps(
                    {
                        "settlements": [
                            {
                                "settlement_slot": 0,
                                "x": 0.5,
                                "y": 0.5,
                                "world_x": first_xy[0],
                                "world_y": first_xy[1],
                            },
                            {
                                "settlement_slot": 1,
                                "x": 0.5,
                                "y": 0.5,
                                "world_x": first_xy[0],
                                "world_y": first_xy[1],
                            },
                        ]
                    }
                )

                existing = ctx.active_settlement_in_same_map_polygon(
                    first.region_id,
                    site_slot=2,
                    local_geography_json=proposed_geo,
                )

                self.assertIsNotNone(existing)
                self.assertEqual(existing.settlement_id, first.settlement_id)
            finally:
                ctx.finalize_run()

    def test_abandonment_evacuates_nondetailed_residents(self) -> None:
        from contextlib import closing
        from pathlib import Path
        import sqlite3
        import tempfile
        from unittest.mock import patch

        from library.config_import import load_all_csvs_into_sqlite
        from library.nondetailed_population import (
            NondetailedPersonSeed,
            add_nondetailed_person,
            nondetailed_counts_by_settlement,
        )
        from library.simulation_context import SimulationContext
        from library.world_save import ensure_checkpoint_schema

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            save = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext(
                db_path=cfg,
                save_db_path=save,
                world="default",
                simulation_start_year=1000,
                history_equivalent_start_year=1000,
                current_year=1010,
            )
            try:
                dest = ctx.ensure_active_settlement_for_region("boreas_west")
                distressed = SettlementState(
                    region_id="boreas_west",
                    region_display_name="Boreas West",
                    settlement_id="boreas_west:s9",
                    site_slot=9,
                    status="active",
                    resident_count=40,
                    food_pressure=1.9,
                    stability=0.05,
                    prosperity_pool=0.08,
                    consecutive_empty_years=ABANDON_DISTRESS_GRACE_YEARS + 2,
                )
                ctx.settlements_by_id[distressed.settlement_id] = distressed
                ctx.rebuild_settlement_region_index()
                with closing(sqlite3.connect(save)) as conn:
                    conn.row_factory = sqlite3.Row
                    ensure_checkpoint_schema(conn)
                    conn.execute(
                        "INSERT OR IGNORE INTO simulation_settlement_lookup(settlement_id, region_key) "
                        "SELECT ?, region_key FROM simulation_settlement_lookup WHERE settlement_id = ?",
                        (distressed.settlement_id, dest.settlement_id),
                    )
                    for pid in range(501, 511):
                        add_nondetailed_person(
                            conn,
                            NondetailedPersonSeed(
                                birthyear=980,
                                gender="Female" if pid % 2 else "Male",
                                region_id="boreas_west",
                                settlement_id=distressed.settlement_id,
                            ),
                            person_id=pid,
                        )
                    conn.commit()

                with patch(
                    "library.settlements.roll_abandon_this_year",
                    return_value=True,
                ):
                    ctx.evolve_settlements_one_year()

                abandoned = ctx.settlements_by_id[distressed.settlement_id]
                self.assertEqual((abandoned.status or "").strip().lower(), "abandoned")
                with closing(sqlite3.connect(save)) as conn:
                    conn.row_factory = sqlite3.Row
                    ensure_checkpoint_schema(conn)
                    counts = nondetailed_counts_by_settlement(conn)
                self.assertEqual(counts.get(distressed.settlement_id, 0), 0)
                self.assertEqual(sum(counts.values()), 10)
                self.assertGreater(
                    sum(
                        count
                        for sid, count in counts.items()
                        if sid != distressed.settlement_id
                    ),
                    0,
                )
            finally:
                ctx.finalize_run()


if __name__ == "__main__":
    unittest.main()
