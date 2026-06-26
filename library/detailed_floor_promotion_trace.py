"""Trace logging for ``ensure_detailed_floor_for_active_settlements`` decisions."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

_TRACE_COLUMNS = (
    "year",
    "settlement_id",
    "settlement_name",
    "detailed_alive_before",
    "nondetailed_alive_before",
    "mixed_alive_before",
    "target_detailed_floor",
    "promotion_needed",
    "promotion_count",
    "person_ids_selected",
    "promotion_reason",
    "cached_counts_used",
    "direct_detailed_alive",
    "direct_nondetailed_alive",
    "already_promoted_this_year",
    "cache_invalidation_after",
    "in_memory_count_update",
    "batch_conn",
    "promotion_ordinal",
)


def trace_enabled() -> bool:
    return bool(os.environ.get("DETAILED_FLOOR_PROMOTION_TRACE", "").strip())


def trace_path() -> Path | None:
    raw = os.environ.get("DETAILED_FLOOR_PROMOTION_TRACE", "").strip()
    return Path(raw) if raw else None


def batch_conn_enabled() -> bool:
    raw = os.environ.get("DETAILED_FLOOR_BATCH_CONN", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


class DetailedFloorPromotionTrace:
    """Append-only TSV trace for one simulation run."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists() or self._path.stat().st_size == 0:
            with self._path.open("w", encoding="utf-8", newline="") as f:
                csv.writer(f, delimiter="\t").writerow(_TRACE_COLUMNS)

    @classmethod
    def open_if_enabled(cls) -> DetailedFloorPromotionTrace | None:
        path = trace_path()
        if path is None:
            return None
        return cls(path)

    def write_row(self, **fields: Any) -> None:
        row = [fields.get(col, "") for col in _TRACE_COLUMNS]
        with self._path.open("a", encoding="utf-8", newline="") as f:
            csv.writer(f, delimiter="\t").writerow(row)
