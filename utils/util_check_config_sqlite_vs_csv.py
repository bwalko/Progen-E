"""Exit 1 if ``worlds/<world>/config.sqlite`` may be stale vs ``config/*.csv`` mtimes.

Use after editing CSVs to confirm you ran ``util_load_config.py``, or in a manual
pre-flight before long runs. Compares file modification times only (not content).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from library.config_import import config_csv_paths
from library.world_paths import config_db_path

# Allow small skew between CSV and SQLite write completion (seconds).
_MTIME_SLACK_SEC = 2.0


def main() -> None:
    p = argparse.ArgumentParser(
        description="Fail if config.sqlite is older than the newest config/*.csv mtime."
    )
    p.add_argument(
        "--world",
        default="default",
        help="World folder under worlds/ (default: default)",
    )
    args = p.parse_args()
    world_id = str(args.world).strip()
    if not world_id:
        raise SystemExit("world id must be non-empty")

    csv_files = config_csv_paths()
    if not csv_files:
        raise SystemExit("No CSV files found under config/ (see library.config_import)")

    newest_csv_mtime = max(fp.stat().st_mtime for fp in csv_files)
    newest_name = max(csv_files, key=lambda fp: fp.stat().st_mtime).name

    out = config_db_path(world_id)
    if not out.is_file():
        print(
            f"Missing config DB: {out}\n"
            f"Run: python utils/util_load_config.py --world {world_id}",
            file=sys.stderr,
        )
        sys.exit(1)

    db_mtime = out.stat().st_mtime
    if db_mtime + _MTIME_SLACK_SEC < newest_csv_mtime:
        print(
            f"Config SQLite may be stale:\n"
            f"  {out}\n"
            f"  DB mtime is older than newest CSV ({newest_name}) by more than "
            f"{_MTIME_SLACK_SEC:g}s.\n"
            f"Run: python utils/util_load_config.py --world {world_id}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"OK: {out} is at least as new as config/*.csv (newest CSV: {newest_name}).")


if __name__ == "__main__":
    main()
