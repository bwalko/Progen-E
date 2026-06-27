"""Resource-pressure migration out of overcrowded regions."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from library.config_import import load_all_csvs_into_sqlite
from library.generator import generate_person_random
from library.person import Person
from library.simulation_context import SimulationContext
from library.simulation_migration import (
    MIGRATION_MAX_OUTFLOW_SHARE,
    MIGRATION_PRESSURE_THRESHOLD,
    simulation_migration_annual_tick,
)
from library.simulation_outlaws import SimulationOutlawRefuge


class TestSimulationMigration(unittest.TestCase):
    def _add_simple_adults(
        self,
        ctx: SimulationContext,
        *,
        count: int,
        region_id: str,
        settlement_id: str,
        year: int,
    ) -> None:
        for i in range(int(count)):
            gender = "Male" if i % 2 == 0 else "Female"
            ctx.add_person(
                person=Person(
                    first_name=f"Adult{i}",
                    last_name=region_id,
                    gender=gender,
                    ethnic="Human",
                    species="Human",
                    birthyear=int(year) - 30,
                    birthplace_region_id=region_id,
                    birthplace_settlement_id=settlement_id,
                    current_settlement_id=settlement_id,
                    min_fertility_age=18,
                ),
                is_founder=False,
            )

    def test_record_year_summary_calls_migration_annual_tick(self) -> None:
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
                placename_rng_salt=7,
                flush_run_store=False,
            ) as ctx:
                year = 1000
                rates = {**ctx.get_mortality_rates_for_year(year), "deaths_count": 0.0}
                with patch(
                    "library.simulation_migration.simulation_migration_annual_tick"
                ) as mock_mig:
                    ctx.record_year_summary(
                        year=year,
                        births_count=0,
                        deaths_count=0,
                        mortality_rates=rates,
                        evolve_settlements_this_tick=False,
                    )
                mock_mig.assert_called_once_with(ctx, year)

    def test_pressure_moves_singles_to_neighbor_region(self) -> None:
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
                placename_rng_salt=42,
            ) as ctx:
                st_n = ctx.ensure_active_settlement_for_region("aeria_north")
                ctx.ensure_active_settlement_for_region("aeria_granite_range")
                ctx.ensure_active_settlement_for_region("aeria_westwater_river")
                ctx.region_effective_cap_multiplier["aeria_north"] = 0.007
                for _ in range(145):
                    p = generate_person_random(
                        gender="Male",
                        age=30,
                        simulation_year=1000,
                        simulation_context=ctx,
                    )
                    p = replace(
                        p,
                        partner_person_id=None,
                        birthplace_region_id="aeria_north",
                        birthplace_settlement_id=st_n.settlement_id,
                        current_settlement_id=st_n.settlement_id,
                    )
                    ctx.add_person(person=p, is_founder=False)
                north_before = ctx.count_alive_in_region("aeria_north")
                self.assertEqual(north_before, 145)
                granite_before = ctx.count_alive_in_region("aeria_granite_range")
                ww_before = ctx.count_alive_in_region("aeria_westwater_river")
                simulation_migration_annual_tick(ctx, 1000)
                self.assertEqual(ctx.count_alive_in_region("aeria_north"), north_before)
                planned = [
                    pl
                    for _y, et, pl in ctx._pending_simulation_events
                    if et == "settlement_move_planned"
                ]
                self.assertTrue(planned)
                self.assertTrue(ctx.pending_settlement_moves)
                ctx.apply_pending_settlement_moves(1001)
                north_after = ctx.count_alive_in_region("aeria_north")
                granite_after = ctx.count_alive_in_region("aeria_granite_range")
                ww_after = ctx.count_alive_in_region("aeria_westwater_river")
                self.assertLess(north_after, north_before)
                self.assertGreater(
                    (granite_after + ww_after),
                    (granite_before + ww_before),
                )
                moved = [
                    pl
                    for _y, et, pl in ctx._pending_simulation_events
                    if et == "settlement_moved"
                ]
                self.assertTrue(moved)
                cross = [
                    m
                    for m in moved
                    if m.get("cross_region") and m.get("move_reason")
                ]
                self.assertTrue(cross)
                sample = cross[0]
                self.assertEqual(sample.get("move_reason"), "resource_pressure_migration")
                self.assertEqual(sample.get("from_region_id"), "aeria_north")
                self.assertIn(
                    sample.get("to_region_id"),
                    ("aeria_granite_range", "aeria_westwater_river", "aeria_port"),
                )

    def test_spinoff_requires_multiple_colonist_families(self) -> None:
        """New hamlets accrue colonist ``wins'' before founding (see ``spinoff_families_required``)."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="spin",
                world="default",
                start_year=1000,
                refresh_config=False,
                placename_rng_salt=2,
                flush_run_store=False,
            ) as ctx:
                st = ctx.ensure_active_settlement_for_region("aeria_north")
                rid = "aeria_north"
                sid = st.settlement_id
                for _ in range(25):
                    p = generate_person_random(
                        gender="Female",
                        age=30,
                        simulation_year=1000,
                        simulation_context=ctx,
                    )
                    p = replace(
                        p,
                        partner_person_id=None,
                        birthplace_region_id=rid,
                        birthplace_settlement_id=sid,
                        current_settlement_id=sid,
                    )
                    ctx.add_person(person=p, is_founder=False)
                ctx.current_year = 1000
                ctx.spinoff_families_required = 3
                ctx.spinoff_min_mother_settlement_population = 1
                ctx.spinoff_cooldown_years = 0
                ctx.region_effective_cap_multiplier[rid] = 1.0
                rng = Mock()
                rng.random = Mock(side_effect=[0.01, 0.05] * 5)
                active_before = len(ctx.active_settlements_in_region(rid))
                _r, s1 = ctx.maybe_spin_off_birth_settlement(rid, sid, rng)
                self.assertEqual(s1, sid)
                self.assertEqual(ctx.spinoff_pending_families_by_region.get(rid, 0), 1)
                _r, s2 = ctx.maybe_spin_off_birth_settlement(rid, sid, rng)
                self.assertEqual(s2, sid)
                self.assertEqual(ctx.spinoff_pending_families_by_region.get(rid, 0), 2)
                _r, s3 = ctx.maybe_spin_off_birth_settlement(rid, sid, rng)
                self.assertIsNotNone(s3)
                self.assertNotEqual(s3, sid)
                self.assertEqual(
                    len(ctx.active_settlements_in_region(rid)),
                    active_before + 1,
                )
                new_st = ctx.settlements_by_id[str(s3)]
                self.assertEqual(new_st.founding_reason, "birth_spinoff")
                self.assertEqual(new_st.mother_settlement_id, sid)
                self.assertEqual(ctx.spinoff_pending_families_by_region.get(rid, 0), 0)

    def test_ordinary_satellite_founding_ignores_outlaw_refuges(self) -> None:
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
                placename_rng_salt=17,
                flush_run_store=False,
            ) as ctx:
                rid = "aeria_north"
                st = ctx.ensure_active_settlement_for_region(rid)
                ctx.outlaw_refuges[f"outlaw_refuge:{rid}:1"] = SimulationOutlawRefuge(
                    refuge_id=f"outlaw_refuge:{rid}:1",
                    region_id=rid,
                    near_settlement_id=st.settlement_id,
                    founded_year=1000,
                )
                self._add_simple_adults(
                    ctx,
                    count=1100,
                    region_id=rid,
                    settlement_id=st.settlement_id,
                    year=1000,
                )
                active_before = ctx.active_settlements_in_region(rid)
                self.assertEqual(len(active_before), 1)
                self.assertEqual(len(ctx.outlaw_refuges), 1)

                satellite = ctx.maybe_found_ordinary_satellite_settlement(
                    rid,
                    year=1000,
                    region_population=ctx.mixed_population_count_in_region(rid),
                    region_cap=5000,
                )

                self.assertIsNotNone(satellite)
                self.assertEqual(len(ctx.active_settlements_in_region(rid)), 2)
                self.assertEqual(len(ctx.outlaw_refuges), 1)
                self.assertEqual(satellite.founding_reason, "birth_spinoff")
                self.assertEqual(satellite.mother_settlement_id, st.settlement_id)
                self.assertGreater(int(satellite.site_slot), 1)

                again = ctx.maybe_found_ordinary_satellite_settlement(
                    rid,
                    year=1000,
                    region_population=ctx.mixed_population_count_in_region(rid),
                    region_cap=5000,
                )
                self.assertIsNone(again)
                self.assertEqual(len(ctx.active_settlements_in_region(rid)), 2)

    def test_service_village_founding_uses_regional_hinterland_need(self) -> None:
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
                placename_rng_salt=17,
                flush_run_store=False,
            ) as ctx:
                rid = "aeria_north"
                st = ctx.ensure_active_settlement_for_region(rid)
                self._add_simple_adults(
                    ctx,
                    count=30,
                    region_id=rid,
                    settlement_id=st.settlement_id,
                    year=1000,
                )

                with patch.object(ctx, "active_settlement_in_same_map_polygon", return_value=None):
                    satellite = ctx.maybe_found_ordinary_satellite_settlement(
                        rid,
                        year=1000,
                        region_population=22_000,
                        region_cap=22_000,
                    )

                self.assertIsNotNone(satellite)
                self.assertEqual(len(ctx.active_settlements_in_region(rid)), 2)
                self.assertEqual(satellite.founding_reason, "regional_service_village")
                self.assertEqual(satellite.mother_settlement_id, st.settlement_id)
                self.assertGreater(int(satellite.site_slot), 1)

    def test_migration_tick_founds_local_satellite_before_outflow(self) -> None:
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
                placename_rng_salt=18,
                flush_run_store=False,
            ) as ctx:
                rid = "aeria_north"
                st = ctx.ensure_active_settlement_for_region(rid)
                ctx.region_effective_cap_multiplier[rid] = 5.0
                self._add_simple_adults(
                    ctx,
                    count=1100,
                    region_id=rid,
                    settlement_id=st.settlement_id,
                    year=1000,
                )

                simulation_migration_annual_tick(ctx, 1000)

                active = ctx.active_settlements_in_region(rid)
                self.assertEqual(len(active), 2)
                satellite = [s for s in active if int(s.site_slot) > 1][0]
                self.assertEqual(satellite.founding_reason, "birth_spinoff")
                self.assertEqual(satellite.mother_settlement_id, st.settlement_id)

    def test_satellite_founding_cooldown_uses_saved_spinoff_rows(self) -> None:
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
                placename_rng_salt=19,
                flush_run_store=False,
            ) as ctx:
                rid = "aeria_north"
                st = ctx.ensure_active_settlement_for_region(rid)
                existing = ctx.create_additional_active_settlement(
                    rid,
                    founding_reason="birth_spinoff",
                    mother_settlement_id=st.settlement_id,
                )
                ctx.settlements_by_id[existing.settlement_id] = replace(
                    existing,
                    founded_sim_year=998,
                )
                self._add_simple_adults(
                    ctx,
                    count=1100,
                    region_id=rid,
                    settlement_id=st.settlement_id,
                    year=1000,
                )

                satellite = ctx.maybe_found_ordinary_satellite_settlement(
                    rid,
                    year=1000,
                    region_population=5000,
                    region_cap=10000,
                )

                self.assertIsNone(satellite)
                self.assertEqual(len(ctx.active_settlements_in_region(rid)), 2)

    def test_married_couple_migrates_together(self) -> None:
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
                placename_rng_salt=99,
            ) as ctx:
                st_n = ctx.ensure_active_settlement_for_region("aeria_north")
                ctx.ensure_active_settlement_for_region("aeria_granite_range")
                ctx.region_effective_cap_multiplier["aeria_north"] = 0.007
                for _ in range(140):
                    p = generate_person_random(
                        gender="Male",
                        age=30,
                        simulation_year=1000,
                        simulation_context=ctx,
                    )
                    p = replace(
                        p,
                        partner_person_id=None,
                        birthplace_region_id="aeria_north",
                        birthplace_settlement_id=st_n.settlement_id,
                        current_settlement_id=st_n.settlement_id,
                    )
                    ctx.add_person(person=p, is_founder=False)
                m = generate_person_random(
                    gender="Male",
                    age=30,
                    simulation_year=1000,
                    simulation_context=ctx,
                )
                f = generate_person_random(
                    gender="Female",
                    age=30,
                    simulation_year=1000,
                    simulation_context=ctx,
                )
                m = replace(
                    m,
                    partner_person_id=None,
                    birthplace_region_id="aeria_north",
                    birthplace_settlement_id=st_n.settlement_id,
                    current_settlement_id=st_n.settlement_id,
                )
                f = replace(
                    f,
                    partner_person_id=None,
                    birthplace_region_id="aeria_north",
                    birthplace_settlement_id=st_n.settlement_id,
                    current_settlement_id=st_n.settlement_id,
                )
                mr = ctx.add_person(person=m, is_founder=False)
                fr = ctx.add_person(person=f, is_founder=False)
                ctx.add_couple(mr.person_id, fr.person_id)
                pid_a, pid_b = mr.person_id, fr.person_id
                north_before = ctx.count_alive_in_region("aeria_north")
                simulation_migration_annual_tick(ctx, 1000)
                self.assertEqual(ctx.count_alive_in_region("aeria_north"), north_before)
                self.assertTrue(ctx.pending_settlement_moves)
                ctx.apply_pending_settlement_moves(1001)
                self.assertLess(ctx.count_alive_in_region("aeria_north"), north_before)
                pa = ctx.id_to_record[pid_a].person
                pb = ctx.id_to_record[pid_b].person
                self.assertEqual(
                    (pa.current_settlement_id or ""),
                    (pb.current_settlement_id or ""),
                )

    def test_high_pressure_uses_tuned_outflow_share(self) -> None:
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
                placename_rng_salt=5,
                flush_run_store=False,
            ) as ctx:
                origin = ctx.ensure_active_settlement_for_region("aeria_north")
                ctx.ensure_active_settlement_for_region("aeria_granite_range")
                ctx.ensure_active_settlement_for_region("aeria_westwater_river")
                self._add_simple_adults(
                    ctx,
                    count=1600,
                    region_id="aeria_north",
                    settlement_id=origin.settlement_id,
                    year=1000,
                )

                simulation_migration_annual_tick(ctx, 1000)

                planned = [
                    payload
                    for _year, event_type, payload in ctx._pending_simulation_events
                    if event_type == "settlement_move_planned"
                ]
                self.assertLessEqual(
                    len(planned),
                    int(1600 * MIGRATION_MAX_OUTFLOW_SHARE),
                )
                self.assertGreater(len(planned), 0)

    def test_near_threshold_migration_does_not_overshoot_surplus(self) -> None:
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
                placename_rng_salt=6,
                flush_run_store=False,
            ) as ctx:
                origin = ctx.ensure_active_settlement_for_region("aeria_north")
                ctx.ensure_active_settlement_for_region("aeria_granite_range")
                ctx.ensure_active_settlement_for_region("aeria_westwater_river")
                cap = ctx.effective_regional_population_cap("aeria_north")
                population = int(cap * MIGRATION_PRESSURE_THRESHOLD) + 22
                self._add_simple_adults(
                    ctx,
                    count=population,
                    region_id="aeria_north",
                    settlement_id=origin.settlement_id,
                    year=1000,
                )

                simulation_migration_annual_tick(ctx, 1000)

                planned = [
                    payload
                    for _year, event_type, payload in ctx._pending_simulation_events
                    if event_type == "settlement_move_planned"
                ]
                self.assertLessEqual(len(planned), 22)
                self.assertLess(len(planned), int(population * MIGRATION_MAX_OUTFLOW_SHARE))


if __name__ == "__main__":
    unittest.main()
