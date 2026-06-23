"""Refresh cached Narrative Heat / ARI scores in a world's save.sqlite."""

from __future__ import annotations

import argparse
import importlib.util
import json
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
    parser.add_argument(
        "--debug-breakdown",
        action="store_true",
        help="Print selected people's v3 score breakdown and texture flags after refresh.",
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
        conn.row_factory = sqlite3.Row
        count = _SCORES.refresh_person_archive_scores(
            conn,
            person_ids=args.person_ids,
            simulation_year=args.year,
        )
        conn.commit()
        debug_rows = []
        if args.debug_breakdown and args.person_ids:
            placeholders = ", ".join("?" for _ in args.person_ids)
            debug_rows = conn.execute(
                f"""
                SELECT
                    s.person_id,
                    COALESCE(TRIM(COALESCE(p.first_name, '') || ' ' || COALESCE(p.last_name, '')), '') AS name,
                    s.narrative_heat_total,
                    s.archive_recognition_index,
                    s.hidden_heat,
                    s.violet_marginalia_score,
                    s.violet_marginalia,
                    s.recognition_bucket,
                    s.recognition_scope,
                    s.infamy_gap,
                    s.prestige_gap,
                    s.score_breakdown_json,
                    s.texture_flags_json,
                    s.component_json
                FROM simulation_person_archive_scores s
                LEFT JOIN simulation_people p ON p.person_id = s.person_id
                WHERE s.person_id IN ({placeholders})
                ORDER BY s.person_id
                """,
                tuple(args.person_ids),
            ).fetchall()
    scope = "selected people" if args.person_ids else "all people"
    print(f"refreshed {count} archive score rows for {scope} in {save_path}")
    if args.debug_breakdown and args.person_ids:
        for row in debug_rows:
            breakdown = json.loads(row["score_breakdown_json"] or "{}")
            texture_flags = json.loads(row["texture_flags_json"] or "[]")
            component = json.loads(row["component_json"] or "{}")
            print()
            print(f"person {row['person_id']} {row['name']}".rstrip())
            print(
                "scores: "
                f"heat={float(row['narrative_heat_total']):.1f} "
                f"ari={float(row['archive_recognition_index']):.1f} "
                f"hidden={float(row['hidden_heat']):.1f} "
                f"violet={float(row['violet_marginalia_score']):.2f} "
                f"scope={row['recognition_scope']} "
                f"quadrant={row['recognition_bucket']}"
            )
            channels = breakdown.get("channels") or {}
            caps = breakdown.get("caps") or {}
            arcs = breakdown.get("arc_bonuses") or {}
            for key in sorted(channels):
                print(f"  {key}: {float(channels[key]):.1f}")
            if caps:
                print(
                    "  latent_potential_cap: "
                    f"{float(caps.get('latent_potential_cap') or 0.0):.1f}; "
                    "capped_from "
                    f"{float(caps.get('latent_potential_capped_from') or 0.0):.1f}"
                )
                print(
                    "  repeat_pattern_damped_from: "
                    f"{float(caps.get('repeat_pattern_damped_from') or 0.0):.1f}"
                )
            for key in sorted(arcs):
                if float(arcs[key] or 0.0):
                    print(f"  {key}: {float(arcs[key]):.1f}")
            print(f"  infamy_gap: {float(row['infamy_gap']):.1f}")
            print(f"  prestige_gap: {float(row['prestige_gap']):.1f}")
            if texture_flags:
                print("  texture_flags:")
                for flag in texture_flags:
                    print(
                        "    "
                        + json.dumps(flag, ensure_ascii=False, sort_keys=True)
                    )
            summary = component.get("summary")
            if summary:
                print(f"  summary: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
