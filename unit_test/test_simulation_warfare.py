"""Smoke tests for ``library.simulation_warfare``."""

from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path

from library.config_import import load_all_csvs_into_sqlite
from library.generator import generate_person_random
from library.government_catalog import load_genome_composite_rows, load_government_catalog
from library.simulation_context import SimulationContext
from library.simulation_warfare import advance_campaigns


class TestSimulationWarfare(unittest.TestCase):
    def test_advance_campaigns_empty_list_noop(self) -> None:
        random.seed(3)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "c.sqlite"
            sav = root / "s.sqlite"
            load_all_csvs_into_sqlite(cfg)
            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="swar",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            ) as ctx:
                ctx.add_person(
                    person=generate_person_random(
                        simulation_context=ctx, simulation_year=1000
                    ),
                    is_founder=True,
                )
                self.assertEqual(ctx.gov_campaigns, [])
                catalog = load_government_catalog(ctx.db_path)
                rows = load_genome_composite_rows(ctx.db_path)
                advance_campaigns(
                    ctx, 1000, catalog=catalog, composite_rows=rows, rng=random.Random(0)
                )
                self.assertEqual(ctx.gov_campaigns, [])


if __name__ == "__main__":
    unittest.main()
