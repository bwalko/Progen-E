"""Event ontology rows loaded from ``config/event_ontology.csv``.

The ontology is the authoring/spec layer for narrative events. It answers what
an event needs, why it might happen, how it can matter, and how public knowledge
can differ from admin truth. Runtime generators can keep using narrower catalogs
for weighted variant selection.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class EventOntologyEntry:
    event_key: str
    event_family: str
    minimum_context: tuple[str, ...]
    probability_traits: tuple[str, ...]
    preconditions: tuple[str, ...]
    likely_witnesses: tuple[str, ...]
    consequence_hooks: tuple[str, ...]
    importance_min: float
    importance_max: float
    default_record_type: str
    default_visibility: str
    preservation_defaults: tuple[str, ...]
    public_unknown_view: str
    public_rumored_view: str
    public_known_view: str
    prose_tone_variants: tuple[str, ...]
    notes: str = ""


_FALLBACK_ROWS: tuple[EventOntologyEntry, ...] = (
    EventOntologyEntry(
        event_key="murder",
        event_family="violent_crime",
        minimum_context=("actor", "victim", "settlement"),
        probability_traits=("justice-", "empathy-", "temperance-", "patience-"),
        preconditions=("grievance", "privacy", "opportunity"),
        likely_witnesses=("household", "neighbors"),
        consequence_hooks=("death", "feud", "faction_memory"),
        importance_min=0.55,
        importance_max=0.95,
        default_record_type="violent_crime_record",
        default_visibility="rumored",
        preservation_defaults=("rumor_then_court_or_lost",),
        public_unknown_view="victim missing or death cause unknown",
        public_rumored_view="blame tale names suspect monster bandit or curse",
        public_known_view="record names killer victim place motive",
        prose_tone_variants=("rumor", "court_record", "later_reconstruction"),
    ),
    EventOntologyEntry(
        event_key="theft",
        event_family="property_survival_crime",
        minimum_context=("actor", "target", "settlement"),
        probability_traits=("honesty-", "justice-", "empathy-", "adaptability+"),
        preconditions=("opportunity", "need", "low_witnesses"),
        likely_witnesses=("market", "neighbors"),
        consequence_hooks=("property_loss", "reputation", "obligation"),
        importance_min=0.2,
        importance_max=0.65,
        default_record_type="property_crime_record",
        default_visibility="rumored",
        preservation_defaults=("local_memory_or_lost",),
        public_unknown_view="goods missing with culprit unknown",
        public_rumored_view="market talk blames suspect or outsider",
        public_known_view="record names thief target and goods",
        prose_tone_variants=("market_rumor", "court_roll", "annal"),
    ),
    EventOntologyEntry(
        event_key="affair_exposed",
        event_family="household_scandal",
        minimum_context=("accused", "paramour", "settlement"),
        probability_traits=("mating_drive+", "loyalty-", "modesty-", "honesty-"),
        preconditions=("existing_paramour", "public_exposure"),
        likely_witnesses=("spouse", "neighbors", "household"),
        consequence_hooks=("relationship_break", "legal_fallout", "reputation"),
        importance_min=0.3,
        importance_max=0.8,
        default_record_type="scandal_record",
        default_visibility="rumored",
        preservation_defaults=("household_rumor_or_lost",),
        public_unknown_view="household strain known but cause hidden",
        public_rumored_view="whispers join accused and paramour",
        public_known_view="record names paramours betrayed partners and fallout",
        prose_tone_variants=("household_memory", "rumor", "court_record"),
    ),
    EventOntologyEntry(
        event_key="rescue",
        event_family="public_virtue",
        minimum_context=("benefactor", "beneficiary", "settlement"),
        probability_traits=("empathy+", "courage+", "nurturance+", "justice+"),
        preconditions=("danger", "proximity", "witnesses"),
        likely_witnesses=("neighbors", "household", "crowd"),
        consequence_hooks=("relief_debt", "reputation", "stability_gain"),
        importance_min=0.35,
        importance_max=0.85,
        default_record_type="public_virtue_record",
        default_visibility="public_known",
        preservation_defaults=("public_chronicle_or_song",),
        public_unknown_view="beneficiary saved but rescuer unclear",
        public_rumored_view="rumor embellishes danger or helper",
        public_known_view="record praises rescuer beneficiary and danger",
        prose_tone_variants=("annal", "bardic", "household_memory"),
    ),
    EventOntologyEntry(
        event_key="invention",
        event_family="knowledge_culture",
        minimum_context=("creator", "settlement"),
        probability_traits=("curiosity+", "creativity+", "intellect+", "focus+"),
        preconditions=("craft_problem", "resources", "time"),
        likely_witnesses=("witnesses", "patron", "apprentices"),
        consequence_hooks=("knowledge_state", "institution_seed", "reputation"),
        importance_min=0.45,
        importance_max=0.9,
        default_record_type="knowledge_record",
        default_visibility="public_known",
        preservation_defaults=("public_record_or_guild_memory",),
        public_unknown_view="new device appears with maker unknown",
        public_rumored_view="rumor credits patron god or foreigner",
        public_known_view="record credits creator domain and novelty",
        prose_tone_variants=("annal", "guild_memory", "scholarly"),
    ),
)


def _row_value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    return row[key] if key in row.keys() else default


def _split(raw: object) -> tuple[str, ...]:
    text = str(raw or "").strip()
    if not text:
        return ()
    return tuple(part.strip() for part in text.replace(",", ";").split(";") if part.strip())


def _float(raw: object, default: float) -> float:
    try:
        if raw is None or str(raw).strip() == "":
            return default
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _entry_from_row(row: sqlite3.Row) -> EventOntologyEntry | None:
    event_key = str(_row_value(row, "event_key", "") or "").strip().lower()
    event_family = str(_row_value(row, "event_family", "") or "").strip().lower()
    if not event_key or not event_family:
        return None
    return EventOntologyEntry(
        event_key=event_key,
        event_family=event_family,
        minimum_context=_split(_row_value(row, "minimum_context", "")),
        probability_traits=_split(_row_value(row, "probability_traits", "")),
        preconditions=_split(_row_value(row, "preconditions", "")),
        likely_witnesses=_split(_row_value(row, "likely_witnesses", "")),
        consequence_hooks=_split(_row_value(row, "consequence_hooks", "")),
        importance_min=_float(_row_value(row, "importance_min", None), 0.0),
        importance_max=_float(_row_value(row, "importance_max", None), 1.0),
        default_record_type=str(_row_value(row, "default_record_type", "") or "").strip(),
        default_visibility=str(_row_value(row, "default_visibility", "") or "").strip(),
        preservation_defaults=_split(_row_value(row, "preservation_defaults", "")),
        public_unknown_view=str(_row_value(row, "public_unknown_view", "") or "").strip(),
        public_rumored_view=str(_row_value(row, "public_rumored_view", "") or "").strip(),
        public_known_view=str(_row_value(row, "public_known_view", "") or "").strip(),
        prose_tone_variants=_split(_row_value(row, "prose_tone_variants", "")),
        notes=str(_row_value(row, "notes", "") or "").strip(),
    )


@lru_cache(maxsize=64)
def _load_event_ontology_rows(db_path_s: str) -> tuple[EventOntologyEntry, ...]:
    path = Path(db_path_s)
    if not path.is_file():
        return _FALLBACK_ROWS
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        exists = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'event_ontology'
            """
        ).fetchone()
        if exists is None:
            return _FALLBACK_ROWS
        rows = conn.execute("SELECT * FROM event_ontology").fetchall()
    loaded = tuple(entry for row in rows if (entry := _entry_from_row(row)) is not None)
    return loaded or _FALLBACK_ROWS


def clear_event_ontology_cache() -> None:
    _load_event_ontology_rows.cache_clear()


def event_ontology_entries(
    *,
    db_path: Path | str,
    event_family: str | None = None,
    event_key: str | None = None,
) -> tuple[EventOntologyEntry, ...]:
    entries = _load_event_ontology_rows(str(Path(db_path).resolve()))
    family = str(event_family or "").strip().lower()
    key = str(event_key or "").strip().lower()
    return tuple(
        entry
        for entry in entries
        if (not family or entry.event_family == family)
        and (not key or entry.event_key == key)
    )


def event_ontology_by_key(*, db_path: Path | str) -> dict[str, EventOntologyEntry]:
    return {entry.event_key: entry for entry in event_ontology_entries(db_path=db_path)}


def event_public_view_columns(entry: EventOntologyEntry) -> dict[str, str]:
    return {
        "unknown": entry.public_unknown_view,
        "rumored": entry.public_rumored_view,
        "known": entry.public_known_view,
    }
