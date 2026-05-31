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
    refresh_passive_background_cohorts,
    run_population_growth_simulation,
)
from library.passive_population import PassiveCohort, promote_passive_candidate_for_office
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

    def test_passive_cohort_allocation_varies_between_sibling_settlements(self) -> None:
        def build_allocations() -> dict[str, int]:
            ctx = SimulationContext(
                db_path=Path("unused-config.sqlite"),
                save_db_path=Path("unused-save.sqlite"),
                world="default",
                simulation_start_year=START_YEAR,
                current_year=START_YEAR + 100,
                settlements_by_id={
                    f"region:s{i}": SettlementState(
                        region_id="region",
                        settlement_id=f"region:s{i}",
                        site_slot=i,
                        founded_sim_year=START_YEAR + i,
                        status="active",
                    )
                    for i in range(1, 11)
                },
            )
            ctx.effective_regional_population_cap = lambda region_id: 600
            refresh_passive_background_cohorts(ctx, START_YEAR + 100)
            out: dict[str, int] = {}
            for cohort in ctx.passive_cohorts:
                sid = str(cohort.settlement_id)
                out[sid] = out.get(sid, 0) + int(cohort.population_count)
            return out

        first = build_allocations()
        second = build_allocations()

        self.assertEqual(first, second)
        self.assertEqual(sum(first.values()), 600)
        self.assertGreater(len(set(first.values())), 1)
        self.assertNotEqual(sorted(first.values()), [60] * 10)

    def test_passive_cohorts_age_birth_die_and_keep_counts_deterministic(self) -> None:
        ctx = SimulationContext(
            db_path=Path("unused-config.sqlite"),
            save_db_path=Path("unused-save.sqlite"),
            world="default",
            simulation_start_year=START_YEAR,
            current_year=START_YEAR,
            settlements_by_id={
                "region:s1": SettlementState(
                    region_id="region",
                    settlement_id="region:s1",
                    status="active",
                )
            },
        )
        ctx.effective_regional_population_cap = lambda region_id: 400
        first_total = refresh_passive_background_cohorts(ctx, START_YEAR)
        second_total = refresh_passive_background_cohorts(ctx, START_YEAR + 1)
        latest = [c for c in ctx.passive_cohorts if c.sim_year == START_YEAR + 1]

        self.assertGreater(first_total, 0)
        self.assertGreater(second_total, 0)
        self.assertTrue(any(c.age_band == "0" and c.birth_count > 0 for c in latest))
        self.assertTrue(any(c.death_count > 0 for c in latest))
        self.assertTrue(any(c.age_band == "31" for c in latest))

    def test_passive_newborns_are_kept_when_background_scale_is_zero(self) -> None:
        ctx = SimulationContext(
            db_path=Path("unused-config.sqlite"),
            save_db_path=Path("unused-save.sqlite"),
            world="default",
            simulation_start_year=START_YEAR,
            current_year=START_YEAR,
            settlements_by_id={
                "region:s1": SettlementState(
                    region_id="region",
                    settlement_id="region:s1",
                    status="active",
                )
            },
        )

        total = refresh_passive_background_cohorts(
            ctx,
            START_YEAR,
            population_scale=0,
            extra_newborns_by_place={("region", "region:s1", "Human", "Gaulish"): 7},
        )

        self.assertEqual(total, 7)
        self.assertEqual(sum(c.population_count for c in ctx.passive_cohorts), 7)
        self.assertEqual(sum(c.birth_count for c in ctx.passive_cohorts), 7)

    def test_passive_office_promotion_materializes_full_person(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="default",
                world="default",
                start_year=START_YEAR,
                refresh_config=False,
                flush_run_store=False,
            )
            st = ctx.ensure_active_settlement_for_region("aeria_north")
            ctx.effective_regional_population_cap = lambda region_id: 50
            refresh_passive_background_cohorts(ctx, START_YEAR)

            promoted = promote_passive_candidate_for_office(
                ctx,
                year=START_YEAR,
                settlement_id=st.settlement_id,
                min_age=18,
            )

            self.assertIsNotNone(promoted)
            assert promoted is not None
            self.assertIn(promoted.person_id, ctx.current_people_ids)
            self.assertTrue(promoted.person.genome)
            self.assertTrue(promoted.person.mind_body)
            self.assertEqual(promoted.person.current_settlement_id, st.settlement_id)
            self.assertTrue(
                any(e[1] == "passive_person_promoted" for e in ctx._pending_simulation_events)
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

    def test_pairing_can_promote_passive_spouse_for_unpaired_detailed_person(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="default",
                world="default",
                start_year=START_YEAR,
                refresh_config=False,
                flush_run_store=False,
            )
            sid = "region:s1"
            ctx.settlements_by_id[sid] = SettlementState(
                region_id="region",
                settlement_id=sid,
                status="active",
            )
            detailed = ctx.add_person(
                person=Person(
                    first_name="Unpaired",
                    last_name="Detailed",
                    gender="Male",
                    ethnic="Human",
                    species="Human",
                    birthyear=START_YEAR - 30,
                    birthplace_region_id="region",
                    birthplace_settlement_id=sid,
                    current_settlement_id=sid,
                    min_fertility_age=18,
                ),
                is_founder=False,
            )
            ctx.add_passive_cohort(
                PassiveCohort(
                    sim_year=START_YEAR,
                    region_id="region",
                    settlement_id=sid,
                    age_band="28",
                    gender="Female",
                    species="",
                    culture="",
                    job_family="farm",
                    status_bucket="single",
                    population_count=1,
                )
            )

            pair_people_by_settlement_then_region(
                ctx, START_YEAR, ctx.current_people_by_settlement()
            )

            self.assertEqual(len(ctx.couples), 1)
            spouse_id = next(pid for pid in ctx.couples[0] if pid != detailed.person_id)
            self.assertIn(spouse_id, ctx.current_people_ids)
            self.assertEqual(ctx.id_to_record[spouse_id].person.gender, "Female")
            self.assertEqual(ctx.passive_cohorts[0].population_count, 0)
            self.assertTrue(
                any(
                    event_type == "passive_person_promoted"
                    and payload.get("reason") == "marriage_into_detailed_family"
                    for _, event_type, payload in ctx._pending_simulation_events
                )
            )

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
