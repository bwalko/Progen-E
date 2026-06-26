"""Legacy vs batch-conn detailed-floor paths must produce identical promotion plans."""

from __future__ import annotations

import csv
import os
import tempfile
import unittest
from pathlib import Path

from library.config_import import load_all_csvs_into_sqlite
from library.population_growth_runner import run_population_growth_simulation
from library.simulation_context import SimulationContext

_START_YEAR = 1000
_DURATION_YEARS = 10
_STARTING_COUPLES = 100
_SIM_SEED = 639789854

_YEARLY_COLS = (
    "detailed_alive_count",
    "nondetailed_alive_count",
    "mixed_mode_alive_count",
    "births_count",
    "deaths_count",
)

_TRACE_DECISION_COLS = (
    "year",
    "settlement_id",
    "promotion_ordinal",
    "person_ids_selected",
    "promotion_reason",
    "promotion_count",
    "target_detailed_floor",
)


def _run_with_floor_mode(*, cfg: Path, sav: Path, legacy: bool) -> tuple[dict[int, dict], list[dict[str, str]]]:
    os.environ.pop("DETAILED_FLOOR_LEGACY_CODE", None)
    os.environ.pop("DETAILED_FLOOR_BATCH_CONN", None)
    os.environ.pop("DETAILED_FLOOR_BATCH_UNFIXED", None)
    os.environ.pop("DETAILED_FLOOR_PROMOTION_TRACE", None)
    if legacy:
        os.environ["DETAILED_FLOOR_LEGACY_CODE"] = "1"
    else:
        os.environ["DETAILED_FLOOR_BATCH_CONN"] = "1"
    trace_path = sav.parent / ("trace_legacy.tsv" if legacy else "trace_fixed.tsv")
    os.environ["DETAILED_FLOOR_PROMOTION_TRACE"] = str(trace_path)
    with SimulationContext.create(
        db_path=cfg,
        save_db_path=sav,
        world_id="default",
        world="default",
        start_year=_START_YEAR,
        placename_rng_salt=_SIM_SEED,
        refresh_config=False,
        flush_run_store=False,
    ) as ctx:
        run_population_growth_simulation(
            ctx,
            sim_seed=_SIM_SEED,
            start_year=_START_YEAR,
            duration_years=_DURATION_YEARS,
            starting_couples=_STARTING_COUPLES,
            use_nondetailed_directory=True,
        )
        yearly = {
            int(row["year"]): dict(row)
            for row in getattr(ctx.file_store, "_yearly_summary_rows", ())
        }
    with trace_path.open(encoding="utf-8") as f:
        trace_rows = list(csv.DictReader(f, delimiter="\t"))
    return yearly, trace_rows


def _promotion_decisions(trace_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    decisions = [
        row
        for row in trace_rows
        if row.get("promotion_reason") not in {"", "none", "pass_complete"}
    ]
    return [
        {col: row.get(col, "") for col in _TRACE_DECISION_COLS}
        for row in decisions
    ]


class DetailedFloorLegacyEquivalenceTests(unittest.TestCase):
    def test_legacy_and_batch_conn_match_yearly_summary_and_promotions(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            load_all_csvs_into_sqlite(cfg)
            legacy_yearly, legacy_trace = _run_with_floor_mode(
                cfg=cfg,
                sav=root / "legacy_save.sqlite",
                legacy=True,
            )
            fixed_yearly, fixed_trace = _run_with_floor_mode(
                cfg=cfg,
                sav=root / "fixed_save.sqlite",
                legacy=False,
            )
            self.assertEqual(set(legacy_yearly), set(fixed_yearly))
            for year in sorted(legacy_yearly):
                for col in _YEARLY_COLS:
                    self.assertEqual(
                        legacy_yearly[year].get(col),
                        fixed_yearly[year].get(col),
                        f"year={year} col={col}",
                    )
            self.assertEqual(
                _promotion_decisions(legacy_trace),
                _promotion_decisions(fixed_trace),
            )


if __name__ == "__main__":
    unittest.main()
