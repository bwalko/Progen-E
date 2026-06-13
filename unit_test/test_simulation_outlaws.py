"""Tests for outlaw cases, refuge flight, and checkpoint persistence."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from library.config_import import load_all_csvs_into_sqlite
from library.generator import generate_person_random
from library.simulation_context import SimulationContext
from library.simulation_careers import simulation_careers_annual_tick
from library.simulation_outlaws import (
    OUTLAW_STATUS_CLEARED,
    OUTLAW_STATUS_FUGITIVE,
    OUTLAW_STATUS_PUNISHED,
    OUTLAW_STATUS_RETURNED,
    flee_to_refuge,
    kill_outlaw,
    normalize_outlaw_labor_state,
    open_outlaw_case,
    resolve_outlaw_case,
    _maybe_buy_off,
)
from library.world_save import checkpoint_simulation_to_save, try_load_simulation_checkpoint


_GENOME_TRAITS = (
    "physical",
    "intellect",
    "symmetry",
    "mating drive",
    "neurochemical",
    "courage",
    "temperance",
    "patience",
    "wit",
    "friendliness",
    "modesty",
    "ambition",
    "frugality",
    "persuasion",
    "curiosity",
    "justice",
    "humility",
    "generosity",
    "empathy",
    "discipline",
    "adaptability",
    "resilience",
    "focus",
    "honesty",
    "creativity",
    "assertiveness",
    "loyalty",
    "nurturance",
    "perception",
    "civics",
)


def _genome(**overrides: float) -> dict[str, float]:
    out = {trait: 0.0 for trait in _GENOME_TRAITS}
    out.update({str(k): float(v) for k, v in overrides.items()})
    return out


class _ZeroRandom:
    def random(self) -> float:
        return 0.0


class TestSimulationOutlaws(unittest.TestCase):
    def _context(self, root: Path) -> SimulationContext:
        cfg = root / "config.sqlite"
        sav = root / "save.sqlite"
        load_all_csvs_into_sqlite(cfg)
        return SimulationContext.create(
            db_path=cfg,
            save_db_path=sav,
            world_id="outlaws",
            world="default",
            start_year=1000,
            refresh_config=False,
            flush_run_store=False,
            checkpoint_full_snapshot_every_n_years=None,
        )

    def _add_adult(
        self,
        ctx: SimulationContext,
        *,
        settlement_id: str,
        region_id: str,
        gender: str = "Male",
        genome: dict[str, float] | None = None,
    ):
        person = generate_person_random(
            simulation_context=ctx,
            simulation_year=1000,
            age=30,
            gender=gender,
            genome=genome or _genome(),
            birthplace_region_id=region_id,
            birthplace_settlement_id=settlement_id,
        )
        return ctx.add_person(person=person, is_founder=True)

    def test_case_opening_and_flight_cut_off_contacts(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            accused = self._add_adult(
                ctx,
                settlement_id=settlement.settlement_id,
                region_id=settlement.region_id,
                gender="Male",
            )
            partner = self._add_adult(
                ctx,
                settlement_id=settlement.settlement_id,
                region_id=settlement.region_id,
                gender="Female",
            )
            paramour = self._add_adult(
                ctx,
                settlement_id=settlement.settlement_id,
                region_id=settlement.region_id,
                gender="Female",
            )
            ctx.add_couple(accused.person_id, partner.person_id)
            accused.person = replace(accused.person, paramour_person_id=paramour.person_id)
            paramour.person = replace(paramour.person, paramour_person_id=accused.person_id)
            ctx.paramours.append((accused.person_id, paramour.person_id))

            case = open_outlaw_case(
                ctx,
                year=1001,
                accused=accused,
                offense_type="murder",
                offense_kind="ambush_killing",
                severity_01=0.95,
                knownness_01=0.75,
                source_event_key="test:murder:flight",
                victim_person_id=partner.person_id,
            )
            self.assertIsNotNone(case)
            self.assertEqual(accused.person.outlaw_status, "wanted")

            refuge = flee_to_refuge(ctx, case.case_key, year=1001)

            self.assertIsNotNone(refuge)
            self.assertTrue(refuge.display_name)
            self.assertNotIn("outlaw_refuge", refuge.display_name)
            self.assertNotIn(refuge.refuge_id, ctx.settlements_by_id)
            self.assertEqual(accused.person.outlaw_status, OUTLAW_STATUS_FUGITIVE)
            self.assertIsNone(accused.person.current_settlement_id)
            self.assertEqual(accused.person.outlaw_refuge_id, refuge.refuge_id)
            self.assertEqual(accused.person.housing_status, "outlaw_refuge")
            self.assertIsNone(accused.person.partner_person_id)
            self.assertIsNone(partner.person.partner_person_id)
            self.assertIsNone(accused.person.paramour_person_id)
            self.assertIsNone(paramour.person.paramour_person_id)
            self.assertNotIn(
                accused.person_id,
                {
                    rec.person_id
                    for rec in ctx.current_people_by_settlement().get(
                        settlement.settlement_id, []
                    )
                },
            )

    def test_outlaws_do_not_keep_or_receive_normal_jobs(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            accused = self._add_adult(
                ctx,
                settlement_id=settlement.settlement_id,
                region_id=settlement.region_id,
                gender="Male",
            )
            accused.person = replace(
                accused.person,
                job="mill worker",
                job_assigned_year=1000,
                job_market_type="settlement_market",
                employment_status="employed",
                housing_status="own_household",
            )
            case = open_outlaw_case(
                ctx,
                year=1001,
                accused=accused,
                offense_type="property_crime",
                offense_kind="storehouse_robbery",
                severity_01=0.62,
                knownness_01=0.70,
                source_event_key="test:property:job-block",
                target_person_id=accused.person_id,
            )
            self.assertIsNotNone(case)
            refuge = flee_to_refuge(ctx, case.case_key, year=1001)
            self.assertIsNotNone(refuge)
            self.assertIsNone(accused.person.job)
            self.assertEqual(accused.person.employment_status, "outlaw")

            accused.person = replace(
                accused.person,
                current_settlement_id=settlement.settlement_id,
                job="mill worker",
                job_assigned_year=1002,
                job_market_type="settlement_market",
                employment_status="employed",
                housing_status="own_household",
                household_role="worker",
            )
            self.assertTrue(normalize_outlaw_labor_state(ctx, accused, 1002))
            simulation_careers_annual_tick(ctx, 1002)
            self.assertIsNone(accused.person.job)
            self.assertIsNone(accused.person.current_settlement_id)
            self.assertEqual(accused.person.employment_status, "outlaw")
            self.assertEqual(accused.person.housing_status, "outlaw_refuge")
            self.assertEqual(accused.person.job_market_type, "criminal")

            resolve_outlaw_case(ctx, case.case_key, year=1003, resolution="captured")
            accused.person = replace(
                accused.person,
                job="mill worker",
                job_assigned_year=1004,
                job_market_type="settlement_market",
                employment_status="employed",
                housing_status="own_household",
                household_role="worker",
            )
            simulation_careers_annual_tick(ctx, 1004)
            self.assertEqual(accused.person.outlaw_status, OUTLAW_STATUS_PUNISHED)
            self.assertIsNone(accused.person.job)
            self.assertEqual(accused.person.employment_status, "unemployed")
            self.assertEqual(accused.person.job_market_type, "none")

    def test_buyoff_has_hard_limit_for_severe_public_murder(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            wealthy = self._add_adult(
                ctx,
                settlement_id=settlement.settlement_id,
                region_id=settlement.region_id,
            )
            wealthy.person = replace(
                wealthy.person,
                household_prosperity=25.0,
                social_standing_01=1.0,
            )
            property_case = open_outlaw_case(
                ctx,
                year=1001,
                accused=wealthy,
                offense_type="property_crime",
                offense_kind="storehouse_robbery",
                severity_01=0.42,
                knownness_01=0.40,
                source_event_key="test:property:buyoff",
                target_person_id=wealthy.person_id,
            )
            self.assertIsNotNone(property_case)
            self.assertTrue(_maybe_buy_off(ctx, property_case, 1001, _ZeroRandom()))
            self.assertEqual(ctx.outlaw_cases[property_case.case_key].resolution, "bought_off")
            self.assertEqual(wealthy.person.outlaw_status, OUTLAW_STATUS_CLEARED)

            murderer = self._add_adult(
                ctx,
                settlement_id=settlement.settlement_id,
                region_id=settlement.region_id,
            )
            murderer.person = replace(
                murderer.person,
                household_prosperity=25.0,
                social_standing_01=1.0,
            )
            murder_case = open_outlaw_case(
                ctx,
                year=1001,
                accused=murderer,
                offense_type="murder",
                offense_kind="predatory_murder",
                severity_01=0.96,
                knownness_01=0.80,
                source_event_key="test:murder:no-buyoff",
                victim_person_id=wealthy.person_id,
            )
            self.assertIsNotNone(murder_case)
            self.assertFalse(_maybe_buy_off(ctx, murder_case, 1001, _ZeroRandom()))
            self.assertEqual(ctx.outlaw_cases[murder_case.case_key].status, "active")

    def test_capture_death_and_forgotten_return_resolve_cases(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            killed = self._add_adult(
                ctx,
                settlement_id=settlement.settlement_id,
                region_id=settlement.region_id,
            )
            killed_case = open_outlaw_case(
                ctx,
                year=1001,
                accused=killed,
                offense_type="murder",
                offense_kind="ambush_killing",
                severity_01=0.95,
                knownness_01=0.70,
                source_event_key="test:murder:killed",
                victim_person_id=killed.person_id,
            )
            self.assertIsNotNone(killed_case)
            flee_to_refuge(ctx, killed_case.case_key, year=1001)
            kill_outlaw(ctx, killed_case.case_key, year=1002)
            self.assertNotIn(killed.person_id, ctx.current_people_ids)
            self.assertEqual(ctx.outlaw_cases[killed_case.case_key].resolution, "killed")

            returned = self._add_adult(
                ctx,
                settlement_id=settlement.settlement_id,
                region_id=settlement.region_id,
            )
            returned_case = open_outlaw_case(
                ctx,
                year=1001,
                accused=returned,
                offense_type="property_crime",
                offense_kind="livestock_theft",
                severity_01=0.50,
                knownness_01=0.20,
                source_event_key="test:property:return",
                target_person_id=killed.person_id,
            )
            self.assertIsNotNone(returned_case)
            flee_to_refuge(ctx, returned_case.case_key, year=1001)
            resolve_outlaw_case(ctx, returned_case.case_key, year=1015, resolution="forgotten")
            self.assertEqual(returned.person.outlaw_status, OUTLAW_STATUS_RETURNED)
            self.assertEqual(returned.person.current_settlement_id, settlement.settlement_id)
            self.assertEqual(ctx.outlaw_cases[returned_case.case_key].resolution, "forgotten")

            punished = self._add_adult(
                ctx,
                settlement_id=settlement.settlement_id,
                region_id=settlement.region_id,
            )
            punished_case = open_outlaw_case(
                ctx,
                year=1001,
                accused=punished,
                offense_type="property_crime",
                offense_kind="debt_fraud",
                severity_01=0.50,
                knownness_01=0.40,
                source_event_key="test:property:captured",
                target_person_id=returned.person_id,
            )
            self.assertIsNotNone(punished_case)
            flee_to_refuge(ctx, punished_case.case_key, year=1001)
            resolve_outlaw_case(ctx, punished_case.case_key, year=1003, resolution="captured")
            self.assertEqual(punished.person.outlaw_status, OUTLAW_STATUS_PUNISHED)
            self.assertEqual(punished.person.housing_status, "street")

    def test_outlaw_checkpoint_roundtrip_and_readable_views(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            ctx = self._context(root)
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            accused = self._add_adult(
                ctx,
                settlement_id=settlement.settlement_id,
                region_id=settlement.region_id,
            )
            case = open_outlaw_case(
                ctx,
                year=1001,
                accused=accused,
                offense_type="murder",
                offense_kind="kin_killing",
                severity_01=0.90,
                knownness_01=0.70,
                source_event_key="test:roundtrip",
                victim_person_id=accused.person_id,
            )
            self.assertIsNotNone(case)
            refuge = flee_to_refuge(ctx, case.case_key, year=1001)
            self.assertIsNotNone(refuge)
            ctx.current_year = 1001
            checkpoint_simulation_to_save(ctx)

            with closing(sqlite3.connect(root / "save.sqlite")) as con:
                con.row_factory = sqlite3.Row
                row = con.execute(
                    """
                    SELECT case_key, accused_person_id, refuge_id, refuge_display_name, status
                    FROM simulation_outlaw_cases_readable
                    WHERE case_key = ?
                    """,
                    (case.case_key,),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row["refuge_id"], refuge.refuge_id)
                self.assertEqual(row["refuge_display_name"], refuge.display_name)
                self.assertEqual(row["status"], "active")
                refuge_row = con.execute(
                    """
                    SELECT refuge_id, display_name, active_case_count
                    FROM simulation_outlaw_refuges_readable
                    WHERE refuge_id = ?
                    """,
                    (refuge.refuge_id,),
                ).fetchone()
                self.assertIsNotNone(refuge_row)
                self.assertEqual(refuge_row["display_name"], refuge.display_name)
                self.assertEqual(int(refuge_row["active_case_count"]), 1)

            loaded = SimulationContext.create(
                db_path=root / "config.sqlite",
                save_db_path=root / "save.sqlite",
                world_id="outlaws",
                world="default",
                refresh_config=False,
                flush_run_store=False,
                checkpoint_full_snapshot_every_n_years=None,
            )
            self.assertTrue(try_load_simulation_checkpoint(loaded))
            loaded_accused = loaded.id_to_record[accused.person_id]
            self.assertEqual(loaded_accused.person.outlaw_status, OUTLAW_STATUS_FUGITIVE)
            self.assertIsNone(loaded_accused.person.current_settlement_id)
            self.assertIn(case.case_key, loaded.outlaw_cases)
            self.assertIn(refuge.refuge_id, loaded.outlaw_refuges)
            self.assertEqual(
                loaded.outlaw_refuges[refuge.refuge_id].display_name,
                refuge.display_name,
            )


if __name__ == "__main__":
    unittest.main()
