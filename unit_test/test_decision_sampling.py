"""Deterministic capped samples for settlement/regional decision drivers."""

from __future__ import annotations

import unittest
from pathlib import Path

from library.person import Person
from library.settlements import SettlementState
from library.simulation_context import SimulationContext, SimulationPersonRecord


def _make_context(*, cap: int, n: int) -> SimulationContext:
    rid = "sample_region"
    sid = f"{rid}:s1"
    ctx = SimulationContext(
        db_path=Path("unused_config.sqlite"),
        save_db_path=Path("unused_save.sqlite"),
        world="default",
        simulation_start_year=1000,
        current_year=1000,
        placename_rng_salt=12345,
        decision_sample_size=cap,
    )
    ctx.settlements_by_id = {
        sid: SettlementState(region_id=rid, settlement_id=sid)
    }
    ctx.settlement_ids_by_region = {rid: [sid]}
    records: list[SimulationPersonRecord] = []
    for pid in range(1, n + 1):
        p = Person(
            first_name=f"P{pid}",
            last_name="Sample",
            gender="Female" if pid % 2 else "Male",
            ethnic="Fixture",
            species="Human",
            birthyear=980,
            birthplace_region_id=rid,
            birthplace_settlement_id=sid,
            current_settlement_id=sid,
        )
        records.append(SimulationPersonRecord(person_id=pid, person=p, is_founder=False))
    ctx.people = records
    ctx.id_to_record = {rec.person_id: rec for rec in records}
    ctx.current_people_ids = {rec.person_id for rec in records}
    return ctx


class TestDecisionSampling(unittest.TestCase):
    def test_small_region_uses_full_population(self) -> None:
        ctx = _make_context(cap=1000, n=12)
        sample = ctx.decision_sample_people_in_region(
            "sample_region",
            year=1000,
            stream=1,
        )
        self.assertEqual([r.person_id for r in sample], list(range(1, 13)))

    def test_large_region_is_capped_and_deterministic(self) -> None:
        ctx = _make_context(cap=5, n=20)
        first = ctx.decision_sample_people_in_region(
            "sample_region",
            year=1000,
            stream=77,
        )
        second = ctx.decision_sample_people_in_region(
            "sample_region",
            year=1000,
            stream=77,
        )
        self.assertEqual(len(first), 5)
        self.assertEqual([r.person_id for r in first], [r.person_id for r in second])
        self.assertEqual([r.person_id for r in first], sorted(r.person_id for r in first))


if __name__ == "__main__":
    unittest.main()
