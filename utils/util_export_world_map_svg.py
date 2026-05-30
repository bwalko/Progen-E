"""Export generated world geometry as an SVG debug map.

Typical usage::

    python utils/util_load_config.py --world default
    python utils/util_export_world_map_svg.py --world-id default --output temp/world_map.svg
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from library.world_map_geometry import build_world_map_debug_data, build_world_map_geometry  # noqa: E402
from library.world_map_svg import (  # noqa: E402
    load_world_map_overlays,
    render_world_map_svg,
)
from library.world_paths import config_db_path, derive_save_db_path_from_config  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--world-id", default="default", help="World id to render.")
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Explicit config.sqlite path. Defaults to worlds/<world-id>/config.sqlite.",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("temp/world_map.svg"),
        help="SVG output path (default: temp/world_map.svg).",
    )
    p.add_argument("--width", type=int, default=1200, help="SVG viewBox width.")
    p.add_argument("--height", type=int, default=800, help="SVG viewBox height.")
    p.add_argument("--no-labels", action="store_true", help="Omit feature and region labels.")
    p.add_argument("--straight-edges", action="store_true", help="Render raw polygon edges without noisy displacement.")
    p.add_argument(
        "--debug-output",
        type=Path,
        default=None,
        help="Optional JSON output with map comparison/debug metrics.",
    )
    p.add_argument(
        "--save",
        type=Path,
        default=None,
        help="Optional save.sqlite for settlement and polity overlays.",
    )
    p.add_argument("--no-overlays", action="store_true", help="Do not load save.sqlite overlays.")
    p.add_argument(
        "--include-inactive-settlements",
        action="store_true",
        help="Include inactive settlement markers when rendering overlays.",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    cfg = args.config if args.config is not None else config_db_path(args.world_id)
    save_path = args.save
    if save_path is None and not args.no_overlays:
        save_path = derive_save_db_path_from_config(cfg)
    geometry = build_world_map_geometry(
        world=args.world_id,
        db_path=cfg,
        save_db_path=save_path,
    )
    overlays = (
        None
        if args.no_overlays
        else load_world_map_overlays(
            geometry=geometry,
            save_db_path=save_path,
            include_inactive_settlements=args.include_inactive_settlements,
        )
    )
    svg = render_world_map_svg(
        geometry,
        width=max(200, int(args.width)),
        height=max(200, int(args.height)),
        noisy_edges=not args.straight_edges,
        labels=not args.no_labels,
        overlays=overlays,
    )
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(svg, encoding="utf-8", newline="\n")
    print(f"wrote {output}")
    if args.debug_output is not None:
        debug_output = args.debug_output.resolve()
        debug_output.parent.mkdir(parents=True, exist_ok=True)
        debug_output.write_text(
            json.dumps(build_world_map_debug_data(geometry), indent=2, sort_keys=True),
            encoding="utf-8",
            newline="\n",
        )
        print(f"wrote {debug_output}")


if __name__ == "__main__":
    main()

