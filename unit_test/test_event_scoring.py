"""Tests for reusable event-scoring helpers."""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from library.event_scoring import (
    EventPropensitySpec,
    EventScoringContext,
    TraitFactor,
    composite_score,
    contextual_propensity_by_person_id,
    eligible_records_by_threshold,
    ideal_strength,
    infer_role_tags,
    moral_friction_relief,
    negative_extreme,
    positive_extreme,
    pressure_excess,
    political_crime_propensity,
    private_life_seed_propensity,
    property_crime_propensity,
    propensity_by_person_id,
    religious_cultural_conflict_propensity,
    score_composite_scores,
    score_propensity,
    serial_killer_composite_pressure,
    serial_predation_risk,
    serial_predator_propensity,
    threshold_excess_weights,
    violent_actor_propensity,
)


@dataclass
class PersonStub:
    genome: dict[str, float]
    genome_composite_names: tuple[str, ...] = ()
    genome_composite_scores: dict[str, float] | None = None
    birthyear: int = 980
    partner_person_id: int | None = None
    unemployment_started_year: int | None = None
    job: str = ""
    birthplace_region_id: str | None = None
    birthplace_settlement_id: str | None = None
    current_settlement_id: str | None = None
    household_purseholder_person_id: int | None = None


@dataclass
class RecordStub:
    person_id: int
    person: PersonStub


@dataclass
class CareIndexesStub:
    children_by_parent: dict[int, frozenset[int]]


def _record(person_id: int, genome: dict[str, float], **person_kwargs: object) -> RecordStub:
    return RecordStub(person_id, PersonStub(genome=dict(genome), **person_kwargs))


class TestEventScoring(unittest.TestCase):
    def test_trait_basis_preserves_centered_signed_genome_semantics(self) -> None:
        rec = _record(1, {"justice": -95, "courage": 95, "empathy": 0})
        ordinary = _record(2, {"justice": -50, "courage": 50, "empathy": 50})

        self.assertAlmostEqual(negative_extreme(rec, "justice"), 1.0)
        self.assertAlmostEqual(positive_extreme(rec, "courage"), 1.0)
        self.assertEqual(ideal_strength(rec, "empathy"), 1.0)
        self.assertEqual(negative_extreme(ordinary, "justice"), 0.0)
        self.assertEqual(positive_extreme(ordinary, "courage"), 0.0)
        self.assertEqual(ideal_strength(ordinary, "empathy"), 0.0)
        self.assertEqual(negative_extreme(rec, "missing"), 0.0)
        self.assertEqual(ideal_strength(rec, "missing"), 0.0)

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

    def test_numeric_composite_weights_and_moral_friction_relief(self) -> None:
        rec = _record(
            1,
            {},
            genome_composite_scores={
                "psychopathy": 1.2,
                "good_done_desire": 1.0,
                "honest_work_desire": 1.0,
            },
        )
        spec = EventPropensitySpec(
            key="fixture",
            composite_score_risk_weights={"psychopathy": 0.40},
            composite_score_protective_weights={
                "good_done_desire": 0.40,
                "honest_work_desire": 0.20,
            },
            protective_cap=0.80,
        )
        pressure = EventScoringContext(
            pressure_tags=frozenset({"scarcity", "debt", "survival_need"}),
            resource_pressure=1.25,
        )

        self.assertEqual(composite_score(rec, "missing"), 0.0)
        self.assertAlmostEqual(composite_score(rec, "psychopathy"), 1.2)
        self.assertAlmostEqual(
            score_composite_scores(rec, {"psychopathy": 0.25}), 0.30
        )
        self.assertGreater(moral_friction_relief(pressure), 0.40)
        self.assertGreater(
            score_propensity(rec, spec, context=pressure),
            score_propensity(rec, spec),
        )

    def test_role_inference_accepts_cached_family_and_office_context(self) -> None:
        rec = _record(
            5,
            {},
            birthyear=940,
            partner_person_id=7,
            job="heir guard",
            birthplace_settlement_id="old_hill",
            current_settlement_id="new_port",
            household_purseholder_person_id=5,
        )

        roles = infer_role_tags(
            rec,
            year=1010,
            care_indexes=CareIndexesStub({5: frozenset({9, 10})}),
            office_holder_ids={5},
        )

        self.assertIn("elder", roles)
        self.assertIn("spouse", roles)
        self.assertIn("parent", roles)
        self.assertIn("title_holder", roles)
        self.assertIn("heir", roles)
        self.assertIn("soldier", roles)
        self.assertIn("household_head", roles)
        self.assertIn("migrant", roles)

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

    def test_serial_predation_risk_rejects_low_control_low_psychopathy_example(self) -> None:
        example = _record(
            20,
            {},
            genome_composite_scores={
                "insanity": 0.65,
                "evil_done_desire": 0.64,
                "ruthless_ambition": 0.61,
                "lie_or_cheat_willingness": 0.57,
                "psychopathy": 0.34,
                "disguise_motive": 0.22,
                "practical_intellect": 0.08,
                "convince_people": 0.17,
                "force_get_way_desire": 0.50,
                "revenge_desire": 0.25,
                "enrich_self_desire": 0.25,
            },
        )

        risk = serial_predation_risk(example)

        self.assertFalse(risk.eligible)
        self.assertEqual(risk.risk_lane, "none")
        self.assertIn("harm_drive_below_gate", risk.rejection_reasons)
        self.assertIn("lane_gate_not_met", risk.rejection_reasons)

    def test_serial_predation_risk_hard_gates_reject_good_or_honest_work(self) -> None:
        base_scores = {
            "evil_done_desire": 0.95,
            "psychopathy": 0.95,
            "force_get_way_desire": 0.90,
            "ruthless_ambition": 0.90,
            "revenge_desire": 0.70,
            "enrich_self_desire": 0.60,
            "lie_or_cheat_willingness": 0.90,
            "disguise_motive": 0.90,
            "practical_intellect": 0.90,
            "convince_people": 0.90,
            "isolation_preference": 0.80,
        }
        good = _record(
            21,
            {},
            genome_composite_scores={**base_scores, "good_done_desire": 0.35},
        )
        honest = _record(
            22,
            {},
            genome_composite_scores={**base_scores, "honest_work_desire": 0.40},
        )

        good_risk = serial_predation_risk(good)
        honest_risk = serial_predation_risk(honest)

        self.assertFalse(good_risk.eligible)
        self.assertIn("good_done_desire_gate", good_risk.rejection_reasons)
        self.assertFalse(honest_risk.eligible)
        self.assertIn("honest_work_desire_gate", honest_risk.rejection_reasons)

    def test_serial_predation_risk_reports_organized_and_disorganized_lanes(self) -> None:
        organized = _record(
            23,
            {},
            genome_composite_scores={
                "evil_done_desire": 0.92,
                "psychopathy": 0.90,
                "force_get_way_desire": 0.86,
                "ruthless_ambition": 0.86,
                "revenge_desire": 0.65,
                "enrich_self_desire": 0.45,
                "lie_or_cheat_willingness": 0.85,
                "disguise_motive": 0.85,
                "practical_intellect": 0.80,
                "convince_people": 0.75,
                "isolation_preference": 0.70,
                "good_done_desire": 0.0,
                "honest_work_desire": 0.0,
                "make_friends": 0.0,
                "insanity": 0.15,
                "make_enemies": 0.05,
            },
        )
        disorganized = _record(
            24,
            {},
            genome_composite_scores={
                "evil_done_desire": 0.95,
                "psychopathy": 0.20,
                "force_get_way_desire": 0.92,
                "ruthless_ambition": 0.82,
                "revenge_desire": 0.82,
                "enrich_self_desire": 0.82,
                "insanity": 0.90,
                "good_done_desire": 0.0,
                "honest_work_desire": 0.0,
                "make_friends": 0.0,
            },
        )

        organized_risk = serial_predation_risk(organized)
        disorganized_risk = serial_predation_risk(disorganized)

        self.assertTrue(organized_risk.eligible)
        self.assertEqual(organized_risk.risk_lane, "organized")
        self.assertGreater(organized_risk.risk_score, 0.0)
        self.assertTrue(disorganized_risk.eligible)
        self.assertEqual(disorganized_risk.risk_lane, "disorganized")
        self.assertGreater(disorganized_risk.risk_score, 0.0)

    def test_serial_predator_propensity_compatibility_ignores_prior_murders(self) -> None:
        ordinary = _record(1, {trait: 0.0 for trait in ("empathy", "justice", "honesty")})
        extreme = _record(
            2,
            {},
            genome_composite_scores={
                "evil_done_desire": 0.95,
                "psychopathy": 0.95,
                "force_get_way_desire": 0.90,
                "ruthless_ambition": 0.90,
                "revenge_desire": 0.70,
                "enrich_self_desire": 0.60,
                "lie_or_cheat_willingness": 0.90,
                "disguise_motive": 0.90,
                "practical_intellect": 0.90,
                "convince_people": 0.90,
                "isolation_preference": 0.80,
            },
        )
        context = EventScoringContext(
            pressure_tags=frozenset({"scarcity"}),
            opportunity_tags=frozenset({"isolated", "privacy"}),
        )

        self.assertLess(serial_predator_propensity(ordinary), 0.05)
        self.assertGreater(serial_predator_propensity(extreme, context=context), 0.0)
        self.assertEqual(
            serial_predator_propensity(extreme, context=context, previous_murders=2),
            serial_predator_propensity(extreme, context=context),
        )

    def test_serial_composite_pressure_requires_multiple_aligned_scores(self) -> None:
        one_loud_score = _record(
            30,
            {},
            genome_composite_scores={"psychopathy": 1.0},
        )
        aligned = _record(
            31,
            {},
            genome_composite_scores={
                "psychopathy": 0.9,
                "force_get_way_desire": 0.85,
                "disguise_motive": 0.8,
                "isolation_preference": 0.75,
                "evil_done_desire": 0.8,
                "lie_or_cheat_willingness": 0.75,
                "ruthless_ambition": 0.85,
                "practical_intellect": 0.75,
                "convince_people": 0.75,
            },
        )

        self.assertLess(serial_killer_composite_pressure(one_loud_score), 0.10)
        self.assertGreater(serial_killer_composite_pressure(aligned), 0.70)
        self.assertGreater(
            serial_predator_propensity(aligned),
            serial_predator_propensity(one_loud_score),
        )

    def test_good_composites_suppress_neutral_context_violence_not_pressure(self) -> None:
        conflicted = _record(
            32,
            {},
            genome_composite_scores={
                "psychopathy": 0.55,
                "force_get_way_desire": 0.55,
                "revenge_desire": 0.45,
                "good_done_desire": 0.9,
                "honest_work_desire": 0.8,
            },
        )
        pressure = EventScoringContext(
            pressure_tags=frozenset({"relationship_strain", "status_fall", "war"}),
            opportunity_tags=frozenset({"isolated"}),
            resource_pressure=1.30,
        )

        self.assertLess(violent_actor_propensity(conflicted), 0.20)
        self.assertGreater(
            violent_actor_propensity(conflicted, context=pressure),
            violent_actor_propensity(conflicted),
        )

    def test_new_workstream_propensity_specs_cover_future_event_families(self) -> None:
        political_actor = _record(
            10,
            {
                "ambition": 95,
                "loyalty": -95,
                "justice": -90,
                "honesty": -90,
                "persuasion": 95,
                "discipline": 90,
                "courage": 85,
                "civics": -85,
            },
            genome_composite_names=("Legitimacy Seizer",),
            job="heir noble",
        )
        religious_actor = _record(
            11,
            {
                "justice": 95,
                "loyalty": 90,
                "civics": 90,
                "empathy": -95,
                "persuasion": 95,
                "creativity": 90,
                "discipline": 85,
                "courage": 80,
                "adaptability": -90,
            },
            genome_composite_names=("Fanatic", "Cult Leader"),
            job="village priest",
        )
        private_seed_actor = _record(
            12,
            {
                "patience": -95,
                "temperance": -90,
                "justice": 90,
                "empathy": -80,
                "loyalty": -80,
                "honesty": -95,
                "persuasion": 95,
                "perception": 90,
                "ambition": 90,
                "neurochemical": 90,
                "humility": 90,
            },
            genome_composite_names=("Hidden Manipulator",),
            partner_person_id=13,
        )
        stable = _record(13, {})

        political_context = EventScoringContext(
            role_tags=infer_role_tags(
                political_actor,
                year=1010,
                office_holder_ids={10},
            ),
            pressure_tags=frozenset({"succession_crisis", "office_tension"}),
            opportunity_tags=frozenset({"court", "office_access", "faction_network"}),
        )
        religious_context = EventScoringContext(
            role_tags=infer_role_tags(religious_actor, year=1010),
            pressure_tags=frozenset({"doctrine_tension", "social_stress"}),
            opportunity_tags=frozenset({"temple", "crowd"}),
        )
        private_context = EventScoringContext(
            role_tags=infer_role_tags(
                private_seed_actor,
                year=1010,
                parent_ids={12},
            ),
            pressure_tags=frozenset({"relationship_strain", "status_fall"}),
            opportunity_tags=frozenset({"shared_household", "privacy", "secret_access"}),
        )

        self.assertGreater(
            political_crime_propensity(political_actor, context=political_context),
            0.85,
        )
        self.assertLess(political_crime_propensity(stable), 0.05)
        self.assertGreater(
            religious_cultural_conflict_propensity(
                religious_actor, context=religious_context
            ),
            0.75,
        )
        self.assertLess(religious_cultural_conflict_propensity(stable), 0.05)
        self.assertGreater(
            private_life_seed_propensity(private_seed_actor, context=private_context),
            0.65,
        )
        self.assertLess(private_life_seed_propensity(stable), 0.15)

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

    def test_contextual_propensity_map_supports_future_bounded_candidate_pools(self) -> None:
        political_actor = _record(
            20,
            {
                "ambition": 95,
                "loyalty": -95,
                "justice": -90,
                "honesty": -85,
                "persuasion": 90,
                "discipline": 85,
            },
            genome_composite_names=("Legitimacy Seizer",),
            job="heir noble",
        )
        religious_actor = _record(
            21,
            {
                "justice": 95,
                "loyalty": 90,
                "civics": 90,
                "empathy": -90,
                "persuasion": 90,
                "creativity": 90,
                "adaptability": -90,
            },
            genome_composite_names=("Cult Leader",),
            job="priest",
        )
        private_actor = _record(
            22,
            {
                "patience": -95,
                "temperance": -85,
                "honesty": -90,
                "persuasion": 90,
                "perception": 85,
                "ambition": 85,
            },
            genome_composite_names=("Hidden Manipulator",),
            partner_person_id=23,
        )
        stable = _record(23, {})
        records = [political_actor, religious_actor, private_actor, stable]
        contexts = {
            20: EventScoringContext(
                role_tags=infer_role_tags(
                    political_actor, year=1010, office_holder_ids={20}
                ),
                pressure_tags=frozenset({"succession_crisis", "office_tension"}),
                opportunity_tags=frozenset({"court", "office_access"}),
            ),
            21: EventScoringContext(
                role_tags=infer_role_tags(religious_actor, year=1010),
                pressure_tags=frozenset({"doctrine_tension", "social_stress"}),
                opportunity_tags=frozenset({"temple", "crowd"}),
            ),
            22: EventScoringContext(
                role_tags=infer_role_tags(private_actor, year=1010),
                pressure_tags=frozenset({"relationship_strain"}),
                opportunity_tags=frozenset({"shared_household", "privacy"}),
            ),
            23: EventScoringContext(),
        }

        political_scores = contextual_propensity_by_person_id(
            records, political_crime_propensity, contexts
        )
        religious_scores = contextual_propensity_by_person_id(
            records, religious_cultural_conflict_propensity, contexts
        )
        private_scores = contextual_propensity_by_person_id(
            records, private_life_seed_propensity, contexts
        )

        self.assertEqual(
            [rec.person_id for rec in eligible_records_by_threshold(records, political_scores, 0.50)],
            [20],
        )
        self.assertEqual(
            [rec.person_id for rec in eligible_records_by_threshold(records, religious_scores, 0.45)],
            [21],
        )
        self.assertEqual(
            [rec.person_id for rec in eligible_records_by_threshold(records, private_scores, 0.35)],
            [22],
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
