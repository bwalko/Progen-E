"""Write event-history tuning reports from a world save DB.

Examples:

    python utils/util_event_history_report.py --world default
    python utils/util_event_history_report.py --save-db worlds/default/save.sqlite --output-dir temp/event_history_report/default
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from library.event_history_report import (  # noqa: E402
    INCIDENT_EVENT_TYPES,
    build_event_history_report,
    format_event_history_summary,
    write_event_history_report,
)
from library.world_save import ensure_checkpoint_schema  # noqa: E402


def _parse_event_types(raw: str) -> set[str] | None:
    text = str(raw or "").strip()
    if not text or text.lower() == "all":
        return None
    return {part.strip() for part in text.replace(";", ",").split(",") if part.strip()}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", default="default", help="World folder under worlds/.")
    parser.add_argument(
        "--save-db",
        type=Path,
        default=None,
        help="Explicit save.sqlite path. Overrides --world.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Report directory. Defaults to temp/event_history_report/<world>.",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=12,
        help="Public chronicle prose rows to include (default: 12).",
    )
    parser.add_argument(
        "--sample-event-types",
        default=",".join(sorted(INCIDENT_EVENT_TYPES)),
        help="Comma-separated event types for prose samples, or 'all'.",
    )
    parser.add_argument(
        "--ensure-schema",
        action="store_true",
        help="Run ensure_checkpoint_schema before reporting.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    world = str(args.world or "default").strip() or "default"
    save_db = args.save_db or (_ROOT / "worlds" / world / "save.sqlite")
    output_dir = args.output_dir or (_ROOT / "temp" / "event_history_report" / world)
    sample_types = _parse_event_types(str(args.sample_event_types or ""))
    if not save_db.exists():
        raise FileNotFoundError(save_db)

    with closing(sqlite3.connect(save_db)) as conn:
        conn.row_factory = sqlite3.Row
        if args.ensure_schema:
            ensure_checkpoint_schema(conn)
            conn.commit()
        report = build_event_history_report(
            conn,
            save_path=save_db,
            sample_limit=max(0, int(args.sample_limit)),
            sample_event_types=sample_types,
        )
    write_event_history_report(report, output_dir)
    print(format_event_history_summary(report), end="")
    print(f"report_dir: {output_dir.resolve()}")


if __name__ == "__main__":
    main()
