"""Audit detailed-person residence intervals for overlaps and churn from save.sqlite.

Flags:
- overlapping residence intervals for the same person;
- open-ended outlaw-refuge spans that continue after a later civic/custody residence starts;
- three or more consecutive years with settlement moves.

Example::

    python utils/util_audit_residence_intervals.py --world default
    python utils/util_audit_residence_intervals.py --save-db worlds/default/save.sqlite --output temp/residence_interval_audit.tsv
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from library.world_save import ensure_checkpoint_schema
from utils.gradio_data_browser import (
    _current_year,
    _custody_history_entries,
    _lookup_person,
    _outlaw_refuge_history_entries,
    _person_event_rows,
    _person_from_row,
    _residence_history_entries,
    _settlement_history_entries,
    _trait_slots_for_world,
)


def _history_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _intervals_overlap(
    left: dict[str, object],
    right: dict[str, object],
) -> bool:
    left_start = _history_int(left.get("start_year"))
    left_end = _history_int(left.get("end_year"))
    right_start = _history_int(right.get("start_year"))
    right_end = _history_int(right.get("end_year"))
    if None in (left_start, left_end, right_start, right_end):
        return False
    if str(left.get("place_id") or left.get("place_label") or "") == str(
        right.get("place_id") or right.get("place_label") or ""
    ) and str(left.get("residence_kind") or "") == str(right.get("residence_kind") or ""):
        return False
    return left_end > right_start and right_end > left_start


def _residence_rows_for_person(
    con: sqlite3.Connection,
    world: str,
    person_id: int,
) -> list[dict[str, object]]:
    row, person = _lookup_person(con, world, person_id)
    if row is None:
        return []
    current_year = _current_year(con, world)
    trait_slots = _trait_slots_for_world(world)
    person = _person_from_row(row, trait_slots)
    events = _person_event_rows(con, world, person_id, include_feed_hidden=True)
    settlement = _settlement_history_entries(
        con, world, events, person_id, person, current_year
    )
    refuge = _outlaw_refuge_history_entries(
        con, world, events, person_id, person, current_year
    )
    custody = _custody_history_entries(con, world, events, person, current_year)
    return _residence_history_entries(settlement, refuge, custody)


def _consecutive_move_years(con: sqlite3.Connection, person_id: int) -> int:
    if not _table_exists(con, "simulation_event_moves"):
        return 0
    rows = con.execute(
        """
        SELECT e.sim_year
        FROM simulation_events e
        JOIN simulation_event_moves m ON m.event_id = e.id
        WHERE e.event_type = 'settlement_moved'
          AND m.moved_person_id = ?
        ORDER BY e.sim_year ASC, e.id ASC
        """,
        (int(person_id),),
    ).fetchall()
    years = [int(row["sim_year"]) for row in rows if row["sim_year"] is not None]
    if not years:
        return 0
    best = streak = 1
    for index in range(1, len(years)):
        if years[index] == years[index - 1] + 1:
            streak += 1
            best = max(best, streak)
        else:
            streak = 1
    return best


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def audit_residence_intervals(
    con: sqlite3.Connection,
    *,
    world: str = "default",
) -> list[dict[str, object]]:
    if not _table_exists(con, "simulation_people"):
        return []
    world_clause = "WHERE is_alive = 1"
    params: tuple[object, ...] = ()
    if "world" in {row[1] for row in con.execute("PRAGMA table_info(simulation_people)")}:
        world_clause = "WHERE world = ? AND is_alive = 1"
        params = (world,)
    person_ids = [
        int(row["person_id"])
        for row in con.execute(
            f"SELECT person_id FROM simulation_people {world_clause} ORDER BY person_id",
            params,
        ).fetchall()
    ]
    findings: list[dict[str, object]] = []
    for person_id in person_ids:
        intervals = _residence_rows_for_person(con, world, person_id)
        for left_index, left in enumerate(intervals):
            for right in intervals[left_index + 1 :]:
                if _intervals_overlap(left, right):
                    findings.append(
                        {
                            "category": "overlapping_residence",
                            "person_id": person_id,
                            "detail": (
                                f"{left.get('residence_kind')}:{left.get('place_label')} "
                                f"{left.get('start_year')}-{left.get('end_year')} overlaps "
                                f"{right.get('residence_kind')}:{right.get('place_label')} "
                                f"{right.get('start_year')}-{right.get('end_year')}"
                            ),
                        }
                    )
                    break
        for entry in intervals:
            if str(entry.get("residence_kind") or "") != "refuge":
                continue
            refuge_end = _history_int(entry.get("end_year"))
            refuge_start = _history_int(entry.get("start_year"))
            if refuge_start is None or refuge_end is None:
                continue
            for other in intervals:
                if other is entry:
                    continue
                if str(other.get("residence_kind") or "") == "refuge":
                    continue
                other_start = _history_int(other.get("start_year"))
                if other_start is None:
                    continue
                if refuge_start < other_start <= refuge_end:
                    findings.append(
                        {
                            "category": "open_refuge_after_later_residence",
                            "person_id": person_id,
                            "detail": (
                                f"refuge {entry.get('place_label')} open through {refuge_end} "
                                f"after {other.get('residence_kind')}:{other.get('place_label')} "
                                f"starts {other_start}"
                            ),
                        }
                    )
                    break
        move_streak = _consecutive_move_years(con, person_id)
        if move_streak >= 3:
            findings.append(
                {
                    "category": "consecutive_settlement_moves",
                    "person_id": person_id,
                    "detail": f"{move_streak} consecutive years with settlement_moved",
                }
            )
    return findings


def _default_save_path(world: str) -> Path:
    return _ROOT / "worlds" / world / "save.sqlite"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", default="default")
    parser.add_argument("--save-db", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    save_path = args.save_db or _default_save_path(args.world)
    if not save_path.exists():
        print(f"save database not found: {save_path}", file=sys.stderr)
        return 1
    with closing(sqlite3.connect(save_path)) as con:
        con.row_factory = sqlite3.Row
        ensure_checkpoint_schema(con)
        findings = audit_residence_intervals(con, world=args.world)
    rows = findings or [{"category": "ok", "person_id": "", "detail": "no issues found"}]
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["category", "person_id", "detail"])
            writer.writeheader()
            writer.writerows(rows)
    for row in rows:
        print(f"{row['category']}\tperson={row['person_id']}\t{row['detail']}")
    return 1 if any(row["category"] != "ok" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
