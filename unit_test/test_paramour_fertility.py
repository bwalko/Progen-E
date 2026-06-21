import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from library.config_import import load_all_csvs_into_sqlite
from library.person import Person
from library.population_growth_runner import births_by_settlement
from library.settlements import SettlementState
from library.simulation_context import SimulationContext


class TestParamourFertility(unittest.TestCase):
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
            display_name="Aeria Test Hamlet",
            resident_count=3,
            household_cap=3,
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
    ):
        return ctx.add_person(
            person=Person(
                first_name=first_name,
                last_name="Test",
                gender=gender,
                ethnic="Alemannic",
                species="Human",
                birthyear=birthyear,
                birthplace="Aeria Test Hamlet",
                birthplace_region_id="aeria_north",
                birthplace_settlement_id="aeria_north:s1",
                current_settlement_id="aeria_north:s1",
                min_fertility_age=16,
                max_fertility_age=45 if gender == "Female" else None,
            ),
            is_founder=True,
        )

    def _run_births(self, ctx: SimulationContext, year: int = 1001) -> int:
        ctx.current_year = year
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
        ):
            return births_by_settlement(
                ctx,
                year,
                sim_seed=17,
                by_settlement=ctx.current_people_by_settlement(),
            )

    def test_paramour_relationship_can_produce_out_of_wedlock_child(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            mother = self._adult(
                ctx, first_name="Ada", gender="Female", birthyear=970
            )
            paramour = self._adult(
                ctx, first_name="Berengar", gender="Male", birthyear=968
            )
            ctx.add_paramour_relationship(mother.person_id, paramour.person_id)

            births = self._run_births(ctx)

        self.assertEqual(births, 1)
        child = next(rec for rec in ctx.people if not rec.is_founder)
        self.assertEqual(child.mother_id, mother.person_id)
        self.assertEqual(child.father_id, paramour.person_id)
        self.assertEqual(child.person.birth_relationship_type, "paramour")
        self.assertTrue(child.person.born_out_of_wedlock)
        self.assertEqual(child.person.legitimacy_status, "bastard")
        event_payload = next(
            payload
            for _year, event_type, payload in ctx._pending_simulation_events
            if event_type == "birth" and payload.get("child_id") == child.person_id
        )
        self.assertEqual(event_payload["birth_relationship_type"], "paramour")
        self.assertTrue(event_payload["born_out_of_wedlock"])
        self.assertEqual(event_payload["legitimacy_status"], "bastard")

    def test_spouse_and_paramour_candidates_produce_one_birth_event_per_mother_year(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            mother = self._adult(
                ctx, first_name="Ada", gender="Female", birthyear=970
            )
            spouse = self._adult(
                ctx, first_name="Adalbert", gender="Male", birthyear=969
            )
            paramour = self._adult(
                ctx, first_name="Berengar", gender="Male", birthyear=968
            )
            ctx.add_couple(spouse.person_id, mother.person_id)
            ctx.add_paramour_relationship(mother.person_id, paramour.person_id)

            births = self._run_births(ctx)

        self.assertEqual(births, 1)
        self.assertEqual(ctx.id_to_record[mother.person_id].person.last_birth_event_year, 1001)
        birth_events = [
            payload
            for _year, event_type, payload in ctx._pending_simulation_events
            if event_type == "birth"
        ]
        self.assertEqual(len(birth_events), 1)
        self.assertEqual(birth_events[0]["person_b_id"], mother.person_id)

    def test_same_sex_paramour_does_not_supply_genetic_father(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            mother = self._adult(
                ctx, first_name="Ada", gender="Female", birthyear=970
            )
            paramour = self._adult(
                ctx, first_name="Berta", gender="Female", birthyear=969
            )
            ctx.add_paramour_relationship(mother.person_id, paramour.person_id)

            births = self._run_births(ctx)

        self.assertEqual(births, 0)
        self.assertIsNone(ctx.id_to_record[mother.person_id].person.last_birth_event_year)


if __name__ == "__main__":
    unittest.main()
