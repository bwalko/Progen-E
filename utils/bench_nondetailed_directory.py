"""Benchmark SQLite city-directory operations for non-detailed population."""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
import tempfile
import time
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from library.nondetailed_population import run_nondetailed_sql_annual_tick
from library.world_save import ensure_checkpoint_schema

_OUT = _ROOT / "temp" / "nondetailed_directory_bench.tsv"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--total", type=int, default=2_000_000)
    p.add_argument("--alive", type=int, default=500_000)
    p.add_argument("--settlements", type=int, default=124)
    p.add_argument("--year", type=int, default=250)
    p.add_argument("--output", type=Path, default=_OUT)
    p.add_argument("--keep-db", action="store_true")
    return p.parse_args()


def _timed(label: str, rows: list[dict[str, object]], fn) -> object:
    t0 = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - t0
    rows.append({"phase": label, "elapsed_seconds": f"{elapsed:.6f}", "result": repr(result)})
    print(f"{label}\t{elapsed:.3f}s\t{result!r}")
    return result


def _create_rows(conn: sqlite3.Connection, *, total: int, alive: int, settlements: int, year: int) -> tuple[int, int]:
    first_alive = int(total) - int(alive) + 1
    batch: list[tuple[object, ...]] = []
    chunk = 50_000
    for rid in range(1, 5):
        conn.execute(
            "INSERT OR IGNORE INTO simulation_region_lookup(region_id) VALUES (?)",
            (f"r{rid}",),
        )
    for sid in range(1, int(settlements) + 1):
        region_key = 1 + ((sid - 1) % 4)
        conn.execute(
            """
            INSERT OR IGNORE INTO simulation_settlement_lookup(settlement_id, region_key)
            VALUES (?, ?)
            """,
            (f"r{region_key}:s{sid}", region_key),
        )
    for pid in range(1, int(total) + 1):
        is_alive = 1 if pid >= first_alive else 0
        age = (pid * 37) % 91
        birthyear = int(year) - age
        deathyear = None if is_alive else birthyear + 20 + ((pid * 17) % 70)
        gender = "Female" if (pid & 1) else "Male"
        settlement_key = 1 + (((pid * 97) + (pid // 2)) % int(settlements))
        job_family = "dependent" if age < 14 else ("food", "military", "craft", "trade", "care", "admin", "religious", "criminal", "other")[(pid * 13) % 9]
        is_partnered = 1 if is_alive and 18 <= age <= 65 and ((pid * 17 + year) % 100) < 52 else 0
        batch.append(
            (
                pid,
                birthyear,
                deathyear,
                is_alive,
                gender,
                "Human",
                None,
                ((settlement_key - 1) % 4) + 1,
                settlement_key,
                settlement_key,
                job_family,
                is_partnered,
                None,
                None,
                None,
                0,
                None,
            )
        )
        if len(batch) >= chunk:
            conn.executemany(
                """
                INSERT INTO simulation_people_nondetailed (
                    person_id, birthyear, deathyear, is_alive, gender, species_key, culture_key,
                    birthplace_region_key, birthplace_settlement_key, current_settlement_key,
                    job_family, is_partnered, partner_person_id, father_id, mother_id,
                    child_count, name_key
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                batch,
            )
            batch.clear()
    if batch:
        conn.executemany(
            """
            INSERT INTO simulation_people_nondetailed (
                person_id, birthyear, deathyear, is_alive, gender, species_key, culture_key,
                birthplace_region_key, birthplace_settlement_key, current_settlement_key,
                job_family, is_partnered, partner_person_id, father_id, mother_id,
                child_count, name_key
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
    row = conn.execute(
        "SELECT COUNT(*) AS total, SUM(is_alive) AS alive FROM simulation_people_nondetailed"
    ).fetchone()
    return int(row["total"]), int(row["alive"])


def main() -> None:
    args = _parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    db_path = Path(tempfile.gettempdir()) / "progene_nondetailed_directory_bench.sqlite"
    if db_path.exists():
        db_path.unlink()
    rows: list[dict[str, object]] = []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA cache_size=-200000")
        ensure_checkpoint_schema(conn)
        _timed(
            "insert_directory_rows",
            rows,
            lambda: _create_rows(
                conn,
                total=int(args.total),
                alive=int(args.alive),
                settlements=int(args.settlements),
                year=int(args.year),
            ),
        )
        conn.commit()
        tick = _timed(
            "annual_sql_tick",
            rows,
            lambda: run_nondetailed_sql_annual_tick(conn, year=int(args.year)),
        )
        conn.commit()
        grouped = _timed(
            "group_jobs_by_settlement",
            rows,
            lambda: len(
                conn.execute(
                    """
                    SELECT current_settlement_key, job_family, COUNT(*) AS c
                    FROM simulation_people_nondetailed
                    WHERE is_alive = 1
                    GROUP BY current_settlement_key, job_family
                    """
                ).fetchall()
            ),
        )
        final = conn.execute(
            "SELECT COUNT(*) AS total, SUM(is_alive) AS alive FROM simulation_people_nondetailed"
        ).fetchone()
    rows.append(
        {
            "phase": "final_counts",
            "elapsed_seconds": "",
            "result": repr(
                {
                    "total": int(final["total"]),
                    "alive": int(final["alive"]),
                    "tick": tick,
                    "job_groups": grouped,
                    "db_path": str(db_path),
                }
            ),
        }
    )
    with args.output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["phase", "elapsed_seconds", "result"], delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    print(f"benchmark_tsv={args.output.resolve()}")
    if not args.keep_db:
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(db_path) + suffix)
            if p.exists():
                try:
                    p.unlink()
                except PermissionError:
                    pass


if __name__ == "__main__":
    main()
