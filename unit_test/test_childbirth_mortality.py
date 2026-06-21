"""Maternal death from childbirth in the detailed birth pipeline."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from library.config_import import load_all_csvs_into_sqlite
from library.person import Person
from library.population_growth_runner import (
    assess_childbirth_mortality,
    births_by_settlement,
)
from library.settlements import SettlementState
from library.simulation_context import SimulationContext


class _FixedRandom:
    def __init__(self, value: float) -> None:
        self.value = float(value)

    def random(self) -> float:
        return self.value


class TestChildbirthMortality(unittest.TestCase):
    def _context(self, root: Path) -> SimulationContext:
        cfg = root / "config.sqlite"
        save = root / "save.sqlite"
        load_all_csvs_into_sqlite(cfg)
        ctx = SimulationContext(
            db_path=cfg,
            save_db_path=save,
            world="default",
            simulation_start_year=1000,
            current_year=1000,
        )
        ctx.settlements_by_id["aeria_north:s1"] = SettlementState(
            region_id="aeria_north",
            region_display_name="Aeria North",
            settlement_id="aeria_north:s1",
            display_name="Small Hamlet",
            resident_count=4,
            household_cap=1,
            stability=0.1,
            prosperity_pool=0.1,
            status="active",
        )
        ctx.settlements_by_id["aeria_north:s2"] = SettlementState(
            region_id="aeria_north",
            region_display_name="Aeria North",
            settlement_id="aeria_north:s2",
            display_name="Supported Town",
            resident_count=450,
            household_cap=110,
            stability=0.95,
            prosperity_pool=1.0,
            status="active",
        )
        return ctx

    def _adult(
        self,
        ctx: SimulationContext,
        *,
        first_name: str,
        gender: str,
        birthyear: int,
        settlement_id: str = "aeria_north:s1",
        genome: dict[str, float] | None = None,
        household_prosperity: float | None = None,
        job_prosperity_01: float | None = None,
    ):
        return ctx.add_person(
            person=Person(
                first_name=first_name,
                last_name="Test",
                gender=gender,
                ethnic="Alemannic",
                species="Human",
                birthyear=birthyear,
                birthplace="Aeria Test",
                birthplace_region_id="aeria_north",
                birthplace_settlement_id=settlement_id,
                current_settlement_id=settlement_id,
                min_fertility_age=16,
                max_fertility_age=50 if gender == "Female" else None,
                genome=dict(genome or {}),
                household_prosperity=household_prosperity,
                job_prosperity_01=job_prosperity_01,
            ),
            is_founder=True,
        )

    def _child_record(self, ctx: SimulationContext, mother_id: int, birthyear: int):
        return ctx.add_person(
            person=Person(
                first_name=f"Child{birthyear}",
                last_name="Test",
                gender="Female",
                ethnic="Alemannic",
                species="Human",
                birthyear=birthyear,
                birthplace="Aeria Test",
                birthplace_region_id="aeria_north",
                birthplace_settlement_id="aeria_north:s1",
                current_settlement_id="aeria_north:s1",
            ),
            is_founder=False,
            mother_id=mother_id,
        )

    def _run_births(self, ctx: SimulationContext, *, death_roll: float) -> int:
        ctx.current_year = 1001
        with (
            patch(
                "library.population_growth_runner.resource_pressure_for_person",
                return_value=0.0,
            ),
            patch(
                "library.population_growth_runner.annual_conception_probability",
                return_value=1.0,
            ),
            patch("library.reproduction.roll_birth_litter_size", return_value=1),
            patch(
                "library.population_growth_runner.childbirth_mortality_rng",
                return_value=_FixedRandom(death_roll),
            ),
        ):
            return births_by_settlement(
                ctx,
                1001,
                sim_seed=17,
                by_settlement=ctx.current_people_by_settlement(),
            )

    def test_childbirth_mortality_probability_uses_risk_modifiers(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            risky = self._adult(
                ctx,
                first_name="Risky",
                gender="Female",
                birthyear=957,
                settlement_id="aeria_north:s1",
                genome={"physical": -95, "resilience": -95, "neurochemical": 95},
                household_prosperity=0.0,
                job_prosperity_01=0.0,
            )
            safe = self._adult(
                ctx,
                first_name="Safe",
                gender="Female",
                birthyear=973,
                settlement_id="aeria_north:s2",
                genome={"physical": 0, "resilience": 0, "neurochemical": 0},
                household_prosperity=1.0,
                job_prosperity_01=1.0,
            )
            for offset in range(6):
                self._child_record(ctx, risky.person_id, 990 + offset)

            risky_assessment = assess_childbirth_mortality(
                ctx,
                risky,
                year=1001,
                resource_pressure=2.0,
                litter_size=3,
                settlement_id="aeria_north:s1",
            )
            safe_assessment = assess_childbirth_mortality(
                ctx,
                safe,
                year=1001,
                resource_pressure=0.0,
                litter_size=1,
                settlement_id="aeria_north:s2",
            )

        self.assertEqual(risky_assessment.prior_births, 6)
        self.assertEqual(risky_assessment.litter_size, 3)
        self.assertLess(risky_assessment.settlement_care_01, safe_assessment.settlement_care_01)
        self.assertGreater(risky_assessment.health_pressure_01, safe_assessment.health_pressure_01)
        self.assertGreater(risky_assessment.probability, safe_assessment.probability * 3.0)

    def test_birth_can_leave_mother_alive_without_duplicate_births(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            mother = self._adult(ctx, first_name="Ada", gender="Female", birthyear=970)
            father = self._adult(ctx, first_name="Berto", gender="Male", birthyear=968)
            ctx.add_couple(father.person_id, mother.person_id)

            births = self._run_births(ctx, death_roll=1.0)

        self.assertEqual(births, 1)
        self.assertIn(mother.person_id, ctx.current_people_ids)
        self.assertEqual(ctx.id_to_record[mother.person_id].person.last_birth_event_year, 1001)
        self.assertEqual(ctx.last_childbirth_maternal_deaths_count, 0)
        self.assertEqual(len([rec for rec in ctx.people if not rec.is_founder]), 1)
        self.assertFalse(
            [
                payload
                for _year, event_type, payload in ctx._pending_simulation_events
                if event_type == "death"
                and payload.get("death_cause") == "childbirth"
            ]
        )

    def test_childbirth_death_keeps_child_and_closes_family_relationship(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            mother = self._adult(ctx, first_name="Ada", gender="Female", birthyear=970)
            father = self._adult(ctx, first_name="Berto", gender="Male", birthyear=968)
            ctx.add_couple(father.person_id, mother.person_id)

            births = self._run_births(ctx, death_roll=0.0)

        child = next(rec for rec in ctx.people if not rec.is_founder)
        mother_after = ctx.id_to_record[mother.person_id].person
        father_after = ctx.id_to_record[father.person_id].person
        death_payload = next(
            payload
            for _year, event_type, payload in ctx._pending_simulation_events
            if event_type == "death" and payload.get("person_id") == mother.person_id
        )
        birth_payload = next(
            payload
            for _year, event_type, payload in ctx._pending_simulation_events
            if event_type == "birth" and payload.get("child_id") == child.person_id
        )

        self.assertEqual(births, 1)
        self.assertEqual(child.mother_id, mother.person_id)
        self.assertEqual(child.father_id, father.person_id)
        self.assertEqual(birth_payload["child_id"], child.person_id)
        self.assertEqual(mother_after.deathyear, 1001)
        self.assertNotIn(mother.person_id, ctx.current_people_ids)
        self.assertIsNone(father_after.partner_person_id)
        self.assertNotIn((father.person_id, mother.person_id), ctx.couples)
        self.assertEqual(ctx.last_childbirth_maternal_deaths_count, 1)
        self.assertEqual(death_payload["death_cause"], "childbirth")
        self.assertEqual(death_payload["related_child_ids"], [child.person_id])
        self.assertEqual(death_payload["child_id"], child.person_id)
        self.assertGreater(death_payload["childbirth_mortality_probability"], 0.0)


if __name__ == "__main__":
    unittest.main()
