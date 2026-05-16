"""Import authoritative ``config/*.csv`` into a config SQLite file (immutable at runtime)."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from library.world_paths import PROJECT_ROOT

CONFIG_DIR = PROJECT_ROOT / "config"
WORLD_START_CSV = CONFIG_DIR / "world_start.csv"


def config_csv_paths() -> list[Path]:
    """All `config/*.csv` paths, with `world_start.csv` first if it exists."""
    paths = sorted(CONFIG_DIR.glob("*.csv"))
    if WORLD_START_CSV in paths:
        paths = [WORLD_START_CSV] + [p for p in paths if p != WORLD_START_CSV]
    return paths


def table_name_from_csv(csv_path: Path) -> str:
    stem = csv_path.stem
    return stem.replace("-", "_").replace(" ", "_")


def _load_csv(conn: sqlite3.Connection, csv_path: Path) -> int:
    table = table_name_from_csv(csv_path)
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return 0
        columns = [h.strip() for h in reader.fieldnames if h and h.strip()]
        col_defs = ", ".join(f'"{c}" TEXT' for c in columns)
        cur = conn.cursor()
        cur.execute(f'DROP TABLE IF EXISTS "{table}"')
        cur.execute(f'CREATE TABLE "{table}" ({col_defs})')
        quoted = ", ".join(f'"{c}"' for c in columns)
        placeholders = ", ".join("?" * len(columns))
        insert_sql = f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})'
        n = 0
        for row in reader:
            values: list[str | None] = []
            for c in columns:
                v = row.get(c)
                if v is None:
                    values.append(None)
                else:
                    s = v.strip()
                    values.append(s if s else None)
            cur.execute(insert_sql, values)
            n += 1
    conn.commit()
    return n


def load_all_csvs_into_sqlite(config_db_path: Path | str) -> dict[str, int]:
    """Rebuild ``config_db_path`` from all CSVs under ``config/``. Returns table -> row count."""
    if not CONFIG_DIR.is_dir():
        raise FileNotFoundError(f"Missing config directory: {CONFIG_DIR}")
    csv_files = config_csv_paths()
    if not csv_files:
        raise FileNotFoundError(f"No CSV files in {CONFIG_DIR}")
    path = Path(config_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    conn = sqlite3.connect(path)
    try:
        for p in csv_files:
            n = _load_csv(conn, p)
            counts[table_name_from_csv(p)] = n
    finally:
        conn.close()
    return counts


def refresh_world_config_from_csv(world_id: str) -> Path:
    """Import CSVs into ``worlds/<world_id>/config.sqlite``; return that path."""
    from library.world_paths import config_db_path as _config_path

    out = _config_path(world_id)
    load_all_csvs_into_sqlite(out)
    return out
