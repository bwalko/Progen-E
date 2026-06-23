"""Smoke tests for ``library.simulation_government``."""

from __future__ import annotations

import random
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from library.config_import import load_all_csvs_into_sqlite
from library.generator import generate_person_random
from library.geography import _population_scale_cache
from library.government_catalog import TitleRow
from library.government_checkpoint import (
    append_office_holding,
    close_office_holding,
    ensure_government_schema,
)
from library.polity import polity_for_region
from library.simulation_context import SimulationContext
from library.simulation_government import (
    _government_office_composite_multiplier,
    _government_scored_candidate_pool,
    simulation_government_annual_tick,
)


def _force_population_scale(cfg_path: Path, scale: float) -> None:
    """Override ``world_start.population_scale`` for a test config DB and clear caches."""
    with closing(sqlite3.connect(cfg_path)) as conn:
        conn.execute("UPDATE world_start SET population_scale = ?", (str(scale),))
        conn.commit()
    _population_scale_cache.clear()


class TestSimulationGovernment(unittest.TestCase):
    def test_government_scored_pool_skips_cheap_ineligible_candidates_before_composites(self) -> None:
        ctx = SimpleNamespace(
            _gov_candidate_fact_cache={},
            _gov_scored_candidate_cache={},
            _gov_care_indexes=SimpleNamespace(childcare_duty_factor_by_adult={}),
        )
        title = TitleRow(
            title_id="councilor",
            title_names_by_era={},
            polity_type_id="city_state",
            role="council",
            selection_rule="election",
            max_holders=1,
            term_years=None,
            min_age=16,
            min_leadership_index=0.0,
            min_military_quality_index=0.0,
            min_career_fitness=0.5,
            male_weight=0.5,
            can_be_usurped=False,
            usurp_base_chance=0.0,
            eligibility_kinship="none",
        )
        records = [
            SimpleNamespace(
                person_id=1,
                person=SimpleNamespace(
                    marker="underage",
                    birthyear=990,
                    deathyear=None,
                    gender="Male",
                    career_fitness_score=1.0,
                ),
            ),
            SimpleNamespace(
                person_id=2,
                person=SimpleNamespace(
                    marker="low_cfs",
                    birthyear=970,
                    deathyear=None,
                    gender="Female",
                    career_fitness_score=0.2,
                ),
            ),
            SimpleNamespace(
                person_id=3,
                person=SimpleNamespace(
                    marker="eligible",
                    birthyear=970,
                    deathyear=None,
                    gender="Male",
                    career_fitness_score=0.9,
                ),
            ),
        ]
        scored_people: list[str] = []

        def fake_indexes(person, *, composite_rows):
            scored_people.append(person.marker)
            return (0.8, 0.2)

        with patch(
            "library.simulation_government.leadership_and_military_indexes",
            side_effect=fake_indexes,
        ):
            scored = _government_scored_candidate_pool(
                ctx,
                records,
                title=title,
                composite_rows=(),
                year=1000,
            )

        self.assertEqual(scored_people, ["eligible"])
        self.assertEqual([pid for _score, pid in scored], [3])

    def test_force_authority_titles_use_body_power_without_overriding_low_force_offices(self) -> None:
        ctx = SimpleNamespace(
            _gov_candidate_fact_cache={},
            _gov_scored_candidate_cache={},
            _gov_care_indexes=SimpleNamespace(childcare_duty_factor_by_adult={}),
        )
        base_title = TitleRow(
            title_id="office",
            title_names_by_era={},
            polity_type_id="city_state",
            role="court",
            selection_rule="election",
            max_holders=1,
            term_years=None,
            min_age=16,
            min_leadership_index=0.0,
            min_military_quality_index=0.0,
            min_career_fitness=0.5,
            male_weight=0.5,
            can_be_usurped=False,
            usurp_base_chance=0.0,
            eligibility_kinship="none",
            force_authority_01=0.0,
        )
        high_force_title = replace(
            base_title,
            title_id="marshal",
            force_authority_01=0.85,
        )
        male_weighted_title = replace(base_title, title_id="elder", male_weight=0.80)
        records = [
            SimpleNamespace(
                person_id=10,
                person=SimpleNamespace(
                    birthyear=970,
                    deathyear=None,
                    gender="Male",
                    career_fitness_score=0.9,
                    genome={"physical": 0.0},
                    mind_body={"physical": 0.0},
                ),
            ),
            SimpleNamespace(
                person_id=11,
                person=SimpleNamespace(
                    birthyear=970,
                    deathyear=None,
                    gender="Female",
                    career_fitness_score=0.9,
                    genome={"physical": -85.0},
                    mind_body={"physical": -85.0},
                ),
            ),
        ]

        with patch(
            "library.simulation_government.leadership_and_military_indexes",
            return_value=(0.8, 0.5),
        ):
            low_force = dict(
                (pid, score)
                for score, pid in _government_scored_candidate_pool(
                    ctx, records, title=base_title, composite_rows=(), year=1000
                )
            )
            high_force = dict(
                (pid, score)
                for score, pid in _government_scored_candidate_pool(
                    ctx, records, title=high_force_title, composite_rows=(), year=1000
                )
            )
            male_weighted = dict(
                (pid, score)
                for score, pid in _government_scored_candidate_pool(
                    ctx, records, title=male_weighted_title, composite_rows=(), year=1000
                )
            )

        self.assertAlmostEqual(low_force[10], low_force[11])
        self.assertGreater(high_force[10], high_force[11])
        self.assertGreater(male_weighted[10], male_weighted[11])

    def test_office_composite_multiplier_penalizes_insane_candidates_without_exclusion(self) -> None:
        ctx = SimpleNamespace(
            _gov_candidate_fact_cache={},
            _gov_scored_candidate_cache={},
            _gov_care_indexes=SimpleNamespace(childcare_duty_factor_by_adult={}),
        )
        title = TitleRow(
            title_id="mayor",
            title_names_by_era={},
            polity_type_id="republic",
            role="local_merit",
            selection_rule="election_by_council",
            max_holders=1,
            term_years=4,
            min_age=28,
            min_leadership_index=0.0,
            min_military_quality_index=0.0,
            min_career_fitness=0.0,
            male_weight=0.5,
            can_be_usurped=False,
            usurp_base_chance=0.0,
            eligibility_kinship="realm_resident",
            min_population_for_first_holder=0,
            pop_per_holder=0,
            merit_takeover_chance=0.0,
            force_authority_01=0.10,
        )
        records = [
            SimpleNamespace(
                person_id=21,
                person=SimpleNamespace(
                    marker="steady",
                    deathyear=None,
                    birthyear=960,
                    gender="Male",
                    career_fitness_score=0.80,
                    genome={"physical": 0.0},
                    mind_body={"physical": 0.0},
                    genome_composite_scores={
                        "lead_others_ability": 0.55,
                        "practical_intellect": 0.45,
                        "good_done_desire": 0.50,
                    },
                ),
            ),
            SimpleNamespace(
                person_id=22,
                person=SimpleNamespace(
                    marker="unstable",
                    deathyear=None,
                    birthyear=960,
                    gender="Male",
                    career_fitness_score=0.80,
                    genome={"physical": 0.0},
                    mind_body={"physical": 0.0},
                    genome_composite_scores={
                        "insanity": 1.0,
                        "psychopathy": 0.70,
                        "ruthless_ambition": 0.70,
                    },
                ),
            ),
        ]

        def fake_indexes(person, *, composite_rows):
            return (0.75, 0.20) if person.marker == "steady" else (0.90, 0.20)

        with patch(
            "library.simulation_government.leadership_and_military_indexes",
            side_effect=fake_indexes,
        ):
            scored = dict(
                (pid, score)
                for score, pid in _government_scored_candidate_pool(
                    ctx, records, title=title, composite_rows=(), year=1000
                )
            )

        unstable_mult = _government_office_composite_multiplier(records[1], title)
        self.assertGreater(unstable_mult, 0.0)
        self.assertLess(unstable_mult, 0.35)
        self.assertGreater(scored[21], scored[22])

    def test_office_history_readable_view_tracks_successions_and_death_endings(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute(
                """
                CREATE TABLE simulation_people (
                    person_id INTEGER PRIMARY KEY,
                    first_name TEXT,
                    last_name TEXT
                )
                """
            )
            ensure_government_schema(conn)
            conn.executemany(
                "INSERT INTO simulation_people (person_id, first_name, last_name) VALUES (?, ?, ?)",
                [(1, "Ada", "Forge"), (2, "Bea", "Forge")],
            )
            conn.execute(
                """
                INSERT INTO simulation_polities (
                    polity_id, polity_type_id, name, founded_sim_year
                ) VALUES (1, 'kingdom', 'Northrealm', 1000)
                """
            )
            conn.execute(
                """
                INSERT INTO simulation_office_seats (
                    seat_id, polity_id, title_id, slot_index, status
                ) VALUES (10, 1, 'king', 0, 'active')
                """
            )
            append_office_holding(
                conn,
                world="test",
                seat_id=10,
                holder_person_id=1,
                start_sim_year=1000,
                ensure_schema=False,
            )
            close_office_holding(
                conn,
                world="test",
                seat_id=10,
                holder_person_id=1,
                end_sim_year=1004,
                end_reason="death",
                ensure_schema=False,
            )
            append_office_holding(
                conn,
                world="test",
                seat_id=10,
                holder_person_id=2,
                start_sim_year=1005,
                ensure_schema=False,
            )

            rows = conn.execute(
                """
                SELECT *
                FROM simulation_office_history_readable
                ORDER BY start_sim_year
                """
            ).fetchall()

        self.assertEqual(len(rows), 2)
        self.assertEqual(str(rows[0]["polity_name"]), "Northrealm")
        self.assertEqual(str(rows[0]["office_id"]), "king")
        self.assertEqual(str(rows[0]["holder_name"]), "Ada Forge")
        self.assertEqual(int(rows[0]["start_sim_year"]), 1000)
        self.assertEqual(int(rows[0]["end_sim_year"]), 1004)
        self.assertEqual(str(rows[0]["end_reason"]), "death")
        self.assertEqual(str(rows[1]["holder_name"]), "Bea Forge")
        self.assertIsNone(rows[1]["end_sim_year"])
        self.assertEqual(str(rows[1]["holding_status"]), "current")

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
