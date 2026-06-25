"""Tests for outlaw cases, refuge flight, and checkpoint persistence."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
import random
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from library.config_import import load_all_csvs_into_sqlite
from library.generator import generate_person_random
from library.passive_population import PassiveCohort, PassivePerson
from library.polity import PolityState, TerritoryOpenRow
from library.simulation_context import SimulationContext
from library.simulation_careers import simulation_careers_annual_tick
from library.simulation_incidents import (
    _maybe_outlaw_property_crime,
    _outlaw_property_crime_attempt_chance,
    _record_property_crime_incident,
)
from library.simulation_outlaws import (
    OUTLAW_STATUS_CLEARED,
    OUTLAW_STATUS_FUGITIVE,
    OUTLAW_STATUS_IMPRISONED,
    OUTLAW_STATUS_RETURNED,
    flee_to_refuge,
    kill_outlaw,
    normalize_outlaw_labor_state,
    open_outlaw_case_from_passive,
    open_outlaw_case,
    resolve_outlaw_case,
    simulation_outlaws_annual_tick,
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


class _FixedRandom:
    def __init__(self, value: float) -> None:
        self.value = float(value)

    def random(self) -> float:
        return self.value


class _ZeroChoiceRandom(random.Random):
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

    def _set_outlaw_law_profile(
        self,
        ctx: SimulationContext,
        *,
        polity_id: int,
        settlement_id: str,
        profile_id: str,
    ) -> None:
        ctx.gov_polities[polity_id] = PolityState(
            polity_id=polity_id,
            polity_type_id="county",
            parent_polity_id=None,
            name=f"Test Law Polity {polity_id}",
            capital_settlement_id=settlement_id,
            founding_dynasty_id=None,
            founded_sim_year=1000,
            notes={"outlaw_law_profile": profile_id},
        )
        ctx.gov_territory_rows.append(
            TerritoryOpenRow(
                polity_id=polity_id,
                target_kind="settlement",
                target_id=settlement_id,
                since_sim_year=1000,
            )
        )

    def test_case_opening_and_flight_cuts_paramour_but_can_keep_partner(self) -> None:
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
            self.assertEqual(accused.person.partner_person_id, partner.person_id)
            self.assertEqual(partner.person.partner_person_id, accused.person_id)
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

    def test_outlaw_property_crime_chance_uses_wanted_and_fugitive_multipliers(self) -> None:
        normal = _outlaw_property_crime_attempt_chance(
            0.45,
            outlaw_status="",
            pressure=1.0,
        )
        wanted = _outlaw_property_crime_attempt_chance(
            0.45,
            outlaw_status="wanted",
            pressure=1.0,
        )
        fugitive = _outlaw_property_crime_attempt_chance(
            0.45,
            outlaw_status="fugitive",
            pressure=1.0,
        )

        self.assertGreater(wanted, normal)
        self.assertGreater(fugitive, wanted)

    def test_fugitive_property_crime_uses_existing_case_and_real_location(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            accused = self._add_adult(
                ctx,
                settlement_id=settlement.settlement_id,
                region_id=settlement.region_id,
                gender="Male",
                genome=_genome(honesty=-50, justice=-50, ambition=50, persuasion=50),
            )
            target = self._add_adult(
                ctx,
                settlement_id=settlement.settlement_id,
                region_id=settlement.region_id,
                gender="Female",
            )
            case = open_outlaw_case(
                ctx,
                year=1001,
                accused=accused,
                offense_type="property_crime",
                offense_kind="storehouse_robbery",
                severity_01=0.70,
                knownness_01=0.65,
                source_event_key="test:outlaw:property",
                target_person_id=target.person_id,
            )
            self.assertIsNotNone(case)
            refuge = flee_to_refuge(ctx, case.case_key, year=1001)
            self.assertIsNotNone(refuge)
            before_cases = len(ctx.outlaw_cases)

            incident = _maybe_outlaw_property_crime(
                ctx,
                1002,
                accused,
                rng=_ZeroChoiceRandom(),
            )
            self.assertIsNotNone(incident)
            _record_property_crime_incident(ctx, 1002, incident)

        self.assertEqual(incident.settlement_id, refuge.near_settlement_id)
        self.assertNotEqual(incident.settlement_id, "")
        self.assertEqual(incident.outlaw_case_key, case.case_key)
        self.assertEqual(incident.outlaw_status, "fugitive")
        self.assertEqual(len(ctx.outlaw_cases), before_cases)
        payload = next(
            payload
            for _year, event_type, payload in ctx._pending_simulation_events
            if event_type == "property_crime"
            and payload.get("outlaw_case_key") == case.case_key
        )
        self.assertEqual(payload["settlement_id"], refuge.near_settlement_id)
        self.assertEqual(payload["motive"], "survival")
        self.assertTrue(payload["consequences"]["outlaw_case"]["existing_case"])

    def test_outlaw_flight_can_break_disloyal_spouse_relationship(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            accused = self._add_adult(
                ctx,
                settlement_id=settlement.settlement_id,
                region_id=settlement.region_id,
                gender="Male",
            )
            spouse = self._add_adult(
                ctx,
                settlement_id=settlement.settlement_id,
                region_id=settlement.region_id,
                gender="Female",
                genome=_genome(loyalty=99.0),
            )
            ctx.add_couple(accused.person_id, spouse.person_id)

            case = open_outlaw_case(
                ctx,
                year=1001,
                accused=accused,
                offense_type="property_crime",
                offense_kind="storehouse_robbery",
                severity_01=0.75,
                knownness_01=0.65,
                source_event_key="test:property:spouse-break",
                target_person_id=spouse.person_id,
            )
            self.assertIsNotNone(case)
            self.assertIsNotNone(flee_to_refuge(ctx, case.case_key, year=1001))

            self.assertIsNone(accused.person.partner_person_id)
            self.assertIsNone(spouse.person.partner_person_id)
            self.assertNotIn((accused.person_id, spouse.person_id), ctx.couples)

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
            self.assertEqual(accused.person.outlaw_status, OUTLAW_STATUS_IMPRISONED)
            self.assertIsNone(accused.person.job)
            self.assertEqual(accused.person.employment_status, "imprisoned")
            self.assertEqual(accused.person.job_market_type, "custody")
            self.assertEqual(accused.person.housing_status, "custody")

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

    def test_polity_law_profiles_change_case_tuning(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            strict_settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            lenient_settlement = ctx.ensure_active_settlement_for_region("aeria_south")
            self._set_outlaw_law_profile(
                ctx,
                polity_id=100,
                settlement_id=strict_settlement.settlement_id,
                profile_id="strict_justice",
            )
            self._set_outlaw_law_profile(
                ctx,
                polity_id=101,
                settlement_id=lenient_settlement.settlement_id,
                profile_id="lenient_compromise",
            )
            strict_accused = self._add_adult(
                ctx,
                settlement_id=strict_settlement.settlement_id,
                region_id=strict_settlement.region_id,
            )
            lenient_accused = self._add_adult(
                ctx,
                settlement_id=lenient_settlement.settlement_id,
                region_id=lenient_settlement.region_id,
            )
            strict_accused.person = replace(
                strict_accused.person,
                household_prosperity=1.5,
                social_standing_01=0.4,
            )
            lenient_accused.person = replace(
                lenient_accused.person,
                household_prosperity=1.5,
                social_standing_01=0.4,
            )

            strict_case = open_outlaw_case(
                ctx,
                year=1002,
                accused=strict_accused,
                offense_type="property_crime",
                offense_kind="storehouse_robbery",
                severity_01=0.52,
                knownness_01=0.50,
                source_event_key="test:law:strict:tuning",
                target_person_id=lenient_accused.person_id,
            )
            lenient_case = open_outlaw_case(
                ctx,
                year=1002,
                accused=lenient_accused,
                offense_type="property_crime",
                offense_kind="storehouse_robbery",
                severity_01=0.52,
                knownness_01=0.50,
                source_event_key="test:law:lenient:tuning",
                target_person_id=strict_accused.person_id,
            )

            self.assertIsNotNone(strict_case)
            self.assertIsNotNone(lenient_case)
            self.assertEqual(strict_case.details["law_profile"], "strict_justice")
            self.assertEqual(lenient_case.details["law_profile"], "lenient_compromise")
            self.assertEqual(strict_case.details["law_polity_id"], 100)
            self.assertEqual(lenient_case.details["law_polity_id"], 101)
            self.assertAlmostEqual(strict_case.details["base_severity_01"], 0.52)
            self.assertAlmostEqual(lenient_case.details["base_severity_01"], 0.52)
            self.assertGreater(strict_case.severity_01, lenient_case.severity_01)
            self.assertGreater(strict_case.pursuit_pressure_01, lenient_case.pursuit_pressure_01)
            self.assertGreater(
                strict_case.expected_forget_year,
                lenient_case.expected_forget_year,
            )

    def test_polity_law_profiles_change_buyoff_and_punishment(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            strict_settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            lenient_settlement = ctx.ensure_active_settlement_for_region("aeria_south")
            self._set_outlaw_law_profile(
                ctx,
                polity_id=110,
                settlement_id=strict_settlement.settlement_id,
                profile_id="strict_justice",
            )
            self._set_outlaw_law_profile(
                ctx,
                polity_id=111,
                settlement_id=lenient_settlement.settlement_id,
                profile_id="lenient_compromise",
            )
            strict_buyoff = self._add_adult(
                ctx,
                settlement_id=strict_settlement.settlement_id,
                region_id=strict_settlement.region_id,
            )
            lenient_buyoff = self._add_adult(
                ctx,
                settlement_id=lenient_settlement.settlement_id,
                region_id=lenient_settlement.region_id,
            )
            for rec in (strict_buyoff, lenient_buyoff):
                rec.person = replace(
                    rec.person,
                    household_prosperity=4.0,
                    social_standing_01=0.2,
                )

            strict_case = open_outlaw_case(
                ctx,
                year=1002,
                accused=strict_buyoff,
                offense_type="property_crime",
                offense_kind="debt_fraud",
                severity_01=0.43,
                knownness_01=0.35,
                source_event_key="test:law:strict:buyoff",
                target_person_id=lenient_buyoff.person_id,
            )
            lenient_case = open_outlaw_case(
                ctx,
                year=1002,
                accused=lenient_buyoff,
                offense_type="property_crime",
                offense_kind="debt_fraud",
                severity_01=0.43,
                knownness_01=0.35,
                source_event_key="test:law:lenient:buyoff",
                target_person_id=strict_buyoff.person_id,
            )

            self.assertIsNotNone(strict_case)
            self.assertIsNotNone(lenient_case)
            self.assertFalse(_maybe_buy_off(ctx, strict_case, 1002, _ZeroRandom()))
            self.assertTrue(_maybe_buy_off(ctx, lenient_case, 1002, _ZeroRandom()))
            self.assertEqual(ctx.outlaw_cases[strict_case.case_key].status, "active")
            self.assertEqual(ctx.outlaw_cases[lenient_case.case_key].resolution, "bought_off")

            strict_punished = self._add_adult(
                ctx,
                settlement_id=strict_settlement.settlement_id,
                region_id=strict_settlement.region_id,
            )
            lenient_punished = self._add_adult(
                ctx,
                settlement_id=lenient_settlement.settlement_id,
                region_id=lenient_settlement.region_id,
            )
            strict_punished_case = open_outlaw_case(
                ctx,
                year=1003,
                accused=strict_punished,
                offense_type="property_crime",
                offense_kind="storehouse_robbery",
                severity_01=0.62,
                knownness_01=0.60,
                source_event_key="test:law:strict:punishment",
                target_person_id=lenient_punished.person_id,
            )
            lenient_punished_case = open_outlaw_case(
                ctx,
                year=1003,
                accused=lenient_punished,
                offense_type="property_crime",
                offense_kind="storehouse_robbery",
                severity_01=0.62,
                knownness_01=0.60,
                source_event_key="test:law:lenient:punishment",
                target_person_id=strict_punished.person_id,
            )
            self.assertIsNotNone(strict_punished_case)
            self.assertIsNotNone(lenient_punished_case)
            resolve_outlaw_case(
                ctx,
                strict_punished_case.case_key,
                year=1004,
                resolution="captured",
            )
            resolve_outlaw_case(
                ctx,
                lenient_punished_case.case_key,
                year=1004,
                resolution="captured",
            )
            strict_custody = ctx.outlaw_custodies[str(strict_punished.person.outlaw_custody_id)]
            lenient_custody = ctx.outlaw_custodies[str(lenient_punished.person.outlaw_custody_id)]
            self.assertGreater(
                strict_custody.expected_release_year - strict_custody.start_year,
                lenient_custody.expected_release_year - lenient_custody.start_year,
            )

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
            self.assertEqual(punished.person.outlaw_status, OUTLAW_STATUS_IMPRISONED)
            self.assertEqual(punished.person.housing_status, "custody")
            self.assertEqual(punished.person.household_role, "prisoner")
            self.assertTrue(punished.person.outlaw_custody_id)
            self.assertIn(punished.person.outlaw_custody_id, ctx.outlaw_custodies)

    def test_passive_person_can_be_promoted_into_outlaw_case_and_flow(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            target = self._add_adult(
                ctx,
                settlement_id=settlement.settlement_id,
                region_id=settlement.region_id,
            )
            passive = ctx.add_passive_person(
                PassivePerson(
                    name="Mira Lowdetail",
                    birthyear=970,
                    gender="Female",
                    species="Human",
                    ethnic="Gaulish",
                    birthplace_region_id=settlement.region_id,
                    birthplace_settlement_id=settlement.settlement_id,
                    current_settlement_id=settlement.settlement_id,
                    partner_name="Toma Lowdetail",
                    partner_birthyear=968,
                    partnership_start_year=992,
                    child_count=1,
                    child_birthyears=(1000,),
                )
            )

            case = open_outlaw_case_from_passive(
                ctx,
                year=1002,
                passive_person_id=passive.person_id,
                settlement_id=settlement.settlement_id,
                offense_type="property_crime",
                offense_kind="storehouse_robbery",
                severity_01=0.64,
                knownness_01=0.58,
                source_event_key="test:passive:property",
                target_person_id=target.person_id,
                details={"test_marker": "explicit_passive"},
            )

            self.assertIsNotNone(case)
            self.assertNotIn(passive.person_id, ctx.passive_people)
            accused = ctx.id_to_record[passive.person_id]
            self.assertEqual(accused.person.first_name, "Mira")
            self.assertEqual(accused.person.last_name, "Lowdetail")
            self.assertEqual(accused.person.outlaw_status, "wanted")
            self.assertEqual(accused.person.outlaw_case_key, case.case_key)
            self.assertEqual(case.accused_person_id, passive.person_id)
            self.assertTrue(case.details["promoted_from_passive"])
            self.assertEqual(case.details["passive_selector"], "passive_person_id")
            event_types = [event_type for _, event_type, _ in ctx._pending_simulation_events]
            self.assertIn("passive_person_promoted", event_types)
            self.assertIn("promotion_backfill_partnership", event_types)
            self.assertIn("promotion_backfill_children", event_types)
            self.assertIn("outlaw_case_opened", event_types)
            self.assertEqual(ctx.passive_promotion_log[-1].reason, "outlaw_case_accused")
            self.assertEqual(
                ctx.passive_promotion_log[-1].synthesized["source"]["selector"],
                "passive_person_id",
            )

            refuge = flee_to_refuge(ctx, case.case_key, year=1003)
            self.assertIsNotNone(refuge)
            self.assertEqual(accused.person.outlaw_status, OUTLAW_STATUS_FUGITIVE)
            resolved = resolve_outlaw_case(
                ctx,
                case.case_key,
                year=1004,
                resolution="captured",
            )
            self.assertIsNotNone(resolved)
            self.assertEqual(accused.person.outlaw_status, OUTLAW_STATUS_IMPRISONED)
            self.assertTrue(accused.person.outlaw_custody_id)

    def test_passive_cohort_can_be_promoted_into_outlaw_case(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            ctx.add_passive_cohort(
                PassiveCohort(
                    sim_year=1001,
                    population_count=2,
                    region_id=settlement.region_id,
                    settlement_id=settlement.settlement_id,
                    age_band="30-39",
                    gender="Male",
                    species="Human",
                    culture="Gaulish",
                    job_family="farm",
                    status_bucket="common",
                )
            )

            case = open_outlaw_case_from_passive(
                ctx,
                year=1002,
                settlement_id=settlement.settlement_id,
                offense_type="murder",
                offense_kind="ambush_killing",
                severity_01=0.88,
                knownness_01=0.70,
                source_event_key="test:passive:cohort:murder",
            )

            self.assertIsNotNone(case)
            self.assertEqual(ctx.passive_cohorts[0].population_count, 1)
            accused = ctx.id_to_record[case.accused_person_id]
            self.assertEqual(accused.person.outlaw_status, "wanted")
            self.assertEqual(accused.person.current_settlement_id, settlement.settlement_id)
            self.assertEqual(case.region_id, settlement.region_id)
            self.assertEqual(case.settlement_id, settlement.settlement_id)
            self.assertTrue(case.details["promoted_from_passive"])
            self.assertEqual(case.details["passive_selector"], "settlement_cohort")
            self.assertEqual(ctx.passive_promotion_log[-1].reason, "outlaw_case_accused")
            source = ctx.passive_promotion_log[-1].synthesized["source"]
            self.assertEqual(source["selector"], "settlement_cohort")
            self.assertEqual(source["source_cohort_year"], 1001)
            self.assertEqual(source["source_population_before"], 2)

            refuge = flee_to_refuge(ctx, case.case_key, year=1003)
            self.assertIsNotNone(refuge)
            self.assertEqual(accused.person.outlaw_status, OUTLAW_STATUS_FUGITIVE)

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

    def test_captured_outlaw_custody_checkpoint_roundtrip_and_readable_views(self) -> None:
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
                offense_type="property_crime",
                offense_kind="storehouse_robbery",
                severity_01=0.70,
                knownness_01=0.65,
                source_event_key="test:custody:roundtrip",
                target_person_id=accused.person_id,
            )
            self.assertIsNotNone(case)
            flee_to_refuge(ctx, case.case_key, year=1001)
            resolved = resolve_outlaw_case(
                ctx,
                case.case_key,
                year=1004,
                resolution="captured",
            )
            self.assertIsNotNone(resolved)
            custody_id = accused.person.outlaw_custody_id
            self.assertIsNotNone(custody_id)
            self.assertIn(custody_id, ctx.outlaw_custodies)
            custody = ctx.outlaw_custodies[str(custody_id)]
            self.assertEqual(accused.person.outlaw_status, OUTLAW_STATUS_IMPRISONED)
            self.assertEqual(accused.person.employment_status, "imprisoned")
            self.assertEqual(accused.person.job_market_type, "custody")
            self.assertEqual(accused.person.housing_status, "custody")
            self.assertEqual(custody.site_settlement_id, settlement.settlement_id)
            self.assertEqual(custody.expected_release_year, accused.person.outlaw_custody_expected_release_year)

            ctx.current_year = 1004
            checkpoint_simulation_to_save(ctx)

            with closing(sqlite3.connect(root / "save.sqlite")) as con:
                con.row_factory = sqlite3.Row
                case_row = con.execute(
                    """
                    SELECT case_key, custody_id, custody_status,
                           custody_site_settlement_id, custody_expected_release_year
                    FROM simulation_outlaw_cases_readable
                    WHERE case_key = ?
                    """,
                    (case.case_key,),
                ).fetchone()
                self.assertIsNotNone(case_row)
                self.assertEqual(case_row["custody_id"], custody_id)
                self.assertEqual(case_row["custody_status"], "active")
                self.assertEqual(
                    case_row["custody_site_settlement_id"],
                    settlement.settlement_id,
                )
                self.assertEqual(
                    int(case_row["custody_expected_release_year"]),
                    accused.person.outlaw_custody_expected_release_year,
                )

                custody_row = con.execute(
                    """
                    SELECT custody_id, case_key, person_id, custody_type, status,
                           site_settlement_id, expected_release_year
                    FROM simulation_outlaw_custodies_readable
                    WHERE custody_id = ?
                    """,
                    (custody_id,),
                ).fetchone()
                self.assertIsNotNone(custody_row)
                self.assertEqual(custody_row["case_key"], case.case_key)
                self.assertEqual(int(custody_row["person_id"]), accused.person_id)
                self.assertEqual(custody_row["custody_type"], "imprisonment")
                self.assertEqual(custody_row["status"], "active")
                self.assertEqual(
                    custody_row["site_settlement_id"],
                    settlement.settlement_id,
                )
                self.assertEqual(
                    int(custody_row["expected_release_year"]),
                    accused.person.outlaw_custody_expected_release_year,
                )

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
            self.assertIn(str(custody_id), loaded.outlaw_custodies)
            loaded_accused = loaded.id_to_record[accused.person_id]
            self.assertEqual(loaded_accused.person.outlaw_status, OUTLAW_STATUS_IMPRISONED)
            self.assertEqual(loaded_accused.person.outlaw_custody_id, custody_id)
            self.assertEqual(loaded_accused.person.employment_status, "imprisoned")
            self.assertEqual(loaded_accused.person.housing_status, "custody")
            self.assertEqual(
                loaded.outlaw_custodies[str(custody_id)].site_settlement_id,
                settlement.settlement_id,
            )

    def test_custody_blocks_ordinary_residence_until_release(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
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
                offense_type="property_crime",
                offense_kind="storehouse_robbery",
                severity_01=0.70,
                knownness_01=0.65,
                source_event_key="test:custody:release",
                target_person_id=accused.person_id,
            )
            self.assertIsNotNone(case)
            flee_to_refuge(ctx, case.case_key, year=1001)
            resolve_outlaw_case(ctx, case.case_key, year=1002, resolution="captured")
            custody_id = str(accused.person.outlaw_custody_id)
            custody = ctx.outlaw_custodies[custody_id]

            residents = {
                rec.person_id
                for rec in ctx.current_people_by_settlement().get(
                    settlement.settlement_id, []
                )
            }
            self.assertNotIn(accused.person_id, residents)
            self.assertEqual(accused.person.outlaw_status, OUTLAW_STATUS_IMPRISONED)
            self.assertEqual(accused.person.current_settlement_id, settlement.settlement_id)

            simulation_outlaws_annual_tick(
                ctx,
                int(custody.expected_release_year or 1003),
            )

            self.assertEqual(ctx.outlaw_custodies[custody_id].status, "released")
            self.assertEqual(accused.person.outlaw_status, OUTLAW_STATUS_RETURNED)
            self.assertIsNone(accused.person.outlaw_custody_id)
            residents = {
                rec.person_id
                for rec in ctx.current_people_by_settlement().get(
                    settlement.settlement_id, []
                )
            }
            self.assertIn(accused.person_id, residents)
            self.assertIn(
                "outlaw_returned",
                [event_type for _year, event_type, _payload in ctx._pending_simulation_events],
            )

    def test_custody_drops_queued_ordinary_migration_until_release(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            origin = ctx.ensure_active_settlement_for_region("aeria_north")
            destination = ctx.ensure_active_settlement_for_region("aeria_south")
            accused = self._add_adult(
                ctx,
                settlement_id=origin.settlement_id,
                region_id=origin.region_id,
            )
            self.assertTrue(
                ctx.queue_person_move_to_settlement(
                    accused.person_id,
                    destination.settlement_id,
                    move_reason="job_seeker_migration",
                    requested_year=1001,
                    apply_year=1003,
                    source_event="job_seeker_migration",
                )
            )
            case = open_outlaw_case(
                ctx,
                year=1001,
                accused=accused,
                offense_type="property_crime",
                offense_kind="storehouse_robbery",
                severity_01=0.70,
                knownness_01=0.65,
                source_event_key="test:custody:queued-move",
                target_person_id=accused.person_id,
            )
            self.assertIsNotNone(case)
            flee_to_refuge(ctx, case.case_key, year=1001)
            resolve_outlaw_case(ctx, case.case_key, year=1002, resolution="captured")
            custody_id = str(accused.person.outlaw_custody_id)
            custody = ctx.outlaw_custodies[custody_id]

            self.assertFalse(
                ctx.queue_person_move_to_settlement(
                    accused.person_id,
                    destination.settlement_id,
                    move_reason="household_move",
                    requested_year=1002,
                    apply_year=1003,
                    source_event="household_move",
                )
            )
            applied = ctx.apply_pending_settlement_moves(1003)

            self.assertEqual(applied, 0)
            self.assertEqual(accused.person.outlaw_status, OUTLAW_STATUS_IMPRISONED)
            self.assertEqual(accused.person.current_settlement_id, origin.settlement_id)
            self.assertNotEqual(accused.person.current_settlement_id, destination.settlement_id)
            self.assertIn(
                "settlement_move_dropped",
                [event_type for _year, event_type, _payload in ctx._pending_simulation_events],
            )
            self.assertNotIn(
                "settlement_moved",
                [
                    event_type
                    for year, event_type, payload in ctx._pending_simulation_events
                    if year == 1003
                    and payload.get("person_id") == accused.person_id
                    and payload.get("to_settlement_id") == destination.settlement_id
                ],
            )

            simulation_outlaws_annual_tick(
                ctx,
                int(custody.expected_release_year or 1004),
            )
            self.assertEqual(ctx.outlaw_custodies[custody_id].status, "released")
            self.assertIsNone(accused.person.outlaw_custody_id)
            self.assertTrue(
                ctx.queue_person_move_to_settlement(
                    accused.person_id,
                    destination.settlement_id,
                    move_reason="household_move",
                    requested_year=int(custody.expected_release_year or 1004),
                    apply_year=int(custody.expected_release_year or 1004) + 1,
                    source_event="household_move",
                )
            )
            ctx.apply_pending_settlement_moves(
                int(custody.expected_release_year or 1004) + 1
            )
            self.assertEqual(accused.person.current_settlement_id, destination.settlement_id)

    def test_custody_can_end_in_death_or_escape_before_release(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            dying = self._add_adult(
                ctx,
                settlement_id=settlement.settlement_id,
                region_id=settlement.region_id,
            )
            death_case = open_outlaw_case(
                ctx,
                year=1001,
                accused=dying,
                offense_type="property_crime",
                offense_kind="storehouse_robbery",
                severity_01=0.70,
                knownness_01=0.65,
                source_event_key="test:custody:death",
                target_person_id=dying.person_id,
            )
            self.assertIsNotNone(death_case)
            flee_to_refuge(ctx, death_case.case_key, year=1001)
            resolve_outlaw_case(ctx, death_case.case_key, year=1002, resolution="captured")
            death_custody_id = str(dying.person.outlaw_custody_id)

            with patch(
                "library.simulation_outlaws.random.Random",
                lambda *args, **kwargs: _FixedRandom(0.0),
            ):
                simulation_outlaws_annual_tick(ctx, 1003)

            self.assertEqual(ctx.outlaw_custodies[death_custody_id].status, "died")
            self.assertNotIn(dying.person_id, ctx.current_people_ids)
            self.assertEqual(
                ctx.outlaw_cases[death_case.case_key].resolution,
                "died_in_custody",
            )
            self.assertIn(
                "outlaw_died_in_custody",
                [event_type for _year, event_type, _payload in ctx._pending_simulation_events],
            )

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            escaping = self._add_adult(
                ctx,
                settlement_id=settlement.settlement_id,
                region_id=settlement.region_id,
            )
            escape_case = open_outlaw_case(
                ctx,
                year=1001,
                accused=escaping,
                offense_type="property_crime",
                offense_kind="storehouse_robbery",
                severity_01=0.70,
                knownness_01=0.65,
                source_event_key="test:custody:escape",
                target_person_id=escaping.person_id,
            )
            self.assertIsNotNone(escape_case)
            flee_to_refuge(ctx, escape_case.case_key, year=1001)
            resolve_outlaw_case(ctx, escape_case.case_key, year=1002, resolution="captured")
            escape_custody_id = str(escaping.person.outlaw_custody_id)

            with patch(
                "library.simulation_outlaws.random.Random",
                lambda *args, **kwargs: _FixedRandom(0.03),
            ):
                simulation_outlaws_annual_tick(ctx, 1003)

            self.assertEqual(ctx.outlaw_custodies[escape_custody_id].status, "escaped")
            self.assertEqual(escaping.person.outlaw_status, OUTLAW_STATUS_FUGITIVE)
            self.assertIsNone(escaping.person.current_settlement_id)
            self.assertEqual(ctx.outlaw_cases[escape_case.case_key].status, "active")
            event_types = [
                event_type for _year, event_type, _payload in ctx._pending_simulation_events
            ]
            self.assertIn("outlaw_escape", event_types)
            self.assertIn("outlaw_flight", event_types)
            flight_payload = next(
                payload
                for _year, event_type, payload in ctx._pending_simulation_events
                if event_type == "outlaw_flight"
                and payload.get("case_key") == escape_case.case_key
                and payload.get("flight_reason") == "escaped_custody"
            )
            self.assertEqual(
                flight_payload["details"],
                "escaped custody before fleeing to outlaw refuge",
            )


if __name__ == "__main__":
    unittest.main()
