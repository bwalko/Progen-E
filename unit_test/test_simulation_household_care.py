"""Household care yearly batch: gates, orphan routing, shortfall, partner reconcile."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from library.config_import import load_all_csvs_into_sqlite
from library.generator import generate_person_random
from library.simulation_context import SimulationContext
from library.simulation_household_care import (
    CHILD_DUTY_FACTOR_CAP,
    build_year_indexes,
    childcare_duty_factor,
    dependent_minors_in_implicit_household,
    gate_a_co_resident_parent,
    gate_b_extended_family_in_settlement,
    simulation_household_care_annual_tick,
)


class TestSimulationHouseholdCare(unittest.TestCase):
    def test_record_year_summary_calls_household_care_tick(self) -> None:
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
                placename_rng_salt=3,
                flush_run_store=False,
            ) as ctx:
                year = 1000
                rates = {**ctx.get_mortality_rates_for_year(year), "deaths_count": 0.0}
                with patch(
                    "library.simulation_household_care.simulation_household_care_annual_tick"
                ) as mock_care:
                    ctx.record_year_summary(
                        year=year,
                        births_count=0,
                        deaths_count=0,
                        mortality_rates=rates,
                        evolve_settlements_this_tick=False,
                    )
                mock_care.assert_called_once_with(ctx, year)

    def test_gate_a_co_resident_parent_skips_extended_family(self) -> None:
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
                placename_rng_salt=11,
            ) as ctx:
                from dataclasses import replace

                st = ctx.ensure_active_settlement_for_region("aeria_north")
                sid = st.settlement_id
                mom = generate_person_random(
                    gender="Female",
                    age=30,
                    simulation_year=1000,
                    simulation_context=ctx,
                )
                mom = replace(
                    mom,
                    birthplace_settlement_id=sid,
                    current_settlement_id=sid,
                    birthyear=1970,
                    gender_mind="feminine",
                )
                mr = ctx.add_person(person=mom, is_founder=False)
                ch = replace(
                    generate_person_random(
                        gender="Female",
                        age=1,
                        simulation_year=1000,
                        simulation_context=ctx,
                    ),
                    birthyear=1995,
                    birthplace_settlement_id=sid,
                    current_settlement_id=sid,
                    min_fertility_age=18,
                )
                cr = ctx.add_person(
                    person=ch, is_founder=False, mother_id=mr.person_id, father_id=None
                )
                idx = build_year_indexes(ctx, 2000)
                self.assertTrue(
                    gate_a_co_resident_parent(ctx, cr, sid),
                )
                self.assertFalse(
                    gate_b_extended_family_in_settlement(ctx, cr, sid, idx),
                )

    def test_gate_b_aunt_in_settlement_covers_child(self) -> None:
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
                placename_rng_salt=12,
            ) as ctx:
                from dataclasses import replace

                st = ctx.ensure_active_settlement_for_region("aeria_north")
                sid = st.settlement_id
                st_gp = ctx.ensure_active_settlement_for_region("aeria_granite_range")
                sid_gp = st_gp.settlement_id
                gp = generate_person_random(
                    gender="Male",
                    age=60,
                    simulation_year=1000,
                    simulation_context=ctx,
                )
                gp = replace(
                    gp,
                    birthyear=1920,
                    birthplace_settlement_id=sid_gp,
                    current_settlement_id=sid_gp,
                )
                gp_rec = ctx.add_person(person=gp, is_founder=False)
                dead_parent = generate_person_random(
                    gender="Female",
                    age=35,
                    simulation_year=1000,
                    simulation_context=ctx,
                )
                dead_parent = replace(
                    dead_parent,
                    birthyear=1950,
                    birthplace_settlement_id=sid,
                    current_settlement_id=sid,
                )
                pr = ctx.add_person(
                    person=dead_parent,
                    is_founder=False,
                    father_id=gp_rec.person_id,
                    mother_id=None,
                )
                ctx.mark_dead({pr.person_id}, deathyear=1000)

                aunt = generate_person_random(
                    gender="Female",
                    age=40,
                    simulation_year=1000,
                    simulation_context=ctx,
                )
                aunt = replace(
                    aunt,
                    birthyear=1955,
                    birthplace_settlement_id=sid,
                    current_settlement_id=sid,
                )
                ctx.add_person(
                    person=aunt,
                    is_founder=False,
                    father_id=gp_rec.person_id,
                    mother_id=None,
                )

                child = replace(
                    generate_person_random(
                        gender="Female",
                        age=1,
                        simulation_year=1000,
                        simulation_context=ctx,
                    ),
                    birthyear=1995,
                    birthplace_settlement_id=sid,
                    current_settlement_id=sid,
                    min_fertility_age=18,
                )
                cr = ctx.add_person(
                    person=child,
                    is_founder=False,
                    mother_id=pr.person_id,
                    father_id=None,
                )
                idx = build_year_indexes(ctx, 2000)
                self.assertFalse(gate_a_co_resident_parent(ctx, cr, sid))
                self.assertTrue(
                    gate_b_extended_family_in_settlement(ctx, cr, sid, idx),
                )

    def test_orphan_moves_to_largest_settlement(self) -> None:
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
                placename_rng_salt=13,
            ) as ctx:
                from dataclasses import replace

                st_small = ctx.ensure_active_settlement_for_region("aeria_north")
                st_large = ctx.ensure_active_settlement_for_region("aeria_granite_range")
                sid_small = st_small.settlement_id
                sid_large = st_large.settlement_id
                for _ in range(25):
                    p = generate_person_random(
                        gender="Male",
                        age=25,
                        simulation_year=1000,
                        simulation_context=ctx,
                    )
                    p = replace(
                        p,
                        birthyear=1975,
                        birthplace_settlement_id=sid_large,
                        current_settlement_id=sid_large,
                    )
                    ctx.add_person(person=p, is_founder=False)

                dead_m = generate_person_random(
                    gender="Female",
                    age=30,
                    simulation_year=1000,
                    simulation_context=ctx,
                )
                dead_m = replace(
                    dead_m,
                    birthyear=1970,
                    birthplace_settlement_id=sid_small,
                    current_settlement_id=sid_small,
                )
                mr = ctx.add_person(person=dead_m, is_founder=False)
                ctx.mark_dead({mr.person_id}, deathyear=1000)

                child = replace(
                    generate_person_random(
                        gender="Female",
                        age=1,
                        simulation_year=1000,
                        simulation_context=ctx,
                    ),
                    birthyear=1995,
                    birthplace_settlement_id=sid_small,
                    current_settlement_id=sid_small,
                    min_fertility_age=18,
                )
                cr = ctx.add_person(
                    person=child,
                    is_founder=False,
                    mother_id=mr.person_id,
                    father_id=None,
                )
                ctx.current_year = 2000
                simulation_household_care_annual_tick(ctx, 2000)
                self.assertNotEqual(
                    (ctx.id_to_record[cr.person_id].person.current_settlement_id or ""),
                    sid_large,
                )
                ctx.apply_pending_settlement_moves(2001)
                self.assertEqual(
                    (ctx.id_to_record[cr.person_id].person.current_settlement_id or ""),
                    sid_large,
                )
                routed = [
                    pl
                    for _y, et, pl in ctx._pending_simulation_events
                    if et == "orphan_routed_to_largest_settlement"
                ]
                self.assertTrue(routed)

    def test_childcare_shortfall_mortality_with_patched_rng(self) -> None:
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
                placename_rng_salt=14,
            ) as ctx:
                from dataclasses import replace

                st = ctx.ensure_active_settlement_for_region("aeria_north")
                sid = st.settlement_id
                mom = generate_person_random(
                    gender="Female",
                    age=30,
                    simulation_year=1000,
                    simulation_context=ctx,
                )
                mom = replace(
                    mom,
                    birthyear=1970,
                    birthplace_settlement_id=sid,
                    current_settlement_id=sid,
                    gender_mind="masculine",
                    genome={k: 80.0 for k in ("empathy", "patience", "nurturance")},
                )
                dad = generate_person_random(
                    gender="Male",
                    age=30,
                    simulation_year=1000,
                    simulation_context=ctx,
                )
                dad = replace(
                    dad,
                    birthyear=1970,
                    birthplace_settlement_id=sid,
                    current_settlement_id=sid,
                    gender_mind="masculine",
                    genome={k: 80.0 for k in ("empathy", "patience", "nurturance")},
                )
                mr = ctx.add_person(person=mom, is_founder=False)
                dr = ctx.add_person(person=dad, is_founder=False)
                ctx.add_couple(mr.person_id, dr.person_id)
                kids: list[int] = []
                for _ in range(6):
                    ch = replace(
                        generate_person_random(
                            gender="Female",
                            age=1,
                            simulation_year=1000,
                            simulation_context=ctx,
                        ),
                        birthyear=1996,
                        birthplace_settlement_id=sid,
                        current_settlement_id=sid,
                        min_fertility_age=18,
                    )
                    kr = ctx.add_person(
                        person=ch,
                        is_founder=False,
                        mother_id=mr.person_id,
                        father_id=dr.person_id,
                    )
                    kids.append(kr.person_id)

                ctx.current_year = 2000
                rm = MagicMock()
                rm.random.side_effect = [0.0, 0.99]
                with patch(
                    "library.simulation_household_care._tick_rng", return_value=rm
                ), patch(
                    "library.simulation_household_care.CARE_SHORTFALL_CRISIS_BASE",
                    9.0,
                ):
                    simulation_household_care_annual_tick(ctx, 2000)

                victim = min(kids)
                self.assertNotIn(victim, ctx.current_people_ids)
                shortfall_events = [
                    pl
                    for _y, et, pl in ctx._pending_simulation_events
                    if et == "household_childcare_shortfall"
                ]
                self.assertTrue(
                    any(e.get("outcome") == "mortality" for e in shortfall_events)
                )

    def test_grandparent_same_settlement_adds_childcare_supply(self) -> None:
        """Co-resident grandparent increases supply so high shortfall roll does not fire."""
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
                placename_rng_salt=77,
            ) as ctx:
                from dataclasses import replace

                st = ctx.ensure_active_settlement_for_region("aeria_north")
                sid = st.settlement_id
                gp = replace(
                    generate_person_random(
                        gender="Female",
                        age=70,
                        simulation_year=2000,
                        simulation_context=ctx,
                    ),
                    birthyear=1940,
                    birthplace_settlement_id=sid,
                    current_settlement_id=sid,
                    max_fertility_age=50,
                    gender_mind="feminine",
                    genome={},
                    job=None,
                )
                gp_rec = ctx.add_person(person=gp, is_founder=False)
                mom = replace(
                    generate_person_random(
                        gender="Female",
                        age=35,
                        simulation_year=2000,
                        simulation_context=ctx,
                    ),
                    birthyear=1985,
                    birthplace_settlement_id=sid,
                    current_settlement_id=sid,
                    gender_mind="masculine",
                    genome={k: 85.0 for k in ("empathy", "patience", "nurturance")},
                    job="clerk",
                )
                mr = ctx.add_person(
                    person=mom,
                    is_founder=False,
                    father_id=gp_rec.person_id,
                    mother_id=None,
                )
                ch = replace(
                    generate_person_random(
                        gender="Female",
                        age=1,
                        simulation_year=2000,
                        simulation_context=ctx,
                    ),
                    birthyear=2015,
                    birthplace_settlement_id=sid,
                    current_settlement_id=sid,
                    min_fertility_age=18,
                )
                ctx.add_person(
                    person=ch,
                    is_founder=False,
                    mother_id=mr.person_id,
                    father_id=None,
                )
                ctx.current_year = 2025
                rm = MagicMock()
                rm.random.side_effect = [0.0, 0.99]
                with patch(
                    "library.simulation_household_care._tick_rng", return_value=rm
                ), patch(
                    "library.simulation_household_care.CARE_SHORTFALL_CRISIS_BASE",
                    9.0,
                ):
                    simulation_household_care_annual_tick(ctx, 2025)
                shortfall_events = [
                    pl
                    for _y, et, pl in ctx._pending_simulation_events
                    if et == "household_childcare_shortfall"
                ]
                self.assertEqual(shortfall_events, [])

    def test_partner_residence_reconciled_moves_lower_id(self) -> None:
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
                placename_rng_salt=15,
            ) as ctx:
                from dataclasses import replace

                st_a = ctx.ensure_active_settlement_for_region("aeria_north")
                st_b = ctx.ensure_active_settlement_for_region("aeria_granite_range")
                sid_a = st_a.settlement_id
                sid_b = st_b.settlement_id
                m = generate_person_random(
                    gender="Male",
                    age=30,
                    simulation_year=1000,
                    simulation_context=ctx,
                )
                m = replace(
                    m,
                    birthyear=1970,
                    birthplace_settlement_id=sid_a,
                    current_settlement_id=sid_a,
                )
                f = generate_person_random(
                    gender="Female",
                    age=30,
                    simulation_year=1000,
                    simulation_context=ctx,
                )
                f = replace(
                    f,
                    birthyear=1970,
                    birthplace_settlement_id=sid_b,
                    current_settlement_id=sid_b,
                )
                mr = ctx.add_person(person=m, is_founder=False)
                fr = ctx.add_person(person=f, is_founder=False)
                ctx.add_couple(mr.person_id, fr.person_id)
                lo, hi = (
                    (mr.person_id, fr.person_id)
                    if mr.person_id < fr.person_id
                    else (fr.person_id, mr.person_id)
                )
                ctx.current_year = 2000
                simulation_household_care_annual_tick(ctx, 2000)
                self.assertNotEqual(
                    (ctx.id_to_record[lo].person.current_settlement_id or ""),
                    (ctx.id_to_record[hi].person.current_settlement_id or ""),
                )
                ctx.apply_pending_settlement_moves(2001)
                self.assertEqual(
                    (ctx.id_to_record[lo].person.current_settlement_id or ""),
                    (ctx.id_to_record[hi].person.current_settlement_id or ""),
                )
                reconciled = [
                    pl
                    for _y, et, pl in ctx._pending_simulation_events
                    if et == "partner_residence_reconciled"
                ]
                self.assertTrue(reconciled)

    def test_childcare_duty_factor_grows_with_minors_and_caps(self) -> None:
        """A parent with dependent minors yields a positive, capped duty factor."""
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
                placename_rng_salt=37,
                flush_run_store=False,
            ) as ctx:
                from dataclasses import replace

                st = ctx.ensure_active_settlement_for_region("aeria_north")
                sid = st.settlement_id

                mom = generate_person_random(
                    gender="Female",
                    age=30,
                    simulation_year=2000,
                    simulation_context=ctx,
                )
                mom = replace(
                    mom,
                    birthyear=1970,
                    birthplace_settlement_id=sid,
                    current_settlement_id=sid,
                )
                mr = ctx.add_person(person=mom, is_founder=False)

                solo_adult = generate_person_random(
                    gender="Male",
                    age=30,
                    simulation_year=2000,
                    simulation_context=ctx,
                )
                solo_adult = replace(
                    solo_adult,
                    birthyear=1970,
                    birthplace_settlement_id=sid,
                    current_settlement_id=sid,
                )
                sr = ctx.add_person(person=solo_adult, is_founder=False)

                # Control: no dependent minors -> duty == 0
                self.assertEqual(
                    dependent_minors_in_implicit_household(ctx, mr, 2000), 0
                )
                self.assertEqual(childcare_duty_factor(ctx, mr, 2000), 0.0)
                self.assertEqual(childcare_duty_factor(ctx, sr, 2000), 0.0)

                # Add three young minors all parented by ``mr``.
                for i in range(3):
                    ch = generate_person_random(
                        gender="Female",
                        age=1,
                        simulation_year=2000,
                        simulation_context=ctx,
                    )
                    ch = replace(
                        ch,
                        birthyear=1998 + i,
                        birthplace_settlement_id=sid,
                        current_settlement_id=sid,
                        min_fertility_age=18,
                    )
                    ctx.add_person(
                        person=ch,
                        is_founder=False,
                        mother_id=mr.person_id,
                        father_id=None,
                    )

                duty = childcare_duty_factor(ctx, mr, 2000)
                self.assertGreater(duty, 0.0)
                self.assertLessEqual(duty, CHILD_DUTY_FACTOR_CAP)
                self.assertGreaterEqual(
                    dependent_minors_in_implicit_household(ctx, mr, 2000), 3
                )

                # The unrelated co-resident adult has no implicit children -> still 0.
                self.assertEqual(childcare_duty_factor(ctx, sr, 2000), 0.0)


if __name__ == "__main__":
    unittest.main()
