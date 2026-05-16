"""Load every ``config/*.csv`` into a per-world config SQLite (``worlds/<id>/config.sqlite``).

CSV files are authoritative; this command rebuilds the config database from them.
Default world id is ``default`` (Progen-E layout).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from library.config_import import (
    config_csv_paths,
    load_all_csvs_into_sqlite,
    table_name_from_csv,
)
from library.world_paths import config_db_path, world_directory


def main() -> None:
    p = argparse.ArgumentParser(description="Import config/*.csv into worlds/<world_id>/config.sqlite")
    p.add_argument(
        "--world",
        default="default",
        help="World folder name under worlds/ (default: default)",
    )
    args = p.parse_args()
    world_id = str(args.world).strip()
    if not world_id:
        raise SystemExit("world id must be non-empty")

    csv_files = config_csv_paths()
    if not csv_files:
        raise SystemExit(f"No CSV files found (see library.config_import)")

    out = config_db_path(world_id)
    world_directory(world_id).mkdir(parents=True, exist_ok=True)
    counts = load_all_csvs_into_sqlite(out)
    for path in csv_files:
        tname = table_name_from_csv(path)
        print(f"{path.name} -> {tname} ({counts.get(tname, 0)} rows)")
    print(f"Database: {out}")


if __name__ == "__main__":
    main()
