from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

if "numpy" not in sys.modules and importlib.util.find_spec("numpy") is None:
    class _NumpyRandomStub:
        def seed(self, *_):
            return None

        def normal(self, loc=0.0, scale=1.0, size=None):
            if size is None:
                return float(loc)
            return [float(loc)] * int(size)

    sys.modules["numpy"] = types.SimpleNamespace(
        random=_NumpyRandomStub(),
        clip=lambda value, lo, hi: max(lo, min(hi, value)),
    )

from library.person import Person
from library.simulation_social import (
    PARAMOUR_CONTACT_TRIAL_CAP,
    PARAMOUR_EXHAUSTIVE_PAIR_LIMIT,
    _paramour_contact_trial_budget,
    _paramour_end_probability,
    _paramour_orientation_multiplier,
    _paramour_pair_probability,
    _partner_breakup_probability,
    maybe_end_paramour_relationships,
    maybe_dissolve_partner_couples,
    maybe_promote_paramours_to_partners,
)


def _person(
    pid: int,
    traits: dict[str, float],
    *,
    partner_id: int | None = None,
    paramour_id: int | None = None,
    sexual_nature: str = "heterosexual",
) -> Person:
    base_traits = {
        "mating drive": 0.0,
        "loyalty": 0.0,
        "neurochemical": 0.0,
        "empathy": 0.0,
        "honesty": 0.0,
        "patience": 0.0,
        "assertiveness": 0.0,
        "temperance": 0.0,
    }
    base_traits.update(traits)
    return Person(
        first_name=f"P{pid}",
        last_name="Test",
        gender="Female" if pid % 2 else "Male",
        ethnic="Human",
        species="Human",
        birthyear=970,
        partner_person_id=partner_id,
        paramour_person_id=paramour_id,
        genome=dict(base_traits),
        mind_body=dict(base_traits),
        sexual_nature=sexual_nature,
        attractiveness_01=0.75,
    )


def _rec(pid: int, person: Person):
    return SimpleNamespace(person_id=pid, person=person, father_id=None, mother_id=None)


class _FakeCtx:
    def __init__(self, a, b, *extra) -> None:
        self.current_year = 1000
        self.placename_rng_salt = 123
        self.couples = []
        records = (a, b, *extra)
        self.id_to_record = {rec.person_id: rec for rec in records}
        self.current_people_ids = {rec.person_id for rec in records}
        for rec in records:
            partner_id = rec.person.partner_person_id
            if partner_id is None:
                continue
            pair = tuple(sorted((int(rec.person_id), int(partner_id))))
            if pair not in self.couples:
                self.couples.append(pair)
        self.paramours = []
        for rec in records:
            paramour_id = rec.person.paramour_person_id
            if paramour_id is None:
                continue
            pair = tuple(sorted((int(rec.person_id), int(paramour_id))))
            if pair not in self.paramours:
                self.paramours.append(pair)
        self.settlements_by_id = {}
        self._pending_simulation_events = []

    def _residence_region_id(self, _rec) -> None:
        return None

    def effective_regional_population_cap(self, _region_id: str) -> int:
        return 1

    def count_alive_in_region(self, _region_id: str) -> int:
        return 0

    def dissolve_couple(self, person_a_id: int, person_b_id: int) -> None:
        pair = set((person_a_id, person_b_id))
        self.couples = [(a, b) for (a, b) in self.couples if {a, b} != pair]
        self.id_to_record[person_a_id].person = replace(
            self.id_to_record[person_a_id].person, partner_person_id=None
        )
        self.id_to_record[person_b_id].person = replace(
            self.id_to_record[person_b_id].person, partner_person_id=None
        )
        self._pending_simulation_events.append(
            (
                self.current_year,
                "couple_dissolved",
                {
                    "person_a_id": person_a_id,
                    "person_b_id": person_b_id,
                },
            )
        )

    def end_paramour_relationship(self, person_a_id: int, person_b_id: int) -> None:
        pair = set((person_a_id, person_b_id))
        self.paramours = [(a, b) for (a, b) in self.paramours if {a, b} != pair]
        self.id_to_record[person_a_id].person = replace(
            self.id_to_record[person_a_id].person, paramour_person_id=None
        )
        self.id_to_record[person_b_id].person = replace(
            self.id_to_record[person_b_id].person, paramour_person_id=None
        )
        self._pending_simulation_events.append(
            (
                self.current_year,
                "paramour_ended",
                {
                    "person_a_id": person_a_id,
                    "person_b_id": person_b_id,
                },
            )
        )

    def add_couple(self, person_a_id: int, person_b_id: int) -> None:
        self.couples.append(tuple(sorted((person_a_id, person_b_id))))
        self.id_to_record[person_a_id].person = replace(
            self.id_to_record[person_a_id].person, partner_person_id=person_b_id
        )
        self.id_to_record[person_b_id].person = replace(
            self.id_to_record[person_b_id].person, partner_person_id=person_a_id
        )
        self._pending_simulation_events.append(
            (
                self.current_year,
                "couple_formed",
                {
                    "person_a_id": person_a_id,
                    "person_b_id": person_b_id,
                },
            )
        )

    def _record_simulation_event(self, year, event_type, payload) -> None:
        self._pending_simulation_events.append((year, event_type, payload))


class TestSimulationSocialBreakups(unittest.TestCase):
    def test_paramour_contact_budget_is_exhaustive_only_for_small_pair_sets(self) -> None:
        self.assertEqual(_paramour_contact_trial_budget(2), 1)
        # 90 residents -> 4,005 possible pairs, just over the exhaustive threshold.
        self.assertLess(_paramour_contact_trial_budget(90), 90 * 89 // 2)
        self.assertGreaterEqual(PARAMOUR_EXHAUSTIVE_PAIR_LIMIT, 1)

    def test_paramour_contact_budget_caps_large_settlements(self) -> None:
        budget = _paramour_contact_trial_budget(100_000)
        self.assertLessEqual(budget, PARAMOUR_CONTACT_TRIAL_CAP)
        self.assertLess(budget, 100_000 * 99_999 // 2)

    def test_paramour_probability_uses_mating_drive_and_loyalty(self) -> None:
        restrained = _person(1, {"mating drive": -90.0, "loyalty": 0.0})
        tempted = _person(2, {"mating drive": 90.0, "loyalty": -90.0})
        neutral = _person(3, {})

        self.assertLess(
            _paramour_pair_probability(restrained, neutral),
            _paramour_pair_probability(tempted, neutral),
        )

    def test_same_sex_homosexual_paramour_probability_rises_when_married(self) -> None:
        married_homosexual = _rec(
            1,
            _person(1, {}, partner_id=4, sexual_nature="homosexual"),
        )
        same_sex_homosexual = _rec(3, _person(3, {}, sexual_nature="homosexual"))
        spouse = _rec(4, _person(4, {}, partner_id=1))
        ctx = _FakeCtx(married_homosexual, same_sex_homosexual, spouse)
        bisexual_a = _person(5, {}, sexual_nature="bisexual")
        bisexual_b = _person(7, {}, sexual_nature="bisexual")

        self.assertGreater(
            _paramour_pair_probability(
                married_homosexual.person,
                same_sex_homosexual.person,
                ctx,
            ),
            _paramour_pair_probability(bisexual_a, bisexual_b),
        )
        self.assertGreater(
            _paramour_orientation_multiplier(
                ctx, married_homosexual.person, same_sex_homosexual.person
            ),
            1.0,
        )

    def test_paramour_end_probability_records_social_reasons(self) -> None:
        a = _rec(1, _person(1, {"mating drive": -90.0, "loyalty": 0.0}, paramour_id=2))
        b = _rec(2, _person(2, {"mating drive": -90.0}, paramour_id=1))
        ctx = _FakeCtx(a, b)

        p, reasons = _paramour_end_probability(ctx, a, b, 1000)

        self.assertGreater(p, 0.0)
        self.assertIn("waning_desire", reasons)

    def test_maybe_end_paramour_relationship_records_reasons(self) -> None:
        a = _rec(1, _person(1, {"mating drive": -90.0}, paramour_id=2))
        b = _rec(2, _person(2, {"mating drive": -90.0}, paramour_id=1))
        ctx = _FakeCtx(a, b)

        with patch("library.simulation_social._paramour_end_rng") as rng_factory:
            rng_factory.return_value.random.return_value = 0.0
            maybe_end_paramour_relationships(ctx, 1000)

        self.assertEqual(ctx.paramours, [])
        payload = ctx._pending_simulation_events[-1][2]
        self.assertIn("end_probability", payload)
        self.assertIn("end_reasons", payload)

    def test_paramour_can_become_partner_and_leave_existing_partner(self) -> None:
        a = _rec(
            1,
            _person(
                1,
                {"loyalty": -80.0, "neurochemical": 0.0, "patience": 0.0},
                partner_id=4,
                paramour_id=2,
            ),
        )
        b = _rec(2, _person(2, {"neurochemical": 0.0, "patience": 0.0}, paramour_id=1))
        spouse = _rec(4, _person(4, {}, partner_id=1))
        ctx = _FakeCtx(a, b, spouse)

        with patch("library.simulation_social._paramour_promotion_rng") as rng_factory:
            rng_factory.return_value.random.return_value = 0.0
            maybe_promote_paramours_to_partners(ctx, 1000)

        self.assertEqual(ctx.id_to_record[1].person.partner_person_id, 2)
        self.assertEqual(ctx.id_to_record[2].person.partner_person_id, 1)
        self.assertIsNone(ctx.id_to_record[4].person.partner_person_id)
        self.assertEqual(ctx.paramours, [])
        payload = ctx._pending_simulation_events[-1][2]
        self.assertEqual(payload["partnership_motive"], "paramour_became_partner")

    def test_partner_breakup_probability_rises_with_stressors(self) -> None:
        calm_a = _rec(1, _person(1, {}, partner_id=2))
        calm_b = _rec(2, _person(2, {}, partner_id=1))
        stressed_a = _rec(
            1,
            replace(
                _person(
                    1,
                    {
                        "neurochemical": 90.0,
                        "loyalty": -85.0,
                        "empathy": -80.0,
                        "honesty": -75.0,
                    },
                    partner_id=2,
                ),
                paramour_person_id=3,
                household_prosperity=0.05,
            ),
        )
        stressed_b = _rec(2, _person(2, {}, partner_id=1))

        calm_p, _ = _partner_breakup_probability(_FakeCtx(calm_a, calm_b), calm_a, calm_b)
        stressed_p, reasons = _partner_breakup_probability(
            _FakeCtx(stressed_a, stressed_b), stressed_a, stressed_b
        )

        self.assertGreater(stressed_p, calm_p)
        self.assertIn("paramour", reasons)
        self.assertIn("mental_instability", reasons)

    def test_maybe_dissolve_partner_couples_records_reasons(self) -> None:
        a = _rec(
            1,
            replace(
                _person(1, {"neurochemical": 90.0, "loyalty": -90.0}, partner_id=2),
                paramour_person_id=3,
            ),
        )
        b = _rec(2, _person(2, {}, partner_id=1))
        ctx = _FakeCtx(a, b)

        with patch("library.simulation_social._partner_breakup_rng") as rng_factory:
            rng_factory.return_value.random.return_value = 0.0
            maybe_dissolve_partner_couples(ctx, 1000)

        self.assertEqual(ctx.couples, [])
        self.assertIsNone(ctx.id_to_record[1].person.partner_person_id)
        payload = ctx._pending_simulation_events[-1][2]
        self.assertIn("breakup_probability", payload)
        self.assertIn("paramour", payload["breakup_reasons"])


if __name__ == "__main__":
    unittest.main()
