"""Two identical population-growth runs must yield identical structural fingerprints."""

from __future__ import annotations

import importlib.util
import random
import sys
import tempfile
import types
import unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import MagicMock, patch

if "numpy" not in sys.modules and importlib.util.find_spec("numpy") is None:
    import random

    class _NumpyRandomStub:
        def __init__(self) -> None:
            self._rng = random.Random(0)

        def seed(self, value: int) -> None:
            self._rng.seed(int(value))

        def normal(self, mean: float, stdev: float) -> float:
            return self._rng.gauss(float(mean), float(stdev))

    sys.modules["numpy"] = types.SimpleNamespace(
        random=_NumpyRandomStub(),
        clip=lambda value, lo, hi: max(float(lo), min(float(hi), float(value))),
    )

from library.config_import import load_all_csvs_into_sqlite
from library.person import Person
from library.population_growth_runner import (
    KIN_PAIR_PARENT_CHILD_PROB,
    generate_population_founder,
    pair_people_by_settlement_then_region,
    run_population_growth_simulation,
)
from library.simulation_context import SimulationContext
from library.settlements import SettlementState

START_YEAR = 1000
YEARS = 45
STARTING_COUPLES = 10
SIM_SEED = 9_001_355_027


def _run_once(*, cfg: Path, sav: Path) -> tuple[tuple[int, ...], tuple[tuple[int, int], ...], int, int]:
    with SimulationContext.create(
        db_path=cfg,
        save_db_path=sav,
        world_id="default",
        world="default",
        start_year=START_YEAR,
        placename_rng_salt=SIM_SEED,
        refresh_config=False,
    ) as ctx:
        run_population_growth_simulation(
            ctx,
            sim_seed=SIM_SEED,
            start_year=START_YEAR,
            duration_years=YEARS,
            starting_couples=STARTING_COUPLES,
        )
    return (
        tuple(sorted(ctx.current_people_ids)),
        tuple(sorted(ctx.couples)),
        len(ctx.people),
        int(ctx.next_person_id),
    )


class TestPopulationGrowthDeterminism(unittest.TestCase):
    def test_population_founder_age_is_fertile_and_has_parent_names(self) -> None:
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
                start_year=START_YEAR,
                placename_rng_salt=SIM_SEED,
                refresh_config=False,
                flush_run_store=False,
            ) as ctx:
                founder = generate_population_founder(
                    ctx,
                    gender="Female",
                    simulation_year=START_YEAR,
                    rng=random.Random(1234),
                )
                age = START_YEAR - int(founder.birthyear)
                self.assertIsNotNone(founder.min_fertility_age)
                self.assertIsNotNone(founder.max_fertility_age)
                self.assertGreaterEqual(age, int(founder.min_fertility_age or 0))
                self.assertLessEqual(age, int(founder.max_fertility_age or age))
                self.assertTrue((founder.father_name or "").strip())
                self.assertTrue((founder.mother_name or "").strip())

    def test_two_runs_same_fingerprint(self) -> None:
        fingerprints: list[tuple[tuple[int, ...], tuple[tuple[int, int], ...], int, int]] = []
        for _ in range(2):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
                root = Path(td)
                cfg = root / "config.sqlite"
                sav = root / "save.sqlite"
                load_all_csvs_into_sqlite(cfg)
                fingerprints.append(_run_once(cfg=cfg, sav=sav))
        self.assertEqual(
            fingerprints[0],
            fingerprints[1],
            "population growth should be deterministic for a fixed sim seed",
        )

    def test_pairing_prefers_same_settlement_before_same_region_fallback(self) -> None:
        ctx = SimulationContext(
            db_path=Path("unused-config.sqlite"),
            save_db_path=Path("unused-save.sqlite"),
            world="default",
            simulation_start_year=START_YEAR,
            current_year=START_YEAR,
            settlements_by_id={
                "region:s1": SettlementState(region_id="region", settlement_id="region:s1"),
                "region:s2": SettlementState(region_id="region", settlement_id="region:s2"),
            },
        )

        def person(gender: str, sid: str) -> Person:
            return Person(
                first_name=gender,
                last_name=sid,
                gender=gender,
                ethnic="Human",
                species="Human",
                birthyear=START_YEAR - 30,
                birthplace_region_id="region",
                birthplace_settlement_id=sid,
                current_settlement_id=sid,
                min_fertility_age=18,
            )

        local_male = ctx.add_person(person=person("Male", "region:s1"), is_founder=False)
        local_female = ctx.add_person(person=person("Female", "region:s1"), is_founder=False)
        fallback_male = ctx.add_person(person=person("Male", "region:s1"), is_founder=False)
        fallback_female = ctx.add_person(person=person("Female", "region:s2"), is_founder=False)
        # Make one same-settlement person unavailable so the remaining pair requires same-region fallback.
        unavailable = ctx.add_person(
            person=replace(person("Female", "region:s2"), partner_person_id=999),
            is_founder=False,
        )
        ctx.couples.append((999, unavailable.person_id))

        pair_people_by_settlement_then_region(
            ctx, START_YEAR, ctx.current_people_by_settlement()
        )

        self.assertIn((local_male.person_id, local_female.person_id), ctx.couples)
        self.assertIn((fallback_male.person_id, fallback_female.person_id), ctx.couples)

    def test_pairing_skips_parent_child_when_other_partner_exists(self) -> None:
        ctx = SimulationContext(
            db_path=Path("unused-config.sqlite"),
            save_db_path=Path("unused-save.sqlite"),
            world="default",
            simulation_start_year=START_YEAR,
            current_year=START_YEAR,
            settlements_by_id={
                "region:s1": SettlementState(region_id="region", settlement_id="region:s1"),
            },
        )

        def person(first: str, gender: str, age: int) -> Person:
            return Person(
                first_name=first,
                last_name="Kin",
                gender=gender,
                ethnic="Human",
                species="Human",
                birthyear=START_YEAR - age,
                birthplace_region_id="region",
                birthplace_settlement_id="region:s1",
                current_settlement_id="region:s1",
                min_fertility_age=18,
            )

        mother = ctx.add_person(person=person("Mother", "Female", 50), is_founder=False)
        other = ctx.add_person(person=person("Other", "Female", 30), is_founder=False)
        son = ctx.add_person(
            person=person("Son", "Male", 30),
            is_founder=False,
            mother_id=mother.person_id,
        )

        pair_people_by_settlement_then_region(
            ctx, START_YEAR, ctx.current_people_by_settlement()
        )

        self.assertNotIn((son.person_id, mother.person_id), ctx.couples)
        self.assertIn((son.person_id, other.person_id), ctx.couples)

    def test_parent_child_pairing_possible_only_through_tiny_exception(self) -> None:
        ctx = SimulationContext(
            db_path=Path("unused-config.sqlite"),
            save_db_path=Path("unused-save.sqlite"),
            world="default",
            simulation_start_year=START_YEAR,
            current_year=START_YEAR,
            settlements_by_id={
                "region:s1": SettlementState(region_id="region", settlement_id="region:s1"),
            },
        )

        mother = ctx.add_person(
            person=Person(
                first_name="Mother",
                last_name="Rare",
                gender="Female",
                ethnic="Human",
                species="Human",
                birthyear=START_YEAR - 50,
                birthplace_region_id="region",
                birthplace_settlement_id="region:s1",
                current_settlement_id="region:s1",
                min_fertility_age=18,
            ),
            is_founder=False,
        )
        son = ctx.add_person(
            person=Person(
                first_name="Son",
                last_name="Rare",
                gender="Male",
                ethnic="Human",
                species="Human",
                birthyear=START_YEAR - 30,
                birthplace_region_id="region",
                birthplace_settlement_id="region:s1",
                current_settlement_id="region:s1",
                min_fertility_age=18,
            ),
            is_founder=False,
            mother_id=mother.person_id,
        )

        rng = MagicMock()
        rng.random.return_value = 0.0
        with patch("library.population_growth_runner._kin_pairing_rng", return_value=rng):
            pair_people_by_settlement_then_region(
                ctx, START_YEAR, ctx.current_people_by_settlement()
            )

        self.assertIn((son.person_id, mother.person_id), ctx.couples)
        event = ctx._pending_simulation_events[-1][2]
        self.assertEqual(event.get("kinship_exception"), "parent_child")
        self.assertEqual(event.get("kinship_exception_probability"), KIN_PAIR_PARENT_CHILD_PROB)


if __name__ == "__main__":
    unittest.main()
