"""Run the population-growth scenario for N years against the real world layout.

Uses ``worlds/<world_id>/config.sqlite`` and ``worlds/<world_id>/save.sqlite`` (no tempfile,
no unittest). On exit, :class:`library.simulation_context.SimulationContext` persists a full
checkpoint to ``save.sqlite``.

Also writes the canonical report files next to ``unit_test/test_population_growth_100_years.py``:

- ``unit_test/population_growth_simulation_report.txt``
- ``unit_test/population_growth_simulation_people.json``
- ``unit_test/population_growth_simulation_places_geo.json``

Examples::

    python utils/run_population_simulation.py --years 400
    python utils/run_population_simulation.py --years 100 --world-id default
    python utils/run_population_simulation.py --years 100 --starting-couples 25 --seed 12345

Environment (optional):

- ``SIM_STORE_FLUSH_BATCH_YEARS`` (default ``50``)
- ``POPULATION_GROWTH_SIM_SEED`` — fixed seed; otherwise a random seed is chosen and stored in env
- ``HISTORY_SIM_RESET_WORLD`` — if ``1``/``true``, deletes ``save.sqlite`` before create (full wipe)
- After each run, appends one TSV row to ``unit_test/population_sim_timing.tsv`` (wall time, seed, flush batch, alive count) for trend tracking. Set ``POPULATION_SIM_SKIP_TIMING_LOG=1`` to disable.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import library.simulation_context as sc  # noqa: E402
from library import simulation_timing  # noqa: E402
from library.population_growth_runner import (  # noqa: E402
    resolve_population_sim_seed,
    run_population_growth_simulation,
    write_population_growth_report_files,
)

_FLUSH_DEFAULT = 10

# Match ``unit_test/test_population_growth_100_years.py`` scenario parameters.
_START_YEAR = 1000
_STARTING_COUPLES = 10
_REPORT_DIR = _ROOT / "unit_test"
_OUTPUT_PATH = _REPORT_DIR / "population_growth_simulation_report.txt"
_PEOPLE_JSON_PATH = _REPORT_DIR / "population_growth_simulation_people.json"
_PLACES_GEO_PATH = _REPORT_DIR / "population_growth_simulation_places_geo.json"
_TIMING_LOG_PATH = _REPORT_DIR / "population_sim_timing.tsv"


def _append_population_sim_timing_row(
    *,
    path: Path,
    iso_ts: str,
    elapsed_s: float,
    years: int,
    world_id: str,
    sim_seed: int,
    flush: int,
    starting_couples: int,
    alive_end: int,
) -> None:
    """Append one tab-separated row; write header on first create."""
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "iso_timestamp\telapsed_s\tyears\tworld_id\tsim_seed\t"
        "flush_batch_years\tstarting_couples\talive_end\n"
    )
    if not path.exists():
        path.write_text(header, encoding="utf-8")
    line = (
        f"{iso_ts}\t{elapsed_s:.3f}\t{years}\t{world_id}\t{sim_seed}\t"
        f"{flush}\t{starting_couples}\t{alive_end}\n"
    )
    with path.open("a", encoding="utf-8") as f:
        f.write(line)


def _elapsed_hhmmss(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--years",
        type=int,
        default=100,
        metavar="N",
        help="Simulation length in years (default: 100)",
    )
    p.add_argument(
        "--world-id",
        type=str,
        default="default",
        metavar="ID",
        help="World folder under worlds/ (default: default)",
    )
    p.add_argument(
        "--starting-couples",
        type=int,
        default=_STARTING_COUPLES,
        metavar="N",
        help=f"Founder couples to create at start (default: {_STARTING_COUPLES})",
    )
    p.add_argument(
        "--start-year",
        type=int,
        default=_START_YEAR,
        metavar="YEAR",
        help=f"Simulation start year (default: {_START_YEAR})",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=None,
        metavar="N",
        help="Set POPULATION_GROWTH_SIM_SEED for this run.",
    )
    p.add_argument(
        "--flush-batch-years",
        type=int,
        default=None,
        metavar="N",
        help=f"Set SIM_STORE_FLUSH_BATCH_YEARS for this run (default/env: {_FLUSH_DEFAULT}).",
    )
    p.add_argument(
        "--reset-world",
        action="store_true",
        help="Set HISTORY_SIM_RESET_WORLD=1 before creating the simulation context.",
    )
    p.add_argument(
        "--skip-timing-log",
        action="store_true",
        help="Do not append to unit_test/population_sim_timing.tsv.",
    )
    p.add_argument(
        "--progress",
        action="store_true",
        help="Print SIM_PROGRESS lines after each yearly save for streaming UIs.",
    )
    args = p.parse_args()
    if args.years < 1:
        p.error("--years must be >= 1")
    if args.starting_couples < 1:
        p.error("--starting-couples must be >= 1")
    if args.flush_batch_years is not None and args.flush_batch_years < 1:
        p.error("--flush-batch-years must be >= 1")
    return args


def main() -> None:
    args = _parse_args()
    if args.seed is not None:
        os.environ["POPULATION_GROWTH_SIM_SEED"] = str(int(args.seed))
    if args.flush_batch_years is not None:
        os.environ["SIM_STORE_FLUSH_BATCH_YEARS"] = str(int(args.flush_batch_years))
    if args.reset_world:
        os.environ["HISTORY_SIM_RESET_WORLD"] = "1"
    if args.skip_timing_log:
        os.environ["POPULATION_SIM_SKIP_TIMING_LOG"] = "1"

    flush = int(os.environ.get("SIM_STORE_FLUSH_BATCH_YEARS", str(_FLUSH_DEFAULT)))
    orig = sc.SimulationContext.create.__func__

    def _create_with_flush(cls, **kwargs: object):
        kw = dict(kwargs)
        kw["store_flush_batch_years"] = flush
        return orig(cls, **kw)

    sc.SimulationContext.create = classmethod(_create_with_flush)

    sim_seed = resolve_population_sim_seed()
    print(f"POPULATION_GROWTH_SIM_SEED={sim_seed}")
    print(
        f"config={_ROOT / 'worlds' / args.world_id / 'config.sqlite'} | "
        f"save={_ROOT / 'worlds' / args.world_id / 'save.sqlite'}"
    )

    t0 = time.perf_counter()
    end_year = int(args.start_year) + int(args.years) - 1

    def _print_progress(year: int) -> None:
        if not args.progress:
            return
        elapsed_text = _elapsed_hhmmss(time.perf_counter() - t0)
        print(
            f"SIM_PROGRESS year={int(year)} end_year={end_year} elapsed={elapsed_text}",
            flush=True,
        )

    with sc.SimulationContext.create(
        world_id=args.world_id.strip(),
        world="default",
        start_year=int(args.start_year),
        placename_rng_salt=sim_seed,
    ) as ctx:
        run_population_growth_simulation(
            ctx,
            sim_seed=sim_seed,
            start_year=int(args.start_year),
            duration_years=int(args.years),
            starting_couples=int(args.starting_couples),
            progress_callback=_print_progress,
            print_timing_report=False,
        )

    write_population_growth_report_files(
        ctx,
        sim_seed=sim_seed,
        start_year=int(args.start_year),
        duration_years=int(args.years),
        output_path=_OUTPUT_PATH,
        people_json_path=_PEOPLE_JSON_PATH,
        places_geo_path=_PLACES_GEO_PATH,
    )
    simulation_timing.print_report_if_configured()

    elapsed = time.perf_counter() - t0
    alive_end = len(ctx.current_people_ids)
    if os.environ.get("POPULATION_SIM_SKIP_TIMING_LOG", "").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        _append_population_sim_timing_row(
            path=_TIMING_LOG_PATH,
            iso_ts=datetime.now(timezone.utc).isoformat(),
            elapsed_s=elapsed,
            years=int(args.years),
            world_id=str(args.world_id).strip(),
            sim_seed=int(sim_seed),
            flush=flush,
            starting_couples=int(args.starting_couples),
            alive_end=alive_end,
        )
        print(f"timing_log_appended={_TIMING_LOG_PATH.resolve()}")
    candidates = sorted(
        (_ROOT / "worlds" / args.world_id / "temp").glob("simulation_run_*/yearly_summary.csv"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    yearly = candidates[0] if candidates else None
    print(
        f"store_flush_batch_years={flush} | years={args.years} | "
        f"start_year={args.start_year} | starting_couples={args.starting_couples} | "
        f"wrote {_OUTPUT_PATH} in {elapsed:.2f}s"
    )
    if yearly is not None:
        yp = yearly.resolve()
        print(f"yearly_summary_csv={yp}")
        print(f"alive_by_year: python utils/util_print_alive_by_year.py --yearly-summary {yp}")


if __name__ == "__main__":
    main()
