"""Focused incident-generation helper regressions."""

from __future__ import annotations

import random
import unittest
from dataclasses import dataclass
from types import SimpleNamespace

from library.detailed_population_variance import apply_detailed_selection_variance
from library.event_scoring import threshold_excess_weights
from library.event_scoring import serial_predator_propensity
from library.person import Person
from library.simulation_incidents import (
    MURDER_PROPENSITY_THRESHOLD,
    MURDER_REPEAT_KILLER_SELECTION_MULTIPLIER_CAP,
    MURDER_SETTLEMENT_SAMPLE_CAP,
    _genome_signal_payload,
    _previous_murder_counts_by_killer,
    _repeat_murder_selection_multiplier,
    _weighted_choice,
)

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


@dataclass
class PersonStub:
    genome: dict[str, float]


@dataclass
class RecordStub:
    person_id: int
    person: PersonStub


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


class TestSimulationIncidentHelpers(unittest.TestCase):
    def test_genome_signal_payload_returns_selected_trait_values(self) -> None:
        rec = RecordStub(1, PersonStub({"justice": -91.234, "empathy": 12.345}))

        payload = _genome_signal_payload(rec, ("justice", "missing"))

        self.assertEqual(payload, {"justice": -91.234})

    def test_previous_murder_counts_include_pending_prior_years_only(self) -> None:
        ctx = SimpleNamespace(
            save_db_path=None,
            _pending_simulation_events=[
                (1000, "murder", {"killer_person_id": 7}),
                (1001, "murder", {"killer_person_id": 7}),
                (1002, "murder", {"killer_person_id": 7}),
                (1000, "property_crime", {"perpetrator_person_id": 7}),
            ],
        )

        counts = _previous_murder_counts_by_killer(ctx, {7, 8}, before_year=1002)

        self.assertEqual(counts[7], 2)
        self.assertEqual(counts[8], 0)

    def test_repeat_murder_selection_multiplier_is_bounded_and_meaningful(self) -> None:
        ordinary = _repeat_murder_selection_multiplier(
            serial_propensity=0.10,
            previous_murders=0,
        )
        repeat_capable = _repeat_murder_selection_multiplier(
            serial_propensity=0.72,
            previous_murders=2,
        )
        capped = _repeat_murder_selection_multiplier(
            serial_propensity=1.0,
            previous_murders=20,
        )

        self.assertEqual(ordinary, 1.0)
        self.assertGreater(repeat_capable, 1.75)
        self.assertLessEqual(repeat_capable, MURDER_REPEAT_KILLER_SELECTION_MULTIPLIER_CAP)
        self.assertEqual(capped, MURDER_REPEAT_KILLER_SELECTION_MULTIPLIER_CAP)

    def test_repeat_murder_cap_keeps_single_candidate_under_guardrail(self) -> None:
        capped_repeat_weight = _repeat_murder_selection_multiplier(
            serial_propensity=1.0,
            previous_murders=20,
        )
        ordinary_count = int(MURDER_SETTLEMENT_SAMPLE_CAP) - 1
        capped_share = capped_repeat_weight / (ordinary_count + capped_repeat_weight)

        self.assertLess(capped_share, 0.01)
        self.assertGreater(capped_repeat_weight, 2.0)

    def test_repeat_murder_cap_supports_rare_emergence_at_large_sample(self) -> None:
        capped_repeat_weight = _repeat_murder_selection_multiplier(
            serial_propensity=1.0,
            previous_murders=20,
        )
        ordinary_count = int(MURDER_SETTLEMENT_SAMPLE_CAP) - 1
        capped_share = capped_repeat_weight / (ordinary_count + capped_repeat_weight)
        expected_murders_in_emergence_sample = capped_share * 500.0

        self.assertLess(capped_share, 0.01)
        self.assertGreaterEqual(expected_murders_in_emergence_sample, 3.0)

    def test_weighted_killer_selection_can_emerge_within_serial_guardrail(self) -> None:
        records = [
            SimpleNamespace(person_id=i)
            for i in range(int(MURDER_SETTLEMENT_SAMPLE_CAP))
        ]
        propensities = {int(rec.person_id): 0.50 for rec in records}
        base_weights = threshold_excess_weights(
            records,
            propensities,
            MURDER_PROPENSITY_THRESHOLD,
        )
        weights = [
            base_weight
            * _repeat_murder_selection_multiplier(
                serial_propensity=1.0 if int(rec.person_id) == 0 else 0.10,
                previous_murders=20 if int(rec.person_id) == 0 else 0,
            )
            for rec, base_weight in zip(records, base_weights)
        ]
        repeat_expected_share = float(weights[0]) / sum(float(w) for w in weights)

        rng = random.Random(1)
        repeat_killer_murders = sum(
            1
            for _ in range(500)
            if _weighted_choice(records, weights, rng).person_id == 0
        )

        self.assertLess(repeat_expected_share, 0.01)
        self.assertGreaterEqual(repeat_killer_murders, 3)
        self.assertLessEqual(repeat_killer_murders / 500.0, 0.01)

    def test_generated_detailed_profile_can_emerge_within_guardrail(self) -> None:
        baseline = _person({trait: 18.0 for trait in _TRAITS})
        repeat_capable = apply_detailed_selection_variance(
            baseline,
            person_id=1,
            year=1000,
            reason="founder",
            source={"source_kind": "founder", "settlement_id": "r1:s1"},
        )
        repeat_record = SimpleNamespace(person_id=0, person=repeat_capable)
        records = [repeat_record] + [
            SimpleNamespace(person_id=i, person=baseline)
            for i in range(1, int(MURDER_SETTLEMENT_SAMPLE_CAP))
        ]
        propensities = {int(rec.person_id): 0.50 for rec in records}
        base_weights = threshold_excess_weights(
            records,
            propensities,
            MURDER_PROPENSITY_THRESHOLD,
        )
        generated_serial_score = serial_predator_propensity(repeat_record)
        weights = [
            base_weight
            * _repeat_murder_selection_multiplier(
                serial_propensity=(
                    generated_serial_score if int(rec.person_id) == 0 else 0.10
                ),
                previous_murders=0,
            )
            for rec, base_weight in zip(records, base_weights)
        ]
        repeat_expected_share = float(weights[0]) / sum(float(w) for w in weights)

        rng = random.Random(1)
        repeat_killer_murders = sum(
            1
            for _ in range(500)
            if _weighted_choice(records, weights, rng).person_id == 0
        )

        self.assertGreaterEqual(generated_serial_score, 0.62)
        self.assertLess(repeat_expected_share, 0.01)
        self.assertGreaterEqual(repeat_killer_murders, 3)
        self.assertLessEqual(repeat_killer_murders / 500.0, 0.01)


if __name__ == "__main__":
    unittest.main()
