"""Print per-simulation-year alive counts from a file-store ``yearly_summary.csv``.

Typical workflow after a population run::
    python utils/util_print_alive_by_year.py --world default --latest-run
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _latest_yearly_summary(world_id: str) -> Path:
    base = _ROOT / "worlds" / world_id.strip() / "temp"
    candidates = sorted(
        base.glob("simulation_run_*/yearly_summary.csv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise SystemExit(
            f"No simulation_run_*/yearly_summary.csv under {base}. "
            "Run a simulation first (it writes under worlds/<id>/temp/)."
        )
    return candidates[0]


def _print_rows(csv_path: Path) -> None:
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"year", "alive_count"}
        if not reader.fieldnames or not required <= set(reader.fieldnames):
            raise SystemExit(f"unexpected CSV columns in {csv_path}: {reader.fieldnames!r}")
        rows = list(reader)

    def _year_key(r: dict[str, str]) -> tuple[int, str]:
        y_raw = r.get("year") or ""
        try:
            return (int(y_raw), "")
        except ValueError:
            return (10**9, y_raw)

    rows.sort(key=_year_key)
    hdr = reader.fieldnames
    assert hdr is not None
    widths = [max(len(col), *(len(str(r.get(col, "") or "")) for r in rows)) for col in hdr]
    line = " | ".join(col.ljust(w) for col, w in zip(hdr, widths))
    print(line)
    print("-+-".join("-" * w for w in widths))
    extinct_year: str | None = None
    for r in rows:
        alive_raw = str(r.get("alive_count") or "").strip()
        try:
            if extinct_year is None and int(alive_raw) == 0:
                extinct_year = str(r.get("year") or "")
        except ValueError:
            pass
        print(
            " | ".join(str(r.get(col, "") or "").ljust(w) for col, w in zip(hdr, widths))
        )
    if extinct_year is not None:
        print(f"\nFirst year with alive_count=0: {extinct_year}", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Print yearly_summary rows (full table) from a simulation file store."
    )
    p.add_argument(
        "--yearly-summary",
        type=Path,
        help="Path to yearly_summary.csv from a run (e.g. worlds/default/temp/simulation_run_.../yearly_summary.csv)",
    )
    p.add_argument(
        "--world",
        default="default",
        help="World id under worlds/ (used with --latest-run only)",
    )
    p.add_argument(
        "--latest-run",
        action="store_true",
        help="Use the most recently modified simulation_run_*/yearly_summary.csv under worlds/<world>/temp/",
    )
    args = p.parse_args()

    if args.latest_run and args.yearly_summary is not None:
        raise SystemExit("use either --latest-run or --yearly-summary, not both")
    if args.latest_run:
        path = _latest_yearly_summary(str(args.world))
    elif args.yearly_summary is not None:
        path = args.yearly_summary
    else:
        raise SystemExit("required: --yearly-summary PATH or --latest-run (with optional --world)")

    path = path.resolve()
    if not path.is_file():
        raise SystemExit(f"not a file: {path}")
    _print_rows(path)


if __name__ == "__main__":
    main()
