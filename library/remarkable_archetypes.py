"""Config-backed remarkable-person archetypes for rare historical events."""

from __future__ import annotations

import random
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from library.event_scoring import EventPropensitySpec, TraitFactor


@dataclass(frozen=True)
class RemarkableEventOption:
    event_type: str
    incident_kind: str
    weight: float
    domain: str = ""


@dataclass(frozen=True)
class RemarkableArchetype:
    key: str
    bucket: str
    display_name: str
    share_weight: float
    trait_factors: tuple[TraitFactor, ...]
    composite_weights: dict[str, float]
    role_weights: dict[str, float]
    pressure_weights: dict[str, float]
    opportunity_weights: dict[str, float]
    event_options: tuple[RemarkableEventOption, ...]
    minimum_score: float
    importance_min: float
    importance_max: float
    promotion_allowed: bool
    notes: str = ""

    def propensity_spec(self) -> EventPropensitySpec:
        return EventPropensitySpec(
            key=f"remarkable_archetype.{self.key}",
            risk_factors=self.trait_factors,
            composite_weights=self.composite_weights,
            role_weights=self.role_weights,
            pressure_weights=self.pressure_weights,
            opportunity_weights=self.opportunity_weights,
        )


def _row_value(row: sqlite3.Row, key: str, default: object = None) -> object:
    return row[key] if key in row.keys() else default


def _clean_key(value: object) -> str:
    return str(value or "").strip().lower()


def _parse_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None or str(value).strip() == "":
            return float(default)
        return float(str(value).strip())
    except (TypeError, ValueError):
        return float(default)


def _parse_bool(value: object, default: bool = False) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return bool(default)
    return text in {"1", "true", "yes", "y", "on"}


def _split_semicolon(value: object) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    return [part.strip() for part in text.split(";") if part.strip()]


def _parse_trait_factors(value: object) -> tuple[TraitFactor, ...]:
    factors: list[TraitFactor] = []
    valid_modes = {"negative_extreme", "positive_extreme", "ideal_strength"}
    for part in _split_semicolon(value):
        pieces = [piece.strip() for piece in part.split("|")]
        if len(pieces) != 3:
            continue
        trait, mode, weight_s = pieces
        mode = _clean_key(mode)
        if not trait or mode not in valid_modes:
            continue
        weight = _parse_float(weight_s)
        if weight <= 0.0:
            continue
        factors.append(TraitFactor(trait, mode, weight))
    return tuple(factors)


def _parse_weight_map(value: object) -> dict[str, float]:
    out: dict[str, float] = {}
    for part in _split_semicolon(value):
        pieces = [piece.strip() for piece in part.split("|")]
        if len(pieces) != 2:
            continue
        key, weight_s = pieces
        if not key:
            continue
        weight = _parse_float(weight_s)
        if weight <= 0.0:
            continue
        out[key.strip().lower()] = weight
    return out


def _parse_event_options(value: object) -> tuple[RemarkableEventOption, ...]:
    options: list[RemarkableEventOption] = []
    for part in _split_semicolon(value):
        pieces = [piece.strip() for piece in part.split("|")]
        if len(pieces) < 3:
            continue
        event_type = _clean_key(pieces[0])
        incident_kind = _clean_key(pieces[1])
        weight = _parse_float(pieces[2], 1.0)
        domain = pieces[3].strip().lower() if len(pieces) >= 4 else ""
        if not event_type or weight <= 0.0:
            continue
        options.append(
            RemarkableEventOption(
                event_type=event_type,
                incident_kind=incident_kind or event_type,
                weight=weight,
                domain=domain,
            )
        )
    return tuple(options)


def _entry_from_row(row: sqlite3.Row) -> RemarkableArchetype | None:
    key = _clean_key(_row_value(row, "archetype_key", ""))
    if not key:
        return None
    options = _parse_event_options(_row_value(row, "event_options", ""))
    if not options:
        return None
    return RemarkableArchetype(
        key=key,
        bucket=_clean_key(_row_value(row, "bucket", "")) or key,
        display_name=str(_row_value(row, "display_name", "") or "").strip()
        or key.replace("_", " "),
        share_weight=max(0.0, _parse_float(_row_value(row, "share_weight", None))),
        trait_factors=_parse_trait_factors(_row_value(row, "trait_factors", "")),
        composite_weights=_parse_weight_map(_row_value(row, "composite_weights", "")),
        role_weights=_parse_weight_map(_row_value(row, "role_weights", "")),
        pressure_weights=_parse_weight_map(_row_value(row, "pressure_weights", "")),
        opportunity_weights=_parse_weight_map(
            _row_value(row, "opportunity_weights", "")
        ),
        event_options=options,
        minimum_score=max(0.0, _parse_float(_row_value(row, "minimum_score", None), 0.25)),
        importance_min=max(0.0, _parse_float(_row_value(row, "importance_min", None), 0.35)),
        importance_max=max(0.0, _parse_float(_row_value(row, "importance_max", None), 0.85)),
        promotion_allowed=_parse_bool(
            _row_value(row, "promotion_allowed", None),
            default=True,
        ),
        notes=str(_row_value(row, "notes", "") or "").strip(),
    )


@lru_cache(maxsize=64)
def _load_remarkable_archetype_rows(db_path_s: str) -> tuple[RemarkableArchetype, ...]:
    path = Path(db_path_s)
    if not path.is_file():
        return ()
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        exists = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'remarkable_archetypes'
            """
        ).fetchone()
        if exists is None:
            return ()
        rows = conn.execute("SELECT * FROM remarkable_archetypes").fetchall()
    loaded = tuple(entry for row in rows if (entry := _entry_from_row(row)) is not None)
    return tuple(entry for entry in loaded if entry.share_weight > 0.0)


def clear_remarkable_archetype_cache() -> None:
    _load_remarkable_archetype_rows.cache_clear()


def remarkable_archetypes(*, db_path: Path | str) -> tuple[RemarkableArchetype, ...]:
    return _load_remarkable_archetype_rows(str(Path(db_path).resolve()))


def choose_weighted_archetype(
    entries: Iterable[RemarkableArchetype], rng: random.Random
) -> RemarkableArchetype | None:
    options = [entry for entry in entries if entry.share_weight > 0.0]
    if not options:
        return None
    weights = [entry.share_weight for entry in options]
    if sum(weights) <= 0.0:
        return None
    return rng.choices(options, weights=weights, k=1)[0]


def choose_weighted_event_option(
    entry: RemarkableArchetype, rng: random.Random
) -> RemarkableEventOption | None:
    options = [option for option in entry.event_options if option.weight > 0.0]
    if not options:
        return None
    weights = [option.weight for option in options]
    if sum(weights) <= 0.0:
        return None
    return rng.choices(options, weights=weights, k=1)[0]
