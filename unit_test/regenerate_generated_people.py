"""Write ``generated_people.txt`` with random ``Person`` lines from ``generate_person_random``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from library.config_import import refresh_world_config_from_csv  # noqa: E402
from library.generator import generate_person_random  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regenerate unit_test/generated_people.txt using generate_person_random."
    )
    parser.add_argument(
        "-n",
        "--count",
        type=int,
        default=20,
        help="Number of Person lines to write (default: 20).",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=PROJECT_ROOT / "unit_test" / "generated_people.txt",
        help="Output path (default: unit_test/generated_people.txt under project root).",
    )
    parser.add_argument(
        "--skip-config",
        action="store_true",
        help="Skip reloading config/*.csv into worlds/default/config.sqlite (use existing DB).",
    )
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be at least 1")
    if not args.skip_config:
        refresh_world_config_from_csv("default")
    out = args.output if args.output.is_absolute() else (PROJECT_ROOT / args.output)
    lines = "\n".join(repr(generate_person_random()) for _ in range(args.count)) + "\n"
    out.write_text(lines, encoding="utf-8")
    print(f"Wrote {args.count} line(s) to {out}")


if __name__ == "__main__":
    main()
