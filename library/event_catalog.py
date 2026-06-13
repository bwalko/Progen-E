"""Authored event/incident catalog rows loaded from ``config/event_catalog.csv``."""

from __future__ import annotations

import random
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class EventCatalogEntry:
    event_type: str
    incident_kind: str
    event_family: str
    display_name: str
    context_tags: tuple[str, ...]
    consequence_profile: str
    default_record_type: str
    default_visibility: str
    selection_weight: float
    notes: str = ""


_FALLBACK_ROWS: tuple[EventCatalogEntry, ...] = (
    EventCatalogEntry("murder", "murder", "violent_crime", "murder", ("ordinary",), "death", "violent_crime_record", "rumored", 1.0),
    EventCatalogEntry("murder", "domestic_murder", "violent_crime", "domestic murder", ("domestic",), "death", "violent_crime_record", "rumored", 1.0),
    EventCatalogEntry("murder", "feud_killing", "violent_crime", "feud killing", ("feud",), "death", "violent_crime_record", "rumored", 1.0),
    EventCatalogEntry("murder", "rash_brawl_killing", "violent_crime", "rash brawl killing", ("brawl",), "death", "violent_crime_record", "rumored", 1.0),
    EventCatalogEntry("murder", "predatory_murder", "violent_crime", "predatory murder", ("predatory",), "death", "violent_crime_record", "rumored", 1.0),
    EventCatalogEntry("property_crime", "theft", "property_crime", "theft", ("theft",), "property_loss", "property_crime_record", "rumored", 1.0),
    EventCatalogEntry("property_crime", "fraud", "property_crime", "fraud", ("fraud",), "property_loss", "property_crime_record", "rumored", 1.0),
    EventCatalogEntry("property_crime", "extortion", "property_crime", "extortion", ("extortion",), "property_loss", "property_crime_record", "rumored", 1.0),
    EventCatalogEntry("property_crime", "hoarding_theft", "property_crime", "hoarding theft", ("hoarding", "theft"), "property_loss", "property_crime_record", "rumored", 1.0),
    EventCatalogEntry("property_crime", "street_theft", "property_crime", "street theft", ("theft", "survival", "street"), "property_loss", "property_crime_record", "rumored", 0.95),
    EventCatalogEntry("vagrancy", "vagrancy", "street_precarity", "vagrancy", ("street", "unemployed"), "housing_crisis", "street_record", "rumored", 1.0),
    EventCatalogEntry("begging", "begging", "street_precarity", "begging", ("street", "survival"), "housing_crisis", "street_record", "public_known", 1.0),
    EventCatalogEntry("street_vice_scandal", "street_vice_scandal", "street_precarity", "street vice scandal", ("street", "vice", "survival"), "reputation_loss", "scandal_record", "rumored", 1.0),
    EventCatalogEntry("affair_scandal", "affair_exposed", "household_scandal", "affair exposed", ("rumor",), "relationship_fallout", "scandal_record", "rumored", 1.0),
    EventCatalogEntry("affair_scandal", "affair_witnessed", "household_scandal", "affair witnessed", ("witnessed",), "relationship_fallout", "scandal_record", "rumored", 1.0),
    EventCatalogEntry("affair_scandal", "confessed_affair", "household_scandal", "confessed affair", ("confession",), "relationship_fallout", "scandal_record", "rumored", 1.0),
    EventCatalogEntry("affair_scandal", "double_affair_exposed", "household_scandal", "double affair exposed", ("double_household",), "relationship_fallout", "scandal_record", "rumored", 1.0),
    EventCatalogEntry("public_virtue", "heroic_rescue", "public_virtue", "heroic rescue", ("rescue",), "public_relief", "public_virtue_record", "public_known", 1.0),
    EventCatalogEntry("public_virtue", "public_mercy", "public_virtue", "public mercy", ("mercy", "relief"), "public_relief", "public_virtue_record", "public_known", 1.0),
    EventCatalogEntry("public_virtue", "public_arbitration", "public_virtue", "public arbitration", ("arbitration", "legal"), "public_relief", "public_virtue_record", "public_known", 1.0),
    EventCatalogEntry("public_virtue", "loyal_service", "public_virtue", "loyal service", ("loyal_service",), "public_relief", "public_virtue_record", "public_known", 1.0),
    EventCatalogEntry("knowledge_culture", "invention", "knowledge_culture", "invention", ("invention",), "knowledge_state", "knowledge_record", "public_known", 1.0),
    EventCatalogEntry("knowledge_culture", "discovery", "knowledge_culture", "discovery", ("discovery",), "knowledge_state", "knowledge_record", "public_known", 1.0),
    EventCatalogEntry("knowledge_culture", "legal_precedent", "knowledge_culture", "legal precedent", ("legal",), "knowledge_state", "knowledge_record", "public_known", 1.0),
    EventCatalogEntry("knowledge_culture", "artistic_triumph", "knowledge_culture", "artistic triumph", ("art",), "knowledge_state", "knowledge_record", "public_known", 1.0),
    EventCatalogEntry("knowledge_culture", "scholarly_breakthrough", "knowledge_culture", "scholarly breakthrough", ("scholarship",), "knowledge_state", "knowledge_record", "public_known", 1.0),
)


def _row_value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    return row[key] if key in row.keys() else default


def _split_tags(raw: object) -> tuple[str, ...]:
    text = str(raw or "").strip().lower()
    if not text:
        return ()
    parts = text.replace(",", ";").split(";")
    return tuple(part.strip() for part in parts if part.strip())


def _parse_float(raw: object, default: float) -> float:
    try:
        if raw is None or str(raw).strip() == "":
            return float(default)
        return max(0.0, float(str(raw).strip()))
    except (TypeError, ValueError):
        return float(default)


def _entry_from_row(row: sqlite3.Row) -> EventCatalogEntry | None:
    event_type = str(_row_value(row, "event_type", "") or "").strip().lower()
    incident_kind = str(_row_value(row, "incident_kind", "") or "").strip().lower()
    if not event_type or not incident_kind:
        return None
    display = str(_row_value(row, "display_name", "") or "").strip()
    return EventCatalogEntry(
        event_type=event_type,
        incident_kind=incident_kind,
        event_family=str(_row_value(row, "event_family", "") or "").strip().lower(),
        display_name=display or incident_kind.replace("_", " "),
        context_tags=_split_tags(_row_value(row, "context_tags", "")),
        consequence_profile=str(_row_value(row, "consequence_profile", "") or "").strip().lower(),
        default_record_type=str(_row_value(row, "default_record_type", "") or "").strip().lower(),
        default_visibility=str(_row_value(row, "default_visibility", "") or "").strip().lower(),
        selection_weight=_parse_float(_row_value(row, "selection_weight", None), 1.0),
        notes=str(_row_value(row, "notes", "") or "").strip(),
    )


@lru_cache(maxsize=64)
def _load_event_catalog_rows(db_path_s: str) -> tuple[EventCatalogEntry, ...]:
    path = Path(db_path_s)
    if not path.is_file():
        return _FALLBACK_ROWS
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        exists = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'event_catalog'
            """
        ).fetchone()
        if exists is None:
            return _FALLBACK_ROWS
        rows = conn.execute("SELECT * FROM event_catalog").fetchall()
    loaded = tuple(entry for row in rows if (entry := _entry_from_row(row)) is not None)
    return loaded or _FALLBACK_ROWS


def clear_event_catalog_cache() -> None:
    _load_event_catalog_rows.cache_clear()


def event_catalog_entries(
    *,
    db_path: Path | str,
    event_type: str | None = None,
    any_tags: Iterable[str] | None = None,
) -> tuple[EventCatalogEntry, ...]:
    entries = _load_event_catalog_rows(str(Path(db_path).resolve()))
    event_key = str(event_type or "").strip().lower()
    tag_set = {str(tag).strip().lower() for tag in (any_tags or ()) if str(tag).strip()}
    out: list[EventCatalogEntry] = []
    for entry in entries:
        if event_key and entry.event_type != event_key:
            continue
        if tag_set and not tag_set.intersection(entry.context_tags):
            continue
        out.append(entry)
    return tuple(out)


def choose_event_catalog_kind(
    *,
    db_path: Path | str,
    event_type: str,
    any_tags: Iterable[str],
    default: str,
    rng: random.Random,
) -> str:
    options = event_catalog_entries(
        db_path=db_path,
        event_type=event_type,
        any_tags=any_tags,
    )
    if not options:
        return str(default)
    weights = [max(0.0, float(entry.selection_weight)) for entry in options]
    if sum(weights) <= 0.0:
        return str(default)
    return str(rng.choices([entry.incident_kind for entry in options], weights=weights, k=1)[0])
