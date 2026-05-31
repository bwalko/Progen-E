"""Smoke-test mixed-mode passive/cohort population at large historical scales.

This utility exercises the aggregate passive model without creating millions of
detailed people. It seeds one active settlement in every configured region,
scales aggregate cohorts to a target population, evolves them for N years, and
writes a compact TSV summary under ``temp/``.
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
from library.population_growth_runner import refresh_passive_background_cohorts  # noqa: E402
from library.settlements import SettlementState  # noqa: E402
from library.simulation_context import SimulationContext  # noqa: E402

_DEFAULT_TARGETS = (100_000, 1_000_000, 10_000_000)
_DEFAULT_START_YEAR = 1000
_DEFAULT_YEARS = 10
_DEFAULT_OUTPUT = _ROOT / "temp" / "mixed_mode_scale_smoke.tsv"


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


def _active_settlements_for_regions(regions) -> dict[str, SettlementState]:
    out: dict[str, SettlementState] = {}
    for i, region in enumerate(regions, start=1):
        sid = f"{region.region_id}:scale_smoke:1"
        out[sid] = SettlementState(
            region_id=region.region_id,
            settlement_id=sid,
            site_slot=1,
            founded_sim_year=_DEFAULT_START_YEAR,
            status="active",
            display_name=f"Scale Smoke {i}",
        )
    return out


def _latest_counts(ctx: SimulationContext) -> dict[str, int]:
    latest_year = max((int(c.sim_year) for c in ctx.passive_cohorts), default=None)
    latest = [
        c
        for c in ctx.passive_cohorts
        if latest_year is not None and int(c.sim_year) == latest_year
    ]
    return {
        "cohort_rows": len(latest),
        "aggregate_alive": sum(int(c.population_count) for c in latest),
        "aggregate_births": sum(int(c.birth_count) for c in latest),
        "aggregate_deaths": sum(int(c.death_count) for c in latest),
        "aggregate_partnered": sum(
            int(c.population_count)
            for c in latest
            if (c.status_bucket or "").strip().lower() == "partnered"
        ),
    }


def run_scale_smoke(
    *,
    target_population: int,
    years: int,
    world: str,
    cfg_path: Path,
    save_path: Path,
) -> dict[str, object]:
    regions = list_regions(world=world, db_path=cfg_path)
    if not regions:
        raise LookupError(f"No regions found for world={world!r}")
    base_capacity = sum(max(1, int(r.carrying_capacity)) for r in regions)
    scale = float(target_population) / float(max(1, base_capacity))
    ctx = SimulationContext(
        db_path=cfg_path,
        save_db_path=save_path,
        world=world,
        simulation_start_year=_DEFAULT_START_YEAR,
        current_year=_DEFAULT_START_YEAR,
        settlements_by_id=_active_settlements_for_regions(regions),
    )

    t0 = time.perf_counter()
    for offset in range(int(years)):
        year = _DEFAULT_START_YEAR + offset
        ctx.current_year = year
        refresh_passive_background_cohorts(ctx, year, population_scale=scale)
    elapsed = time.perf_counter() - t0
    counts = _latest_counts(ctx)
    return {
        "target_population": int(target_population),
        "years": int(years),
        "world": world,
        "region_count": len(regions),
        "base_capacity": int(base_capacity),
        "passive_population_scale": f"{scale:.8f}",
        "elapsed_s": f"{elapsed:.6f}",
        **counts,
    }


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "target_population",
        "years",
        "world",
        "region_count",
        "base_capacity",
        "passive_population_scale",
        "elapsed_s",
        "cohort_rows",
        "aggregate_alive",
        "aggregate_births",
        "aggregate_deaths",
        "aggregate_partnered",
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
        help="Comma-separated target aggregate populations (default: 100000,1000000,10000000).",
    )
    p.add_argument(
        "--years",
        type=int,
        default=_DEFAULT_YEARS,
        help=f"Passive years to evolve for each target (default: {_DEFAULT_YEARS}).",
    )
    p.add_argument("--world", default="default", help="Config world id (default: default).")
    p.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help=f"TSV output path (default: {_DEFAULT_OUTPUT}).",
    )
    args = p.parse_args()
    if args.years < 1:
        p.error("--years must be >= 1")
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
        save = root / "save.sqlite"
        load_all_csvs_into_sqlite(cfg)
        rows = [
            run_scale_smoke(
                target_population=target,
                years=int(args.years),
                world=str(args.world).strip(),
                cfg_path=cfg,
                save_path=save,
            )
            for target in args.targets
        ]
    _write_rows(Path(args.output), rows)
    for row in rows:
        print(
            " | ".join(
                (
                    f"target={row['target_population']}",
                    f"years={row['years']}",
                    f"alive={row['aggregate_alive']}",
                    f"births={row['aggregate_births']}",
                    f"deaths={row['aggregate_deaths']}",
                    f"partnered={row['aggregate_partnered']}",
                    f"rows={row['cohort_rows']}",
                    f"elapsed_s={row['elapsed_s']}",
                )
            )
        )
    print(f"wrote {Path(args.output).resolve()}")


if __name__ == "__main__":
    main()
