"""100-year population growth simulation test.

Uses the shared :mod:`library.population_growth_runner` with isolated SQLite under
:class:`tempfile.TemporaryDirectory` so the default-world save stays untouched.

Founding scale is ``STARTING_COUPLES`` (default **10** couples, i.e. **20** founders)
passed through to :func:`library.population_growth_runner.run_population_growth_simulation`
as ``starting_couples``. Change the constant and this docstring together if you change
the default scenario size.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from library.config_import import load_all_csvs_into_sqlite
from library.population_growth_runner import (
    resolve_population_sim_seed,
    run_population_growth_simulation,
    write_population_growth_report_files,
)
from library.simulation_context import SimulationContext

# Canonical artifact paths (used by unittest and tooling that reads these files).
_OUTPUT_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = _OUTPUT_DIR / "population_growth_simulation_report.txt"
PLACES_GEO_PATH = _OUTPUT_DIR / "population_growth_simulation_places_geo.json"
PEOPLE_JSON_PATH = _OUTPUT_DIR / "population_growth_simulation_people.json"

START_YEAR = 1000
YEARS_TO_SIMULATE = 100
STARTING_COUPLES = 10

_SIM_SEED = resolve_population_sim_seed()


class TestPopulationGrowth100Years(unittest.TestCase):
    def test_population_growth_and_report(self) -> None:
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
                start_year=START_YEAR,
                placename_rng_salt=_SIM_SEED,
                refresh_config=False,
            ) as ctx:
                run_population_growth_simulation(
                    ctx,
                    sim_seed=_SIM_SEED,
                    start_year=START_YEAR,
                    duration_years=YEARS_TO_SIMULATE,
                    starting_couples=STARTING_COUPLES,
                )

            write_population_growth_report_files(
                ctx,
                sim_seed=_SIM_SEED,
                start_year=START_YEAR,
                duration_years=YEARS_TO_SIMULATE,
                output_path=OUTPUT_PATH,
                people_json_path=PEOPLE_JSON_PATH,
                places_geo_path=PLACES_GEO_PATH,
            )

            self.assertTrue(OUTPUT_PATH.exists())
            self.assertTrue(PEOPLE_JSON_PATH.exists())
            total_people_created = int(ctx.next_person_id) - 1
            self.assertGreater(total_people_created, STARTING_COUPLES * 2)


if __name__ == "__main__":
    unittest.main()
