"""Detailed-person variance for hybrid population materialization."""

from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from library.config_import import load_all_csvs_into_sqlite
from library.detailed_population_variance import (
    HIGH_VARIANCE_DETAIL_COMPOSITE,
    apply_detailed_selection_variance,
    detailed_selection_profile,
    detail_variance_score,
)
from library.event_scoring import serial_predator_propensity
from library.passive_population import PassivePerson
from library.person import Person
from library.simulation_context import SimulationContext


_TRAITS = (
    "ambition",
    "assertiveness",
    "courage",
    "creativity",
    "curiosity",
    "discipline",
    "empathy",
    "focus",
    "frugality",
    "generosity",
    "honesty",
    "intellect",
    "justice",
    "loyalty",
    "mating drive",
    "neurochemical",
    "patience",
    "perception",
    "persuasion",
    "temperance",
    "wit",
)


def _person(genome: dict[str, float]) -> Person:
    return Person(
        first_name="Ada",
        last_name="Vale",
        gender="Female",
        ethnic="Test",
        species="Human",
        birthyear=970,
        genome=dict(genome),
        mind_body=dict(genome),
    )


class TestDetailedPopulationVariance(unittest.TestCase):
    def test_selection_profiles_classify_reason_specific_salience(self) -> None:
        self.assertEqual(
            detailed_selection_profile(
                "murder_case_spotlight",
                {"source_kind": "criminal_context"},
            ).key,
            "criminal_outlaw",
        )
        self.assertEqual(
            detailed_selection_profile(
                "migration_into_focal_settlement",
                {"settlement_id": "r1:s1"},
            ).key,
            "migrant_frontier",
        )
        self.assertEqual(
            detailed_selection_profile(
                "user_inspection",
                {"focus": "spotlight"},
            ).key,
            "spotlight",
        )
        self.assertGreater(
            detailed_selection_profile("narrative_spotlight").intensity,
            detailed_selection_profile("marriage_into_detailed_family").intensity,
        )

    def test_narrative_spotlight_promotion_increases_trait_variance(self) -> None:
        baseline = _person({trait: 45.0 for trait in _TRAITS})

        detailed = apply_detailed_selection_variance(
            baseline,
            person_id=101,
            year=1000,
            reason="narrative_spotlight",
            source={"focus": "spotlight", "source_kind": "criminal_context"},
        )

        self.assertGreater(detail_variance_score(detailed), detail_variance_score(baseline))
        self.assertIn(HIGH_VARIANCE_DETAIL_COMPOSITE, detailed.genome_composite_names)
        self.assertNotEqual(detailed.genome, baseline.genome)

    def test_variance_materialization_is_deterministic_for_same_selection(self) -> None:
        baseline = _person({trait: -42.0 for trait in _TRAITS})

        first = apply_detailed_selection_variance(
            baseline,
            person_id=202,
            year=1000,
            reason="office_selection",
            source={"polity_id": 7},
        )
        second = apply_detailed_selection_variance(
            baseline,
            person_id=202,
            year=1000,
            reason="office_selection",
            source={"polity_id": 7},
        )

        self.assertEqual(first.genome, second.genome)
        self.assertEqual(first.genome_composite_names, second.genome_composite_names)

    def test_founder_selection_receives_higher_variance_marker(self) -> None:
        baseline = _person({trait: 38.0 for trait in _TRAITS})

        founder = apply_detailed_selection_variance(
            baseline,
            person_id=0,
            year=1000,
            reason="founder",
            source={"source_kind": "founder", "settlement_id": "r1:s1"},
        )

        self.assertIn(HIGH_VARIANCE_DETAIL_COMPOSITE, founder.genome_composite_names)
        self.assertGreater(detail_variance_score(founder), detail_variance_score(baseline))

    def test_founder_selection_can_rarely_seed_repeat_capable_profiles(self) -> None:
        baseline = _person({trait: 18.0 for trait in _TRAITS})

        profiles = [
            apply_detailed_selection_variance(
                baseline,
                person_id=person_id,
                year=1000,
                reason="founder",
                source={"source_kind": "founder", "settlement_id": "r1:s1"},
            )
            for person_id in range(1, 501)
        ]
        serial_capable = [
            person
            for person in profiles
            if serial_predator_propensity(person) >= 0.62
        ]

        self.assertGreaterEqual(len(serial_capable), 1)
        self.assertLessEqual(len(serial_capable), 35)

    def test_passive_promotion_uses_detailed_variance_path(self) -> None:
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
                passive = ctx.add_passive_person(
                    PassivePerson(
                        name="Mira Vale",
                        birthyear=970,
                        gender="Female",
                        species="Human",
                        current_settlement_id=None,
                    )
                )
                rec = ctx.promote_passive_person(
                    passive.person_id,
                    year=1000,
                    reason="narrative_spotlight",
                    source={"focus": "spotlight"},
                )

                self.assertIn(
                    HIGH_VARIANCE_DETAIL_COMPOSITE,
                    rec.person.genome_composite_names,
                )
                self.assertGreater(detail_variance_score(rec.person), 0.0)


if __name__ == "__main__":
    unittest.main()
