"""Integration test: zero-point multi-colony bootstrap and ordered yearly ticks."""

from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path

from library.simulation_context import SimulationContext
from library.zero_point_colonies import simulate_calendar_year_ordered_settlements


class TestSimulationEngineZeroPoint(unittest.TestCase):
    def test_zero_point_three_colonies_reset_and_ordered_years(self) -> None:
        random.seed(42)
        start = 1000
        duration = 5
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            save_db = Path(td) / "test_save.sqlite"
            with SimulationContext.create(
                world_id="default",
                world="default",
                save_db_path=save_db,
                start_year=start,
                reset_world_for_test=True,
                zero_point_foundation=True,
                foundation_rng_seed=42,
                placename_rng_salt=42,
                flush_run_store=False,
                foundation_couples_per_colony=10,
            ) as ctx:
                self._run_zero_point_assertions(ctx, start, duration)

    def _run_zero_point_assertions(
        self, ctx: SimulationContext, start: int, duration: int
    ) -> None:

        order = ctx.foundation_colony_region_order
        self.assertIsNotNone(order)
        assert order is not None
        self.assertEqual(len(order), 3)
        self.assertEqual(len(ctx.settlements_by_id), 3)

        founders = sum(1 for r in ctx.people if r.is_founder)
        self.assertEqual(founders, 60)
        self.assertEqual(len(ctx.couples), 30)

        by_region: dict[str, set[tuple[str, str]]] = {}
        for rec in ctx.people:
            if not rec.is_founder:
                continue
            rid = (rec.person.birthplace_region_id or "").strip()
            self.assertIn(rid, order)
            by_region.setdefault(rid, set()).add((rec.person.species, rec.person.ethnic))

        for rid in order:
            self.assertEqual(len(by_region.get(rid, set())), 1, rid)

        for year in range(start, start + duration):
            simulate_calendar_year_ordered_settlements(
                ctx, year=year, colony_region_order=order
            )

        descendants = [r for r in ctx.people if not r.is_founder]
        self.assertGreater(len(descendants), 0)
        for rec in descendants:
            rid = (rec.person.birthplace_region_id or "").strip()
            self.assertIn(rid, order)


if __name__ == "__main__":
    unittest.main()
