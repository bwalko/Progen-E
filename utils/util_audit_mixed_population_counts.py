"""Audit mixed population counts on save.sqlite and optional in-run cache checks.

Examples::

    python utils/util_audit_mixed_population_counts.py --world default
    python utils/util_audit_mixed_population_counts.py --save-db worlds/default/save.sqlite --output temp/mixed_population_audit.tsv

In-run cache validation (a few years during mixed-mode sim)::

    set HISTORY_SIM_VALIDATE_MIXED_COUNTS=1
    python utils/run_population_simulation.py --world-id default --reset-world --years 5 \\
      --use-nondetailed-directory --profile-last-years 5 --skip-report-files
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from library.mixed_population_audit import audit_save_sqlite_counts

_COLUMNS = (
    "settlement_id",
    "region_id",
    "status",
    "abandoned_sim_year",
    "detailed_alive",
    "nondetailed_alive",
    "mixed_alive",
)


def _resolve_save_db(world: str | None, save_db: str | None) -> Path:
    if save_db:
        return Path(save_db)
    if not world:
        raise SystemExit("Provide --world or --save-db")
    return _ROOT / "worlds" / world / "save.sqlite"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", default="default")
    parser.add_argument("--save-db", default=None)
    parser.add_argument(
        "--output",
        default=None,
        help="Optional TSV output path (default: stdout summary only)",
    )
    args = parser.parse_args()
    save_path = _resolve_save_db(args.world, args.save_db)
    if not save_path.exists():
        raise SystemExit(f"save database not found: {save_path}")

    rows = audit_save_sqlite_counts(str(save_path))
    abandoned_populated = [
        row
        for row in rows
        if str(row["status"]).lower() == "abandoned" and int(row["mixed_alive"]) > 0
    ]
    active_empty = [
        row
        for row in rows
        if str(row["status"]).lower() == "active" and int(row["mixed_alive"]) <= 0
    ]
    print(
        f"mixed_population_audit save={save_path} settlements={len(rows)} "
        f"abandoned_populated={len(abandoned_populated)} active_empty={len(active_empty)}"
    )
    for row in abandoned_populated[:10]:
        print(
            f"  abandoned_populated {row['settlement_id']}: "
            f"mixed={row['mixed_alive']} detailed={row['detailed_alive']} "
            f"nondetailed={row['nondetailed_alive']}"
        )
    for row in active_empty[:10]:
        print(f"  active_empty {row['settlement_id']}")

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=_COLUMNS, delimiter="\t")
            writer.writeheader()
            for row in rows:
                writer.writerow({col: row.get(col, "") for col in _COLUMNS})
        print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
