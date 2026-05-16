"""Print current open region-to-polity ownership from ``save.sqlite``.

Reads ``simulation_polity_territory`` (open rows: ``until_sim_year IS NULL``) joined
with ``simulation_polities``. Typical::

    python utils/util_load_config.py --world default
    python utils/run_population_simulation.py --years 50
    python utils/util_print_polity_map.py --world-id default

Or with an explicit save path (e.g. temp test DB)::

    python utils/util_print_polity_map.py --save path/to/save.sqlite --sim-world default
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from library.world_paths import config_db_path, derive_save_db_path_from_config
from library.world_save import ensure_checkpoint_schema


def _print_map(save_path: Path, *, sim_world: str) -> None:
    w = sim_world.strip()
    with sqlite3.connect(save_path) as conn:
        conn.row_factory = sqlite3.Row
        ensure_checkpoint_schema(conn)
        rows = conn.execute(
            """
            SELECT
                t.target_id AS region_id,
                p.polity_id,
                p.name AS polity_name,
                p.polity_type_id,
                p.status AS polity_status,
                p.parent_polity_id
            FROM simulation_polity_territory t
            JOIN simulation_polities p
              ON p.world = t.world AND p.polity_id = t.polity_id
            WHERE t.world = ?
              AND t.until_sim_year IS NULL
              AND t.target_kind = 'region'
            ORDER BY t.target_id, p.polity_id
            """,
            (w,),
        ).fetchall()

    if not rows:
        print(f"(no open region territory rows for world={w!r} in {save_path})")
        return

    hdr = (
        "region_id",
        "polity_id",
        "polity_name",
        "polity_type_id",
        "polity_status",
        "parent_polity_id",
    )
    print(" | ".join(hdr))
    print("-+-".join("-" * len(h) for h in hdr))
    for r in rows:
        print(
            " | ".join(
                str(r[c] if r[c] is not None else "")
                for c in hdr
            )
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--world-id",
        default="default",
        help="World folder id (used to locate save.sqlite if --save omitted).",
    )
    ap.add_argument(
        "--save",
        type=Path,
        default=None,
        help="Explicit path to save.sqlite (overrides --world-id layout).",
    )
    ap.add_argument(
        "--sim-world",
        default="default",
        help="Simulation ``world`` column in checkpoint tables (usually 'default').",
    )
    args = ap.parse_args()

    save_path = args.save
    if save_path is None:
        cfg = config_db_path(args.world_id)
        save_path = derive_save_db_path_from_config(cfg)
    save_path = Path(save_path).resolve()
    if not save_path.exists():
        raise SystemExit(f"save DB not found: {save_path}")

    _print_map(save_path, sim_world=str(args.sim_world))


if __name__ == "__main__":
    main()
