"""Population growth + optional late-year CPU phase timing (temp SQLite).

Defaults are short so this finishes quickly. Override with env or args::

    python -u temp/run_profile_250_pop.py --years 120 --profile-last 25
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from library.config_import import load_all_csvs_into_sqlite  # noqa: E402
from library.population_growth_runner import run_population_growth_simulation  # noqa: E402
from library.simulation_context import SimulationContext  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--years",
        type=int,
        default=90,
        metavar="N",
        help="Simulation length (default: 90)",
    )
    p.add_argument(
        "--profile-last",
        type=int,
        default=20,
        metavar="N",
        help="Aggregate perf for last N years only (default: 20). Set 0 to disable.",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=42,
        help="RNG seed (default: 42)",
    )
    args = p.parse_args()
    if args.years < 1:
        p.error("--years must be >= 1")
    if args.profile_last < 0:
        p.error("--profile-last must be >= 0")
    return args


def main() -> None:
    args = _parse_args()
    if args.profile_last > 0:
        os.environ["HISTORY_SIM_PROFILE_LAST_N_YEARS"] = str(args.profile_last)
    else:
        os.environ.pop("HISTORY_SIM_PROFILE_LAST_N_YEARS", None)
    os.environ["POPULATION_GROWTH_SIM_SEED"] = str(args.seed)

    td = Path(tempfile.mkdtemp(prefix="histprof_"))
    cfg, sav = td / "config.sqlite", td / "save.sqlite"
    load_all_csvs_into_sqlite(cfg)
    print(f"temp_dbs={td} years={args.years} profile_last={args.profile_last}", flush=True)
    t0 = time.perf_counter()
    with SimulationContext.create(
        db_path=cfg,
        save_db_path=sav,
        world_id="default",
        world="default",
        start_year=1000,
        placename_rng_salt=args.seed,
        refresh_config=False,
    ) as ctx:
        run_population_growth_simulation(
            ctx,
            sim_seed=args.seed,
            start_year=1000,
            duration_years=args.years,
            starting_couples=10,
        )
    w = time.perf_counter() - t0
    print(
        "alive",
        len(ctx.current_people_ids),
        "total",
        len(ctx.people),
        "wall_s",
        round(w, 1),
        flush=True,
    )


if __name__ == "__main__":
    main()
