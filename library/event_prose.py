"""Deterministic prose rendering for simulation events and memory records.

The save DB stores factual event payloads plus compact event-record metadata.
This module derives display prose from those structured facts without adding
large text columns to ``save.sqlite``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
import json
import sqlite3
from typing import Any

from library.serious_crime_taxonomy import (
    murder_payload_taxonomy_category,
    serious_crime_category_label,
)


PUBLIC_UNKNOWN_VISIBILITY_STATES: frozenset[str] = frozenset({"public_unknown"})
PUBLIC_RUMOR_VISIBILITY_STATES: frozenset[str] = frozenset(
    {"rumored", "misattributed"}
)
PUBLIC_KNOWN_VISIBILITY_STATES: frozenset[str] = frozenset(
    {"public_known", "rediscovered"}
)
PUBLIC_CHRONICLE_VISIBILITY_STATES: frozenset[str] = frozenset(
    PUBLIC_UNKNOWN_VISIBILITY_STATES
    | PUBLIC_RUMOR_VISIBILITY_STATES
    | PUBLIC_KNOWN_VISIBILITY_STATES
)


@dataclass(frozen=True)
class EventAdminSummary:
    event_id: int
    sim_year: int
    event_type: str
    template_key: str
    prose: str


@dataclass(frozen=True)
class EventRecordProse:
    event_id: int
    record_id: int
    sim_year: int
    event_type: str
    record_type: str
    visibility_state: str
    public_knowledge_stage: str
    prose_variant_key: str
    admin_summary: str
    public_prose: str


class EventProseResolver:
    """Resolve compact event ids into readable names with safe fallbacks."""

    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self.conn = conn
        self._person_cache: dict[int, str] = {}
        self._settlement_cache: dict[str, str] = {}
        self._region_cache: dict[str, str] = {}
        self._table_cache: dict[str, bool] = {}

    def person(self, person_id: object) -> str:
        pid = _coerce_int(person_id)
        if pid is None:
            return "an unknown person"
        cached = self._person_cache.get(pid)
        if cached is not None:
            return cached
        label = f"person {pid}"
        if self.conn is not None and self._table_exists("simulation_people"):
            row = self.conn.execute(
                """
                SELECT first_name, last_name
                FROM simulation_people
                WHERE person_id = ?
                """,
                (pid,),
            ).fetchone()
            if row is not None:
                first = str(_row_value(row, "first_name", 0, "") or "").strip()
                last = str(_row_value(row, "last_name", 1, "") or "").strip()
                label = " ".join(part for part in (first, last) if part) or label
        self._person_cache[pid] = label
        return label

    def settlement(self, settlement_id: object) -> str:
        sid = str(settlement_id or "").strip()
        if not sid:
            return ""
        cached = self._settlement_cache.get(sid)
        if cached is not None:
            return cached
        label = _humanize_settlement_id(sid)
        if self.conn is not None and self._table_exists("simulation_settlements_readable"):
            row = self.conn.execute(
                """
                SELECT display_name
                FROM simulation_settlements_readable
                WHERE settlement_id = ?
                """,
                (sid,),
            ).fetchone()
            if row is not None:
                display = str(_row_value(row, "display_name", 0, "") or "").strip()
                if display:
                    label = display
        self._settlement_cache[sid] = label
        return label

    def region(self, region_id: object) -> str:
        rid = str(region_id or "").strip()
        if not rid:
            return ""
        cached = self._region_cache.get(rid)
        if cached is not None:
            return cached
        label = _humanize_slug(rid)
        if self.conn is not None and self._table_exists("simulation_regions_readable"):
            row = self.conn.execute(
                """
                SELECT region_display_name
                FROM simulation_regions_readable
                WHERE region_id = ?
                """,
                (rid,),
            ).fetchone()
            if row is not None:
                display = str(
                    _row_value(row, "region_display_name", 0, "") or ""
                ).strip()
                if display:
                    label = display
        self._region_cache[rid] = label
        return label

    def place(self, settlement_id: object, region_id: object) -> str:
        settlement = self.settlement(settlement_id)
        region = self.region(region_id)
        if settlement and region and region.lower() not in settlement.lower():
            return f"{settlement}, {region}"
        return settlement or region or "an unknown place"

    def _table_exists(self, table_name: str) -> bool:
        cached = self._table_cache.get(table_name)
        if cached is not None:
            return cached
        if self.conn is None:
            return False
        row = self.conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type IN ('table', 'view') AND name = ?
            """,
            (table_name,),
        ).fetchone()
        exists = row is not None
        self._table_cache[table_name] = exists
        return exists


def render_event_admin_summary(
    event_row: Mapping[str, Any] | sqlite3.Row,
    *,
    resolver: EventProseResolver | None = None,
) -> EventAdminSummary:
    """Render a compact factual summary for an admin/debug event row."""

    row = _mapping(event_row)
    rr = resolver or EventProseResolver()
    payload = _payload(row, conn=rr.conn)
    event_id = _coerce_int(row.get("event_id", row.get("id"))) or 0
    year = _coerce_int(row.get("sim_year", payload.get("year"))) or 0
    event_type = _clean_key(row.get("event_type") or payload.get("event_type"))
    place = _event_place(row, payload, rr)
    template_key = f"admin.{event_type or 'event'}.default"

    if event_type == "murder":
        prose = (
            f"{year}: {rr.person(payload.get('killer_person_id'))} killed "
            f"{rr.person(payload.get('victim_person_id'))} at {place}; "
            f"{_kind(payload)}{_murder_category_clause(payload)}; "
            f"motive: {_motive(payload)}; "
            f"importance {_number(payload.get('historical_importance'))}"
            f"{_witness_clause(payload, rr)}."
        )
    elif event_type == "property_crime":
        prose = (
            f"{year}: {rr.person(payload.get('perpetrator_person_id'))} committed "
            f"{_kind(payload)} against {rr.person(payload.get('target_person_id'))} "
            f"at {place}; motive: {_motive(payload)}; "
            f"loss {_number(payload.get('loss_value'))}{_witness_clause(payload, rr)}."
        )
    elif event_type == "affair_scandal":
        prose = (
            f"{year}: {rr.person(payload.get('accused_person_id'))} and "
            f"{rr.person(payload.get('paramour_person_id'))} were exposed in "
            f"{_kind(payload)} at {place}; motive: {_motive(payload)}"
            f"{_betrayed_clause(payload, rr)}{_witness_clause(payload, rr)}."
        )
    elif event_type == "public_virtue":
        prose = (
            f"{year}: {rr.person(payload.get('benefactor_person_id'))} performed "
            f"{_kind(payload)} for {rr.person(payload.get('beneficiary_person_id'))} "
            f"at {place}; motive: {_motive(payload)}; "
            f"relief {_number(payload.get('relief_value'))}"
            f"{_witness_clause(payload, rr)}."
        )
    elif event_type == "knowledge_culture":
        patron = _optional_person_phrase(rr, payload.get("patron_person_id"))
        prose = (
            f"{year}: {rr.person(payload.get('creator_person_id'))} produced "
            f"{_knowledge_focus(payload)} at {place}{patron}; "
            f"motive: {_motive(payload)}; "
            f"novelty {_number(payload.get('novelty_value'))}"
            f"{_witness_clause(payload, rr)}."
        )
    elif event_type == "event_rediscovered":
        prose = (
            f"{year}: {rr.person(payload.get('source_person_id'))} rediscovered "
            f"record {payload.get('original_record_key', 'default')} for event "
            f"{payload.get('original_event_id')} at {place}; "
            f"source: {_source_label(payload)}."
        )
    elif event_type == "birth":
        prose = f"{year}: {rr.person(payload.get('person_id'))} was born at {place}."
    elif event_type == "death":
        cause = _label(payload.get("death_cause") or payload.get("cause"))
        cause_clause = "" if cause == "unknown" else f" from {cause}"
        prose = f"{year}: {rr.person(payload.get('person_id'))} died{cause_clause} at {place}."
    elif event_type in {
        "settlement_move_planned",
        "settlement_move_dropped",
        "settlement_moved",
    }:
        actor = rr.person(payload.get("person_id"))
        prose = (
            f"{year}: {actor} moved at {place}"
            f"{_move_destination_clause(payload, rr)}{_move_reason_clause(payload)}."
        )
    elif event_type in {"office_selection", "office_succession"}:
        holder = rr.person(payload.get("holder_person_id"))
        previous = _coerce_int(payload.get("previous_holder_id"))
        prev = f"; previous: {rr.person(previous)}" if previous is not None else ""
        prose = (
            f"{year}: {holder} took {_label(payload.get('title_id') or 'office')} "
            f"at {place}; via {_label(payload.get('via') or payload.get('selection_rule'))}"
            f"{prev}."
        )
    elif event_type.startswith("campaign_"):
        prose = (
            f"{year}: {_label(event_type)} at {place}; kind "
            f"{_label(payload.get('kind'))}; outcome {_label(payload.get('outcome'))}."
        )
    elif event_type == "battle_fought":
        prose = (
            f"{year}: battle fought at {place}; outcome "
            f"{_label(payload.get('battle_outcome') or payload.get('outcome'))}."
        )
    elif payload.get("archetype_key"):
        actor = rr.person(_archetype_actor_id(payload))
        prose = (
            f"{year}: {actor} drew notice as {_archetype_label(payload)} "
            f"through {_kind(payload)} at {place}; importance "
            f"{_number(payload.get('historical_importance'))}."
        )
    elif event_type.startswith("city_state_"):
        prose = f"{year}: {_city_state_focus(event_type, payload)} at {place}."
    else:
        primary = _coerce_int(row.get("primary_person_id"))
        actor = f"; primary: {rr.person(primary)}" if primary is not None else ""
        prose = f"{year}: {_label(event_type)} at {place}{actor}."

    return EventAdminSummary(
        event_id=event_id,
        sim_year=year,
        event_type=event_type,
        template_key=template_key,
        prose=prose,
    )


def render_event_record_prose(
    record_row: Mapping[str, Any] | sqlite3.Row,
    *,
    resolver: EventProseResolver | None = None,
) -> EventRecordProse:
    """Render record-aware chronicle prose for a joined event/record row."""

    row = _mapping(record_row)
    rr = resolver or EventProseResolver()
    admin = render_event_admin_summary(row, resolver=rr)
    payload = _payload(row, conn=rr.conn)
    distortion = _json_object(row.get("distortion_json"))
    event_type = admin.event_type
    year = admin.sim_year
    place = _record_place(row, payload, rr)
    state = _clean_key(row.get("visibility_state"))
    record_type = _clean_key(row.get("record_type"))
    variant = str(row.get("prose_variant_key") or "").strip()
    if not variant:
        variant = f"{record_type or 'event_memory'}.{state or 'public_known'}.default"

    if state == "lost":
        public = _lost_memory_text(event_type, year, payload, place, rr, row)
    elif state == "sealed":
        public = _sealed_memory_text(event_type, year, payload, place, rr, row)
    elif state == "private_known":
        public = _private_memory_text(event_type, year, payload, place, rr)
    elif state == "admin_known":
        public = f"Admin note: {admin.prose}"
    elif state == "rediscovered":
        public = _rediscovered_memory_text(event_type, year, payload, place, rr, row)
    elif state == "public_unknown":
        public = _public_unknown_text(event_type, year, payload, distortion, place, rr, row)
    elif state == "misattributed":
        public = _misattributed_text(event_type, year, payload, distortion, place, rr, row)
    elif state in PUBLIC_RUMOR_VISIBILITY_STATES:
        public = _rumor_text(event_type, year, payload, distortion, place, rr, row)
    else:
        public = _public_text(event_type, year, payload, distortion, place, rr, row)

    return EventRecordProse(
        event_id=admin.event_id,
        record_id=_coerce_int(row.get("record_id")) or 0,
        sim_year=year,
        event_type=event_type,
        record_type=record_type,
        visibility_state=state,
        public_knowledge_stage=_public_knowledge_stage(state),
        prose_variant_key=variant,
        admin_summary=admin.prose,
        public_prose=public,
    )


def load_admin_event_summaries(
    conn: sqlite3.Connection,
    *,
    event_ids: Iterable[object] | None = None,
    event_types: Iterable[str] | None = None,
    search: str = "",
    limit: int = 100,
    offset: int = 0,
) -> list[EventAdminSummary]:
    """Load factual admin summaries from ``simulation_events_readable``."""

    clauses: list[str] = []
    params: list[Any] = []
    _add_in_clause(clauses, params, "id", event_ids)
    _add_in_clause(clauses, params, "event_type", event_types)
    _add_search_clause(
        clauses,
        params,
        search,
        (
            "event_type",
            "payload_json",
            "settlement_id",
            "region_id",
            "event_origin",
            "primary_person_id",
            "secondary_person_id",
        ),
    )
    params.extend([max(1, int(limit)), max(0, int(offset))])
    where = _where_sql(clauses)
    sql = f"""
        SELECT id, id AS event_id, sim_year, event_type, primary_person_id,
               secondary_person_id, settlement_id, region_id, event_origin,
               payload_json
        FROM simulation_events_readable
        {where}
        ORDER BY sim_year, id
        LIMIT ? OFFSET ?
    """
    resolver = EventProseResolver(conn)
    return [
        render_event_admin_summary(row, resolver=resolver)
        for row in _fetch_dicts(conn, sql, params)
    ]


def load_event_record_prose_rows(
    conn: sqlite3.Connection,
    *,
    event_ids: Iterable[object] | None = None,
    visibility_states: Iterable[str] | None = None,
    event_types: Iterable[str] | None = None,
    search: str = "",
    limit: int = 100,
    offset: int = 0,
) -> list[EventRecordProse]:
    """Load record-aware prose rows from event-memory records."""

    clauses: list[str] = []
    params: list[Any] = []
    _add_in_clause(clauses, params, "e.id", event_ids)
    _add_in_clause(clauses, params, "r.visibility_state", visibility_states)
    _add_in_clause(clauses, params, "e.event_type", event_types)
    _add_search_clause(
        clauses,
        params,
        search,
        (
            "e.event_type",
            "e.payload_json",
            "e.settlement_id",
            "e.region_id",
            "e.event_origin",
            "r.record_type",
            "r.visibility_state",
            "r.prose_variant_key",
            "r.source_institution_id",
            "r.public_actor_person_id",
            "r.public_victim_person_id",
        ),
    )
    clause = _where_sql(clauses)
    params.extend([max(1, int(limit)), max(0, int(offset))])
    sql = f"""
        SELECT
            e.id AS event_id,
            e.id AS id,
            e.sim_year,
            e.event_type,
            e.primary_person_id,
            e.secondary_person_id,
            e.settlement_id,
            e.region_id,
            e.event_origin,
            e.payload_json,
            r.record_id,
            r.record_key,
            r.record_type,
            r.visibility_state,
            r.known_since_year,
            r.lost_year,
            r.rediscovered_year,
            r.confidence,
            r.source_person_id,
            r.source_institution_id,
            r.preserving_settlement_id,
            r.preserving_region_id,
            r.public_actor_person_id,
            r.public_victim_person_id,
            r.distortion_json,
            r.prose_variant_key
        FROM simulation_event_records_readable r
        JOIN simulation_events_readable e ON e.id = r.event_id
        {clause}
        ORDER BY e.sim_year, e.id, r.record_id
        LIMIT ? OFFSET ?
    """
    resolver = EventProseResolver(conn)
    return [
        render_event_record_prose(row, resolver=resolver)
        for row in _fetch_dicts(conn, sql, params)
    ]


def load_public_chronicle_prose(
    conn: sqlite3.Connection,
    *,
    event_ids: Iterable[object] | None = None,
    event_types: Iterable[str] | None = None,
    search: str = "",
    limit: int = 100,
    offset: int = 0,
) -> list[EventRecordProse]:
    """Load in-world public chronicle prose for visible/rumored records."""

    return load_event_record_prose_rows(
        conn,
        event_ids=event_ids,
        visibility_states=PUBLIC_CHRONICLE_VISIBILITY_STATES,
        event_types=event_types,
        search=search,
        limit=limit,
        offset=offset,
    )


def load_public_unknown_prose(
    conn: sqlite3.Connection,
    *,
    event_ids: Iterable[object] | None = None,
    event_types: Iterable[str] | None = None,
    search: str = "",
    limit: int = 100,
    offset: int = 0,
) -> list[EventRecordProse]:
    """Load public records where the public knows only that something is unresolved."""

    return load_event_record_prose_rows(
        conn,
        event_ids=event_ids,
        visibility_states=PUBLIC_UNKNOWN_VISIBILITY_STATES,
        event_types=event_types,
        search=search,
        limit=limit,
        offset=offset,
    )


def load_public_rumor_prose(
    conn: sqlite3.Connection,
    *,
    event_ids: Iterable[object] | None = None,
    event_types: Iterable[str] | None = None,
    search: str = "",
    limit: int = 100,
    offset: int = 0,
) -> list[EventRecordProse]:
    """Load public records whose public version is uncertain or distorted."""

    return load_event_record_prose_rows(
        conn,
        event_ids=event_ids,
        visibility_states=PUBLIC_RUMOR_VISIBILITY_STATES,
        event_types=event_types,
        search=search,
        limit=limit,
        offset=offset,
    )


def load_public_known_prose(
    conn: sqlite3.Connection,
    *,
    event_ids: Iterable[object] | None = None,
    event_types: Iterable[str] | None = None,
    search: str = "",
    limit: int = 100,
    offset: int = 0,
) -> list[EventRecordProse]:
    """Load public records whose public version is treated as known history."""

    return load_event_record_prose_rows(
        conn,
        event_ids=event_ids,
        visibility_states=PUBLIC_KNOWN_VISIBILITY_STATES,
        event_types=event_types,
        search=search,
        limit=limit,
        offset=offset,
    )


def _public_text(
    event_type: str,
    year: int,
    payload: Mapping[str, Any],
    distortion: Mapping[str, Any],
    place: str,
    rr: EventProseResolver,
    row: Mapping[str, Any],
) -> str:
    summary = _distortion_summary(
        distortion, ("public_known_summary", "known_summary", "public_summary")
    )
    if summary:
        return _contextual_public_summary(summary, year, place)
    if event_type == "murder":
        actor = _public_person_without_truth_fallback(
            row, rr, "public_actor_person_id", unknown="an unknown killer"
        )
        victim = _public_person(row, payload, rr, "public_victim_person_id", "victim_person_id")
        kind = _kind(payload)
        motive = _motive_clause(payload)
        witnesses = _witness_clause(payload, rr)
        return _choose_text(
            row,
            payload,
            "public.murder.known",
            (
                f"The record of {place} names {actor} as the killer of {victim} "
                f"in {year}, remembered as {kind}{motive}{witnesses}.",
                f"The court roll at {place} set down {victim}'s death in {year}: "
                f"{actor} was named for {kind}{motive}{witnesses}.",
                f"Later copyists kept the public charge from {place}: {actor} "
                f"killed {victim} in {year}, a case remembered as {kind}{motive}.",
            ),
        )
    if event_type == "property_crime":
        actor = _public_person(
            row, payload, rr, "public_actor_person_id", "perpetrator_person_id"
        )
        victim = _public_person(row, payload, rr, "public_victim_person_id", "target_person_id")
        kind = _kind(payload)
        loss = _value_clause("loss", payload.get("loss_value"))
        motive = _motive_clause(payload)
        witnesses = _witness_clause(payload, rr)
        return _choose_text(
            row,
            payload,
            "public.property.known",
            (
                f"The market record of {place} set down {kind} by {actor} "
                f"against {victim}{loss}{motive}{witnesses}.",
                f"In the account books of {place}, {victim}'s loss was tied to "
                f"{actor}: {kind}{loss}{motive}.",
                f"The public ledger at {place} named {actor} in {kind} against "
                f"{victim}{loss}{witnesses}.",
            ),
        )
    if event_type == "affair_scandal":
        actor = _public_person(row, payload, rr, "public_actor_person_id", "accused_person_id")
        partner = rr.person(payload.get("paramour_person_id"))
        betrayed = _betrayed_clause(payload, rr)
        motive = _motive_clause(payload)
        witnesses = _witness_clause(payload, rr)
        return _choose_text(
            row,
            payload,
            "public.scandal.known",
            (
                f"The household talk of {place} openly named {actor} and "
                f"{partner} in {_kind(payload)}{betrayed}{motive}{witnesses}.",
                f"The scandal roll of {place} joined {actor} with {partner} in "
                f"{year}, recording {_kind(payload)}{betrayed}{witnesses}.",
                f"By public telling in {place}, {actor} and {partner} were exposed "
                f"in {_kind(payload)}{betrayed}{motive}.",
            ),
        )
    if event_type == "public_virtue":
        benefactor = _public_person(row, payload, rr, "public_actor_person_id", "benefactor_person_id")
        beneficiary = _public_person(row, payload, rr, "public_victim_person_id", "beneficiary_person_id")
        relief = _value_clause("relief", payload.get("relief_value"))
        motive = _motive_clause(payload)
        witnesses = _witness_clause(payload, rr)
        return _choose_text(
            row,
            payload,
            "public.virtue.known",
            (
                f"The chronicle of {place} praised {benefactor}, who performed "
                f"{_kind(payload)} for {beneficiary}{relief}{motive}{witnesses}.",
                f"Public memory in {place} kept {benefactor}'s {_kind(payload)} "
                f"for {beneficiary}{relief}{witnesses}.",
                f"A civic notice at {place} remembered {benefactor} aiding "
                f"{beneficiary} through {_kind(payload)}{motive}{relief}.",
            ),
        )
    if event_type == "knowledge_culture":
        creator = _public_person(row, payload, rr, "public_actor_person_id", "creator_person_id")
        patron = _optional_person_phrase(rr, payload.get("patron_person_id"))
        focus = _knowledge_focus(payload)
        novelty = _value_clause("novelty", payload.get("novelty_value"))
        witnesses = _witness_clause(payload, rr)
        return _choose_text(
            row,
            payload,
            "public.knowledge.known",
            (
                f"In {place}, {creator} was credited with {focus}{patron}"
                f"{novelty}{witnesses}.",
                f"The learned notice of {place} gave {creator} credit for {focus}"
                f"{patron}{novelty}.",
                f"Copyists at {place} preserved {creator}'s {focus}{patron}"
                f"{witnesses}.",
            ),
        )
    if event_type == "event_rediscovered":
        source = _record_source_clause(row, payload, rr)
        return (
            f"A later record at {place}{source} brought event "
            f"{payload.get('original_event_id')} back into memory."
        )
    if event_type.startswith("city_state_"):
        focus = _city_state_focus(event_type, payload)
        return _choose_text(
            row,
            payload,
            "public.city_state.known",
            (
                f"The city chronicle of {place} recorded {focus} in {year}.",
                f"A civic notice at {place} preserved {focus} for {year}.",
                f"Later copyists kept {focus} in the public record of {place}.",
            ),
        )
    if event_type == "death":
        return (
            f"The mortuary roll of {place} recorded the death of "
            f"{rr.person(payload.get('person_id'))} in {year}."
        )
    if event_type == "birth":
        return f"The lineage memory of {place} recorded the birth of {rr.person(payload.get('person_id'))} in {year}."
    if event_type in {
        "settlement_move_planned",
        "settlement_move_dropped",
        "settlement_moved",
    }:
        actor = _public_person(row, payload, rr, "public_actor_person_id", "person_id")
        destination = _move_destination_clause(payload, rr)
        reason = _move_reason_clause(payload)
        return (
            f"The settlement chronicle of {place} recorded {actor}'s move in "
            f"{year}{destination}{reason}."
        )
    if event_type in {"office_selection", "office_succession"}:
        holder = _public_person(row, payload, rr, "public_actor_person_id", "holder_person_id")
        title = _label(payload.get("title_id") or "office")
        return f"The court record of {place} named {holder} to {title} in {year}."
    if event_type.startswith("polity_"):
        label = _label(
            payload.get("name")
            or payload.get("to_polity_type_id")
            or payload.get("from_polity_type_id")
            or payload.get("reason")
            or event_type
        )
        return f"The court chronicle of {place} recorded {label} in {year}."
    if event_type.startswith("campaign_"):
        label = _label(payload.get("kind") or payload.get("outcome") or event_type)
        return f"The war chronicle of {place} recorded {label} in {year}."
    if event_type == "battle_fought":
        label = _label(payload.get("battle_outcome") or "battle")
        return f"The war chronicle of {place} recorded {label} in {year}."
    if event_type == "dynastic_marriage_alliance":
        return f"The court chronicle of {place} recorded a dynastic marriage alliance in {year}."
    return f"The chronicle of {place} recorded {_label(event_type)} in {year}."


def _rumor_text(
    event_type: str,
    year: int,
    payload: Mapping[str, Any],
    distortion: Mapping[str, Any],
    place: str,
    rr: EventProseResolver,
    row: Mapping[str, Any],
) -> str:
    summary = _distortion_summary(
        distortion, ("rumor_summary", "rumored_summary", "public_summary")
    )
    if summary:
        return _contextual_public_summary(summary, year, place)
    if event_type == "murder":
        victim = _public_person(row, payload, rr, "public_victim_person_id", "victim_person_id")
        actor = _public_person_without_truth_fallback(
            row, rr, "public_actor_person_id", unknown="an unknown suspect"
        )
        rumored_cause = _label(
            distortion.get("rumored_cause")
            or distortion.get("public_cause")
            or distortion.get("suspected_cause")
        )
        if rumored_cause != "unknown":
            return _choose_text(
                row,
                payload,
                "public.murder.rumor.cause",
                (
                    f"Rumor in {place} claimed {victim} was {rumored_cause} in {year}.",
                    f"It was said in {place} that {victim}'s end in {year} came by "
                    f"{rumored_cause}.",
                    f"The uncertain tale at {place} carried {rumored_cause} as "
                    f"the cause of {victim}'s death in {year}.",
                ),
            )
        motive = _motive_clause(payload)
        witnesses = _witness_clause(payload, rr)
        return _choose_text(
            row,
            payload,
            "public.murder.rumor",
            (
                f"It was said in {place} that {victim}'s death was an unresolved "
                f"killing; the tale called it {_kind(payload)}{motive}{witnesses}.",
                f"Tavern and court rumor in {place} put {actor} near {victim}'s "
                f"death in {year}, naming {_kind(payload)}{motive}.",
                f"No formal roll settled the matter, but {place} repeated "
                f"uncertain suspicion beside {victim}'s death{witnesses}.",
            ),
        )
    if event_type == "property_crime":
        actor = _public_person(row, payload, rr, "public_actor_person_id", "perpetrator_person_id")
        victim = _public_person(row, payload, rr, "public_victim_person_id", "target_person_id")
        loss = _value_clause("loss", payload.get("loss_value"))
        motive = _motive_clause(payload)
        return _choose_text(
            row,
            payload,
            "public.property.rumor",
            (
                f"Market talk in {place} blamed {actor} for {_kind(payload)} "
                f"against {victim}{loss}{motive}.",
                f"The stalls of {place} repeated that {actor} had taken from "
                f"{victim}: {_kind(payload)}{loss}{motive}.",
                f"Unsettled accounts in {place} pointed toward {actor} after "
                f"{victim}'s {_kind(payload)}{loss}.",
            ),
        )
    if event_type == "affair_scandal":
        actor = _public_person(row, payload, rr, "public_actor_person_id", "accused_person_id")
        partner = rr.person(payload.get("paramour_person_id"))
        betrayed = _betrayed_clause(payload, rr)
        witnesses = _witness_clause(payload, rr)
        return _choose_text(
            row,
            payload,
            "public.scandal.rumor",
            (
                f"Whispers in {place} joined {actor} with {partner} in "
                f"{_kind(payload)}{betrayed}{witnesses}.",
                f"The household rumor at {place} put {actor} and {partner} "
                f"together in {year}, calling it {_kind(payload)}{betrayed}.",
                f"No household spoke plainly, but {place} repeated {actor} and "
                f"{partner}'s names beside {_kind(payload)}{witnesses}.",
            ),
        )
    if event_type == "public_virtue":
        benefactor = _public_person(row, payload, rr, "public_actor_person_id", "benefactor_person_id")
        beneficiary = _public_person(row, payload, rr, "public_victim_person_id", "beneficiary_person_id")
        return _choose_text(
            row,
            payload,
            "public.virtue.rumor",
            (
                f"It was said in {place} that {benefactor} aided {beneficiary} "
                f"through {_kind(payload)}{_value_clause('relief', payload.get('relief_value'))}.",
                f"Public gratitude in {place} attached {benefactor}'s name to "
                f"{beneficiary}'s relief, though the account stayed uncertain.",
                f"The story in {place} praised {benefactor} for {_kind(payload)}, "
                f"but later tellers were unsure how much reached {beneficiary}.",
            ),
        )
    if event_type == "knowledge_culture":
        creator = _public_person(row, payload, rr, "public_actor_person_id", "creator_person_id")
        focus = _knowledge_focus(payload)
        return _choose_text(
            row,
            payload,
            "public.knowledge.rumor",
            (
                f"Learned rumor in {place} credited {creator} with {focus}, "
                f"though the workshop account remained uncertain.",
                f"It was said in {place} that {creator}'s hand lay behind {focus}; "
                f"the tale named {_motive(payload)} as the spur.",
                f"The school talk of {place} preserved {focus} as {creator}'s work, "
                f"but not every witness agreed.",
            ),
        )
    if event_type.startswith("city_state_"):
        focus = _city_state_focus(event_type, payload)
        cause = _label(distortion.get("rumored_cause") or payload.get("reason"))
        cause_clause = "" if cause == "unknown" else f"; the story gave {cause} as the cause"
        return _choose_text(
            row,
            payload,
            "public.city_state.rumor",
            (
                f"City rumor in {place} carried {focus} from {year}{cause_clause}.",
                f"The civic tale at {place} said {focus} marked {year}{cause_clause}.",
                f"Later talk in {place} remembered {focus}, though the terms stayed unsettled.",
            ),
        )
    if event_type in {
        "settlement_move_planned",
        "settlement_move_dropped",
        "settlement_moved",
    }:
        actor = _public_person(row, payload, rr, "public_actor_person_id", "person_id")
        cause = _label(distortion.get("rumored_cause") or "unknown pressure")
        return f"Rumor in {place} said {actor}'s move in {year} was driven by {cause}."
    if event_type in {"office_selection", "office_succession"}:
        holder = _public_person(row, payload, rr, "public_actor_person_id", "holder_person_id")
        cause = _label(distortion.get("rumored_cause") or "a disputed claim")
        return f"Court rumor in {place} said {holder}'s office in {year} rested on {cause}."
    if event_type.startswith("polity_") or event_type == "dynastic_marriage_alliance":
        cause = _label(distortion.get("rumored_cause") or "uncertain terms")
        return f"Court rumor in {place} explained the {_label(event_type)} of {year} as {cause}."
    if event_type.startswith("campaign_") or event_type == "battle_fought":
        cause = _label(
            distortion.get("rumored_outcome")
            or distortion.get("rumored_cause")
            or "uncertain war news"
        )
        return f"War rumor in {place} carried the {_label(event_type)} of {year} as {cause}."
    return f"It was said in {place} that {_label(event_type)} occurred in {year}."


def _misattributed_text(
    event_type: str,
    year: int,
    payload: Mapping[str, Any],
    distortion: Mapping[str, Any],
    place: str,
    rr: EventProseResolver,
    row: Mapping[str, Any],
) -> str:
    summary = _distortion_summary(
        distortion,
        ("misattributed_summary", "false_summary", "public_summary"),
    )
    if summary:
        return _contextual_public_summary(summary, year, place)
    if event_type == "murder":
        actor = _public_person(row, payload, rr, "public_actor_person_id")
        victim = _public_person(row, payload, rr, "public_victim_person_id", "victim_person_id")
        return _choose_text(
            row,
            payload,
            "public.murder.misattributed",
            (
                f"A public account in {place} named {actor} as the killer of "
                f"{victim} in {year}.",
                f"The mistaken roll of {place} put {actor}'s name beside "
                f"{victim}'s death in {year}.",
                f"Later correction was needed: the public tale from {place} "
                f"charged {actor} for {victim}'s killing.",
            ),
        )
    if event_type == "property_crime":
        actor = _public_person(row, payload, rr, "public_actor_person_id")
        victim = _public_person(row, payload, rr, "public_victim_person_id", "target_person_id")
        return _choose_text(
            row,
            payload,
            "public.property.misattributed",
            (
                f"A public account in {place} blamed {actor} for {_kind(payload)} "
                f"against {victim}.",
                f"The market's mistaken version in {place} attached "
                f"{victim}'s loss to {actor}.",
                f"The copied ledger from {place} preserved {actor}'s name for "
                f"{_kind(payload)}, though the factual account differed.",
            ),
        )
    if event_type == "affair_scandal":
        actor = _public_person(row, payload, rr, "public_actor_person_id")
        partner = _public_person(row, payload, rr, "public_victim_person_id", "paramour_person_id")
        return _choose_text(
            row,
            payload,
            "public.scandal.misattributed",
            (
                f"A mistaken household account in {place} joined {actor} with "
                f"{partner} in {_kind(payload)}.",
                f"The wrong names traveled through {place}: {actor} and {partner} "
                f"were tied to {_kind(payload)}.",
                f"A public scandal notice from {place} preserved {actor}'s name, "
                f"though the factual record named another party.",
            ),
        )
    if event_type == "public_virtue":
        actor = _public_person(row, payload, rr, "public_actor_person_id")
        beneficiary = _public_person(row, payload, rr, "public_victim_person_id", "beneficiary_person_id")
        return _choose_text(
            row,
            payload,
            "public.virtue.misattributed",
            (
                f"A public account in {place} credited {actor} with "
                f"{_kind(payload)} for {beneficiary}.",
                f"The grateful tale from {place} gave {actor} the honor for "
                f"{beneficiary}'s aid.",
                f"Later comparison showed the civic praise at {place} had placed "
                f"{_kind(payload)} under {actor}'s name.",
            ),
        )
    if event_type == "knowledge_culture":
        actor = _public_person(row, payload, rr, "public_actor_person_id")
        return _choose_text(
            row,
            payload,
            "public.knowledge.misattributed",
            (
                f"A learned account in {place} ascribed {_knowledge_focus(payload)} "
                f"to {actor}.",
                f"The wrong workshop name survived at {place}: {actor} was credited "
                f"with {_knowledge_focus(payload)}.",
                f"Copyists in {place} attached {_knowledge_focus(payload)} to "
                f"{actor}, though the factual record named another creator.",
            ),
        )
    if event_type == "death":
        victim = _public_person(row, payload, rr, "public_victim_person_id", "person_id")
        cause = _label(distortion.get("public_cause") or distortion.get("false_cause"))
        if cause != "unknown":
            return (
                f"The public account of {place} gave {cause} as the cause of "
                f"{victim}'s death in {year}."
            )
    return (
        f"A public account in {place} preserved a mistaken version of "
        f"{_label(event_type)} from {year}."
    )


def _public_unknown_text(
    event_type: str,
    year: int,
    payload: Mapping[str, Any],
    distortion: Mapping[str, Any],
    place: str,
    rr: EventProseResolver,
    row: Mapping[str, Any],
) -> str:
    summary = _distortion_summary(
        distortion, ("unknown_summary", "public_unknown_summary", "public_summary")
    )
    if summary:
        return _contextual_public_summary(summary, year, place)
    if event_type == "murder":
        victim = _public_person(row, payload, rr, "public_victim_person_id", "victim_person_id")
        return _choose_text(
            row,
            payload,
            "public.murder.unknown",
            (
                f"In {place}, {victim} was known only to have gone missing in {year}; "
                f"who was responsible and why remained unknown.",
                f"The first notice from {place} named {victim} absent in {year}, "
                f"but gave no killer, witness, or motive.",
                f"Household memory at {place} kept {victim}'s disappearance, "
                f"while the cause stayed unspoken.",
            ),
        )
    if event_type == "property_crime":
        victim = _public_person(row, payload, rr, "public_victim_person_id", "target_person_id")
        return _choose_text(
            row,
            payload,
            "public.property.unknown",
            (
                f"The market memory of {place} knew {victim} suffered a loss in "
                f"{year}, but not who took it or why.",
                f"An unsettled account at {place} recorded {_kind(payload)} "
                f"against {victim} in {year}, with culprit and motive left blank.",
                f"The public ledger of {place} kept {victim}'s loss, but not the "
                f"hand behind it.",
            ),
        )
    if event_type == "affair_scandal":
        return _choose_text(
            row,
            payload,
            "public.scandal.unknown",
            (
                f"The household memory of {place} knew {_kind(payload)} broke in "
                f"{year}, but not which names could be safely spoken.",
                f"A damaged family notice from {place} preserved {_kind(payload)}, "
                f"while the partners' names fell out of the public copy.",
                f"The public tale at {place} remembered {_kind(payload)}, but not "
                f"the witnesses or the full cause.",
            ),
        )
    if event_type == "public_virtue":
        beneficiary = _public_person(row, payload, rr, "public_victim_person_id", "beneficiary_person_id")
        return _choose_text(
            row,
            payload,
            "public.virtue.unknown",
            (
                f"The public memory of {place} knew aid reached {beneficiary} in "
                f"{year}, but not whose hand gave it.",
                f"A civic notice at {place} kept {_kind(payload)} for "
                f"{beneficiary} from {year}, while the benefactor's name was missing.",
                f"Gratitude survived at {place}, but the record did not say who "
                f"stood behind {beneficiary}'s relief.",
            ),
        )
    if event_type == "knowledge_culture":
        return _choose_text(
            row,
            payload,
            "public.knowledge.unknown",
            (
                f"The public memory of {place} knew {_knowledge_focus(payload)} "
                f"had appeared in {year}, but not whose work it was.",
                f"A learned fragment from {place} preserved "
                f"{_knowledge_focus(payload)}, while the creator's name was absent.",
                f"The workshop notice at {place} kept the breakthrough, but not "
                f"the patron, witnesses, or maker.",
            ),
        )
    if event_type.startswith("city_state_"):
        return _choose_text(
            row,
            payload,
            "public.city_state.unknown",
            (
                f"The public memory of {place} knew a city-state change marked {year}, but not its cause or terms.",
                f"A civic notice at {place} preserved {year} as a turning point, while the details fell out of the public copy.",
                f"The city chronicle of {place} kept the year {year}, but not the agreement, dispute, or office pressure behind it.",
            ),
        )
    if event_type == "death":
        victim = _public_person(row, payload, rr, "public_victim_person_id", "person_id")
        return (
            f"The public memory of {place} knew {victim} had died in {year}, "
            f"but not the cause."
        )
    if event_type in {
        "settlement_move_planned",
        "settlement_move_dropped",
        "settlement_moved",
    }:
        actor = _public_person(row, payload, rr, "public_actor_person_id", "person_id")
        return (
            f"The public memory of {place} knew {actor} moved in {year}, "
            f"but not the route or cause."
        )
    if event_type in {"office_selection", "office_succession"}:
        title = _label(payload.get("title_id") or "office")
        return (
            f"The public court memory of {place} knew {title} changed hands in {year}, "
            f"but not whose claim carried it."
        )
    if event_type.startswith("polity_") or event_type == "dynastic_marriage_alliance":
        return (
            f"The public memory of {place} knew a court or polity change marked {year}, "
            f"but not the cause or terms."
        )
    if event_type.startswith("campaign_"):
        unknown_part = "outcome" if event_type == "campaign_ended" else "cause"
        return (
            f"The public memory of {place} kept war news from {year}, "
            f"but not the {unknown_part}."
        )
    if event_type == "battle_fought":
        return (
            f"The public memory of {place} knew a battle was fought in {year}, "
            f"but not the losses."
        )
    return (
        f"The public memory of {place} knew that {_label(event_type)} touched "
        f"the year {year}, but not what truly happened."
    )


def _lost_memory_text(
    event_type: str,
    year: int,
    payload: Mapping[str, Any],
    place: str,
    rr: EventProseResolver,
    row: Mapping[str, Any],
) -> str:
    focus = _event_focus(event_type, payload, rr)
    lost_year = _coerce_int(row.get("lost_year"))
    lost = f" after {lost_year}" if lost_year is not None else ""
    return _choose_text(
        row,
        payload,
        "public.lost",
        (
            f"No living chronicle preserved {focus} from {year}{lost}; only the "
            f"factual archive still names {place}.",
            f"The local roll at {place} lost its account of {focus} from {year}; "
            f"the public record fell silent.",
            f"By later reckoning, {place} had no active memory of {focus} from "
            f"{year}, though the admin archive retained the fact.",
        ),
    )


def _sealed_memory_text(
    event_type: str,
    year: int,
    payload: Mapping[str, Any],
    place: str,
    rr: EventProseResolver,
    row: Mapping[str, Any],
) -> str:
    focus = _event_focus(event_type, payload, rr)
    source = _source_text(row, payload, rr) or "a closed archive"
    return _choose_text(
        row,
        payload,
        "public.sealed",
        (
            f"A sealed record in {source} held {focus} from {year}, keeping its "
            f"public account from {place}.",
            f"The account of {focus} at {place} was preserved under seal in "
            f"{source}, not in open chronicle.",
            f"{source} kept the hidden notice of {focus} from {year}; public "
            f"memory at {place} could not inspect it.",
        ),
    )


def _rediscovered_memory_text(
    event_type: str,
    year: int,
    payload: Mapping[str, Any],
    place: str,
    rr: EventProseResolver,
    row: Mapping[str, Any],
) -> str:
    if event_type == "birth":
        return (
            f"A later hand recovered the lineage notice: "
            f"{rr.person(payload.get('person_id'))} was born in {year} at {place}."
        )
    if event_type == "death":
        return (
            f"A later hand recovered the mortuary notice: "
            f"{rr.person(payload.get('person_id'))} died in {year} at {place}."
        )
    focus = _event_focus(event_type, payload, rr)
    source = _record_source_clause(row, payload, rr)
    return _choose_text(
        row,
        payload,
        "public.rediscovered",
        (
            f"A later hand recovered {focus} from {year}{source}, restoring "
            f"{place} to the record.",
            f"The damaged memory of {focus} resurfaced at {place}{source}, "
            f"linking the old year {year} back to public history.",
            f"A recovered notice at {place}{source} returned {focus} from {year} "
            f"to the chronicle.",
        ),
    )


def _private_memory_text(
    event_type: str,
    year: int,
    payload: Mapping[str, Any],
    place: str,
    rr: EventProseResolver,
) -> str:
    if event_type == "birth":
        return (
            f"A household memory at {place} kept the birth of "
            f"{rr.person(payload.get('person_id'))} in {year}."
        )
    return f"A private memory at {place} kept {_label(event_type)} from {year}."


def _choose_text(
    row: Mapping[str, Any],
    payload: Mapping[str, Any],
    salt: str,
    variants: tuple[str, ...],
) -> str:
    if not variants:
        return ""
    if len(variants) == 1:
        return variants[0]
    key_parts = (
        salt,
        str(row.get("event_id") or row.get("id") or ""),
        str(row.get("record_id") or ""),
        str(row.get("prose_variant_key") or ""),
        str(row.get("visibility_state") or ""),
        str(payload.get("event_type") or ""),
        str(payload.get("year") or ""),
    )
    digest = hashlib.sha256("|".join(key_parts).encode("utf-8")).digest()
    return variants[int.from_bytes(digest[:4], "big") % len(variants)]


def _event_focus(event_type: str, payload: Mapping[str, Any], rr: EventProseResolver) -> str:
    if payload.get("archetype_key"):
        return f"{_archetype_label(payload)} {_kind(payload)} by {rr.person(_archetype_actor_id(payload))}"
    if event_type == "murder":
        return f"the killing of {rr.person(payload.get('victim_person_id'))}"
    if event_type == "property_crime":
        return (
            f"{_kind(payload)} against "
            f"{rr.person(payload.get('target_person_id'))}"
        )
    if event_type == "affair_scandal":
        return (
            f"{_kind(payload)} involving {rr.person(payload.get('accused_person_id'))} "
            f"and {rr.person(payload.get('paramour_person_id'))}"
        )
    if event_type == "public_virtue":
        return (
            f"{_kind(payload)} for "
            f"{rr.person(payload.get('beneficiary_person_id'))}"
        )
    if event_type == "knowledge_culture":
        return _knowledge_focus(payload)
    if event_type.startswith("city_state_"):
        return _city_state_focus(event_type, payload)
    if event_type == "birth":
        return f"the birth of {rr.person(payload.get('person_id'))}"
    if event_type == "death":
        return f"the death of {rr.person(payload.get('person_id'))}"
    if event_type == "event_rediscovered":
        return f"the rediscovery of event {payload.get('original_event_id')}"
    return _label(event_type)


def _archetype_actor_id(payload: Mapping[str, Any]) -> object:
    for key in (
        "actor_person_id",
        "creator_person_id",
        "benefactor_person_id",
        "patron_person_id",
        "perpetrator_person_id",
        "accused_person_id",
        "person_id",
    ):
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _archetype_label(payload: Mapping[str, Any]) -> str:
    return _label(
        payload.get("archetype_display_name")
        or payload.get("archetype_bucket")
        or payload.get("archetype_key")
        or "remarkable figure"
    )


def _witness_clause(payload: Mapping[str, Any], rr: EventProseResolver) -> str:
    ids = _person_ids(
        payload.get("witness_person_ids")
        or payload.get("witness_ids")
        or payload.get("witnesses")
    )
    if not ids:
        return ""
    names = _person_list_clause([rr.person(pid) for pid in ids])
    plural = "witnesses" if len(ids) != 1 else "witness"
    return f", with {names} named as {plural}"


def _betrayed_clause(payload: Mapping[str, Any], rr: EventProseResolver) -> str:
    ids = _person_ids(
        payload.get("betrayed_partner_person_ids")
        or payload.get("betrayed_partner_ids")
        or payload.get("betrayed_partner_person_id")
    )
    if not ids:
        return ""
    names = _person_list_clause([rr.person(pid) for pid in ids])
    return f", with {names} recorded as betrayed"


def _value_clause(label: str, value: object) -> str:
    number = _number(value)
    if number == "unknown":
        return ""
    return f"; {label} {number}"


def _motive_clause(payload: Mapping[str, Any]) -> str:
    motive = _motive(payload)
    if motive == "unknown":
        return ""
    if _crime_context_value(payload, "motive_prose") not in (None, "") or payload.get("motive_prose"):
        return f"; {motive}"
    return f"; motive {motive}"


def _knowledge_focus(payload: Mapping[str, Any]) -> str:
    kind = _kind(payload)
    domain = _label(payload.get("knowledge_domain"))
    analogue = _label(
        payload.get("innovation_analogue_name")
        or payload.get("source_innovation_title")
    )
    if analogue != "unknown":
        return f"{analogue}, {_article(kind)} {kind} in {domain}"
    if domain != "unknown":
        return f"{kind} in {domain}"
    return kind


def _city_state_focus(event_type: str, payload: Mapping[str, Any]) -> str:
    et = _label(event_type)
    if event_type == "city_state_urban_consolidation":
        return "the city-state entered the civic record"
    if event_type == "city_state_public_works":
        project = _label(payload.get("civic_project") or "public works")
        return f"the city undertook {project}"
    if event_type == "city_state_resource_dispute":
        dispute = _label(payload.get("dispute_kind") or "resource dispute")
        other = payload.get("polity_b_id")
        other_clause = " with a rival city" if other not in (None, "") else ""
        return f"the city-state opened {dispute}{other_clause}"
    if event_type == "city_state_league_formed":
        members = _count_list(payload.get("member_polity_ids"))
        return f"a defensive city league formed with {members} members"
    if event_type == "city_state_hegemony_declared":
        members = _count_list(payload.get("member_polity_ids"))
        return f"the city claimed hegemony over a league of {members} members"
    if event_type == "city_state_colony_status_changed":
        level = _label(payload.get("colony_autonomy_level") or payload.get("autonomy_state"))
        mother = payload.get("mother_settlement_id")
        mother_clause = " from its mother city" if mother not in (None, "") else ""
        return f"a maritime colony shifted to {level}{mother_clause}"
    if event_type == "city_state_autonomy_changed":
        old = _label(payload.get("from_autonomy_state"))
        new = _label(payload.get("autonomy_state"))
        return f"the city-state changed autonomy from {old} to {new}"
    if event_type == "city_state_civic_crisis":
        reason = _label(payload.get("crisis_reason") or "civic crisis")
        return f"the city-state entered {reason}"
    if event_type == "city_state_civic_reform":
        reform = _label(payload.get("reform_kind") or "civic reform")
        return f"the city-state enacted {reform}"
    if event_type == "city_state_occupation_imposed":
        return "the city-state came under occupation"
    if event_type == "city_state_liberated":
        return "the city-state recovered local freedom"
    if event_type == "city_state_tribute_imposed":
        return "tribute was imposed on the city-state"
    if event_type == "city_state_garrison_installed":
        return "a garrison was installed in the city-state"
    if event_type == "city_state_league_broken":
        reason = _label(payload.get("breakdown_reason") or "league breakdown")
        return f"the city league broke over {reason}"
    if event_type == "city_state_tyranny_usurpation":
        return "a contested tyranny seized civic authority"
    if event_type == "city_state_exile_decreed":
        return "the city-state decreed a political exile"
    if event_type == "city_state_debt_relief":
        return "the city-state enacted debt relief"
    return et


def _count_list(value: object) -> int:
    if isinstance(value, (list, tuple, set)):
        return len(value)
    if value is None or value == "":
        return 0
    text = str(value).strip()
    if not text:
        return 0
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, (list, tuple, set)):
        return len(parsed)
    return len([part for part in text.replace(";", ",").split(",") if part.strip()])


def _move_destination_clause(payload: Mapping[str, Any], rr: EventProseResolver) -> str:
    source = rr.settlement(payload.get("from_settlement_id"))
    dest = rr.settlement(payload.get("to_settlement_id"))
    if source and dest:
        return f", from {source} to {dest}"
    if dest:
        return f", toward {dest}"
    return ""


def _move_reason_clause(payload: Mapping[str, Any]) -> str:
    reason = _label(payload.get("move_reason") or payload.get("source_event"))
    if reason == "unknown":
        return ""
    return f"; cause {reason}"


def _record_source_clause(
    row: Mapping[str, Any], payload: Mapping[str, Any], rr: EventProseResolver
) -> str:
    source = _source_text(row, payload, rr)
    return f", through {source}" if source else ""


def _source_text(
    row: Mapping[str, Any], payload: Mapping[str, Any], rr: EventProseResolver
) -> str:
    inst = str(
        row.get("source_institution_id")
        or payload.get("source_institution_id")
        or ""
    ).strip()
    if inst:
        return _label(inst)
    pid = _coerce_int(row.get("source_person_id") or payload.get("source_person_id"))
    if pid is not None:
        return rr.person(pid)
    return ""


def _person_ids(value: object) -> list[int]:
    if value is None or value == "":
        return []
    raw_values: Iterable[object]
    if isinstance(value, (list, tuple, set)):
        raw_values = value
    else:
        text = str(value).strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, (list, tuple)):
            raw_values = parsed
        else:
            raw_values = text.replace(";", ",").split(",")
    ids: list[int] = []
    for raw in raw_values:
        pid = _coerce_int(raw)
        if pid is not None and pid not in ids:
            ids.append(pid)
    return ids


def _person_list_clause(names: list[str]) -> str:
    clean = [name for name in names if name and name != "an unknown person"]
    if not clean:
        return "unknown witnesses"
    if len(clean) == 1:
        return clean[0]
    if len(clean) == 2:
        return f"{clean[0]} and {clean[1]}"
    return f"{clean[0]}, {clean[1]}, and {len(clean) - 2} others"


def _article(text: str) -> str:
    clean = str(text or "").strip().lower()
    return "an" if clean[:1] in {"a", "e", "i", "o", "u"} else "a"


def _event_place(
    row: Mapping[str, Any], payload: Mapping[str, Any], rr: EventProseResolver
) -> str:
    settlement = (
        row.get("settlement_id")
        or payload.get("settlement_id")
        or payload.get("near_settlement_id")
        or payload.get("custody_site_settlement_id")
        or payload.get("from_settlement_id")
    )
    region = row.get("region_id") or payload.get("region_id")
    return rr.place(settlement, region)


def _record_place(
    row: Mapping[str, Any], payload: Mapping[str, Any], rr: EventProseResolver
) -> str:
    settlement = (
        row.get("preserving_settlement_id")
        or row.get("settlement_id")
        or payload.get("settlement_id")
        or payload.get("near_settlement_id")
        or payload.get("custody_site_settlement_id")
    )
    region = (
        row.get("preserving_region_id") or row.get("region_id") or payload.get("region_id")
    )
    return rr.place(settlement, region)


def _optional_person_phrase(rr: EventProseResolver, person_id: object) -> str:
    pid = _coerce_int(person_id)
    if pid is None:
        return ""
    return f", under the patronage of {rr.person(pid)}"


def _source_label(payload: Mapping[str, Any]) -> str:
    inst = str(payload.get("source_institution_id") or "").strip()
    if inst:
        return _label(inst)
    pid = _coerce_int(payload.get("source_person_id"))
    if pid is not None:
        return f"person {pid}"
    return "unknown"


def _payload(
    row: Mapping[str, Any],
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    if conn is not None:
        from library.world_save import event_payload_from_row

        return event_payload_from_row(row, conn, expand=True)
    value = row.get("payload_json")
    return _json_object(value)


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if value is None:
        return {}
    text = str(value).strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _public_knowledge_stage(visibility_state: object) -> str:
    state = _clean_key(visibility_state)
    if state in PUBLIC_UNKNOWN_VISIBILITY_STATES:
        return "unknown"
    if state in PUBLIC_RUMOR_VISIBILITY_STATES:
        return "rumored"
    if state in PUBLIC_KNOWN_VISIBILITY_STATES:
        return "known"
    if state == "admin_known":
        return "admin"
    return "not_public"


def _public_person(
    row: Mapping[str, Any],
    payload: Mapping[str, Any],
    rr: EventProseResolver,
    public_key: str,
    *payload_keys: str,
) -> str:
    pid = _coerce_int(row.get(public_key))
    if pid is not None:
        return rr.person(pid)
    for key in payload_keys:
        pid = _coerce_int(payload.get(key))
        if pid is not None:
            return rr.person(pid)
    return "an unknown person"


def _public_person_without_truth_fallback(
    row: Mapping[str, Any],
    rr: EventProseResolver,
    public_key: str,
    *,
    unknown: str = "an unknown person",
) -> str:
    pid = _coerce_int(row.get(public_key))
    return rr.person(pid) if pid is not None else unknown


def _distortion_summary(
    distortion: Mapping[str, Any], keys: tuple[str, ...]
) -> str:
    for key in keys:
        value = distortion.get(key)
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _contextual_public_summary(summary: str, year: int, place: str) -> str:
    text = summary.strip().replace("\n", " ")
    if "{year}" in text or "{place}" in text:
        try:
            text = text.format(year=year, place=place)
        except (KeyError, ValueError):
            pass
    if str(year) in text or place in text:
        return text
    return f"{year}, {place}: {text}"


def _mapping(row: Mapping[str, Any] | sqlite3.Row) -> dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        return {key: row[key] for key in row.keys()}
    return dict(row)


def _fetch_dicts(
    conn: sqlite3.Connection, sql: str, params: list[Any]
) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params)
    columns = [str(col[0]) for col in cur.description]
    rows: list[dict[str, Any]] = []
    for raw in cur.fetchall():
        if isinstance(raw, sqlite3.Row):
            rows.append({key: raw[key] for key in raw.keys()})
        else:
            rows.append(dict(zip(columns, raw)))
    return rows


def _add_in_clause(
    clauses: list[str],
    params: list[Any],
    column: str,
    values: Iterable[object] | None,
) -> None:
    if values is None:
        return
    clean = [str(v).strip() for v in values if str(v).strip()]
    if not clean:
        clauses.append("0")
        return
    placeholders = ", ".join("?" for _ in clean)
    clauses.append(f"{column} IN ({placeholders})")
    params.extend(clean)


def _add_search_clause(
    clauses: list[str],
    params: list[Any],
    search: str,
    columns: Iterable[str],
) -> None:
    text = str(search or "").strip()
    if not text:
        return
    like = f"%{text}%"
    clean_columns = [str(column).strip() for column in columns if str(column).strip()]
    if not clean_columns:
        return
    clauses.append(
        "(" + " OR ".join(f"CAST({column} AS TEXT) LIKE ?" for column in clean_columns) + ")"
    )
    params.extend([like] * len(clean_columns))


def _where_sql(clauses: Iterable[str]) -> str:
    clean = [str(clause).strip() for clause in clauses if str(clause).strip()]
    return f"WHERE {' AND '.join(clean)}" if clean else ""


def _row_value(row: Any, key: str, index: int, default: Any = None) -> Any:
    try:
        return row[key]
    except (IndexError, KeyError, TypeError):
        try:
            return row[index]
        except (IndexError, TypeError):
            return default


def _coerce_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_key(value: object) -> str:
    return str(value or "").strip()


def _kind(payload: Mapping[str, Any]) -> str:
    return _label(payload.get("incident_kind") or payload.get("event_type") or "event")


def _murder_category_clause(payload: Mapping[str, Any]) -> str:
    category = murder_payload_taxonomy_category(payload)
    if not category:
        return ""
    return f"; category: {serious_crime_category_label(category)}"


def _motive(payload: Mapping[str, Any]) -> str:
    for key in ("motive_prose", "motive_detail"):
        value = payload.get(key)
        if value not in (None, ""):
            return _label(value)
    for key in ("motive_prose", "motive_detail"):
        value = _crime_context_value(payload, key)
        if value not in (None, ""):
            return _label(value)
    return _label(payload.get("motive") or "unknown")


def _crime_context_value(payload: Mapping[str, Any], key: str) -> Any:
    context = payload.get("crime_context")
    if isinstance(context, Mapping):
        return context.get(key)
    return None


def _label(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown"
    return text.replace("_", " ")


def _humanize_slug(value: str) -> str:
    text = value.strip().replace("_", " ").replace("-", " ")
    return " ".join(part.capitalize() for part in text.split()) or value


def _humanize_settlement_id(value: str) -> str:
    if ":settlement:" in value:
        region, number = value.split(":settlement:", 1)
        return f"settlement {number} of {_humanize_slug(region)}"
    return _humanize_slug(value.replace(":", " "))


def _number(value: object) -> str:
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return "unknown"
