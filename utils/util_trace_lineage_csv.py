"""Print ancestor generations for a person_id from a run-store ``people.csv``."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_PEOPLE_CSV = _ROOT / "temp" / "simulation_run_store" / "people.csv"


def _load_people(csv_path: Path) -> dict[int, dict]:
    by_id: dict[int, dict] = {}
    with csv_path.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            pid = int(row["person_id"])
            fa = row.get("father_id", "").strip()
            mo = row.get("mother_id", "").strip()
            by_id[pid] = {
                "first": row.get("first_name", ""),
                "last": row.get("last_name", ""),
                "birthyear": row.get("birthyear", ""),
                "father": int(fa) if fa else None,
                "mother": int(mo) if mo else None,
            }
    return by_id


def main() -> None:
    p = argparse.ArgumentParser(
        description="Trace ancestry for person_id using a SimulationFileStore people.csv export."
    )
    p.add_argument(
        "--people-csv",
        type=Path,
        default=_DEFAULT_PEOPLE_CSV,
        help=f"Path to people.csv (default: {_DEFAULT_PEOPLE_CSV})",
    )
    p.add_argument(
        "--person-id",
        type=int,
        default=77068,
        help="Root person_id for the trace (default: 77068)",
    )
    args = p.parse_args()
    csv_path = args.people_csv.resolve()
    target = int(args.person_id)

    if not csv_path.is_file():
        print(f"Missing people CSV: {csv_path}", file=sys.stderr)
        sys.exit(1)

    by_id = _load_people(csv_path)

    def fmt(pid: int) -> str:
        rec = by_id.get(pid)
        if not rec:
            return str(pid)
        return f"{pid} {rec['first']} {rec['last']} (b.{rec['birthyear']})"

    if target not in by_id:
        print(f"No person_id {target} in {csv_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Lineage for {fmt(target)}")
    print("(generation 0 = subject; each line lists all ancestors at that depth)\n")

    frontier: set[int] = {target}
    gen = 0
    while frontier:
        ids = sorted(frontier)
        print(f"Generation {gen} ({len(ids)} person(s)):")
        for i in ids:
            print(f"  - {fmt(i)}")
        nxt: set[int] = set()
        for pid in frontier:
            rec = by_id[pid]
            if rec["father"] is not None:
                nxt.add(rec["father"])
            if rec["mother"] is not None:
                nxt.add(rec["mother"])
        frontier = nxt
        gen += 1
        if gen > 200:
            print("Stopped at 200 generations (sanity cap).")
            break
        if not frontier:
            break
    print("\nEnd: no more parents (founders or missing links).")


if __name__ == "__main__":
    main()
