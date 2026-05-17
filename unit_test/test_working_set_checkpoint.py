"""Archival sqlite upsert vs bounded RAM working set."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from library.config_import import load_all_csvs_into_sqlite
from library.generator import generate_person_random
from library.simulation_context import SimulationContext
from library.world_save import checkpoint_simulation_to_save
from library.world_time import set_world_current_year


class TestWorkingSetCheckpoint(unittest.TestCase):
    def test_ancient_dead_row_stays_in_sqlite_not_in_ram(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="ws",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
                working_set_dead_retention_years=20,
            ) as ctx:
                p_live = generate_person_random(simulation_context=ctx, simulation_year=1000)
                p_dead = generate_person_random(simulation_context=ctx, simulation_year=1000)
                lr = ctx.add_person(person=p_live, is_founder=True)
                dr = ctx.add_person(person=p_dead, is_founder=True)
                ctx.mark_dead({dr.person_id}, deathyear=1000)
                ctx.current_year = 1030
                set_world_current_year(
                    current_year=1030,
                    config_db_path=cfg,
                    save_db_path=sav,
                    world="default",
                )

                checkpoint_simulation_to_save(ctx)

                with sqlite3.connect(sav) as conn:
                    n_db = conn.execute(
                        "SELECT COUNT(*) FROM simulation_people",
                    ).fetchone()[0]

                self.assertEqual(int(n_db), 2)
                self.assertEqual(len(ctx.people), 1)
                self.assertEqual(ctx.people[0].person_id, lr.person_id)
                alive_ids_in_ram = {r.person_id for r in ctx.people}
                self.assertNotIn(dr.person_id, alive_ids_in_ram)

            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="ws2",
                world="default",
                start_year=None,
                refresh_config=False,
                flush_run_store=False,
                working_set_dead_retention_years=20,
            ) as ctx2:
                self.assertEqual(len(ctx2.people), 1)
                self.assertEqual(ctx2.people[0].person_id, lr.person_id)


if __name__ == "__main__":
    unittest.main()
