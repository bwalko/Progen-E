"""Run a population sim and summarize interesting people + genome narratives.

Examples::

    python utils/util_interesting_people.py --years 120 --seed 42
    python utils/util_interesting_people.py --years 100 --out interesting.txt

Set ``PYTHONHASHSEED=0`` for more stable pairing across runs (optional).
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import sqlite3
import sys
import tempfile
from collections import Counter
from contextlib import closing
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from library.config_import import load_all_csvs_into_sqlite  # noqa: E402
from library.population_growth_runner import run_population_growth_simulation  # noqa: E402
from library.personality_interpreter import interpret_genome_personality  # noqa: E402
from library.simulation_context import SimulationContext  # noqa: E402

_EXTREME_ABS = 92.0  # treat as "about ±99" / clipping tail
_NEAR_ZERO_ABS = 5.0  # "excelled" near ideal
_PATHOLOGICAL_CAP = 28  # max people detailed in extreme-genome section
_HIGHLY_IDEAL_MIN = 5  # count of |trait|<=_NEAR_ZERO_ABS for "many near-ideal" block
_HIGHLY_IDEAL_CAP = 35


def _load_genome_labels(db_path: Path) -> dict[str, sqlite3.Row]:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM genome").fetchall()
    out: dict[str, sqlite3.Row] = {}
    for row in rows:
        k = str(row["trait"] or "").strip()
        if k:
            out[k] = row
    return out


def _lifespan_years(rec, end_year: int) -> int:
    by = int(rec.person.birthyear)
    dy = rec.person.deathyear
    if dy is None:
        return end_year - by + 1
    return int(dy) - by + 1


def _event_counts(store_root: Path | None) -> Counter[int]:
    c: Counter[int] = Counter()
    if store_root is None:
        return c
    path = store_root / "events.csv"
    if not path.is_file():
        return c
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            for col in ("person_id", "person_a_id", "person_b_id", "child_id"):
                v = (row.get(col) or "").strip()
                if v.isdigit():
                    c[int(v)] += 1
    return c


def _extreme_traits(genome: dict[str, float], labels: dict[str, sqlite3.Row]) -> list[str]:
    lines: list[str] = []
    for trait, raw in sorted(genome.items(), key=lambda kv: -abs(kv[1])):
        v = float(raw)
        if abs(v) < _EXTREME_ABS:
            break
        row = labels.get(trait)
        if row is None:
            lines.append(f"  {trait}: {v:+.2f}")
            continue
        pole = (
            str(row["excess deviation"] or "").strip()
            if v > 0
            else str(row["deficient deviation"] or "").strip()
        )
        opt = str(row["optimal centerpoint"] or "").strip()
        lines.append(
            f"  {trait} {v:+.2f}: far from ideal '{opt}' -- reads as strongly '{pole}'."
        )
    return lines


def _excelled_traits(genome: dict[str, float], labels: dict[str, sqlite3.Row]) -> list[str]:
    lines: list[str] = []
    for trait, raw in sorted(genome.items(), key=lambda kv: abs(kv[1])):
        v = float(raw)
        if abs(v) > _NEAR_ZERO_ABS:
            continue
        row = labels.get(trait)
        if row is None:
            lines.append(f"  {trait}: {v:+.2f} (near numeric ideal)")
            continue
        opt = str(row["optimal centerpoint"] or "").strip()
        lines.append(f"  {trait} {v:+.2f}: unusually close to ideal -- excels in '{opt}'.")
    return lines


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--years", type=int, default=120, metavar="N")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--couples", type=int, default=10)
    p.add_argument(
        "--out",
        type=Path,
        default=_ROOT / "temp" / "interesting_people_last_run.txt",
        help="Write UTF-8 text here",
    )
    args = p.parse_args()
    start = 1000
    end_year = start + int(args.years) - 1

    td = Path(tempfile.mkdtemp(prefix="interesting_"))
    cfg, sav = td / "config.sqlite", td / "save.sqlite"
    load_all_csvs_into_sqlite(cfg)
    os.environ["POPULATION_GROWTH_SIM_SEED"] = str(int(args.seed))

    store_root: Path | None = None
    with SimulationContext.create(
        db_path=cfg,
        save_db_path=sav,
        world_id="default",
        world="default",
        start_year=start,
        placename_rng_salt=int(args.seed),
        refresh_config=False,
        store_flush_batch_years=max(50, int(args.years) + 1),
    ) as ctx:
        run_population_growth_simulation(
            ctx,
            sim_seed=int(args.seed),
            start_year=start,
            duration_years=int(args.years),
            starting_couples=int(args.couples),
        )
        store_root = ctx.file_store.root_dir if ctx.file_store else None
        people = list(ctx.people)
        id_to = dict(ctx.id_to_record)

    labels = _load_genome_labels(cfg)
    ev = _event_counts(store_root)

    as_mother: Counter[int] = Counter()
    as_father: Counter[int] = Counter()
    for rec in people:
        if rec.mother_id is not None:
            as_mother[rec.mother_id] += 1
        if rec.father_id is not None:
            as_father[rec.father_id] += 1

    rows: list[dict[str, object]] = []
    for rec in people:
        g = rec.person.genome or {}
        mx = max((abs(float(v)) for v in g.values()), default=0.0)
        l2 = math.sqrt(sum(float(v) ** 2 for v in g.values())) if g else 0.0
        near0 = sum(1 for v in g.values() if abs(float(v)) <= _NEAR_ZERO_ABS)
        rows.append(
            {
                "rec": rec,
                "id": rec.person_id,
                "name": rec.person.full_name,
                "lifespan": _lifespan_years(rec, end_year),
                "kids": as_mother[rec.person_id] + as_father[rec.person_id],
                "events": ev[rec.person_id],
                "g_max": mx,
                "g_l2": l2,
                "near0": near0,
            }
        )

    by_life = sorted(rows, key=lambda x: -int(x["lifespan"]))[:8]
    by_kids = sorted(rows, key=lambda x: (-int(x["kids"]), -int(x["lifespan"])))[:8]
    by_ev = sorted(rows, key=lambda x: (-int(x["events"]), -int(x["lifespan"])))[:8]
    by_extreme = sorted(rows, key=lambda x: (-float(x["g_max"]), -float(x["g_l2"])))[:8]
    by_optimal = sorted(
        [x for x in rows if int(x["near0"]) >= 3],
        key=lambda x: (-int(x["near0"]), float(x["g_l2"])),
    )[:8]

    lines: list[str] = []
    lines.append(
        f"Interesting people ({args.years}y, seed={args.seed}, couples={args.couples}, "
        f"n={len(people)})\n"
        f"Genome: deviations from 0 = ideal; |value|~50 ordinary; |value|≥{_EXTREME_ABS:.0f} extreme; "
        f"|value|≤{_NEAR_ZERO_ABS:.0f} unusually near ideal.\n"
    )

    def block(title: str, picked: list[dict[str, object]]) -> None:
        lines.append(title)
        lines.append("-" * len(title))
        for x in picked:
            rec = x["rec"]
            p = rec.person
            alive = p.deathyear is None or int(p.deathyear) > end_year
            lines.append(
                f"id={x['id']}  {x['name']!r}  ethnic={p.ethnic!r}  "
                f"lifespan={x['lifespan']}y  kids={x['kids']}  events={x['events']}  "
                f"max|g|={float(x['g_max']):.1f}  L2={float(x['g_l2']):.1f}  alive_end={alive}"
            )
        lines.append("")

    block("Longest-lived", by_life)
    block("Most children (as parent on any child record)", by_kids)
    block("Most event rows (any id column in events.csv)", by_ev)
    block("Largest single-trait magnitude", by_extreme)
    block("Most traits near numeric ideal (|trait|≤5)", by_optimal)

    # Genome deep dives: everyone with any trait at ±tail
    lines.append(f"Genome: people with any |trait| >= {_EXTREME_ABS:.0f} (what they were 'like')")
    lines.append("-" * 72)
    tail_people = [x for x in rows if float(x["g_max"]) >= _EXTREME_ABS]
    tail_people.sort(key=lambda x: -float(x["g_max"]))
    for x in tail_people[:_PATHOLOGICAL_CAP]:
        rec = x["rec"]
        lines.append(
            f"\n* id={x['id']}  {x['name']!r}  ({rec.person.gender})  "
            f"max|g|={float(x['g_max']):.2f}"
        )
        lines.extend(_extreme_traits(rec.person.genome or {}, labels))
        notes = interpret_genome_personality(rec.person, db_path=cfg)
        if notes:
            lines.append("  Interpreter highlights:")
            for n in notes[:14]:
                lines.append(f"    - {n.trait} ({n.value:+.2f}): {n.phrase}")
    if not tail_people:
        lines.append("(none in this run)")
    lines.append("")
    lines.append("Clinical interpreter standouts (any note contains 'clinically')")
    lines.append("-" * 72)
    path_rows: list[tuple[float, dict[str, object], list]] = []
    for x in tail_people[: min(100, len(tail_people))]:
        rec = x["rec"]
        notes = interpret_genome_personality(rec.person, db_path=cfg)
        bad = [n for n in notes if "clinically" in n.phrase]
        if bad:
            path_rows.append((float(x["g_max"]), x, bad))
    path_rows.sort(key=lambda t: -t[0])
    for _mx, x, bad in path_rows[:25]:
        rec = x["rec"]
        lines.append(
            f"\n* id={x['id']}  {x['name']!r}  ({rec.person.gender})  max|g|={float(x['g_max']):.2f}"
        )
        for n in bad[:8]:
            lines.append(f"    - {n.trait} ({n.value:+.2f}): {n.phrase}")
    if not path_rows:
        lines.append("(none among scanned extreme-genome people)")
    lines.append("")

    lines.append(
        f"Genome: highly ideal in MANY axes (≥{_HIGHLY_IDEAL_MIN} traits with "
        f"|trait| ≤ {_NEAR_ZERO_ABS:.0f})"
    )
    lines.append("-" * 72)
    ideal_many = [x for x in rows if int(x["near0"]) >= _HIGHLY_IDEAL_MIN]
    ideal_many.sort(key=lambda x: (-int(x["near0"]), float(x["g_l2"])))
    for x in ideal_many[:_HIGHLY_IDEAL_CAP]:
        rec = x["rec"]
        lines.append(
            f"\n* id={x['id']}  {x['name']!r}  ({rec.person.gender})  "
            f"traits_near_ideal={x['near0']}  L2={float(x['g_l2']):.1f}"
        )
        lines.extend(_excelled_traits(rec.person.genome or {}, labels))
    if not ideal_many:
        lines.append(
            f"(none with ≥{_HIGHLY_IDEAL_MIN} near-ideal traits; try lowering threshold in script)"
        )
    lines.append("")

    lines.append(
        f"Genome: several near-ideal traits (≥3 and <{_HIGHLY_IDEAL_MIN} traits |trait| ≤ {_NEAR_ZERO_ABS:.0f})"
    )
    lines.append("-" * 72)
    opt_people = [x for x in rows if 3 <= int(x["near0"]) < _HIGHLY_IDEAL_MIN]
    opt_people.sort(key=lambda x: (-int(x["near0"]), float(x["g_l2"])))
    for x in opt_people[:15]:
        rec = x["rec"]
        lines.append(
            f"\n* id={x['id']}  {x['name']!r}  ({rec.person.gender})  "
            f"traits_near_ideal={x['near0']}  L2={float(x['g_l2']):.1f}"
        )
        lines.extend(_excelled_traits(rec.person.genome or {}, labels))
    if not opt_people:
        lines.append("(none in this band)")
    lines.append("")

    text = "\n".join(lines).rstrip() + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"Wrote {args.out.resolve()}", file=sys.stderr)


if __name__ == "__main__":
    main()
