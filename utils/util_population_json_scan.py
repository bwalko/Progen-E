"""Scan population_growth_simulation_people.json for highlights (CLI).

- Top job_prosperity_01 among living people at export
- Most biological children (count others with father_id or mother_id)
- Largest per-person max |mind_body - genome| across shared trait keys
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_JSON = _ROOT / "unit_test" / "population_growth_simulation_people.json"


def _living(cur: dict) -> bool:
    return cur.get("deathyear") is None


def _max_trait_delta(ac: dict) -> tuple[float, str | None]:
    g = ac.get("genome") or {}
    mb = ac.get("mind_body") or {}
    best = 0.0
    best_key: str | None = None
    for k, gv in g.items():
        if k not in mb:
            continue
        try:
            d = abs(float(mb[k]) - float(gv))
        except (TypeError, ValueError):
            continue
        if d > best:
            best = d
            best_key = str(k)
    return best, best_key


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "json_path",
        type=Path,
        nargs="?",
        default=_DEFAULT_JSON,
        help=f"path to people JSON (default: {_DEFAULT_JSON})",
    )
    p.add_argument(
        "--top",
        type=int,
        default=8,
        metavar="N",
        help="how many rows to show per table (default: 8)",
    )
    args = p.parse_args()
    path = Path(args.json_path).resolve()
    raw = json.loads(path.read_text(encoding="utf-8"))
    people: list[dict] = raw.get("people") or []
    n = max(1, int(args.top))

    # --- job prosperity (living) ---
    by_pros: list[tuple[float, int, str, str | None]] = []
    for rec in people:
        cur = rec.get("current_simulation_state") or {}
        if not _living(cur):
            continue
        jp = cur.get("job_prosperity_01")
        if jp is None:
            continue
        try:
            v = float(jp)
        except (TypeError, ValueError):
            continue
        ac = rec.get("at_creation_or_birth") or {}
        fn = str(ac.get("first_name") or "")
        ln = str(ac.get("last_name") or "").strip()
        name = f"{fn} {ln}".strip() or f"id={rec.get('person_id')}"
        job = cur.get("job")
        by_pros.append((v, int(rec["person_id"]), name, job if job else None))
    by_pros.sort(key=lambda t: (-t[0], t[1]))

    # --- children count ---
    child_count: defaultdict[int, int] = defaultdict(int)
    for rec in people:
        for k in ("father_id", "mother_id"):
            pid = rec.get(k)
            if pid is not None:
                child_count[int(pid)] += 1
    by_kids = sorted(child_count.items(), key=lambda t: (-t[1], t[0]))

    # --- mind vs genome ---
    by_delta: list[tuple[float, str | None, int, str, bool]] = []
    for rec in people:
        ac = rec.get("at_creation_or_birth") or {}
        cur = rec.get("current_simulation_state") or {}
        dmax, trait = _max_trait_delta(ac)
        fn = str(ac.get("first_name") or "")
        ln = str(ac.get("last_name") or "").strip()
        name = f"{fn} {ln}".strip() or f"id={rec.get('person_id')}"
        by_delta.append(
            (dmax, trait, int(rec["person_id"]), name, _living(cur))
        )
    by_delta.sort(key=lambda t: (-t[0], t[2]))

    as_of = raw.get("as_of_simulation_year")
    seed = raw.get("random_seed")
    print(f"file={path}")
    print(f"as_of_simulation_year={as_of} random_seed={seed} people_count={len(people)}")
    print()

    print(f"Top job_prosperity_01 (living, top {n})")
    print("prosperity_01\tperson_id\tname\tjob")
    for v, pid, name, job in by_pros[:n]:
        job_s = (job or "").replace("\t", " ")
        print(f"{v:.5f}\t{pid}\t{name}\t{job_s}")

    print()
    print(f"Most biological children (by parent id, top {n})")
    print("children\tperson_id\tname\tdead?")
    id_to_name: dict[int, str] = {}
    id_dead: dict[int, bool] = {}
    for rec in people:
        pid = int(rec["person_id"])
        ac = rec.get("at_creation_or_birth") or {}
        cur = rec.get("current_simulation_state") or {}
        fn = str(ac.get("first_name") or "")
        ln = str(ac.get("last_name") or "").strip()
        id_to_name[pid] = f"{fn} {ln}".strip() or f"id={pid}"
        id_dead[pid] = not _living(cur)
    for parent_id, count in by_kids[:n]:
        dead = id_dead.get(parent_id, True)
        print(f"{count}\t{parent_id}\t{id_to_name.get(parent_id, '?')}\t{dead}")

    print()
    print(f"Largest max |mind_body - genome| per person (top {n})")
    print("max_delta\ttrait\tperson_id\tname\tliving")
    for dmax, trait, pid, name, live in by_delta[:n]:
        tr = trait or "?"
        print(f"{dmax:.4f}\t{tr}\t{pid}\t{name}\t{live}")


if __name__ == "__main__":
    main()
