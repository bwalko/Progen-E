"""Audit detailed and non-detailed settlement movement rates from save.sqlite.

Writes a summary TSV (one row per metric) and an optional per-year breakdown TSV::

    python utils/util_audit_movement_rates.py --world default
    python utils/util_audit_movement_rates.py --save-db worlds/default/save.sqlite --output temp/movement_rate_audit.tsv
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from library.world_save import ensure_checkpoint_schema


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _current_world_year(conn: sqlite3.Connection) -> int | None:
    if not _table_exists(conn, "world_state"):
        return None
    row = conn.execute("SELECT current_year FROM world_state LIMIT 1").fetchone()
    if row is None:
        return None
    try:
        return int(row["current_year"] if isinstance(row, sqlite3.Row) else row[0])
    except (TypeError, ValueError):
        return None


def _alive_detailed_count(conn: sqlite3.Connection, *, world_year: int | None) -> int:
    if not _table_exists(conn, "simulation_people"):
        return 0
    if world_year is None:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM simulation_people WHERE is_alive = 1"
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT COUNT(*) AS c FROM simulation_people
            WHERE is_alive = 1 AND (deathyear IS NULL OR deathyear > ?)
            """,
            (int(world_year),),
        ).fetchone()
    return int(row["c"] if row else 0)


def _alive_nondetailed_count(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "simulation_people_nondetailed"):
        return 0
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM simulation_people_nondetailed WHERE is_alive = 1"
    ).fetchone()
    return int(row["c"] if row else 0)


def _detailed_moves(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    if not _table_exists(conn, "simulation_event_moves_readable"):
        return []
    return list(
        conn.execute(
            """
            SELECT
              sim_year,
              moved_person_id,
              from_settlement_id,
              to_settlement_id,
              from_region_id,
              to_region_id,
              cross_region,
              move_reason
            FROM simulation_event_moves_readable
            WHERE moved_person_id IS NOT NULL
            ORDER BY sim_year, event_id
            """
        ).fetchall()
    )


def _nondetailed_migration_events(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    if not _table_exists(conn, "simulation_events_readable"):
        return []
    return list(
        conn.execute(
            """
            SELECT sim_year, event_type, payload_json
            FROM simulation_events_readable
            WHERE event_type = 'nondetailed_settlement_migration'
            ORDER BY sim_year, id
            """
        ).fetchall()
    )


def _parse_migrant_count(payload_json: str | None) -> int:
    if not payload_json:
        return 0
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        return 0
    try:
        return max(0, int(payload.get("migrant_count") or 0))
    except (TypeError, ValueError):
        return 0


def _parse_route(payload_json: str | None) -> tuple[str, str]:
    if not payload_json:
        return "", ""
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        return "", ""
    src = str(payload.get("from_settlement_id") or "")
    dst = str(payload.get("to_settlement_id") or "")
    return src, dst


def collect_movement_audit_summary(conn: sqlite3.Connection) -> list[dict[str, object]]:
    """Return summary metric rows for movement-rate diagnostics."""
    ensure_checkpoint_schema(conn)
    world_year = _current_world_year(conn)
    detailed_alive = _alive_detailed_count(conn, world_year=world_year)
    nondetailed_alive = _alive_nondetailed_count(conn)

    detailed_moves = _detailed_moves(conn)
    nd_events = _nondetailed_migration_events(conn)

    moves_by_year: Counter[int] = Counter()
    reason_counts: Counter[str] = Counter()
    cross_region_moves = 0
    person_move_counts: Counter[int] = Counter()
    person_destinations: dict[int, list[str]] = defaultdict(list)
    return_moves = 0

    for row in detailed_moves:
        year = int(row["sim_year"])
        moves_by_year[year] += 1
        reason = str(row["move_reason"] or "unknown").strip() or "unknown"
        reason_counts[reason] += 1
        if int(row["cross_region"] or 0):
            cross_region_moves += 1
        pid = int(row["moved_person_id"])
        person_move_counts[pid] += 1
        dest = str(row["to_settlement_id"] or "")
        if dest:
            hist = person_destinations[pid]
            if hist and hist[-1] != dest and dest in hist:
                return_moves += 1
            hist.append(dest)

    nd_moves_by_year: Counter[int] = Counter()
    nd_route_counts: Counter[str] = Counter()
    nd_migrants_total = 0
    for row in nd_events:
        year = int(row["sim_year"])
        count = _parse_migrant_count(row["payload_json"])
        nd_moves_by_year[year] += count
        nd_migrants_total += count
        src, dst = _parse_route(row["payload_json"])
        if src or dst:
            nd_route_counts[f"{src} -> {dst}"] += count

    total_detailed_moves = sum(moves_by_year.values())
    years_with_moves = len(moves_by_year) or 1
    avg_detailed_moves_per_active_year = total_detailed_moves / years_with_moves

    per_person_counts = list(person_move_counts.values())
    median_moves = statistics.median(per_person_counts) if per_person_counts else 0.0
    p90_moves = (
        statistics.quantiles(per_person_counts, n=10)[8]
        if len(per_person_counts) >= 10
        else (max(per_person_counts) if per_person_counts else 0.0)
    )

    nd_years = len(nd_moves_by_year) or 1
    avg_nd_migrants_per_active_year = nd_migrants_total / nd_years

    summary: list[dict[str, object]] = [
        {"metric": "world_year", "value": world_year if world_year is not None else ""},
        {"metric": "detailed_alive_end", "value": detailed_alive},
        {"metric": "nondetailed_alive_end", "value": nondetailed_alive},
        {"metric": "detailed_moves_total", "value": total_detailed_moves},
        {"metric": "detailed_moves_cross_region", "value": cross_region_moves},
        {"metric": "detailed_moves_return_to_prior_settlement", "value": return_moves},
        {"metric": "detailed_movers_distinct_people", "value": len(person_move_counts)},
        {"metric": "detailed_moves_median_per_mover", "value": round(median_moves, 4)},
        {"metric": "detailed_moves_p90_per_mover", "value": round(float(p90_moves), 4)},
        {
            "metric": "detailed_move_share_if_all_alive_moved_once",
            "value": round(total_detailed_moves / max(1, detailed_alive), 6),
        },
        {
            "metric": "detailed_avg_moves_per_active_move_year",
            "value": round(avg_detailed_moves_per_active_year, 4),
        },
        {"metric": "nondetailed_migrants_total", "value": nd_migrants_total},
        {
            "metric": "nondetailed_avg_migrants_per_active_migration_year",
            "value": round(avg_nd_migrants_per_active_year, 4),
        },
        {
            "metric": "nondetailed_migrant_share_vs_alive_end",
            "value": round(nd_migrants_total / max(1, nondetailed_alive), 6),
        },
    ]

    for reason, count in reason_counts.most_common(12):
        summary.append(
            {
                "metric": f"detailed_move_reason:{reason}",
                "value": count,
            }
        )

    for route, count in nd_route_counts.most_common(8):
        summary.append(
            {
                "metric": f"nondetailed_route:{route}",
                "value": count,
            }
        )

    return summary


def collect_movement_audit_yearly(conn: sqlite3.Connection) -> list[dict[str, object]]:
    """Return per-year detailed and nondetailed movement counts."""
    ensure_checkpoint_schema(conn)
    detailed_moves = _detailed_moves(conn)
    nd_events = _nondetailed_migration_events(conn)

    moves_by_year: Counter[int] = Counter()
    for row in detailed_moves:
        moves_by_year[int(row["sim_year"])] += 1

    nd_by_year: Counter[int] = Counter()
    for row in nd_events:
        nd_by_year[int(row["sim_year"])] += _parse_migrant_count(row["payload_json"])

    years = sorted(set(moves_by_year) | set(nd_by_year))
    return [
        {
            "sim_year": year,
            "detailed_moves": int(moves_by_year.get(year, 0)),
            "nondetailed_migrants": int(nd_by_year.get(year, 0)),
        }
        for year in years
    ]


def write_movement_audit_tsv(
    conn: sqlite3.Connection,
    output_path: Path,
) -> tuple[int, int]:
    summary = collect_movement_audit_summary(conn)
    yearly = collect_movement_audit_yearly(conn)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["metric", "value"], delimiter="\t")
        writer.writeheader()
        for row in summary:
            writer.writerow(row)

    yearly_path = output_path.with_name(output_path.stem + ".yearly.tsv")
    with yearly_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["sim_year", "detailed_moves", "nondetailed_migrants"],
            delimiter="\t",
        )
        writer.writeheader()
        for row in yearly:
            writer.writerow(row)
    return len(summary), len(yearly)


def main() -> None:
    p = argparse.ArgumentParser(description="Audit detailed and nondetailed movement rates.")
    p.add_argument("--world", default="default", help="World id under worlds/<id>/")
    p.add_argument("--save-db", type=Path, help="Path to save.sqlite")
    p.add_argument(
        "--output",
        type=Path,
        default=_ROOT / "temp" / "movement_rate_audit.tsv",
        help="Summary TSV path (yearly written alongside with .yearly.tsv suffix)",
    )
    args = p.parse_args()
    save_db = args.save_db or (_ROOT / "worlds" / args.world.strip() / "save.sqlite")
    if not save_db.is_file():
        raise SystemExit(f"save database not found: {save_db}")

    with closing(sqlite3.connect(save_db)) as conn:
        conn.row_factory = sqlite3.Row
        summary_rows, yearly_rows = write_movement_audit_tsv(conn, args.output)

    yearly_path = args.output.with_name(args.output.stem + ".yearly.tsv")
    print(f"summary_rows={summary_rows}")
    print(f"yearly_rows={yearly_rows}")
    print(f"output={args.output.resolve()}")
    print(f"yearly_output={yearly_path.resolve()}")


if __name__ == "__main__":
    main()
