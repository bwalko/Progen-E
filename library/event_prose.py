"""Deterministic prose rendering for simulation events and memory records.

The save DB stores factual event payloads plus compact event-record metadata.
This module derives display prose from those structured facts without adding
large text columns to ``save.sqlite``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import json
import sqlite3
from typing import Any


PUBLIC_CHRONICLE_VISIBILITY_STATES: frozenset[str] = frozenset(
    {"public_known", "rumored", "rediscovered"}
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
    payload = _payload(row)
    event_id = _coerce_int(row.get("event_id", row.get("id"))) or 0
    year = _coerce_int(row.get("sim_year", payload.get("year"))) or 0
    event_type = _clean_key(row.get("event_type") or payload.get("event_type"))
    rr = resolver or EventProseResolver()
    place = _event_place(row, payload, rr)
    template_key = f"admin.{event_type or 'event'}.default"

    if event_type == "murder":
        prose = (
            f"{year}: {rr.person(payload.get('killer_person_id'))} killed "
            f"{rr.person(payload.get('victim_person_id'))} at {place}; "
            f"{_kind(payload)}; motive: {_motive(payload)}; "
            f"importance {_number(payload.get('historical_importance'))}."
        )
    elif event_type == "property_crime":
        prose = (
            f"{year}: {rr.person(payload.get('perpetrator_person_id'))} committed "
            f"{_kind(payload)} against {rr.person(payload.get('target_person_id'))} "
            f"at {place}; motive: {_motive(payload)}; "
            f"loss {_number(payload.get('loss_value'))}."
        )
    elif event_type == "affair_scandal":
        prose = (
            f"{year}: {rr.person(payload.get('accused_person_id'))} and "
            f"{rr.person(payload.get('paramour_person_id'))} were exposed in "
            f"{_kind(payload)} at {place}; motive: {_motive(payload)}."
        )
    elif event_type == "public_virtue":
        prose = (
            f"{year}: {rr.person(payload.get('benefactor_person_id'))} performed "
            f"{_kind(payload)} for {rr.person(payload.get('beneficiary_person_id'))} "
            f"at {place}; motive: {_motive(payload)}; "
            f"relief {_number(payload.get('relief_value'))}."
        )
    elif event_type == "knowledge_culture":
        patron = _optional_person_phrase(rr, payload.get("patron_person_id"))
        prose = (
            f"{year}: {rr.person(payload.get('creator_person_id'))} produced "
            f"{_kind(payload)} in {_label(payload.get('knowledge_domain'))} "
            f"at {place}{patron}; motive: {_motive(payload)}; "
            f"novelty {_number(payload.get('novelty_value'))}."
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
        prose = f"{year}: {rr.person(payload.get('person_id'))} died at {place}."
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
    payload = _payload(row)
    event_type = admin.event_type
    year = admin.sim_year
    place = _record_place(row, payload, rr)
    state = _clean_key(row.get("visibility_state"))
    record_type = _clean_key(row.get("record_type"))
    variant = str(row.get("prose_variant_key") or "").strip()
    if not variant:
        variant = f"{record_type or 'event_memory'}.{state or 'public_known'}.default"

    if state == "lost":
        public = (
            f"No living chronicle preserved the {_label(event_type)} of {year}; "
            f"only the factual archive still names {place}."
        )
    elif state == "sealed":
        source = str(row.get("source_institution_id") or "a closed archive").strip()
        public = (
            f"A sealed record in {source} held the {_label(event_type)} of {year}, "
            f"keeping its public account from {place}."
        )
    elif state == "private_known":
        public = _private_memory_text(event_type, year, payload, place, rr)
    elif state == "admin_known":
        public = f"Admin note: {admin.prose}"
    elif state == "rediscovered":
        public = _rediscovered_memory_text(event_type, year, payload, place, rr)
    elif state == "rumored":
        public = _rumor_text(event_type, year, payload, place, rr)
    else:
        public = _public_text(event_type, year, payload, place, rr)

    return EventRecordProse(
        event_id=admin.event_id,
        record_id=_coerce_int(row.get("record_id")) or 0,
        sim_year=year,
        event_type=event_type,
        record_type=record_type,
        visibility_state=state,
        prose_variant_key=variant,
        admin_summary=admin.prose,
        public_prose=public,
    )


def load_admin_event_summaries(
    conn: sqlite3.Connection,
    *,
    event_types: Iterable[str] | None = None,
    search: str = "",
    limit: int = 100,
    offset: int = 0,
) -> list[EventAdminSummary]:
    """Load factual admin summaries from ``simulation_events_readable``."""

    clauses: list[str] = []
    params: list[Any] = []
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
    visibility_states: Iterable[str] | None = None,
    event_types: Iterable[str] | None = None,
    search: str = "",
    limit: int = 100,
    offset: int = 0,
) -> list[EventRecordProse]:
    """Load record-aware prose rows from event-memory records."""

    clauses: list[str] = []
    params: list[Any] = []
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
    event_types: Iterable[str] | None = None,
    search: str = "",
    limit: int = 100,
    offset: int = 0,
) -> list[EventRecordProse]:
    """Load in-world public chronicle prose for visible/rumored records."""

    return load_event_record_prose_rows(
        conn,
        visibility_states=PUBLIC_CHRONICLE_VISIBILITY_STATES,
        event_types=event_types,
        search=search,
        limit=limit,
        offset=offset,
    )


def _public_text(
    event_type: str,
    year: int,
    payload: Mapping[str, Any],
    place: str,
    rr: EventProseResolver,
) -> str:
    if event_type == "murder":
        return (
            f"The record of {place} names {rr.person(payload.get('killer_person_id'))} "
            f"as the killer of {rr.person(payload.get('victim_person_id'))} in "
            f"{year}, remembered as {_kind(payload)}."
        )
    if event_type == "property_crime":
        return (
            f"The market record of {place} set down {_kind(payload)} by "
            f"{rr.person(payload.get('perpetrator_person_id'))} against "
            f"{rr.person(payload.get('target_person_id'))}."
        )
    if event_type == "affair_scandal":
        return (
            f"The household talk of {place} openly named "
            f"{rr.person(payload.get('accused_person_id'))} and "
            f"{rr.person(payload.get('paramour_person_id'))} in {_kind(payload)}."
        )
    if event_type == "public_virtue":
        return (
            f"The chronicle of {place} praised "
            f"{rr.person(payload.get('benefactor_person_id'))}, who performed "
            f"{_kind(payload)} for {rr.person(payload.get('beneficiary_person_id'))}."
        )
    if event_type == "knowledge_culture":
        patron = _optional_person_phrase(rr, payload.get("patron_person_id"))
        return (
            f"In {place}, {rr.person(payload.get('creator_person_id'))} was credited "
            f"with {_kind(payload)} in {_label(payload.get('knowledge_domain'))}"
            f"{patron}."
        )
    if event_type == "event_rediscovered":
        return (
            f"A later record at {place} brought event "
            f"{payload.get('original_event_id')} back into memory."
        )
    if event_type == "death":
        return f"The mortuary roll of {place} recorded the death of {rr.person(payload.get('person_id'))} in {year}."
    if event_type == "birth":
        return f"The lineage memory of {place} recorded the birth of {rr.person(payload.get('person_id'))} in {year}."
    return f"The chronicle of {place} recorded {_label(event_type)} in {year}."


def _rumor_text(
    event_type: str,
    year: int,
    payload: Mapping[str, Any],
    place: str,
    rr: EventProseResolver,
) -> str:
    if event_type == "murder":
        return (
            f"It was said in {place} that {rr.person(payload.get('killer_person_id'))} "
            f"killed {rr.person(payload.get('victim_person_id'))}; the tale called it "
            f"{_kind(payload)} and gave {_motive(payload)} as the cause."
        )
    if event_type == "property_crime":
        return (
            f"Market talk in {place} blamed "
            f"{rr.person(payload.get('perpetrator_person_id'))} for {_kind(payload)} "
            f"against {rr.person(payload.get('target_person_id'))}."
        )
    if event_type == "affair_scandal":
        return (
            f"Whispers in {place} joined {rr.person(payload.get('accused_person_id'))} "
            f"with {rr.person(payload.get('paramour_person_id'))} in {_kind(payload)}."
        )
    return f"It was said in {place} that {_label(event_type)} occurred in {year}."


def _rediscovered_memory_text(
    event_type: str,
    year: int,
    payload: Mapping[str, Any],
    place: str,
    rr: EventProseResolver,
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
    return (
        f"A later hand recovered the {_label(event_type)} of {year}, restoring "
        f"{place} to the record."
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


def _event_place(
    row: Mapping[str, Any], payload: Mapping[str, Any], rr: EventProseResolver
) -> str:
    settlement = row.get("settlement_id") or payload.get("settlement_id")
    region = row.get("region_id") or payload.get("region_id")
    return rr.place(settlement, region)


def _record_place(
    row: Mapping[str, Any], payload: Mapping[str, Any], rr: EventProseResolver
) -> str:
    settlement = (
        row.get("preserving_settlement_id")
        or row.get("settlement_id")
        or payload.get("settlement_id")
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


def _payload(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row.get("payload_json")
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
    values: Iterable[str] | None,
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


def _motive(payload: Mapping[str, Any]) -> str:
    return _label(payload.get("motive") or "unknown")


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
