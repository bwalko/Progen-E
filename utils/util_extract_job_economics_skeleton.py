"""Build ``config/job_economics.csv`` from ``genome_jobs.csv``.

Writes:
- One ``row_kind=base`` line per era (``job_key=*``) with absolute pool/wage/value/tax.
- ``row_kind=deviation`` lines only where tier multipliers differ from 1.0 (after premium bump).

    python utils/util_extract_job_economics_skeleton.py
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from library.job_economics import normalize_job_catalog_key  # noqa: E402
from library.job_economics_tiers import (  # noqa: E402
    format_deviation_cells,
    infer_deviation_multipliers,
    deviation_row_non_trivial,
)

GENOME_JOBS = _ROOT / "config" / "genome_jobs.csv"
JOB_ECON_OUT = _ROOT / "config" / "job_economics.csv"

ERA_COLS: tuple[tuple[str, str, bool], ...] = (
    ("prehistoric", "prehistoric_jobs", False),
    ("prehistoric", "prehistoric_premium_jobs", True),
    ("bronze_age", "bronze_age_jobs", False),
    ("bronze_age", "bronze_age_premium_jobs", True),
    ("iron_age", "iron_age_jobs", False),
    ("iron_age", "iron_age_premium_jobs", True),
    ("medieval", "medieval_jobs", False),
    ("medieval", "medieval_premium_jobs", True),
    ("modern", "modern_jobs", False),
    ("modern", "modern_premium_jobs", True),
)

# Absolute baselines per era: pool_draw, wage_yield, value_add, tax_rate
# (typical unspecialized labor / household work — elites come from deviation multipliers.)
ERA_BASELINES: dict[str, tuple[float, float, float, float]] = {
    "prehistoric": (0.30, 0.20, 0.18, 0.035),
    "bronze_age": (0.27, 0.24, 0.22, 0.048),
    "iron_age": (0.25, 0.27, 0.25, 0.058),
    "medieval": (0.23, 0.29, 0.27, 0.068),
    "modern": (0.21, 0.31, 0.29, 0.078),
}


def _split_jobs(cell: str | None) -> tuple[str, ...]:
    if cell is None:
        return ()
    return tuple(p.strip() for p in str(cell).split(";") if p.strip())


def collect_keys_from_genome_jobs(path: Path) -> dict[tuple[str, str], bool]:
    """Map (job_key, era) -> True if any source marked that pair as premium."""
    premium_any: defaultdict[tuple[str, str], bool] = defaultdict(bool)
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return {}
        for row in reader:
            for era, col, is_prem in ERA_COLS:
                if col not in row:
                    continue
                for token in _split_jobs(row.get(col)):
                    jk = normalize_job_catalog_key(token)
                    if not jk:
                        continue
                    key = (jk, era)
                    premium_any[key] = premium_any[key] or is_prem
    return dict(premium_any)


def _parse_base_row(row: dict[str, str]) -> tuple[str, tuple[float, float, float, float]] | None:
    if (row.get("row_kind") or "").strip().lower() != "base":
        return None
    if normalize_job_catalog_key(row.get("job_key", "")) != "*":
        return None
    era = (row.get("era") or "").strip().lower()
    if era not in ERA_BASELINES and era != "*":
        return None
    try:
        t = (
            float(row["pool_draw"]),
            float(row["wage_yield"]),
            float(row["value_add"]),
            float(row["tax_rate"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    return era, t


def load_preserved_bases(path: Path) -> dict[str, tuple[float, float, float, float]]:
    """If CSV already has base rows, reuse their numbers (author-tuned)."""
    if not path.is_file():
        return {}
    out: dict[str, tuple[float, float, float, float]] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "row_kind" not in reader.fieldnames:
            return {}
        for row in reader:
            parsed = _parse_base_row(row)
            if parsed is None:
                continue
            era, tup = parsed
            out[era] = tup
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--genome-jobs", type=Path, default=GENOME_JOBS)
    p.add_argument("--out", type=Path, default=JOB_ECON_OUT)
    args = p.parse_args()

    collected = collect_keys_from_genome_jobs(args.genome_jobs)
    preserved = load_preserved_bases(args.out)

    out_rows: list[list[str]] = []
    header = [
        "job_key",
        "era",
        "row_kind",
        "pool_draw",
        "wage_yield",
        "value_add",
        "tax_rate",
    ]
    out_rows.append(header)

    for era in ("prehistoric", "bronze_age", "iron_age", "medieval", "modern"):
        pool, wage, va, tax = preserved.get(era, ERA_BASELINES[era])
        out_rows.append(
            [
                "*",
                era,
                "base",
                f"{pool:.4f}",
                f"{wage:.4f}",
                f"{va:.4f}",
                f"{tax:.4f}",
            ]
        )
    # Global fallback base (used if an era base row were ever missing)
    gp, gw, gv, gt = preserved.get("*", ERA_BASELINES["modern"])
    out_rows.append(["*", "*", "base", f"{gp:.4f}", f"{gw:.4f}", f"{gv:.4f}", f"{gt:.4f}"])

    seen_dev: set[tuple[str, str]] = set()
    for (jk, era), is_prem in sorted(collected.items(), key=lambda x: (x[0][1], x[0][0])):
        mults = infer_deviation_multipliers(jk, is_premium=is_prem)
        if not deviation_row_non_trivial(mults):
            continue
        key = (jk, era)
        if key in seen_dev:
            continue
        seen_dev.add(key)
        cells = format_deviation_cells(mults)
        out_rows.append(
            [
                jk,
                era,
                "deviation",
                cells["pool_draw"],
                cells["wage_yield"],
                cells["value_add"],
                cells["tax_rate"],
            ]
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(out_rows)
    print(f"Wrote {len(out_rows) - 1} data row(s) (+ header) to {args.out}")


if __name__ == "__main__":
    main()
