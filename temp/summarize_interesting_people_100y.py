"""One-off: 100y population sim (temp DBs) and print notable people."""
from __future__ import annotations

import csv
import json
import math
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.pop("HISTORY_SIM_PROFILE_LAST_N_YEARS", None)
os.environ["POPULATION_GROWTH_SIM_SEED"] = "42"

from library.config_import import load_all_csvs_into_sqlite  # noqa: E402
from library.population_growth_runner import run_population_growth_simulation  # noqa: E402
from library.simulation_context import SimulationContext  # noqa: E402

START = 1000
YEARS = 100
COUPLES = 10
END = START + YEARS - 1


def main() -> None:
    td = Path(tempfile.mkdtemp(prefix="interesting_"))
    cfg, sav = td / "config.sqlite", td / "save.sqlite"
    load_all_csvs_into_sqlite(cfg)
    store_root: Path | None = None
    with SimulationContext.create(
        db_path=cfg,
        save_db_path=sav,
        world_id="default",
        world="default",
        start_year=START,
        placename_rng_salt=42,
        refresh_config=False,
        store_flush_batch_years=100,
    ) as ctx:
        run_population_growth_simulation(
            ctx,
            sim_seed=42,
            start_year=START,
            duration_years=YEARS,
            starting_couples=COUPLES,
        )
        store_root = ctx.file_store.root_dir if ctx.file_store else None
        people = list(ctx.people)
        id_to = dict(ctx.id_to_record)

    as_mother: Counter[int] = Counter()
    as_father: Counter[int] = Counter()
    for rec in people:
        if rec.mother_id is not None:
            as_mother[rec.mother_id] += 1
        if rec.father_id is not None:
            as_father[rec.father_id] += 1

    def lifespan_years(rec) -> int:
        by = int(rec.person.birthyear)
        dy = rec.person.deathyear
        if dy is None:
            return END - by + 1
        return int(dy) - by + 1

    event_refs: Counter[int] = Counter()
    if store_root is not None:
        ev_path = store_root / "events.csv"
        with ev_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                for k in ("person_id", "person_a_id", "person_b_id", "child_id"):
                    v = (row.get(k) or "").strip()
                    if v.isdigit():
                        event_refs[int(v)] += 1

    def genome_stats(g: dict[str, float]) -> tuple[float, float, float]:
        if not g:
            return 0.0, 0.0, 0.0
        vals = list(g.values())
        mx = max(abs(x) for x in vals)
        l2 = math.sqrt(sum(x * x for x in vals))
        mn = min(abs(x) for x in vals)
        return mx, l2, mn

    rows: list[dict[str, object]] = []
    for rec in people:
        g = rec.person.genome or {}
        mx, l2, mn = genome_stats(g)
        rows.append(
            {
                "id": rec.person_id,
                "name": rec.person.full_name,
                "ethnic": rec.person.ethnic,
                "lifespan": lifespan_years(rec),
                "alive_end": rec.person.deathyear is None
                or int(rec.person.deathyear) > END,
                "kids": as_mother[rec.person_id] + as_father[rec.person_id],
                "events": event_refs[rec.person_id],
                "g_max": mx,
                "g_l2": l2,
                "g_min_abs": mn,
            }
        )

    by_life = sorted(rows, key=lambda x: -int(x["lifespan"]))[:10]
    by_kids = sorted(rows, key=lambda x: (-int(x["kids"]), -int(x["lifespan"])))[:10]
    by_ev = sorted(rows, key=lambda x: (-int(x["events"]), -int(x["lifespan"])))[:10]
    by_extreme = sorted(rows, key=lambda x: (-float(x["g_max"]), -float(x["g_l2"])))[:10]
    by_perfect = sorted(
        [x for x in rows if float(x["g_l2"]) > 0],
        key=lambda x: (float(x["g_l2"]), float(x["g_max"])),
    )[:10]

    print(f"=== 100-year sim (seed 42, {COUPLES} founder couples, temp DB) ===")
    print(f"Total people: {len(people)} | events CSV: {store_root}")
    print()
    print("--- Longest-lived (years from birth to death, or to 1099 if still alive) ---")
    for x in by_life:
        print(
            f"  {int(x['lifespan']):3d}y  id={int(x['id']):4d}  {x['name']!r}  "
            f"ethnic={x['ethnic']!r}  alive_at_end={x['alive_end']}"
        )
    print()
    print("--- Most children (counted as mother_id or father_id on others) ---")
    for x in by_kids:
        if int(x["kids"]) == 0:
            continue
        print(
            f"  {int(x['kids']):3d} kids  id={int(x['id']):4d}  {x['name']!r}  "
            f"lifespan={int(x['lifespan'])}"
        )
    print()
    print("--- Most event rows (person appears in any id column) ---")
    for x in by_ev:
        if int(x["events"]) == 0:
            continue
        print(
            f"  {int(x['events']):3d} ev  id={int(x['id']):4d}  {x['name']!r}  "
            f"kids={int(x['kids'])}  lifespan={int(x['lifespan'])}"
        )
    print()
    print("--- Largest single-trait deviation (genome magnitude; typical mid ~50) ---")
    for x in by_extreme:
        print(
            f"  gmax={float(x['g_max']):7.2f}  L2={float(x['g_l2']):7.2f}  "
            f"id={int(x['id']):4d}  {x['name']!r}"
        )
    print()
    print("--- Smallest genome L2 (closest to ideal 0; still >0) ---")
    for x in by_perfect:
        print(
            f"  L2={float(x['g_l2']):6.2f}  max_abs={float(x['g_max']):5.2f}  "
            f"id={int(x['id']):4d}  {x['name']!r}"
        )


if __name__ == "__main__":
    main()
