"""Filesystem layout for Progen-E worlds: per-world folder with config + save SQLite."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORLDS_DIR_NAME = "worlds"
DEFAULT_WORLD_ID = "default"
CONFIG_SQLITE_NAME = "config.sqlite"
SAVE_SQLITE_NAME = "save.sqlite"


def worlds_directory() -> Path:
    return PROJECT_ROOT / WORLDS_DIR_NAME


def world_directory(world_id: str) -> Path:
    return worlds_directory() / world_id.strip()


def config_db_path(world_id: str = DEFAULT_WORLD_ID) -> Path:
    return world_directory(world_id).resolve() / CONFIG_SQLITE_NAME


def save_db_path(world_id: str = DEFAULT_WORLD_ID) -> Path:
    return world_directory(world_id).resolve() / SAVE_SQLITE_NAME


def derive_save_db_path_from_config(config_sqlite_path: Path | str) -> Path:
    """`save.sqlite` lives beside `config.sqlite` in the same world folder."""
    return Path(config_sqlite_path).resolve().parent / SAVE_SQLITE_NAME
