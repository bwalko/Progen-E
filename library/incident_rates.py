"""Era-specific incident-rate knobs loaded from ``config/incident_rates.csv``."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_OPEN_ENDED_YEAR = 10**12


@dataclass(frozen=True)
class IncidentRateParams:
    """Resolved knobs for one incident family in one historical era."""

    incident_key: str
    target_per_10k_per_year: float | None = None
    chance_multiplier: float = 1.0
    annual_cap_multiplier: float = 1.0


@dataclass(frozen=True)
class _IncidentRateRow:
    world: str
    incident_key: str
    history_year_from: int
    history_year_to: int
    target_per_10k_per_year: float | None
    chance_multiplier: float
    annual_cap_multiplier: float


def _parse_int(value: Any, default: int) -> int:
    try:
        if value is None or str(value).strip() == "":
            return int(default)
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return int(default)


def _parse_float_or_none(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_nonnegative_float(value: Any, default: float) -> float:
    parsed = _parse_float_or_none(value)
    if parsed is None or parsed < 0.0:
        return float(default)
    return float(parsed)


def _row_value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    return row[key] if key in row.keys() else default


@lru_cache(maxsize=128)
def _load_incident_rate_rows(db_path_s: str) -> tuple[_IncidentRateRow, ...]:
    path = Path(db_path_s)
    if not path.is_file():
        return ()
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        exists = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'incident_rates'
            """
        ).fetchone()
        if exists is None:
            return ()
        rows = conn.execute("SELECT * FROM incident_rates").fetchall()

    out: list[_IncidentRateRow] = []
    for row in rows:
        incident_key = str(_row_value(row, "incident_key", "") or "").strip().lower()
        if not incident_key:
            continue
        out.append(
            _IncidentRateRow(
                world=str(_row_value(row, "world", "*") or "*").strip() or "*",
                incident_key=incident_key,
                history_year_from=_parse_int(
                    _row_value(row, "history_year_from", None), -_OPEN_ENDED_YEAR
                ),
                history_year_to=_parse_int(
                    _row_value(row, "history_year_to", None), _OPEN_ENDED_YEAR
                ),
                target_per_10k_per_year=_parse_float_or_none(
                    _row_value(row, "target_per_10k_per_year", None)
                ),
                chance_multiplier=_parse_nonnegative_float(
                    _row_value(row, "chance_multiplier", None), 1.0
                ),
                annual_cap_multiplier=_parse_nonnegative_float(
                    _row_value(row, "annual_cap_multiplier", None), 1.0
                ),
            )
        )
    return tuple(out)


def clear_incident_rate_cache() -> None:
    """Clear cached config rows after tests or tools rewrite a config DB."""

    _load_incident_rate_rows.cache_clear()


def incident_rate_for_year(
    *,
    db_path: Path | str,
    world: str,
    incident_key: str,
    historical_year: int,
) -> IncidentRateParams:
    """Resolve the best incident-rate row for ``world`` and ``historical_year``.

    World-specific rows beat ``*`` rows. Within the matching world rank, the row
    with the latest matching ``history_year_from`` wins.
    """

    key = str(incident_key or "").strip().lower()
    world_key = str(world or "").strip()
    hy = int(historical_year)
    best: _IncidentRateRow | None = None
    best_rank: tuple[int, int] | None = None
    for row in _load_incident_rate_rows(str(Path(db_path).resolve())):
        if row.incident_key != key:
            continue
        if row.world not in {"*", "", world_key}:
            continue
        if not (row.history_year_from <= hy <= row.history_year_to):
            continue
        rank = (1 if row.world == world_key else 0, row.history_year_from)
        if best_rank is None or rank > best_rank:
            best = row
            best_rank = rank
    if best is None:
        return IncidentRateParams(incident_key=key)
    return IncidentRateParams(
        incident_key=best.incident_key,
        target_per_10k_per_year=best.target_per_10k_per_year,
        chance_multiplier=best.chance_multiplier,
        annual_cap_multiplier=best.annual_cap_multiplier,
    )
