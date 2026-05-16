"""Smoke tests for ``library.simulation_government``."""

from __future__ import annotations

import random
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from library.config_import import load_all_csvs_into_sqlite
from library.generator import generate_person_random
from library.geography import _population_scale_cache
from library.polity import polity_for_region
from library.simulation_context import SimulationContext
from library.simulation_government import simulation_government_annual_tick


def _force_population_scale(cfg_path: Path, scale: float) -> None:
    """Override ``world_start.population_scale`` for a test config DB and clear caches."""
    with closing(sqlite3.connect(cfg_path)) as conn:
        conn.execute("UPDATE world_start SET population_scale = ?", (str(scale),))
        conn.commit()
    _population_scale_cache.clear()


class TestSimulationGovernment(unittest.TestCase):
    def test_bootstrap_creates_polity_for_inhabited_region(self) -> None:
        random.seed(11)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "c.sqlite"
            sav = root / "s.sqlite"
            load_all_csvs_into_sqlite(cfg)
            # Force a high scale so 1 founder still falls below the duchy threshold
            # (county min_population_to_form=5000 -> 1 alive at scale 0.0002).
            _force_population_scale(cfg, 0.0002)
            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="sgov",
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
                only = next(iter(ctx.gov_polities.values()))
                self.assertEqual(
                    only.polity_type_id,
                    "county",
                    "single founder should form a county (count), not a kingdom",
                )
                self.assertTrue(
                    any(t.target_kind == "settlement" for t in ctx.gov_territory_rows),
                    "county polity should hold settlement-grain territory",
                )

    def test_polity_promotes_county_through_duchy_to_kingdom(self) -> None:
        """Growing a region's alive count past tier thresholds promotes the polity in place."""
        random.seed(31)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "c.sqlite"
            sav = root / "s.sqlite"
            load_all_csvs_into_sqlite(cfg)
            # Scale 0.0002 -> effective thresholds: county=1, duchy=10, kingdom=200.
            _force_population_scale(cfg, 0.0002)
            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="prom",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            ) as ctx:
                # One founder -> county must be created.
                first = ctx.add_person(
                    person=generate_person_random(
                        simulation_context=ctx, simulation_year=1000
                    ),
                    is_founder=True,
                )
                first_region = ctx._residence_region_id(first) or ""
                self.assertTrue(first_region)
                simulation_government_annual_tick(ctx, 1000)
                sid = (
                    first.person.current_settlement_id
                    or first.person.birthplace_settlement_id
                    or ""
                ).strip()
                self.assertTrue(sid)
                county_polity = next(
                    p
                    for p in ctx.gov_polities.values()
                    if p.polity_type_id == "county"
                    and any(
                        t.target_kind == "settlement"
                        and t.target_id == sid
                        and t.polity_id == p.polity_id
                        for t in ctx.gov_territory_rows
                    )
                )
                self.assertEqual(county_polity.polity_type_id, "county")

                # Force same-region growth past the duchy threshold by adding founders
                # pinned to the same birthplace; scale 0.0002 -> duchy at 10 alive.
                base = first.person
                year = 1000
                while ctx.count_alive_in_region(first_region) < 10:
                    year += 1
                    ctx.add_person(
                        person=generate_person_random(
                            simulation_context=ctx,
                            simulation_year=year,
                            birthplace_settlement_id=base.current_settlement_id
                            or base.birthplace_settlement_id,
                            birthplace_region_id=first_region,
                        ),
                        is_founder=True,
                    )
                year += 1
                simulation_government_annual_tick(ctx, year)
                duchy = polity_for_region(ctx, first_region)
                self.assertIsNotNone(duchy)
                self.assertEqual(duchy.polity_type_id, "duchy")
                self.assertEqual(
                    ctx.gov_polities[duchy.polity_id].polity_type_id, "duchy"
                )

                # Continue growing past the kingdom threshold (200 alive at scale 0.0002).
                while ctx.count_alive_in_region(first_region) < 200:
                    year += 1
                    ctx.add_person(
                        person=generate_person_random(
                            simulation_context=ctx,
                            simulation_year=year,
                            birthplace_settlement_id=base.current_settlement_id
                            or base.birthplace_settlement_id,
                            birthplace_region_id=first_region,
                        ),
                        is_founder=True,
                    )
                year += 1
                simulation_government_annual_tick(ctx, year)
                realm = polity_for_region(ctx, first_region)
                self.assertIsNotNone(realm)
                self.assertEqual(realm.polity_type_id, "kingdom")
                self.assertEqual(realm.polity_id, duchy.polity_id)
                # Kingdom must have at least one ``king`` seat (head title).
                king_seats = [
                    s
                    for s in ctx.gov_office_seats.values()
                    if s.polity_id == realm.polity_id and s.title_id == "king"
                ]
                self.assertEqual(len(king_seats), 1)

    def test_region_can_host_multiple_counties(self) -> None:
        """Two settlements in one region above the county threshold get two counties."""
        from dataclasses import replace

        from library.polity import polity_for_settlement
        from library.settlements import make_settlement_id

        random.seed(41)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "c.sqlite"
            sav = root / "s.sqlite"
            load_all_csvs_into_sqlite(cfg)
            _force_population_scale(cfg, 0.0002)
            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="mc2",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            ) as ctx:
                p1 = ctx.add_person(
                    person=generate_person_random(
                        simulation_context=ctx, simulation_year=1000
                    ),
                    is_founder=True,
                )
                simulation_government_annual_tick(ctx, 1000)
                rid = (ctx._residence_region_id(p1) or "").strip()
                sid1 = (
                    p1.person.current_settlement_id
                    or p1.person.birthplace_settlement_id
                    or ""
                ).strip()
                self.assertTrue(rid and sid1)
                st1 = ctx.settlements_by_id[sid1]
                sid2 = make_settlement_id(rid, 2)
                ctx.settlements_by_id[sid2] = replace(
                    st1,
                    settlement_id=sid2,
                    display_name="Second Hamlet",
                    resident_count=0,
                    consecutive_empty_years=0,
                )
                lst = list(ctx.settlement_ids_by_region.get(rid, []))
                if sid2 not in lst:
                    lst.append(sid2)
                ctx.settlement_ids_by_region[rid] = sorted(lst)

                ctx.add_person(
                    person=generate_person_random(
                        simulation_context=ctx,
                        simulation_year=1001,
                        birthplace_settlement_id=sid2,
                        birthplace_region_id=rid,
                    ),
                    is_founder=True,
                )
                simulation_government_annual_tick(ctx, 1001)
                c1 = polity_for_settlement(ctx, sid1)
                c2 = polity_for_settlement(ctx, sid2)
                self.assertIsNotNone(c1)
                self.assertIsNotNone(c2)
                self.assertNotEqual(c1.polity_id, c2.polity_id)
                self.assertEqual(c1.polity_type_id, "county")
                self.assertEqual(c2.polity_type_id, "county")

    def test_county_promotion_absorbs_siblings_as_vassals(self) -> None:
        from dataclasses import replace

        from library.polity import polity_for_region, polity_for_settlement
        from library.settlements import make_settlement_id

        random.seed(42)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "c.sqlite"
            sav = root / "s.sqlite"
            load_all_csvs_into_sqlite(cfg)
            _force_population_scale(cfg, 0.0002)
            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="vass",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            ) as ctx:
                p1 = ctx.add_person(
                    person=generate_person_random(
                        simulation_context=ctx, simulation_year=1000
                    ),
                    is_founder=True,
                )
                simulation_government_annual_tick(ctx, 1000)
                rid = (ctx._residence_region_id(p1) or "").strip()
                sid1 = (
                    p1.person.current_settlement_id
                    or p1.person.birthplace_settlement_id
                    or ""
                ).strip()
                st1 = ctx.settlements_by_id[sid1]
                sid2 = make_settlement_id(rid, 2)
                ctx.settlements_by_id[sid2] = replace(
                    st1,
                    settlement_id=sid2,
                    display_name="Sibling Hamlet",
                    resident_count=0,
                    consecutive_empty_years=0,
                )
                lst = list(ctx.settlement_ids_by_region.get(rid, []))
                if sid2 not in lst:
                    lst.append(sid2)
                ctx.settlement_ids_by_region[rid] = sorted(lst)
                ctx.add_person(
                    person=generate_person_random(
                        simulation_context=ctx,
                        simulation_year=1001,
                        birthplace_settlement_id=sid2,
                        birthplace_region_id=rid,
                    ),
                    is_founder=True,
                )
                simulation_government_annual_tick(ctx, 1001)
                year = 1002
                while ctx.count_alive_in_region(rid) < 10:
                    year += 1
                    ctx.add_person(
                        person=generate_person_random(
                            simulation_context=ctx,
                            simulation_year=year,
                            birthplace_settlement_id=sid1,
                            birthplace_region_id=rid,
                        ),
                        is_founder=True,
                    )
                simulation_government_annual_tick(ctx, year)
                duchy = polity_for_region(ctx, rid)
                self.assertIsNotNone(duchy)
                self.assertEqual(duchy.polity_type_id, "duchy")
                sib = polity_for_settlement(ctx, sid2)
                self.assertIsNotNone(sib)
                self.assertEqual(sib.parent_polity_id, duchy.polity_id)

    def test_merit_seats_do_not_stack_on_one_person(self) -> None:
        """Each office seat should have a distinct holder when enough candidates exist."""
        random.seed(101)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "c.sqlite"
            sav = root / "s.sqlite"
            load_all_csvs_into_sqlite(cfg)
            # Same scale as other government tests: many population-scaled merit seats
            # per alive resident in a large settlement (see ``settlement_alderman``).
            _force_population_scale(cfg, 0.0002)
            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="nodup",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            ) as ctx:
                first = ctx.add_person(
                    person=generate_person_random(
                        simulation_context=ctx,
                        simulation_year=1000,
                        age=40,
                    ),
                    is_founder=True,
                )
                simulation_government_annual_tick(ctx, 1000)
                rid = (ctx._residence_region_id(first) or "").strip()
                sid = (
                    first.person.current_settlement_id
                    or first.person.birthplace_settlement_id
                    or ""
                ).strip()
                self.assertTrue(rid and sid)
                year = 1000
                while ctx.count_alive_in_settlement(sid) < 40:
                    year += 1
                    ctx.add_person(
                        person=generate_person_random(
                            simulation_context=ctx,
                            simulation_year=year,
                            birthplace_settlement_id=sid,
                            birthplace_region_id=rid,
                            age=40,
                        ),
                        is_founder=True,
                    )
                simulation_government_annual_tick(ctx, year)
                holders = [
                    int(s.holder_person_id)
                    for s in ctx.gov_office_seats.values()
                    if s.holder_person_id is not None
                ]
                self.assertGreater(
                    len(holders),
                    5,
                    msg="expected several filled merit seats to exercise de-duplication",
                )
                self.assertEqual(
                    len(holders),
                    len(set(holders)),
                    msg="same person must not hold multiple seats at once",
                )


if __name__ == "__main__":
    unittest.main()
