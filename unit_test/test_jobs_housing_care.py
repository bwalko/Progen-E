"""Robust job archetype, housing, and care-labor tests."""

from __future__ import annotations

import re
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from library.config_import import load_all_csvs_into_sqlite
from library.job_archetypes import JobArchetypeCatalog
from library.person import Person
from library.simulation_careers import (
    CareerFitness,
    _resolve_adult_housing_pressure,
)
from library.simulation_context import SimulationContext, SimulationPersonRecord
from library.simulation_household_care import effective_caregiver_supply
from library.world_save import checkpoint_simulation_to_save, try_load_simulation_checkpoint

ROOT = Path(__file__).resolve().parents[1]


def _person(
    *,
    first_name: str = "Test",
    birthyear: int = 980,
    gender: str = "Female",
    genome: dict[str, float] | None = None,
    settlement_id: str | None = "s1",
    household_prosperity: float | None = 1.0,
) -> Person:
    return Person(
        first_name=first_name,
        last_name="Person",
        gender=gender,
        ethnic="Human",
        species="Human",
        birthyear=birthyear,
        birthplace_settlement_id=settlement_id,
        current_settlement_id=settlement_id,
        min_fertility_age=18,
        max_fertility_age=45,
        gender_mind="feminine" if gender.lower() == "female" else "masculine",
        genome=genome or {},
        mind_body=genome or {},
        household_prosperity=household_prosperity,
    )


class _CareCtx:
    def _person_is_dependent_minor(self, rec: SimulationPersonRecord, year: int) -> bool:
        return int(year) - int(rec.person.birthyear) < 18


class TestJobsHousingCare(unittest.TestCase):
    def test_job_archetypes_parse_and_mark_adult_only_roles(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            load_all_csvs_into_sqlite(cfg)

            catalog = JobArchetypeCatalog.load(cfg)
            child_rearer = catalog.lookup("child rearer")
            self.assertEqual(child_rearer.job_market_type, "household_care")
            self.assertGreater(child_rearer.societal_impact_01, 0.85)
            self.assertLess(child_rearer.personal_prosperity_01, 0.10)

            nanny = catalog.lookup("nanny")
            self.assertEqual(nanny.job_market_type, "domestic_service")
            self.assertTrue(nanny.adult_only)
            self.assertGreater(nanny.board_compensation_01, 0.5)

            vice = catalog.lookup("prostitute")
            self.assertEqual(vice.job_market_type, "vice")
            self.assertTrue(vice.adult_only)

    def test_assignable_job_titles_do_not_carry_banned_qualifiers(self) -> None:
        banned = re.compile(
            r"\b(sloppy|submissive|famine-risk|overlooked|anonymous|blunt)\s+"
            r"(cook|aide|hunter|scout|messenger|scribe|philosopher|elder|consultant|tool helper|brewer|artisan|clerk)\b",
            re.IGNORECASE,
        )
        for rel in (
            "config/genome_jobs.csv",
            "config/job_economics.csv",
            "config/job_market.csv",
        ):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIsNone(banned.search(text), rel)

    def test_household_care_supply_is_not_reduced_like_outside_employment(self) -> None:
        ctx = _CareCtx()
        base_traits = {
            "empathy": 0.0,
            "patience": 0.0,
            "nurturance": 0.0,
            "temperance": 0.0,
            "neurochemical": 0.0,
            "physical": 0.0,
        }
        child_rearer = SimulationPersonRecord(
            1,
            replace(
                _person(genome=base_traits),
                job="child rearer",
                job_market_type="household_care",
            ),
            False,
        )
        outside_worker = SimulationPersonRecord(
            2,
            replace(
                _person(first_name="Outside", genome=base_traits),
                job="scribe",
                job_market_type="settlement_market",
            ),
            False,
        )

        self.assertGreater(
            effective_caregiver_supply(ctx, child_rearer, 1000),
            effective_caregiver_supply(ctx, outside_worker, 1000) * 2.5,
        )

    def test_save_round_trips_work_housing_fields_and_service_contract(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)

            ctx = SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="jobs",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            )
            st = ctx.ensure_active_settlement_for_region("aeria_north")
            employer = ctx.add_person(
                person=_person(
                    first_name="Employer",
                    birthyear=960,
                    gender="Male",
                    settlement_id=st.settlement_id,
                    household_prosperity=4.0,
                ),
                is_founder=True,
            )
            worker = ctx.add_person(
                person=replace(
                    _person(
                        first_name="Worker",
                        birthyear=980,
                        settlement_id=st.settlement_id,
                    ),
                    job="nanny",
                    job_assigned_year=1000,
                    employment_status="employed",
                    job_market_type="domestic_service",
                    housing_status="employer_household",
                    household_role="nanny",
                    host_person_id=employer.person_id,
                    employer_person_id=employer.person_id,
                    social_class_band="servant",
                    social_standing_01=0.36,
                    societal_impact_01=0.82,
                    perceived_worth_01=0.42,
                    job_prosperity_01=0.16,
                ),
                is_founder=False,
            )
            checkpoint_simulation_to_save(ctx)

            with sqlite3.connect(sav) as conn:
                row = conn.execute(
                    """
                    SELECT service_kind, board_included, cash_wage_01, status
                    FROM simulation_household_service_contracts
                    WHERE worker_person_id = ?
                    """,
                    (worker.person_id,),
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "nanny")
            self.assertEqual(int(row[1]), 1)
            self.assertEqual(row[3], "active")

            shell = SimulationContext(
                db_path=cfg,
                save_db_path=sav,
                world="default",
                simulation_start_year=1000,
                history_equivalent_start_year=1000,
                current_year=1000,
            )
            self.assertTrue(try_load_simulation_checkpoint(shell))
            loaded = shell.id_to_record[worker.person_id].person
            self.assertEqual(loaded.job_market_type, "domestic_service")
            self.assertEqual(loaded.housing_status, "employer_household")
            self.assertEqual(loaded.employer_person_id, employer.person_id)
            self.assertAlmostEqual(float(loaded.societal_impact_01 or 0.0), 0.82)

    def test_adult_housing_pressure_retains_cared_for_or_manipulative_adults(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            sav = Path(td) / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="housing",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            )
            st = ctx.ensure_active_settlement_for_region("aeria_north")
            caring_parent = ctx.add_person(
                person=_person(
                    first_name="Caring",
                    birthyear=950,
                    gender="Female",
                    settlement_id=st.settlement_id,
                    household_prosperity=3.0,
                    genome={
                        "nurturance": 0.0,
                        "empathy": 0.0,
                        "generosity": 0.0,
                        "loyalty": 0.0,
                        "frugality": 0.0,
                    },
                ),
                is_founder=True,
            )
            adult_child = ctx.add_person(
                person=_person(
                    first_name="Adult",
                    birthyear=975,
                    settlement_id=st.settlement_id,
                    household_prosperity=3.0,
                    genome={"persuasion": 0.0, "honesty": 0.0, "empathy": 0.0},
                ),
                is_founder=False,
                mother_id=caring_parent.person_id,
            )
            _resolve_adult_housing_pressure(
                ctx,
                adult_child,
                1000,
                pressure=0.10,
                fitness=CareerFitness(0.5, (), (), 0.0, 0.0),
                trait_values=adult_child.person.mind_body,
                care_indexes=None,
                archetypes=JobArchetypeCatalog.load(cfg),
            )
            self.assertEqual(adult_child.person.housing_status, "family_home")

            manip_parent = ctx.add_person(
                person=_person(
                    first_name="ManipParent",
                    birthyear=950,
                    gender="Male",
                    settlement_id=st.settlement_id,
                    household_prosperity=2.0,
                    genome={
                        "nurturance": 50.0,
                        "empathy": 50.0,
                        "generosity": 50.0,
                        "loyalty": 50.0,
                        "frugality": 0.0,
                    },
                ),
                is_founder=True,
            )
            manip_child = ctx.add_person(
                person=_person(
                    first_name="ManipChild",
                    birthyear=975,
                    settlement_id=st.settlement_id,
                    household_prosperity=2.0,
                    genome={"persuasion": 100.0, "honesty": 100.0, "empathy": 100.0},
                ),
                is_founder=False,
                father_id=manip_parent.person_id,
            )
            _resolve_adult_housing_pressure(
                ctx,
                manip_child,
                1000,
                pressure=0.10,
                fitness=CareerFitness(0.5, (), (), 0.0, 0.0),
                trait_values=manip_child.person.mind_body,
                care_indexes=None,
                archetypes=JobArchetypeCatalog.load(cfg),
            )
            self.assertEqual(manip_child.person.housing_status, "family_home")

    def test_adult_in_poor_home_can_be_pushed_to_street(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            sav = Path(td) / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="street",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            )
            st = ctx.ensure_active_settlement_for_region("aeria_north")
            parent = ctx.add_person(
                person=_person(
                    first_name="PoorParent",
                    birthyear=950,
                    gender="Male",
                    settlement_id=st.settlement_id,
                    household_prosperity=0.0,
                    genome={
                        "nurturance": 100.0,
                        "empathy": 100.0,
                        "generosity": 100.0,
                        "loyalty": 100.0,
                        "frugality": 100.0,
                    },
                ),
                is_founder=True,
            )
            adult_child = ctx.add_person(
                person=_person(
                    first_name="Unwanted",
                    birthyear=975,
                    settlement_id=st.settlement_id,
                    household_prosperity=0.0,
                    genome={"persuasion": 0.0, "honesty": 0.0, "empathy": 0.0},
                ),
                is_founder=False,
                father_id=parent.person_id,
            )
            _resolve_adult_housing_pressure(
                ctx,
                adult_child,
                1000,
                pressure=0.80,
                fitness=CareerFitness(0.5, (), (), 0.0, 0.0),
                trait_values=adult_child.person.mind_body,
                care_indexes=None,
                archetypes=JobArchetypeCatalog.load(cfg),
            )
            self.assertEqual(adult_child.person.housing_status, "street")


if __name__ == "__main__":
    unittest.main()
