"""Refresh cached Narrative Heat / ARI scores in a world's save.sqlite."""

from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_score_module():
    path = ROOT / "library" / "person_archive_scores.py"
    spec = importlib.util.spec_from_file_location("_progene_person_archive_scores", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_SCORES = _load_score_module()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh cached person archive scores for a Progen-E save DB."
    )
    parser.add_argument("--world", default="default", help="World folder id.")
    parser.add_argument(
        "--save",
        type=Path,
        help="Explicit save.sqlite path; overrides --world.",
    )
    parser.add_argument(
        "--person-id",
        type=int,
        action="append",
        dest="person_ids",
        help="Refresh one person id. May be repeated. Omit for a full refresh.",
    )
    parser.add_argument(
        "--year",
        type=int,
        help="Simulation year to stamp on refreshed score rows.",
    )
    args = parser.parse_args(argv)

    save_path = (
        args.save
        if args.save is not None
        else (ROOT / "worlds" / str(args.world).strip() / "save.sqlite")
    )
    if not save_path.exists():
        parser.error(f"{save_path} does not exist")

    with sqlite3.connect(save_path) as conn:
        count = _SCORES.refresh_person_archive_scores(
            conn,
            person_ids=args.person_ids,
            simulation_year=args.year,
        )
        conn.commit()
    scope = "selected people" if args.person_ids else "all people"
    print(f"refreshed {count} archive score rows for {scope} in {save_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
