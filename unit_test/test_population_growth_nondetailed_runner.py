"""Runner wiring for the non-detailed city-directory backend."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from library.config_import import load_all_csvs_into_sqlite
from library.nondetailed_population import (
    NondetailedEconomyResult,
    NondetailedMigrationResult,
    NondetailedTickResult,
)
from library.population_growth_runner import _run_population_growth_year_loop
from library.simulation_context import SimulationContext


class TestPopulationGrowthNondetailedRunner(unittest.TestCase):
    def test_nondetailed_year_runs_demographics_economy_and_migration(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            save = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext(
                db_path=cfg,
                save_db_path=save,
                world="default",
                simulation_start_year=1000,
                current_year=1000,
                checkpoint_full_snapshot_every_n_years=None,
            )
            ctx.record_year_summary = MagicMock()

            mortality = {
                "deaths_count": 0.0,
                "historical_year": 1000,
                "milestone_year": 1000,
                "infant_mortality_pct": 0.0,
                "under5_mortality_pct": 0.0,
                "percent_reaching_age_100": 0.0,
            }
            with (
                patch(
                    "library.population_growth_runner._record_profile_scale_snapshot",
                    return_value=None,
                ),
                patch(
                    "library.population_growth_runner._migration_arrivals_by_settlement_from_events",
                    return_value={},
                ),
                patch(
                    "library.population_growth_runner._promote_passive_context_for_migration_arrivals",
                    return_value=0,
                ),
                patch(
                    "library.population_growth_runner.pair_people_by_settlement_then_region",
                    return_value=None,
                ),
                patch(
                    "library.population_growth_runner.births_by_settlement",
                    return_value=0,
                ),
                patch(
                    "library.population_growth_runner.apply_annual_mortality",
                    return_value=mortality,
                ),
                patch(
                    "library.population_growth_runner.seed_nondetailed_from_active_settlements",
                    return_value=0,
                ) as seed,
                patch(
                    "library.population_growth_runner.run_nondetailed_sql_annual_tick_for_save",
                    return_value=NondetailedTickResult(alive_after=25, total_after=30),
                ) as tick,
                patch(
                    "library.population_growth_runner.apply_nondetailed_job_family_economy_effects",
                    return_value=NondetailedEconomyResult(
                        affected_settlements=2,
                        total_population_seen=25,
                    ),
                ) as economy,
                patch(
                    "library.population_growth_runner.run_nondetailed_sql_migration",
                    return_value=NondetailedMigrationResult(
                        moved=7,
                        source_settlements=1,
                    ),
                ) as migration,
                patch(
                    "library.population_growth_runner.ensure_detailed_floor_for_active_settlements",
                    return_value=0,
                ),
            ):
                _run_population_growth_year_loop(
                    ctx,
                    sim_seed=17,
                    start_year=1000,
                    duration_years=1,
                    passive_population_scale=1.0,
                    detailed_active_soft_cap=10,
                    use_nondetailed_directory=True,
                    progress_callback=None,
                )

        seed.assert_called_once()
        tick.assert_called_once_with(save, year=1000, start_person_id=1)
        economy.assert_called_once()
        migration.assert_called_once()
        self.assertEqual(ctx.last_nondetailed_tick_result.alive_after, 25)
        ctx.record_year_summary.assert_called_once()


if __name__ == "__main__":
    unittest.main()
