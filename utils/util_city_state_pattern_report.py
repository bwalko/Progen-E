"""Print broad city-state pattern counts from a world save DB.

Examples:

    python utils/util_city_state_pattern_report.py --world default
    python utils/util_city_state_pattern_report.py --save-db worlds/default/save.sqlite
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

CITY_STATE_PATTERN_BUCKETS: dict[str, str] = {
    "city_state_urban_consolidation": "city_founding_or_consolidation",
    "city_state_public_works": "civic_public_works_or_institution",
    "city_state_resource_dispute": "rivalry_or_resource_dispute",
    "city_state_league_formed": "league_or_hegemony",
    "city_state_hegemony_declared": "league_or_hegemony",
    "city_state_colony_status_changed": "maritime_colony_lifecycle",
    "city_state_autonomy_changed": "empire_pressure_or_autonomy",
    "city_state_civic_crisis": "civic_crisis_or_reform",
    "city_state_civic_reform": "civic_crisis_or_reform",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", default="default", help="World folder under worlds/.")
    parser.add_argument(
        "--save-db",
        type=Path,
        default=None,
        help="Explicit save.sqlite path. Overrides --world.",
    )
    return parser.parse_args()


def _summarize(save_db: Path) -> dict[str, int]:
    with closing(sqlite3.connect(save_db)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT event_type, payload_json
            FROM simulation_events
            WHERE event_type LIKE 'city_state_%'
               OR event_type IN (
                    'settlement_commercial_outpost_founded',
                    'settlement_outpost_autonomized',
                    'trade_network_recentered'
               )
            """
        ).fetchall()
    counts: Counter[str] = Counter()
    counts["total_city_state_pattern_events"] = 0
    for row in rows:
        event_type = str(row["event_type"] or "").strip()
        bucket = CITY_STATE_PATTERN_BUCKETS.get(event_type)
        if bucket is None and event_type.startswith("settlement_"):
            bucket = "maritime_colony_lifecycle"
        elif bucket is None and event_type == "trade_network_recentered":
            bucket = "maritime_colony_lifecycle"
        if bucket is None:
            bucket = "other_city_state_event"
        counts[bucket] += 1
        counts["total_city_state_pattern_events"] += 1
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            state = str(
                payload.get("autonomy_state")
                or payload.get("colony_autonomy_level")
                or ""
            ).strip()
            if state:
                counts[f"autonomy_state:{state}"] += 1
    for bucket in sorted(set(CITY_STATE_PATTERN_BUCKETS.values())):
        counts.setdefault(bucket, 0)
    return dict(sorted(counts.items()))


def main() -> None:
    args = _parse_args()
    world = str(args.world or "default").strip() or "default"
    save_db = args.save_db or (_ROOT / "worlds" / world / "save.sqlite")
    if not save_db.exists():
        raise FileNotFoundError(save_db)
    counts = _summarize(save_db)
    print("metric\tcount")
    for key in sorted(counts):
        print(f"{key}\t{counts[key]}")


if __name__ == "__main__":
    main()
