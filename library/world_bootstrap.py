"""Filesystem and save/bootstrap helpers for per-world simulation layout."""

from __future__ import annotations

import os
from pathlib import Path

from library.world_paths import world_directory
from library.world_paths import derive_save_db_path_from_config
from library.world_paths import config_db_path
from library.world_save import _open_save, ensure_checkpoint_schema, ensure_checkpoint_schema_for_file


def history_sim_reset_world_from_env() -> bool:
    raw = os.environ.get("HISTORY_SIM_RESET_WORLD", "")
    return str(raw).strip().lower() in ("1", "true", "yes", "y", "on")


def ensure_world_directories(world_id: str) -> Path:
    """Create ``worlds/<id>/`` and ``worlds/<id>/temp`` if missing."""
    root = world_directory(world_id)
    root.mkdir(parents=True, exist_ok=True)
    (root / "temp").mkdir(parents=True, exist_ok=True)
    return root.resolve()


def save_has_simulation_people(save_db_path: Path | str, *, world: str) -> bool:
    """Whether this single-world ``save.sqlite`` has at least one person row."""
    p = Path(save_db_path)
    if not p.exists():
        return False
    with _open_save(p) as conn:
        ensure_checkpoint_schema(conn)
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM simulation_people",
        ).fetchone()
    return row is not None and int(row["c"] or 0) > 0


def should_refresh_world_config_auto(
    *,
    reset_world_for_test: bool,
    refresh_config_explicit: bool | None,
    checkpoint_has_simulation_people: bool,
) -> bool:
    """Resolve CSV import when ``refresh_config=None`` passed to ``SimulationContext.create``."""
    if refresh_config_explicit is not None:
        return bool(refresh_config_explicit)
    if reset_world_for_test:
        return True
    return not checkpoint_has_simulation_people


def delete_save_database(save_db_path: Path | str) -> None:
    """Remove ``save.sqlite`` if it exists."""
    p = Path(save_db_path)
    if p.exists():
        p.unlink()


def reset_world_save_and_refresh_config(world_id: str) -> tuple[Path, Path]:
    """Delete save DB, reload ``config.sqlite`` from CSV, ensure save schema exists.

    Returns ``(config_sqlite_path, save_sqlite_path)``.
    """
    from library.config_import import refresh_world_config_from_csv

    ensure_world_directories(world_id)
    cfg = config_db_path(world_id).resolve()
    sav = derive_save_db_path_from_config(cfg)
    delete_save_database(sav)
    refresh_world_config_from_csv(world_id)
    sav.parent.mkdir(parents=True, exist_ok=True)
    ensure_checkpoint_schema_for_file(sav)
    return cfg, sav
