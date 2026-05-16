"""Import ``events.csv`` (and optionally other run-store CSVs) into ``save.sqlite``.

Typical use: one-shot backup/query migration from ``temp/.../simulation_run_*/events.csv``.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from library.world_save import append_simulation_event_rows, ensure_checkpoint_schema


def _import_events(conn: sqlite3.Connection, world: str, events_csv: Path) -> int:
    rows: list[tuple[int | None, str, dict]] = []
    with events_csv.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            payload = {k: r.get(k, "") for k in r}
            y_raw = str(r.get("year") or "").strip()
            sim_year = int(y_raw) if y_raw.isdigit() else None
            et = str(r.get("event_type") or "").strip() or "unknown"
            rows.append((sim_year, et, payload))
    if rows:
        append_simulation_event_rows(conn, world, rows)
    return len(rows)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Append rows from run-store CSV files into save.sqlite simulation_events"
    )
    p.add_argument("--save-db", required=True, type=Path, help="Path to save.sqlite")
    p.add_argument("--world", default="default", help="World key (default: default)")
    p.add_argument("--events-csv", type=Path, help="Path to events.csv")
    p.add_argument(
        "--people-csv",
        type=Path,
        help="Reserved for future import of people.csv into checkpoints",
    )
    args = p.parse_args()
    save_db = Path(args.save_db)
    world = str(args.world).strip()
    if args.people_csv is not None:
        print(
            "Note: --people-csv is not implemented yet; only events import runs.",
            file=sys.stderr,
        )
    if args.events_csv is None:
        raise SystemExit("Provide --events-csv (required for current importer)")
    ev_path = Path(args.events_csv)
    if not ev_path.is_file():
        raise SystemExit(f"Missing events CSV: {ev_path}")

    save_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(save_db)
    conn.row_factory = sqlite3.Row
    try:
        ensure_checkpoint_schema(conn)
        n = _import_events(conn, world, ev_path)
        conn.commit()
    finally:
        conn.close()
    print(f"Imported {n} event row(s) into {save_db}")


if __name__ == "__main__":
    main()
