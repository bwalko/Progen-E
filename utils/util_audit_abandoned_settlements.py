"""Audit abandoned settlements vs alive detailed and non-detailed residents.

Writes TSV rows for abandoned settlements and recent low-resolution promotion
samples in ``save.sqlite``::

    python utils/util_audit_abandoned_settlements.py --world default
    python utils/util_audit_abandoned_settlements.py --save-db worlds/default/save.sqlite --output temp/abandoned_settlement_audit.tsv
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from library.world_save import ensure_checkpoint_schema

_COLUMNS = (
    "audit_category",
    "settlement_id",
    "region_id",
    "status",
    "abandoned_sim_year",
    "founding_reason",
    "level",
    "resident_count_checkpoint",
    "consecutive_empty_years",
    "detailed_alive",
    "nondetailed_alive",
    "mixed_alive",
    "food_pressure",
    "stability",
    "prosperity_pool",
    "last_event_year",
    "last_event_type",
    "abandon_reason",
)


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
    val = row["current_year"] if isinstance(row, sqlite3.Row) else row[0]
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _detailed_alive_by_settlement(conn: sqlite3.Connection, *, world_year: int | None) -> dict[str, int]:
    if not _table_exists(conn, "simulation_people"):
        return {}
    year_clause = ""
    params: tuple[object, ...] = ()
    if world_year is not None:
        year_clause = "AND (p.deathyear IS NULL OR p.deathyear > ?)"
        params = (int(world_year),)
    rows = conn.execute(
        f"""
        SELECT
          COALESCE(cs.settlement_id, bs.settlement_id) AS settlement_id,
          COUNT(*) AS c
        FROM simulation_people p
        LEFT JOIN simulation_settlement_lookup cs
          ON cs.settlement_key = p.current_settlement_key
        LEFT JOIN simulation_settlement_lookup bs
          ON bs.settlement_key = p.birthplace_settlement_key
        WHERE p.is_alive = 1
          {year_clause}
          AND COALESCE(cs.settlement_id, bs.settlement_id) IS NOT NULL
        GROUP BY COALESCE(cs.settlement_id, bs.settlement_id)
        """,
        params,
    ).fetchall()
    return {str(r["settlement_id"]): int(r["c"]) for r in rows}


def _nondetailed_alive_by_settlement(conn: sqlite3.Connection) -> dict[str, int]:
    if not _table_exists(conn, "simulation_people_nondetailed_readable"):
        return {}
    rows = conn.execute(
        """
        SELECT
          COALESCE(current_settlement_id, birthplace_settlement_id) AS settlement_id,
          COUNT(*) AS c
        FROM simulation_people_nondetailed_readable
        WHERE is_alive = 1
          AND COALESCE(current_settlement_id, birthplace_settlement_id) IS NOT NULL
        GROUP BY COALESCE(current_settlement_id, birthplace_settlement_id)
        """
    ).fetchall()
    return {str(r["settlement_id"]): int(r["c"]) for r in rows}


def _payload_dict(raw: object) -> dict[str, object]:
    if raw in (None, ""):
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        parsed = json.loads(str(raw))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_abandon_reason(raw: object) -> str:
    reason = str(raw or "").strip().lower()
    if reason in {"empty", "economic", "absorbed"}:
        return reason
    return ""


def _last_settlement_lifecycle_events(
    conn: sqlite3.Connection,
) -> dict[str, tuple[int | None, str, str]]:
    if not _table_exists(conn, "simulation_events_readable"):
        return {}
    rows = conn.execute(
        """
        SELECT settlement_id, sim_year, event_type, payload_json
        FROM simulation_events_readable
        WHERE settlement_id IS NOT NULL
          AND (
            lower(event_type) LIKE '%abandon%'
            OR lower(event_type) = 'settlement_abandoned'
            OR lower(event_type) = 'settlement_low_resolution_sample'
          )
        ORDER BY settlement_id, sim_year DESC, event_id DESC
        """
    ).fetchall()
    out: dict[str, tuple[int | None, str, str]] = {}
    for row in rows:
        sid = str(row["settlement_id"] or "")
        if not sid or sid in out:
            continue
        payload = _payload_dict(row["payload_json"])
        event_type = str(row["event_type"] or "")
        abandon_reason = _normalize_abandon_reason(payload.get("abandon_reason"))
        if not abandon_reason and "abandon" in event_type.lower():
            if int(payload.get("mixed_alive") or 0) <= 0:
                abandon_reason = "empty"
            elif event_type.lower() == "settlement_abandoned":
                abandon_reason = "economic"
        out[sid] = (
            int(row["sim_year"]) if row["sim_year"] is not None else None,
            event_type,
            abandon_reason,
        )
    return out


def collect_abandoned_settlement_audit_rows(conn: sqlite3.Connection) -> list[dict[str, object]]:
    """Return audit rows for abandoned settlements and low-resolution promotion samples."""
    ensure_checkpoint_schema(conn)
    if not _table_exists(conn, "simulation_settlements_readable"):
        return []

    world_year = _current_world_year(conn)
    detailed = _detailed_alive_by_settlement(conn, world_year=world_year)
    nondetailed = _nondetailed_alive_by_settlement(conn)
    lifecycle_events = _last_settlement_lifecycle_events(conn)

    rows = conn.execute(
        """
        SELECT
          settlement_id,
          region_id,
          status,
          abandoned_sim_year,
          founding_reason,
          level,
          population_cap AS resident_count_checkpoint,
          consecutive_empty_years,
          food_pressure,
          stability,
          prosperity_pool
        FROM simulation_settlements_readable
        WHERE lower(COALESCE(status, '')) = 'abandoned'
        ORDER BY settlement_id
        """
    ).fetchall()

    out: list[dict[str, object]] = []
    for row in rows:
        sid = str(row["settlement_id"] or "")
        d_alive = int(detailed.get(sid, 0))
        nd_alive = int(nondetailed.get(sid, 0))
        event_year, event_type, abandon_reason = lifecycle_events.get(sid, (None, "", ""))
        if not abandon_reason:
            if d_alive + nd_alive <= 0:
                abandon_reason = "empty"
            else:
                abandon_reason = "economic"
        category = {
            "empty": "abandoned_empty",
            "economic": "abandoned_economic",
            "absorbed": "abandoned_absorbed",
        }.get(abandon_reason, "abandoned_unknown")
        out.append(
            {
                "audit_category": category,
                "settlement_id": sid,
                "region_id": str(row["region_id"] or ""),
                "status": str(row["status"] or ""),
                "abandoned_sim_year": row["abandoned_sim_year"],
                "founding_reason": str(row["founding_reason"] or ""),
                "level": str(row["level"] or ""),
                "resident_count_checkpoint": int(row["resident_count_checkpoint"] or 0),
                "consecutive_empty_years": int(row["consecutive_empty_years"] or 0),
                "detailed_alive": d_alive,
                "nondetailed_alive": nd_alive,
                "mixed_alive": d_alive + nd_alive,
                "food_pressure": row["food_pressure"],
                "stability": row["stability"],
                "prosperity_pool": row["prosperity_pool"],
                "last_event_year": event_year if event_year is not None else "",
                "last_event_type": event_type,
                "abandon_reason": abandon_reason,
            }
        )

    if _table_exists(conn, "simulation_events_readable"):
        promo_rows = conn.execute(
            """
            SELECT settlement_id, MAX(sim_year) AS y
            FROM simulation_events_readable
            WHERE lower(event_type) = 'settlement_low_resolution_sample'
              AND settlement_id IS NOT NULL
            GROUP BY settlement_id
            ORDER BY settlement_id
            """
        ).fetchall()
        settlement_meta = {
            str(r["settlement_id"]): r
            for r in conn.execute(
                """
                SELECT
                  settlement_id,
                  region_id,
                  status,
                  abandoned_sim_year,
                  founding_reason,
                  level,
                  population_cap AS resident_count_checkpoint,
                  consecutive_empty_years,
                  food_pressure,
                  stability,
                  prosperity_pool
                FROM simulation_settlements_readable
                """
            ).fetchall()
        }
        for row in promo_rows:
            sid = str(row["settlement_id"] or "")
            if not sid:
                continue
            meta = settlement_meta.get(sid)
            if meta is None:
                continue
            if str(meta["status"] or "").strip().lower() == "abandoned":
                continue
            d_alive = int(detailed.get(sid, 0))
            nd_alive = int(nondetailed.get(sid, 0))
            out.append(
                {
                    "audit_category": "promoted_low_resolution_sample",
                    "settlement_id": sid,
                    "region_id": str(meta["region_id"] or ""),
                    "status": str(meta["status"] or ""),
                    "abandoned_sim_year": meta["abandoned_sim_year"],
                    "founding_reason": str(meta["founding_reason"] or ""),
                    "level": str(meta["level"] or ""),
                    "resident_count_checkpoint": int(meta["resident_count_checkpoint"] or 0),
                    "consecutive_empty_years": int(meta["consecutive_empty_years"] or 0),
                    "detailed_alive": d_alive,
                    "nondetailed_alive": nd_alive,
                    "mixed_alive": d_alive + nd_alive,
                    "food_pressure": meta["food_pressure"],
                    "stability": meta["stability"],
                    "prosperity_pool": meta["prosperity_pool"],
                    "last_event_year": int(row["y"]) if row["y"] is not None else "",
                    "last_event_type": "settlement_low_resolution_sample",
                    "abandon_reason": "",
                }
            )

    out.sort(
        key=lambda r: (
            0 if str(r["audit_category"]).startswith("abandoned") else 1,
            -int(r["mixed_alive"]),
            str(r["settlement_id"]),
        )
    )
    return out


def write_abandoned_settlement_audit_tsv(
    conn: sqlite3.Connection,
    output_path: Path,
) -> int:
    rows = collect_abandoned_settlement_audit_rows(conn)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(_COLUMNS), delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in _COLUMNS})
    return len(rows)


def main() -> None:
    p = argparse.ArgumentParser(description="Audit abandoned settlements vs mixed alive counts.")
    p.add_argument("--world", default="default", help="World id under worlds/<id>/")
    p.add_argument(
        "--save-db",
        type=Path,
        help="Path to save.sqlite (default: worlds/<world>/save.sqlite)",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=_ROOT / "temp" / "abandoned_settlement_audit.tsv",
        help="Output TSV path",
    )
    args = p.parse_args()
    save_db = args.save_db or (_ROOT / "worlds" / args.world.strip() / "save.sqlite")
    if not save_db.is_file():
        raise SystemExit(f"save database not found: {save_db}")

    with closing(sqlite3.connect(save_db)) as conn:
        conn.row_factory = sqlite3.Row
        count = write_abandoned_settlement_audit_tsv(conn, args.output)

    abandoned_empty = 0
    abandoned_economic = 0
    promoted = 0
    with_mixed = 0
    with_nd = 0
    if args.output.is_file():
        with args.output.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                category = str(row.get("audit_category") or "")
                if category == "abandoned_empty":
                    abandoned_empty += 1
                elif category.startswith("abandoned_"):
                    abandoned_economic += 1
                elif category == "promoted_low_resolution_sample":
                    promoted += 1
                if int(row.get("mixed_alive") or 0) > 0:
                    with_mixed += 1
                if int(row.get("nondetailed_alive") or 0) > 0:
                    with_nd += 1

    print(f"audit_rows={count}")
    print(f"abandoned_empty={abandoned_empty}")
    print(f"abandoned_economic_or_other={abandoned_economic}")
    print(f"promoted_low_resolution_sample={promoted}")
    print(f"with_mixed_alive={with_mixed}")
    print(f"with_nondetailed_alive={with_nd}")
    print(f"output={args.output.resolve()}")


if __name__ == "__main__":
    main()
