"""Run full mixed-mode population calibration scenarios.

Unlike ``run_mixed_mode_scale_smoke.py``, this utility calls the canonical
population-growth runner so yearly careers, government, economy, reports, passive
cohorts, and promotion hooks all execute. It keeps detailed people bounded while
scaling aggregate passive population toward large historical targets.
"""

from __future__ import annotations

import argparse
import csv
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from library.config_import import load_all_csvs_into_sqlite  # noqa: E402
from library.geography import list_regions  # noqa: E402
from library.population_growth_runner import run_population_growth_simulation  # noqa: E402
from library.settlements import SettlementState  # noqa: E402
from library.simulation_context import SimulationContext  # noqa: E402

_DEFAULT_TARGETS = (100_000, 1_000_000, 10_000_000)
_DEFAULT_START_YEAR = 1000
_DEFAULT_YEARS = 10
_DEFAULT_STARTING_COUPLES = 10
_DEFAULT_DETAILED_FRACTION = 0.001
_DEFAULT_MIN_DETAILED_CAP = 200
_DEFAULT_MAX_DETAILED_CAP = 2_000
_DEFAULT_OUTPUT = _ROOT / "temp" / "mixed_mode_calibration.tsv"


def _parse_targets(raw: str) -> tuple[int, ...]:
    out: list[int] = []
    for part in str(raw or "").replace(";", ",").split(","):
        s = part.strip().replace("_", "")
        if not s:
            continue
        n = int(s)
        if n < 1:
            raise ValueError("targets must be positive integers")
        out.append(n)
    if not out:
        raise ValueError("at least one target is required")
    return tuple(out)


def _detailed_cap_for_target(
    target: int,
    *,
    fraction: float,
    min_cap: int,
    max_cap: int,
) -> int:
    estimated = int(round(int(target) * float(fraction)))
    return max(int(min_cap), min(int(max_cap), max(1, estimated)))


def _latest_mixed_counts(ctx: SimulationContext, year: int) -> dict[str, int]:
    latest_cohort_year = max((int(c.sim_year) for c in ctx.passive_cohorts), default=None)
    latest_cohorts = [
        c
        for c in ctx.passive_cohorts
        if latest_cohort_year is not None and int(c.sim_year) == latest_cohort_year
    ]
    detailed_alive = len(ctx.current_people_ids)
    passive_person_alive = sum(
        1
        for rec in ctx.passive_people.values()
        if rec.person.deathyear is None or int(rec.person.deathyear) > int(year)
    )
    aggregate_alive = sum(int(c.population_count) for c in latest_cohorts)
    aggregate_births = sum(int(c.birth_count) for c in latest_cohorts)
    aggregate_deaths = sum(int(c.death_count) for c in latest_cohorts)
    aggregate_partnered = sum(
        int(c.population_count)
        for c in latest_cohorts
        if (c.status_bucket or "").strip().lower() == "partnered"
    )
    return {
        "detailed_alive": detailed_alive,
        "passive_person_alive": passive_person_alive,
        "aggregate_cohort_alive": aggregate_alive,
        "aggregate_cohort_births": aggregate_births,
        "aggregate_cohort_deaths": aggregate_deaths,
        "aggregate_cohort_partnered": aggregate_partnered,
        "mixed_mode_alive": detailed_alive + passive_person_alive + aggregate_alive,
        "cohort_rows": len(latest_cohorts),
        "promotion_count": sum(
            1
            for _, event_type, _ in ctx._pending_simulation_events
            if event_type == "passive_person_promoted"
        ),
    }


def _seed_aggregate_settlements(ctx: SimulationContext, regions, *, start_year: int) -> None:
    for i, region in enumerate(regions, start=1):
        sid = f"{region.region_id}:mixed_calibration:1"
        if sid in ctx.settlements_by_id:
            continue
        ctx.settlements_by_id[sid] = SettlementState(
            region_id=region.region_id,
            settlement_id=sid,
            site_slot=1,
            founded_sim_year=int(start_year),
            status="active",
            display_name=f"Mixed Calibration {i}",
        )
    ctx.rebuild_settlement_region_index()


def run_calibration(
    *,
    target_population: int,
    years: int,
    starting_couples: int,
    detailed_fraction: float,
    min_detailed_cap: int,
    max_detailed_cap: int,
    sim_seed: int,
    world: str,
    cfg_path: Path,
    save_path: Path,
) -> dict[str, object]:
    regions = list_regions(world=world, db_path=cfg_path)
    if not regions:
        raise LookupError(f"No regions found for world={world!r}")
    base_capacity = sum(max(1, int(r.carrying_capacity)) for r in regions)
    passive_scale = float(target_population) / float(max(1, base_capacity))
    detailed_cap = _detailed_cap_for_target(
        target_population,
        fraction=detailed_fraction,
        min_cap=min_detailed_cap,
        max_cap=max_detailed_cap,
    )
    t0 = time.perf_counter()
    with SimulationContext.create(
        db_path=cfg_path,
        save_db_path=save_path,
        world_id=f"mixed_calibration_{target_population}",
        world=world,
        start_year=_DEFAULT_START_YEAR,
        refresh_config=False,
        flush_run_store=False,
        store_flush_batch_years=max(1, int(years)),
        checkpoint_full_snapshot_every_n_years=None,
        placename_rng_salt=int(sim_seed),
    ) as ctx:
        _seed_aggregate_settlements(ctx, regions, start_year=_DEFAULT_START_YEAR)
        run_population_growth_simulation(
            ctx,
            sim_seed=int(sim_seed),
            start_year=_DEFAULT_START_YEAR,
            duration_years=int(years),
            starting_couples=int(starting_couples),
            passive_population_scale=passive_scale,
            detailed_active_soft_cap=detailed_cap,
            print_timing_report=False,
        )
        end_year = _DEFAULT_START_YEAR + int(years) - 1
        counts = _latest_mixed_counts(ctx, end_year)
    elapsed = time.perf_counter() - t0
    return {
        "target_population": int(target_population),
        "years": int(years),
        "world": world,
        "sim_seed": int(sim_seed),
        "starting_couples": int(starting_couples),
        "region_count": len(regions),
        "base_capacity": int(base_capacity),
        "passive_population_scale": f"{passive_scale:.8f}",
        "detailed_active_soft_cap": int(detailed_cap),
        "elapsed_s": f"{elapsed:.6f}",
        **counts,
    }


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "target_population",
        "years",
        "world",
        "sim_seed",
        "starting_couples",
        "region_count",
        "base_capacity",
        "passive_population_scale",
        "detailed_active_soft_cap",
        "elapsed_s",
        "detailed_alive",
        "passive_person_alive",
        "aggregate_cohort_alive",
        "aggregate_cohort_births",
        "aggregate_cohort_deaths",
        "aggregate_cohort_partnered",
        "mixed_mode_alive",
        "cohort_rows",
        "promotion_count",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--targets",
        default=",".join(str(n) for n in _DEFAULT_TARGETS),
        help="Comma-separated target mixed-mode populations.",
    )
    p.add_argument("--years", type=int, default=_DEFAULT_YEARS)
    p.add_argument("--starting-couples", type=int, default=_DEFAULT_STARTING_COUPLES)
    p.add_argument("--seed", type=int, default=15000)
    p.add_argument("--world", default="default")
    p.add_argument("--detailed-fraction", type=float, default=_DEFAULT_DETAILED_FRACTION)
    p.add_argument("--min-detailed-cap", type=int, default=_DEFAULT_MIN_DETAILED_CAP)
    p.add_argument("--max-detailed-cap", type=int, default=_DEFAULT_MAX_DETAILED_CAP)
    p.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    args = p.parse_args()
    if args.years < 1:
        p.error("--years must be >= 1")
    if args.starting_couples < 1:
        p.error("--starting-couples must be >= 1")
    if args.detailed_fraction < 0:
        p.error("--detailed-fraction must be >= 0")
    if args.min_detailed_cap < 1 or args.max_detailed_cap < 1:
        p.error("--min-detailed-cap and --max-detailed-cap must be >= 1")
    if args.max_detailed_cap < args.min_detailed_cap:
        p.error("--max-detailed-cap must be >= --min-detailed-cap")
    try:
        args.targets = _parse_targets(args.targets)
    except ValueError as exc:
        p.error(str(exc))
    return args


def main() -> None:
    args = _parse_args()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        root = Path(td)
        cfg = root / "config.sqlite"
        load_all_csvs_into_sqlite(cfg)
        rows: list[dict[str, object]] = []
        for index, target in enumerate(args.targets):
            rows.append(
                run_calibration(
                    target_population=target,
                    years=int(args.years),
                    starting_couples=int(args.starting_couples),
                    detailed_fraction=float(args.detailed_fraction),
                    min_detailed_cap=int(args.min_detailed_cap),
                    max_detailed_cap=int(args.max_detailed_cap),
                    sim_seed=int(args.seed) + index,
                    world=str(args.world).strip(),
                    cfg_path=cfg,
                    save_path=root / f"save_{target}.sqlite",
                )
            )
    _write_rows(Path(args.output), rows)
    for row in rows:
        print(
            " | ".join(
                (
                    f"target={row['target_population']}",
                    f"mixed={row['mixed_mode_alive']}",
                    f"detailed={row['detailed_alive']}/{row['detailed_active_soft_cap']}",
                    f"aggregate={row['aggregate_cohort_alive']}",
                    f"rows={row['cohort_rows']}",
                    f"promotions={row['promotion_count']}",
                    f"elapsed_s={row['elapsed_s']}",
                )
            )
        )
    print(f"wrote {Path(args.output).resolve()}")


if __name__ == "__main__":
    main()
