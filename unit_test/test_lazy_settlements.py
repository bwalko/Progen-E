"""Lazy settlement creation, abandonment escalation, and re-establishment."""

from __future__ import annotations

import json
import random
import unittest

from library.config_import import refresh_world_config_from_csv
from library.settlements import (
    ABANDON_EMPTY_GRACE_YEARS,
    SettlementState,
    roll_abandon_this_year,
)


def setUpModule() -> None:
    refresh_world_config_from_csv("default")


class TestLazySettlements(unittest.TestCase):
    def test_roll_abandon_respects_grace(self) -> None:
        rng = random.Random(0)
        self.assertFalse(roll_abandon_this_year(ABANDON_EMPTY_GRACE_YEARS, rng))
        self.assertFalse(roll_abandon_this_year(ABANDON_EMPTY_GRACE_YEARS - 1, rng))

    def test_roll_abandon_saturates_to_certain(self) -> None:
        rng = random.Random(1)
        self.assertTrue(
            roll_abandon_this_year(ABANDON_EMPTY_GRACE_YEARS + 50, rng)
        )

    def test_reestablish_new_id_same_site_slot(self) -> None:
        from library.simulation_context import SimulationContext
        from pathlib import Path
        import tempfile
        from library.config_import import load_all_csvs_into_sqlite

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext(
                db_path=cfg,
                save_db_path=root / "save.sqlite",
                world="default",
                simulation_start_year=1000,
                history_equivalent_start_year=1000,
                current_year=1005,
            )
            try:
                old = SettlementState(
                    region_id="boreas_west",
                    settlement_id="boreas_west:s1",
                    site_slot=1,
                    status="abandoned",
                    abandoned_sim_year=1002,
                    display_name="Oldtown",
                    region_display_name="Boreas West",
                    local_geography_json="{}",
                )
                ctx.settlements_by_id["boreas_west:s1"] = old
                new = ctx.reestablish_from_abandoned(old)
                self.assertNotEqual(new.settlement_id, old.settlement_id)
                self.assertEqual(new.site_slot, 1)
                self.assertEqual(new.founded_sim_year, 1005)
                self.assertEqual(new.display_name, "Oldtown")
                self.assertEqual(new.status, "active")
            finally:
                ctx.finalize_run()

    def test_additional_settlements_share_multi_slot_region_geography(self) -> None:
        from library.simulation_context import SimulationContext
        from pathlib import Path
        import tempfile
        from library.config_import import load_all_csvs_into_sqlite

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext(
                db_path=cfg,
                save_db_path=root / "save.sqlite",
                world="default",
                simulation_start_year=1000,
                history_equivalent_start_year=1000,
                current_year=1005,
            )
            try:
                first = ctx.ensure_active_settlement_for_region("boreas_fjord_shore")
                second = ctx.create_additional_active_settlement("boreas_fjord_shore")
                third = ctx.create_additional_active_settlement("boreas_fjord_shore")

                self.assertEqual([first.site_slot, second.site_slot, third.site_slot], [1, 2, 3])
                shared_geo = first.local_geography_json
                self.assertEqual(second.local_geography_json, shared_geo)
                self.assertEqual(third.local_geography_json, shared_geo)

                data = json.loads(shared_geo or "{}")
                sites = data.get("settlements", [])
                self.assertEqual(len(sites), 3)
                coords = {(round(float(site["x"]), 6), round(float(site["y"]), 6)) for site in sites}
                self.assertEqual(len(coords), 3)
            finally:
                ctx.finalize_run()


if __name__ == "__main__":
    unittest.main()
