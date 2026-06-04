"""Tests for reusable event-scoring helpers."""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from library.event_scoring import (
    EventPropensitySpec,
    EventScoringContext,
    TraitFactor,
    eligible_records_by_threshold,
    ideal_strength,
    infer_role_tags,
    negative_extreme,
    positive_extreme,
    pressure_excess,
    property_crime_propensity,
    propensity_by_person_id,
    score_propensity,
    threshold_excess_weights,
    violent_actor_propensity,
)


@dataclass
class PersonStub:
    genome: dict[str, float]
    genome_composite_names: tuple[str, ...] = ()
    birthyear: int = 980
    partner_person_id: int | None = None
    unemployment_started_year: int | None = None
    job: str = ""


@dataclass
class RecordStub:
    person_id: int
    person: PersonStub


def _record(person_id: int, genome: dict[str, float], **person_kwargs: object) -> RecordStub:
    return RecordStub(person_id, PersonStub(genome=dict(genome), **person_kwargs))


class TestEventScoring(unittest.TestCase):
    def test_trait_basis_preserves_centered_signed_genome_semantics(self) -> None:
        rec = _record(1, {"justice": -95, "courage": 95, "empathy": 0})

        self.assertAlmostEqual(negative_extreme(rec, "justice"), 60.0 / 65.0)
        self.assertAlmostEqual(positive_extreme(rec, "courage"), 60.0 / 65.0)
        self.assertEqual(ideal_strength(rec, "empathy"), 1.0)
        self.assertEqual(negative_extreme(rec, "missing"), 0.0)

    def test_propensity_specs_accept_raw_records_and_context_tags(self) -> None:
        rec = _record(
            1,
            {"justice": -95},
            genome_composite_names=("Criminal Mastermind",),
            job="market trader",
            partner_person_id=2,
            unemployment_started_year=1000,
        )
        spec = EventPropensitySpec(
            key="fixture",
            risk_factors=(TraitFactor("justice", "negative_extreme", 0.20),),
            composite_weights={"criminal mastermind": 0.15},
            role_weights={"trader": 0.05, "spouse": 0.03, "unemployed": 0.04},
            pressure_weights={"scarcity": 0.07},
            opportunity_weights={"market_day": 0.06},
        )
        roles = infer_role_tags(rec, year=1010)
        ctx = EventScoringContext(
            role_tags=roles,
            pressure_tags=frozenset({"scarcity"}),
            opportunity_tags=frozenset({"market_day"}),
        )

        self.assertIn("trader", roles)
        self.assertIn("spouse", roles)
        self.assertIn("unemployed", roles)
        self.assertGreater(score_propensity(rec, spec, context=ctx), 0.55)

    def test_shared_propensity_functions_keep_vertical_slice_separation(self) -> None:
        violent = _record(
            1,
            {
                "justice": -95,
                "empathy": -95,
                "patience": -90,
                "temperance": -80,
                "courage": 90,
                "assertiveness": 90,
                "neurochemical": 90,
                "ambition": 85,
            },
        )
        stable = _record(2, {})
        property_actor = _record(
            3,
            {
                "justice": -95,
                "honesty": -95,
                "empathy": -75,
                "persuasion": 90,
                "ambition": 90,
                "frugality": 90,
            },
        )

        self.assertGreater(violent_actor_propensity(violent), 0.8)
        self.assertLess(violent_actor_propensity(stable), 0.05)
        self.assertGreater(property_crime_propensity(property_actor), 0.75)
        self.assertLess(property_crime_propensity(stable), 0.05)

    def test_shared_propensity_functions_accept_event_context(self) -> None:
        rec = _record(
            4,
            {},
            job="market trader",
            unemployment_started_year=1000,
        )
        ctx = EventScoringContext(
            role_tags=infer_role_tags(rec, year=1010),
            pressure_tags=frozenset({"scarcity", "debt"}),
            opportunity_tags=frozenset({"market_day", "storehouse_access"}),
        )

        self.assertGreater(
            property_crime_propensity(rec, context=ctx),
            property_crime_propensity(rec),
        )

    def test_candidate_threshold_helpers_are_deterministic(self) -> None:
        records = [_record(1, {}), _record(2, {}), _record(3, {})]
        scores = {1: 0.1, 2: 0.3, 3: 0.5}

        eligible = eligible_records_by_threshold(records, scores, 0.25)
        self.assertEqual([rec.person_id for rec in eligible], [2, 3])
        weights = threshold_excess_weights(eligible, scores, 0.25)
        self.assertAlmostEqual(weights[0], 0.0025)
        self.assertAlmostEqual(weights[1], 0.0625)
        self.assertEqual(
            propensity_by_person_id(records, lambda rec: scores[int(rec.person_id)]),
            scores,
        )
        self.assertAlmostEqual(pressure_excess(1.25, 0.75, 0.5), 1.0)


if __name__ == "__main__":
    unittest.main()
