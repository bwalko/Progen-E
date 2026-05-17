"""Persistent simulation clock.

``world_start`` is read from the **config** database. Runtime progression lives in
``world_state`` in the **save** database.

Legacy: pass ``db_path`` alone to use one file for both (tests).
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from library.random_traits import _as_int, _connect


def _connect_save_sqlite(path: Path | str) -> sqlite3.Connection:
    """Open the save database, creating an empty file if needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_world_state_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS world_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            start_year INTEGER NOT NULL,
            current_year INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _world_start_year_from_config_conn(conn, world: str) -> int:
    row = conn.execute(
        'SELECT "start_year" FROM world_start WHERE "world" = ?',
        (world.strip(),),
    ).fetchone()
    if row is None:
        raise LookupError(f"No world_start row for world={world!r}")
    start_year = _as_int(row["start_year"], 0)
    if start_year <= 0:
        raise LookupError(f"world_start.start_year invalid for world={world!r}")
    return start_year


def _resolve_config_save_paths(
    *,
    db_path: Path | str | None,
    config_db_path: Path | str | None,
    save_db_path: Path | str | None,
) -> tuple[Path, Path]:
    """Return (config_path, save_path). Legacy ``db_path`` uses one file for both."""
    if db_path is not None:
        p = Path(db_path)
        return p, p
    from library.world_paths import config_db_path as default_cfg
    from library.world_paths import derive_save_db_path_from_config

    cfg = Path(config_db_path) if config_db_path is not None else default_cfg()
    sav = Path(save_db_path) if save_db_path is not None else derive_save_db_path_from_config(cfg)
    return cfg, sav


def ensure_world_state(
    *,
    db_path: Path | str | None = None,
    config_db_path: Path | str | None = None,
    save_db_path: Path | str | None = None,
    world: str = "default",
) -> tuple[int, int]:
    """Return ``(start_year, current_year)`` for world, creating save state if missing."""
    cfg, sav = _resolve_config_save_paths(
        db_path=db_path, config_db_path=config_db_path, save_db_path=save_db_path
    )

    with closing(_connect_save_sqlite(sav)) as sconn:
        _ensure_world_state_table(sconn)
        row = sconn.execute(
            "SELECT start_year, current_year FROM world_state WHERE id = 1",
        ).fetchone()
        if row is not None:
            return int(row["start_year"]), int(row["current_year"])

    with closing(_connect(cfg)) as cconn:
        start_year = _world_start_year_from_config_conn(cconn, world)

    with closing(_connect_save_sqlite(sav)) as sconn:
        _ensure_world_state_table(sconn)
        sconn.execute(
            """
            INSERT INTO world_state (id, start_year, current_year)
            VALUES (1, ?, ?)
            """,
            (start_year, start_year),
        )
        sconn.commit()
        return start_year, start_year


def resolve_world_current_year(
    *,
    db_path: Path | str | None = None,
    config_db_path: Path | str | None = None,
    save_db_path: Path | str | None = None,
    world: str = "default",
) -> int:
    """Current simulation year for world, lazily initialized from ``world_start``."""
    _, current_year = ensure_world_state(
        db_path=db_path,
        config_db_path=config_db_path,
        save_db_path=save_db_path,
        world=world,
    )
    return current_year


def set_world_current_year(
    *,
    current_year: int,
    db_path: Path | str | None = None,
    config_db_path: Path | str | None = None,
    save_db_path: Path | str | None = None,
    world: str = "default",
) -> int:
    """Set current simulation year for world and return persisted value."""
    start_year, _ = ensure_world_state(
        db_path=db_path,
        config_db_path=config_db_path,
        save_db_path=save_db_path,
        world=world,
    )
    year = int(current_year)
    if year < start_year:
        raise ValueError(
            f"current_year {year} cannot be before start_year {start_year} for world={world!r}"
        )
    _, sav = _resolve_config_save_paths(
        db_path=db_path, config_db_path=config_db_path, save_db_path=save_db_path
    )
    with closing(_connect_save_sqlite(sav)) as sconn:
        _ensure_world_state_table(sconn)
        sconn.execute(
            """
            UPDATE world_state
            SET current_year = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
            """,
            (year,),
        )
        sconn.commit()
    return year


def reset_world_time(
    *,
    start_year: int,
    current_year: int | None = None,
    db_path: Path | str | None = None,
    config_db_path: Path | str | None = None,
    save_db_path: Path | str | None = None,
    world: str = "default",
) -> tuple[int, int]:
    """Reset saved runtime clock to an explicit simulation start year."""
    start = int(start_year)
    current = start if current_year is None else int(current_year)
    if current < start:
        raise ValueError(
            f"current_year {current} cannot be before start_year {start} for world={world!r}"
        )
    _, sav = _resolve_config_save_paths(
        db_path=db_path, config_db_path=config_db_path, save_db_path=save_db_path
    )
    with closing(_connect_save_sqlite(sav)) as sconn:
        _ensure_world_state_table(sconn)
        sconn.execute(
            """
            INSERT INTO world_state (id, start_year, current_year)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                start_year = excluded.start_year,
                current_year = excluded.current_year,
                updated_at = CURRENT_TIMESTAMP
            """,
            (start, current),
        )
        sconn.commit()
    return start, current


def advance_world_time(
    *,
    years: int = 1,
    db_path: Path | str | None = None,
    config_db_path: Path | str | None = None,
    save_db_path: Path | str | None = None,
    world: str = "default",
) -> int:
    """Advance world clock by ``years`` and return the new current year."""
    _, current_year = ensure_world_state(
        db_path=db_path,
        config_db_path=config_db_path,
        save_db_path=save_db_path,
        world=world,
    )
    return set_world_current_year(
        current_year=current_year + int(years),
        db_path=db_path,
        config_db_path=config_db_path,
        save_db_path=save_db_path,
        world=world,
    )
